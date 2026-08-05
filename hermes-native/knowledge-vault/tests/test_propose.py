import json
import tempfile
import unittest
from pathlib import Path

from knowledge_vault.propose import propose, spool_sender
from knowledge_vault.review import run_review

TYPED = "---\ntype: infra-fact\ntags: [storage]\n---\n# Hallazgo\nEl storage class es local-path."


class ProposeTests(unittest.TestCase):
    def test_a_proposal_lands_in_the_spool_readable_by_the_review_user(self):
        with tempfile.TemporaryDirectory() as root:
            spool = Path(root)
            proposal = propose(TYPED, {"agent": "jarvis"}, spool)
            spooled = spool / f"{proposal.id}.json"
            self.assertTrue(spooled.exists())
            self.assertEqual(0o640, spooled.stat().st_mode & 0o777)
            payload = json.loads(spooled.read_text(encoding="utf-8"))
            self.assertEqual(proposal.id, payload["proposal"]["id"])
            self.assertEqual("jarvis", payload["proposal"]["provenance"]["agent"])

    def test_the_same_content_is_not_proposed_twice(self):
        with tempfile.TemporaryDirectory() as root:
            spool = Path(root)
            first = propose(TYPED, {"agent": "jarvis"}, spool)
            second = propose(TYPED, {"agent": "jarvis"}, spool)
            self.assertEqual(first.id, second.id, "the same knowledge was proposed twice")
            self.assertEqual(1, len(list(spool.glob("*.json"))))

    def test_a_proposal_without_an_okf_type_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                propose("# Sin sobre\nCuerpo.", {"agent": "jarvis"}, Path(root))

    def test_an_empty_proposal_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                propose("   \n  ", {"agent": "jarvis"}, Path(root))

    def test_what_it_writes_is_what_review_reads(self):
        """The spool format is a contract between the agent and the reviewer."""
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            spool, pending, decisions = root / "spool", root / "pending", root / "decisions"
            for directory in (spool, pending, decisions):
                directory.mkdir()
            proposal = propose(TYPED, {"agent": "jarvis"}, spool)
            projected, _ = run_review(spool, pending, decisions)
            self.assertEqual([pending / f"{proposal.id}.md"], projected)
            self.assertIn("# Hallazgo", projected[0].read_text(encoding="utf-8"))

    def test_a_queued_proposal_survives_a_spool_that_is_briefly_unwritable(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            spool, queue = root / "spool", root / "queue"
            sender = spool_sender(spool)
            spool.mkdir(mode=0o500)
            try:
                from knowledge_vault.outbox import DurableOutbox
                from knowledge_vault.models import Proposal

                outbox = DurableOutbox(queue, sender)
                proposal = Proposal.create(TYPED, "key", {"agent": "jarvis"})
                outbox.submit(proposal)
                self.assertEqual([proposal.id], [item.id for item in outbox.pending()])
                self.assertEqual([], list(spool.glob("*.json")))
            finally:
                spool.chmod(0o700)
            self.assertEqual([proposal.id], [item.id for item in outbox.drain()])
            self.assertEqual(1, len(list(spool.glob("*.json"))))


if __name__ == "__main__":
    unittest.main()
