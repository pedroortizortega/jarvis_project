import tempfile
import unittest
from pathlib import Path

from knowledge_vault.search import search_vault


def vault_with(root, notes):
    vault = Path(root) / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    for name, text in notes.items():
        (vault / name).write_text(text, encoding="utf-8")
    return vault


NOTES = {
    "20260805045153.md": (
        "---\ntype: infra-fact\nid: 20260805045153\n"
        "title: Longhorn no esta instalado en trantor\n---\n"
        "# Longhorn no esta instalado en trantor\n\nEl unico storage class es local-path.\n"
    ),
    "20260805090133.md": (
        "---\ntype: decision\nid: 20260805090133\n"
        "title: Por que SQLite en el control plane\n---\n"
        "# Por que SQLite en el control plane\n\nEl cluster no tiene ninguna base de datos.\n"
    ),
}


class SearchTests(unittest.TestCase):
    def test_it_reports_the_id_an_agent_must_link_to(self):
        with tempfile.TemporaryDirectory() as root:
            vault = vault_with(root, NOTES)
            results = search_vault("storage class local-path", vault, Path(root) / "index.json")
            self.assertTrue(results)
            self.assertEqual("20260805045153.md", results[0].note)
            self.assertEqual("Longhorn no esta instalado en trantor", results[0].title)
            self.assertIn("local-path", results[0].excerpt)

    def test_it_builds_the_index_when_none_exists(self):
        with tempfile.TemporaryDirectory() as root:
            vault = vault_with(root, NOTES)
            index = Path(root) / "index.json"
            self.assertFalse(index.exists())
            self.assertTrue(search_vault("sqlite", vault, index))
            self.assertTrue(index.exists())

    def test_a_note_published_after_the_index_is_still_found(self):
        """Retrieval refuses a stale index on purpose; the agent must not be
        told 'unavailable' just because the vault moved on."""
        with tempfile.TemporaryDirectory() as root:
            vault = vault_with(root, NOTES)
            index = Path(root) / "index.json"
            search_vault("sqlite", vault, index)
            (vault / "20260806101500.md").write_text(
                "---\ntype: concept\nid: 20260806101500\ntitle: Piper sintetiza voz\n---\n"
                "# Piper sintetiza voz\n\nPiper genera habla en espanol.\n",
                encoding="utf-8",
            )
            results = search_vault("piper habla", vault, index)
            self.assertEqual(["20260806101500.md"], [hit.note for hit in results])

    def test_no_match_is_an_empty_result_not_a_failure(self):
        with tempfile.TemporaryDirectory() as root:
            vault = vault_with(root, NOTES)
            self.assertEqual([], search_vault("kubernetes en marte", vault, Path(root) / "i.json"))

    def test_an_empty_vault_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root) / "vault"
            vault.mkdir()
            self.assertEqual([], search_vault("lo que sea", vault, Path(root) / "i.json"))

    def test_a_hit_carries_the_underlying_retrieval_score(self):
        with tempfile.TemporaryDirectory() as root:
            vault = vault_with(root, NOTES)
            results = search_vault("storage class local-path", vault, Path(root) / "index.json")
            self.assertTrue(results)
            self.assertIsInstance(results[0].score, float)
            self.assertNotEqual(0.0, results[0].score)


if __name__ == "__main__":
    unittest.main()
