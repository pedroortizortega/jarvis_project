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
            self.assertEqual([vault / f"{approved.proposal.id}.md"], published)
            self.assertEqual("# Note\nBody\n", published[0].read_text(encoding="utf-8"))
            self.assertEqual([], publisher.failures)
            self.assertEqual([], list(vault.glob("*.tmp*")))

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
