import fcntl
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_vault.models import ApprovedRecord, Decision, Proposal
from knowledge_vault.publisher import Publisher, PublisherLocked, load_approved


def record(markdown="# Note\nBody", decision="approved"):
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
            self.assertEqual([vault / "note.md"], published)
            self.assertEqual("# Note\nBody\n", published[0].read_text(encoding="utf-8"))
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
                Proposal.create("# Note\nBroken", "publish-key-2", approved.proposal.provenance),
                Decision(approved.proposal.id, 1, "alex", "approved", "Verified"),
            )
            failing, _ = self.publisher(root, [ApprovedRecord(revised.proposal, revised.decision)])
            with patch("knowledge_vault.publisher.os.replace", side_effect=OSError("disk full")):
                self.assertEqual([], failing.publish())
            self.assertEqual("# Note\nBody\n", note.read_text(encoding="utf-8"))
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


class NoteNamingTests(unittest.TestCase):
    def publisher(self, root, records):
        root = Path(root)
        return Publisher(root / "vault", lambda: list(records), root / "state"), root / "vault"

    def approved(self, markdown, predecessor=None):
        proposal = Proposal.create(markdown, f"key-{markdown[:12]}", {"agent": "hermes"}, predecessor)
        return ApprovedRecord(proposal, Decision(proposal.id, 1, "pedro", "approved", "ok"))

    def test_note_is_named_after_its_heading(self):
        with tempfile.TemporaryDirectory() as root:
            publisher, vault = self.publisher(root, [self.approved("# Primer ciclo real\nBody")])
            self.assertEqual(vault / "primer-ciclo-real.md", publisher.publish()[0])

    def test_note_without_a_heading_falls_back_to_its_id(self):
        with tempfile.TemporaryDirectory() as root:
            record = self.approved("Body with no heading")
            publisher, vault = self.publisher(root, [record])
            self.assertEqual(vault / f"{record.proposal.id}.md", publisher.publish()[0])

    def test_a_revision_replaces_the_note_it_supersedes(self):
        with tempfile.TemporaryDirectory() as root:
            original = self.approved("# Kubernetes\nFirst take")
            self.publisher(root, [original])[0].publish()
            revision = self.approved("# Kubernetes\nSecond take", predecessor=original.proposal.id)
            publisher, vault = self.publisher(root, [revision])
            published = publisher.publish()
            self.assertEqual([vault / "kubernetes.md"], published)
            self.assertEqual(1, len(list(vault.glob("*.md"))), "the superseded note was left behind")
            self.assertIn("Second take", published[0].read_text(encoding="utf-8"))

    def test_a_retitled_revision_moves_the_note(self):
        with tempfile.TemporaryDirectory() as root:
            original = self.approved("# Old title\nBody")
            self.publisher(root, [original])[0].publish()
            revision = self.approved("# New title\nBody", predecessor=original.proposal.id)
            publisher, vault = self.publisher(root, [revision])
            self.assertEqual([vault / "new-title.md"], publisher.publish())
            self.assertFalse((vault / "old-title.md").exists(), "the old title was orphaned")

    def test_unrelated_notes_sharing_a_title_do_not_clobber_each_other(self):
        with tempfile.TemporaryDirectory() as root:
            first, second = self.approved("# Notas\nOne"), self.approved("# Notas\nTwo")
            publisher, vault = self.publisher(root, [first, second])
            published = publisher.publish()
            self.assertEqual(2, len(set(published)), "one note overwrote the other")
            self.assertEqual(2, len(list(vault.glob("*.md"))))
            self.assertEqual(vault / "notas.md", published[0])


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
