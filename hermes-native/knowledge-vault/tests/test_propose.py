import inspect
import tempfile
import unittest
from pathlib import Path

from knowledge_vault import layout
from knowledge_vault.note import parse_frontmatter
from knowledge_vault.propose import propose

TYPED = "---\ntype: infra-fact\ntags: [storage]\n---\n# Hallazgo\nEl storage class es local-path."


class ProposeTests(unittest.TestCase):
    def test_a_proposal_lands_only_under_pending(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root)
            path = propose(TYPED, {"agent": "jarvis"}, vault)
            self.assertEqual(layout.pending_root(vault), path.parent)
            self.assertEqual([], list(layout.knowledge_root(vault).glob("*.md")) if layout.knowledge_root(vault).is_dir() else [])
            self.assertEqual([], list(layout.published_notes(vault)))

    def test_no_parameter_can_retarget_knowledge(self):
        """The agent-facing write path exposes no way to reach knowledge/."""
        parameters = inspect.signature(propose).parameters
        self.assertNotIn("knowledge_directory", parameters)
        self.assertNotIn("target", parameters)
        self.assertNotIn("destination", parameters)

    def test_the_written_note_carries_empty_review_fields_and_a_key(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root)
            path = propose(TYPED, {"agent": "jarvis"}, vault)
            fields = parse_frontmatter(path.read_text(encoding="utf-8"))
            self.assertEqual("", fields["reviewer"])
            self.assertEqual("", fields["decision"])
            self.assertEqual("", fields["rationale"])
            self.assertTrue(fields["idempotency_key"])
            self.assertEqual("infra-fact", fields["type"])

    def test_the_note_id_is_a_zettelkasten_timestamp(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root)
            path = propose(TYPED, {"agent": "jarvis"}, vault)
            self.assertRegex(path.stem, r"^\d{14}$")
            self.assertEqual(path.stem, parse_frontmatter(path.read_text(encoding="utf-8"))["id"])

    def test_the_same_content_is_not_proposed_twice(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root)
            first = propose(TYPED, {"agent": "jarvis"}, vault)
            second = propose(TYPED, {"agent": "jarvis"}, vault)
            self.assertEqual(first, second, "the same knowledge was proposed twice")
            self.assertEqual(1, len(list(layout.pending_root(vault).glob("*.md"))))

    def test_a_proposal_without_an_okf_type_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                propose("# Sin sobre\nCuerpo.", {"agent": "jarvis"}, Path(root))

    def test_an_empty_proposal_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                propose("   \n  ", {"agent": "jarvis"}, Path(root))

    def test_id_collision_is_checked_against_knowledge_and_pending(self):
        """A new id is minted against knowledge/** union pending/* (F-2 follow-through)."""
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root)
            knowledge = layout.knowledge_root(vault)
            knowledge.mkdir(parents=True)
            pending = layout.pending_root(vault)
            pending.mkdir(parents=True)

            import knowledge_vault.propose as propose_module

            calls = []
            real_new_note_id = propose_module.new_note_id

            def spy(taken):
                calls.append(set(taken))
                return real_new_note_id(taken)

            propose_module.new_note_id = spy
            try:
                (knowledge / "20260101000000.md").write_text(
                    "---\ntype: fact\nid: 20260101000000\n---\n# A\nBody\n", encoding="utf-8"
                )
                (pending / "20260101000001.md").write_text(
                    "---\ntype: fact\nid: 20260101000001\nreviewer: \ndecision: \nrationale: \n"
                    "idempotency_key: other\n---\n# B\nBody\n",
                    encoding="utf-8",
                )
                propose(TYPED, {"agent": "jarvis"}, vault)
            finally:
                propose_module.new_note_id = real_new_note_id

            self.assertEqual(1, len(calls))
            self.assertIn("20260101000000", calls[0])
            self.assertIn("20260101000001", calls[0])


if __name__ == "__main__":
    unittest.main()
