import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_vault import retrieval

from knowledge_vault.retrieval import Retriever, build_index


def vault_with(root, notes):
    vault = Path(root) / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    for name, text in notes.items():
        (vault / name).write_text(text, encoding="utf-8")
    return vault


class RetrievalTests(unittest.TestCase):
    notes = {
        "kubernetes.md": "---\ntype: fact\ntags: [infra]\n---\n# Kubernetes\nThe cluster runs k3s on trantor.\n\n# Storage\nLonghorn holds volumes.\n",
        "voice.md": "---\ntype: fact\n---\n# Voice\nPiper synthesises Spanish speech.\n",
    }

    def test_the_okf_envelope_is_not_indexed_as_prose(self):
        with tempfile.TemporaryDirectory() as root:
            retriever, _ = self.retriever(root)
            result = retriever.search("type fact tags infra")
            self.assertTrue(all("type:" not in hit.text for hit in result.hits))

    def retriever(self, root, embedder=None):
        vault = vault_with(root, self.notes)
        index_path = Path(root) / "state" / "index.json"
        build_index(vault, index_path)
        return Retriever(vault, index_path, embedder=embedder), vault

    def test_published_note_is_returned_with_stable_citation(self):
        with tempfile.TemporaryDirectory() as root:
            retriever, vault = self.retriever(root)
            result = retriever.search("k3s cluster")
            self.assertTrue(result.available)
            hit = result.hits[0]
            self.assertEqual(str(vault / "kubernetes.md"), hit.note_path)
            self.assertTrue(hit.fragment_id.startswith("kubernetes-"))
            self.assertIn("k3s", hit.text)
            rebuilt_path = Path(root) / "state" / "rebuilt.json"
            build_index(vault, rebuilt_path)
            rebuilt = Retriever(vault, rebuilt_path).search("k3s cluster")
            self.assertEqual(hit.fragment_id, rebuilt.hits[0].fragment_id)

    def test_content_outside_the_published_vault_is_never_returned(self):
        with tempfile.TemporaryDirectory() as root:
            retriever, vault = self.retriever(root)
            pending = vault.parent / "pending"
            pending.mkdir()
            (pending / "draft.md").write_text("# Kubernetes\nSecret k3s draft.\n", encoding="utf-8")
            result = retriever.search("k3s")
            self.assertTrue(result.available)
            self.assertTrue(all("draft" not in hit.text for hit in result.hits))
            self.assertTrue(all(str(pending) not in hit.note_path for hit in result.hits))

    def test_stale_index_reports_unavailable_instead_of_stale_hits(self):
        with tempfile.TemporaryDirectory() as root:
            retriever, vault = self.retriever(root)
            (vault / "kubernetes.md").write_text("# Kubernetes\nRewritten.\n", encoding="utf-8")
            result = retriever.search("k3s cluster")
            self.assertFalse(result.available)
            self.assertEqual((), result.hits)
            self.assertIn("revision", result.reason)

    def test_missing_index_reports_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            vault = vault_with(root, self.notes)
            result = Retriever(vault, Path(root) / "state" / "absent.json").search("k3s")
            self.assertFalse(result.available)
            self.assertEqual((), result.hits)

    def test_unchanged_notes_are_not_rehashed_on_every_search(self):
        with tempfile.TemporaryDirectory() as root:
            retriever, vault = self.retriever(root)
            retriever.search("k3s")
            with patch(
                "knowledge_vault.retrieval._digest", wraps=retrieval._digest
            ) as digest:
                retriever.search("k3s")
                self.assertEqual(0, digest.call_count, "unchanged notes were re-read")
                (vault / "voice.md").write_text("# Voice\nRewritten.\n", encoding="utf-8")
                self.assertFalse(retriever.search("k3s").available)
                self.assertGreater(digest.call_count, 0)

    def test_semantic_signal_retrieves_what_lexical_matching_misses(self):
        def embedder(text):
            spoken = ("piper", "speech", "habla")
            return [1.0, 0.0] if any(word in text.lower() for word in spoken) else [0.0, 1.0]

        with tempfile.TemporaryDirectory() as root:
            lexical, vault = self.retriever(root)
            self.assertEqual((), lexical.search("habla").hits)
            hybrid = Retriever(vault, Path(root) / "state" / "index.json", embedder=embedder)
            result = hybrid.search("habla")
            self.assertTrue(result.available)
            self.assertEqual(str(vault / "voice.md"), result.hits[0].note_path)


if __name__ == "__main__":
    unittest.main()
