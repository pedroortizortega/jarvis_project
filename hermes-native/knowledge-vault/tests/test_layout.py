import inspect
import tempfile
import unittest
from pathlib import Path

from knowledge_vault import retrieval, search
from knowledge_vault.layout import (
    VaultLocked,
    knowledge_root,
    pending_root,
    published_notes,
    vault_lock,
)
from knowledge_vault.retrieval import build_index, vault_revision
from knowledge_vault.search import search_vault

NOTE = "---\ntype: fact\n---\n# Kubernetes\nThe cluster runs k3s on trantor.\n"

# Arbitrary name, never spelled anywhere in retrieval.py/search.py: passing
# this test cannot mean "we excluded it by name", only "we never looked".
THIRD_FOLDER = "quarantine-2026"


def tree_with(root, knowledge_notes=None):
    tree = Path(root) / "tree"
    knowledge_root(tree).mkdir(parents=True, exist_ok=True)
    pending_root(tree).mkdir(parents=True, exist_ok=True)
    for name, text in (knowledge_notes or {}).items():
        (knowledge_root(tree) / name).write_text(text, encoding="utf-8")
    return tree


class PublishedNotesTests(unittest.TestCase):
    def test_published_notes_only_enumerates_knowledge(self):
        with tempfile.TemporaryDirectory() as root:
            tree = tree_with(root, {"kubernetes.md": NOTE})
            (pending_root(tree) / "draft.md").write_text(NOTE, encoding="utf-8")
            found = {path.name for path in published_notes(tree)}
            self.assertEqual({"kubernetes.md"}, found)


class VaultLockTests(unittest.TestCase):
    def test_contended_lock_fails_fast_not_blocking(self):
        """D-08: reuses Publisher._fence()'s LOCK_NB + typed-exception shape,
        not just its use of flock — a second writer must fail immediately,
        never hang waiting for the first to release."""
        with tempfile.TemporaryDirectory() as root:
            tree = tree_with(root)
            with vault_lock(tree):
                with self.assertRaises(VaultLocked):
                    with vault_lock(tree):
                        pass

    def test_lock_releases_after_the_with_block(self):
        with tempfile.TemporaryDirectory() as root:
            tree = tree_with(root)
            with vault_lock(tree):
                pass
            with vault_lock(tree):
                pass  # would raise VaultLocked if the first lock leaked


class ThirdFolderIsInvisibleTests(unittest.TestCase):
    """design.md D-02: proven with a fixture name spelled nowhere in source."""

    def test_a_folder_the_code_never_names_never_appears_in_the_index(self):
        with tempfile.TemporaryDirectory() as root:
            tree = tree_with(root, {"kubernetes.md": NOTE})
            third = tree / THIRD_FOLDER
            third.mkdir()
            (third / "note.md").write_text(
                "# Kubernetes\nSecret quarantined content mentioning k3s.\n",
                encoding="utf-8",
            )
            (tree / "README.md").write_text("# Vault\nNothing enumerates this.\n", encoding="utf-8")

            index_path = Path(root) / "index.json"
            index = build_index(tree, index_path)
            self.assertTrue(
                all("quarantine" not in fragment["text"] for fragment in index["fragments"])
            )
            self.assertTrue(
                all("quarantine" not in fragment["note_path"] for fragment in index["fragments"])
            )

    def test_vault_revision_is_unchanged_by_writes_to_a_third_folder(self):
        with tempfile.TemporaryDirectory() as root:
            tree = tree_with(root, {"kubernetes.md": NOTE})
            before = vault_revision(tree)

            third = tree / THIRD_FOLDER
            third.mkdir()
            (third / "note.md").write_text("# Quarantined\nSomething.\n", encoding="utf-8")
            after_write = vault_revision(tree)
            self.assertEqual(before, after_write)

            (third / "note.md").write_text("# Quarantined\nSomething else entirely.\n", encoding="utf-8")
            after_rewrite = vault_revision(tree)
            self.assertEqual(before, after_rewrite)

    def test_search_never_returns_a_hit_from_a_third_folder(self):
        with tempfile.TemporaryDirectory() as root:
            tree = tree_with(root, {"kubernetes.md": NOTE})
            third = tree / THIRD_FOLDER
            third.mkdir()
            (third / "note.md").write_text(
                "# Quarantined\nSecret quarantined content mentioning k3s cluster.\n",
                encoding="utf-8",
            )
            index_path = Path(root) / "index.json"
            hits = search_vault("k3s cluster", tree, index_path)
            self.assertTrue(all("quarantine" not in hit.excerpt.lower() for hit in hits))


class PendingIsInvisibleTests(unittest.TestCase):
    def test_a_note_in_pending_is_never_a_search_hit(self):
        with tempfile.TemporaryDirectory() as root:
            tree = tree_with(root, {"kubernetes.md": NOTE})
            (pending_root(tree) / "draft.md").write_text(
                "---\ntype: fact\n---\n# Draft\nUnapproved k3s draft content.\n",
                encoding="utf-8",
            )
            index_path = Path(root) / "index.json"
            hits = search_vault("draft k3s", tree, index_path)
            self.assertTrue(all(hit.note != "draft.md" for hit in hits))

    def test_writing_to_pending_never_changes_the_index_revision(self):
        with tempfile.TemporaryDirectory() as root:
            tree = tree_with(root, {"kubernetes.md": NOTE})
            before = vault_revision(tree)
            (pending_root(tree) / "draft.md").write_text(
                "---\ntype: fact\n---\n# Draft\nUnapproved content.\n", encoding="utf-8"
            )
            after = vault_revision(tree)
            self.assertEqual(before, after)


class StructuralAllowlistTests(unittest.TestCase):
    """Turns "allowlist, not denylist" (design.md D-02) into an assertion.

    The strings "pending"/PENDING_DIRNAME must never appear in retrieval.py
    or search.py: the only way those modules can ever learn about a folder
    is `published_notes()`, never an exclusion list.
    """

    def test_retrieval_module_never_names_pending(self):
        source = inspect.getsource(retrieval)
        self.assertNotIn("pending", source.lower())

    def test_search_module_never_names_pending(self):
        source = inspect.getsource(search)
        self.assertNotIn("pending", source.lower())

    def test_retrieval_module_uses_published_notes_as_its_enumerator(self):
        source = inspect.getsource(retrieval)
        self.assertIn("published_notes", source)
        self.assertNotIn(".rglob(", source)
        self.assertNotIn(".glob(", source)


if __name__ == "__main__":
    unittest.main()
