import tempfile
import threading
import unittest
from pathlib import Path

from knowledge_vault.models import Proposal
from knowledge_vault.outbox import DurableOutbox
class DurableOutboxTests(unittest.TestCase):
    def proposal(self, key="key-1", predecessor_id=None):
        return Proposal.create(
            markdown="# Note\ncontent", idempotency_key=key,
            provenance={"agent": "hermes"}, predecessor_id=predecessor_id,
        )

    def test_retry_returns_existing_immutable_proposal(self):
        delivered = []
        with tempfile.TemporaryDirectory() as directory:
            outbox = DurableOutbox(directory, lambda proposal: delivered.append(proposal) or proposal)
            first = outbox.submit(self.proposal())
            retry = outbox.submit(self.proposal())
        self.assertEqual(first.id, retry.id)
        self.assertEqual(1, len(delivered))
        with self.assertRaises(AttributeError):
            first.id = "changed"

    def test_outage_persists_without_touching_vault(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault.md"
            vault.write_text("published", encoding="utf-8")
            outbox = DurableOutbox(root / "outbox", lambda _: (_ for _ in ()).throw(OSError("offline")))
            proposal = outbox.submit(self.proposal())
            restored = DurableOutbox(root / "outbox", lambda value: value)
            self.assertEqual([proposal.id], [item.id for item in restored.pending()])
            self.assertEqual("published", vault.read_text(encoding="utf-8"))

    def test_concurrent_outages_preserve_every_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            barrier = threading.Barrier(8)

            def submit(index):
                barrier.wait()
                DurableOutbox(directory, lambda _: (_ for _ in ()).throw(OSError("offline"))).submit(self.proposal(f"key-{index}"))

            threads = [threading.Thread(target=submit, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            pending = DurableOutbox(directory, lambda proposal: proposal).pending()
            self.assertEqual({f"key-{index}" for index in range(8)}, {item.idempotency_key for item in pending})

    def test_submit_drains_pending_items_after_recovery(self):
        delivered = []
        with tempfile.TemporaryDirectory() as directory:
            offline = DurableOutbox(directory, lambda _: (_ for _ in ()).throw(OSError("offline")))
            queued = offline.submit(self.proposal("queued"))
            recovered = DurableOutbox(directory, lambda proposal: delivered.append(proposal) or proposal)
            drained = recovered.drain()
            current = recovered.submit(self.proposal("current"))

            self.assertEqual([queued.id], [proposal.id for proposal in drained])
            self.assertEqual([queued.id, current.id], [proposal.id for proposal in delivered])
            self.assertEqual([], recovered.pending())
            self.assertEqual([], recovered.drain())

    def test_rejected_revision_has_new_id_and_predecessor(self):
        rejected = self.proposal()
        revision = Proposal.revise(rejected, "# Note\nrevised", "key-2", rejected=True)
        self.assertNotEqual(rejected.id, revision.id)
        self.assertEqual(rejected.id, revision.predecessor_id)
if __name__ == "__main__":
    unittest.main()
