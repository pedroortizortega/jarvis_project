import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "hermes-native" / "orchestration" / "src")
)

from memory_router.journal import Journal


class JournalDurabilityTests(unittest.TestCase):
    def test_append_persists_entry_across_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.ndjson"
            journal = Journal(path)
            entry_id = journal.append({"namespace": "/global", "content": "hello"})

            # Simulate a process restart: brand new Journal instance over the
            # same on-disk path, nothing carried over in memory.
            reopened = Journal(path)
            entries = reopened.replay()

        self.assertEqual(1, len(entries))
        self.assertEqual(entry_id, entries[0]["id"])
        self.assertEqual("hello", entries[0]["content"])

    def test_append_is_ordered_and_never_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.ndjson"
            journal = Journal(path)
            ids = [journal.append({"seq": i}) for i in range(5)]

            reopened = Journal(path)
            entries = reopened.replay()

        self.assertEqual(ids, [entry["id"] for entry in entries])
        self.assertEqual([0, 1, 2, 3, 4], [entry["seq"] for entry in entries])

    def test_ack_removes_entry_durably(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.ndjson"
            journal = Journal(path)
            first = journal.append({"seq": 0})
            second = journal.append({"seq": 1})

            journal.ack(first)

            reopened = Journal(path)
            entries = reopened.replay()

        self.assertEqual([second], [entry["id"] for entry in entries])

    def test_replay_on_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.ndjson"
            journal = Journal(path)
            self.assertEqual([], journal.replay())

    def test_append_survives_partial_write_recovery(self):
        # A truncated/corrupt trailing line (as could happen from a crash
        # mid-write) must not lose previously committed entries.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.ndjson"
            journal = Journal(path)
            first = journal.append({"seq": 0})
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"id": "broken", "seq"')  # no trailing newline, invalid JSON

            reopened = Journal(path)
            entries = reopened.replay()

        self.assertEqual([first], [entry["id"] for entry in entries])


if __name__ == "__main__":
    unittest.main()
