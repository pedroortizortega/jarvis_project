import tempfile
import unittest
from pathlib import Path

from knowledge_vault.decide import awaiting_decision


def write(directory, name, text):
    (directory / name).write_text(text, encoding="utf-8")


class AwaitingDecisionTests(unittest.TestCase):
    """Deciding needs a proposal id. Without a way to list what is waiting, the
    id is unknowable and the decide command cannot be used at all."""

    def test_it_lists_what_is_waiting_with_its_title(self):
        with tempfile.TemporaryDirectory() as root:
            pending = Path(root)
            write(pending, "p1.md", "---\nproposal_id: p1\ntype: concept\n---\n# La resolucion cuantica\nCuerpo.")
            waiting = awaiting_decision(pending)
            self.assertEqual(["p1"], [item.proposal_id for item in waiting])
            self.assertEqual("La resolucion cuantica", waiting[0].title)

    def test_a_decided_note_is_not_waiting(self):
        with tempfile.TemporaryDirectory() as root:
            pending = Path(root)
            write(pending, "p1.md", "---\nproposal_id: p1\ndecision: approved\n---\n# Ya decidida\n")
            write(pending, "p2.md", "---\nproposal_id: p2\n---\n# Sigue esperando\n")
            self.assertEqual(["p2"], [item.proposal_id for item in awaiting_decision(pending)])

    def test_an_empty_queue_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual([], awaiting_decision(Path(root)))


if __name__ == "__main__":
    unittest.main()
