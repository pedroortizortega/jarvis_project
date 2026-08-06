import fcntl
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_vault.models import ApprovedRecord, Decision, Proposal
from knowledge_vault.note import body_of, parse_frontmatter
from knowledge_vault.publisher import Publisher, PublisherLocked, load_approved


def record(markdown="---\ntype: fact\n---\n# Note\nBody", decision="approved"):
    proposal = Proposal.create(markdown, "publish-key", {"agent": "hermes"})
    return ApprovedRecord(proposal, Decision(proposal.id, 1, "alex", decision, "Verified"))


class PublisherTests(unittest.TestCase):
    def publisher(self, root, records):
        vault, state = Path(root) / "vault", Path(root) / "state"
        return Publisher(vault, lambda: list(records), state), vault

    def test_approved_record_is_published_atomically(self):
        with tempfile.TemporaryDirectory() as root:
            approved = record()
            publisher, vault = self.publisher(root, [approved])
            published = publisher.publish()
            self.assertRegex(published[0].name, r"^\d{14}\.md$")
            self.assertEqual("# Note\nBody", body_of(published[0].read_text(encoding="utf-8")).strip())
            self.assertEqual([], publisher.failures)
            self.assertEqual([], list(vault.glob("*.tmp*")))

    def test_published_note_is_readable_by_the_vault_group(self):
        with tempfile.TemporaryDirectory() as root:
            publisher, _ = self.publisher(root, [record()])
            note = publisher.publish()[0]
            self.assertEqual(0o640, note.stat().st_mode & 0o777)

    def test_unapproved_or_invalid_record_is_never_written(self):
        with tempfile.TemporaryDirectory() as root:
            rejected, empty = record(decision="rejected"), record(markdown="   ")
            publisher, vault = self.publisher(root, [rejected, empty])
            self.assertEqual([], publisher.publish())
            self.assertEqual([], list(vault.glob("*.md")))
            self.assertEqual(
                [rejected.proposal.id, empty.proposal.id],
                [failure.proposal_id for failure in publisher.failures],
            )

    def test_write_failure_preserves_existing_published_note(self):
        with tempfile.TemporaryDirectory() as root:
            approved = record()
            publisher, vault = self.publisher(root, [approved])
            note = publisher.publish()[0]
            revised = ApprovedRecord(
                Proposal.create("---\ntype: fact\n---\n# Note\nBroken", "publish-key-2", approved.proposal.provenance),
                Decision(approved.proposal.id, 1, "alex", "approved", "Verified"),
            )
            failing, _ = self.publisher(root, [ApprovedRecord(revised.proposal, revised.decision)])
            with patch("knowledge_vault.publisher.os.replace", side_effect=OSError("disk full")):
                self.assertEqual([], failing.publish())
            self.assertEqual("# Note\nBody", body_of(note.read_text(encoding="utf-8")).strip())
            self.assertEqual([], list(vault.glob("*.tmp*")))
            self.assertEqual(1, len(failing.failures))

    def test_second_writer_is_fenced_out(self):
        with tempfile.TemporaryDirectory() as root:
            publisher, vault = self.publisher(root, [record()])
            publisher.publish()
            with publisher.lock_path.open("a+") as held:
                fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    with self.assertRaises(PublisherLocked):
                        self.publisher(root, [record()])[0].publish()
                finally:
                    fcntl.flock(held.fileno(), fcntl.LOCK_UN)
            self.assertEqual(1, len(list(vault.glob("*.md"))))


class NoteIdentityTests(unittest.TestCase):
    """Zettelkasten identity: the file name is an id that never changes, so a
    link written today still resolves after the note is retitled."""

    def publisher(self, root, records):
        root = Path(root)
        return Publisher(root / "vault", lambda: list(records), root / "state"), root / "vault"

    def approved(self, markdown, predecessor=None, note_type="fact"):
        markdown = f"---\ntype: {note_type}\n---\n{markdown}" if note_type else markdown
        proposal = Proposal.create(markdown, f"key-{len(markdown)}-{markdown[:20]}", {"agent": "hermes"}, predecessor)
        return ApprovedRecord(proposal, Decision(proposal.id, 1, "pedro", "approved", "ok"))

    def test_a_note_is_filed_under_a_timestamp_id(self):
        with tempfile.TemporaryDirectory() as root:
            publisher, vault = self.publisher(root, [self.approved("# Primer ciclo\nBody")])
            published = publisher.publish()[0]
            self.assertEqual(vault, published.parent)
            self.assertRegex(published.name, r"^\d{14}\.md$")

    def test_notes_created_in_the_same_second_get_distinct_ids(self):
        with tempfile.TemporaryDirectory() as root:
            records = [self.approved("# Una\nA"), self.approved("# Otra\nB")]
            publisher, _ = self.publisher(root, records)
            published = publisher.publish()
            self.assertEqual(2, len(set(published)))

    def test_publishing_the_same_record_twice_yields_one_note(self):
        """The approved record stays on disk, so a timer re-publishes it every
        few minutes. Without this each run filed a brand new note and the vault
        filled with copies of the same thing."""
        with tempfile.TemporaryDirectory() as root:
            approved = self.approved("# Un solo storage class\nCuerpo")
            first = self.publisher(root, [approved])[0].publish()[0]
            publisher, vault = self.publisher(root, [approved])
            self.assertEqual([first], publisher.publish(), "the same record was filed twice")
            self.assertEqual(1, len(list(vault.glob("*.md"))))

    def test_republishing_updates_the_note_in_place(self):
        with tempfile.TemporaryDirectory() as root:
            approved = self.approved("# Titulo\nPrimera")
            self.publisher(root, [approved])[0].publish()
            corrected = ApprovedRecord(
                Proposal(
                    approved.proposal.id,
                    "---\ntype: fact\n---\n# Titulo\nCorregida",
                    approved.proposal.idempotency_key,
                    approved.proposal.provenance,
                    None,
                ),
                approved.decision,
            )
            publisher, vault = self.publisher(root, [corrected])
            published = publisher.publish()[0]
            self.assertEqual(1, len(list(vault.glob("*.md"))))
            self.assertIn("Corregida", published.read_text(encoding="utf-8"))

    def test_a_revision_keeps_the_file_name_so_links_never_break(self):
        with tempfile.TemporaryDirectory() as root:
            original = self.approved("# Longhorn no esta instalado\nPrimera version")
            first = self.publisher(root, [original])[0].publish()[0]
            revision = self.approved("# Storage: solo local-path\nSegunda version", predecessor=original.proposal.id)
            publisher, vault = self.publisher(root, [revision])
            second = publisher.publish()[0]
            self.assertEqual(first, second, "the id moved and every link to it broke")
            self.assertEqual(1, len(list(vault.glob("*.md"))))
            self.assertIn("Segunda version", second.read_text(encoding="utf-8"))

    def test_a_retitled_note_stays_findable_under_its_old_title(self):
        with tempfile.TemporaryDirectory() as root:
            original = self.approved("# Longhorn no esta instalado\nCuerpo")
            self.publisher(root, [original])[0].publish()
            revision = self.approved("# Storage: solo local-path\nCuerpo", predecessor=original.proposal.id)
            publisher, _ = self.publisher(root, [revision])
            fields = parse_frontmatter(publisher.publish()[0].read_text(encoding="utf-8"))
            self.assertIn("Longhorn no esta instalado", fields["aliases"])
            self.assertIn("Storage: solo local-path", fields["aliases"])
            self.assertEqual("Storage: solo local-path", fields["title"])

    def test_the_published_note_carries_its_okf_envelope(self):
        with tempfile.TemporaryDirectory() as root:
            publisher, _ = self.publisher(root, [self.approved("# Con sobre\nCuerpo", note_type="decision")])
            fields = parse_frontmatter(publisher.publish()[0].read_text(encoding="utf-8"))
            self.assertEqual("decision", fields["type"])
            self.assertEqual("Con sobre", fields["title"])
            self.assertRegex(fields["id"], r"^\d{14}$")
            self.assertRegex(fields["timestamp"], r"^\d{4}-\d{2}-\d{2}T")

    def test_a_note_without_an_okf_type_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            publisher, vault = self.publisher(root, [self.approved("# Sin tipo\nCuerpo", note_type=None)])
            self.assertEqual([], publisher.publish())
            self.assertEqual([], list(vault.glob("*.md")))
            self.assertIn("type", publisher.failures[0].reason)


class UnpublishableTests(unittest.TestCase):
    """A record that can never be valid must be reported once, not on every run
    forever: a unit that is always red stops being read."""

    def publisher(self, root, records):
        root = Path(root)
        return Publisher(root / "vault", lambda: list(records), root / "state")

    def bad(self):
        proposal = Proposal.create("# Sin tipo\nCuerpo", "legacy-key", {"agent": "hermes"})
        return ApprovedRecord(proposal, Decision(proposal.id, 1, "pedro", "approved", "ok"))

    def test_it_is_reported_the_first_time(self):
        with tempfile.TemporaryDirectory() as root:
            publisher = self.publisher(root, [self.bad()])
            publisher.publish()
            self.assertEqual(1, len(publisher.failures))

    def test_it_is_not_reported_again(self):
        with tempfile.TemporaryDirectory() as root:
            record = self.bad()
            self.publisher(root, [record]).publish()
            again = self.publisher(root, [record])
            self.assertEqual([], again.publish())
            self.assertEqual([], again.failures, "a permanent failure was reported twice")

    def test_a_transient_failure_is_always_reported(self):
        with tempfile.TemporaryDirectory() as root:
            good = record()
            publisher = self.publisher(root, [good])
            with patch("knowledge_vault.atomic.os.replace", side_effect=OSError("disk full")):
                publisher.publish()
            self.assertEqual(1, len(publisher.failures))
            retry = self.publisher(root, [good])
            self.assertEqual(1, len(retry.publish()), "a transient failure was quarantined")


class ApprovedSpoolTests(unittest.TestCase):
    def test_malformed_spool_entries_are_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            spool = Path(root)
            approved = record()
            (spool / "good.json").write_text(
                json.dumps(
                    {
                        "proposal": approved.proposal.__dict__,
                        "decision": approved.decision.__dict__,
                    }
                ),
                encoding="utf-8",
            )
            (spool / "broken.json").write_text("{not json", encoding="utf-8")
            loaded = load_approved(spool)
        self.assertEqual([approved.proposal.id], [item.proposal.id for item in loaded])

    def test_malformed_spool_entries_are_reported_not_silently_dropped(self):
        reported = []
        with tempfile.TemporaryDirectory() as root:
            spool = Path(root)
            (spool / "broken.json").write_text("{not json", encoding="utf-8")
            (spool / "incomplete.json").write_text(json.dumps({"proposal": {}}), encoding="utf-8")
            self.assertEqual([], load_approved(spool, on_failure=reported.append))
        self.assertEqual(
            [str(spool / "broken.json"), str(spool / "incomplete.json")],
            [failure.proposal_id for failure in reported],
        )
        self.assertTrue(all(failure.reason for failure in reported))


if __name__ == "__main__":
    unittest.main()
