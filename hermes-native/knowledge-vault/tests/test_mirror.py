import subprocess
import tempfile
import unittest
from pathlib import Path

from knowledge_vault.mirror import GitMirror


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


class MirrorTests(unittest.TestCase):
    def setup(self, root):
        root = Path(root)
        vault, repo = root / "vault", root / "mirror"
        vault.mkdir()
        (vault / "kubernetes.md").write_text("# Kubernetes\nFirst\n", encoding="utf-8")
        return GitMirror(vault, repo), vault, repo

    def test_first_run_initialises_the_repository_and_commits_the_vault(self):
        with tempfile.TemporaryDirectory() as root:
            mirror, _, repo = self.setup(root)
            changed = mirror.sync()
            self.assertEqual(["kubernetes.md"], changed)
            self.assertTrue((repo / "kubernetes.md").exists())
            self.assertIn("kubernetes.md", git(repo, "show", "--name-only", "--format="))

    def test_an_unchanged_vault_creates_no_commit(self):
        with tempfile.TemporaryDirectory() as root:
            mirror, _, repo = self.setup(root)
            mirror.sync()
            before = git(repo, "rev-parse", "HEAD")
            self.assertEqual([], mirror.sync())
            self.assertEqual(before, git(repo, "rev-parse", "HEAD"))

    def test_a_revised_note_updates_the_mirror(self):
        with tempfile.TemporaryDirectory() as root:
            mirror, vault, repo = self.setup(root)
            mirror.sync()
            (vault / "kubernetes.md").write_text("# Kubernetes\nSecond\n", encoding="utf-8")
            self.assertEqual(["kubernetes.md"], mirror.sync())
            self.assertIn("Second", (repo / "kubernetes.md").read_text(encoding="utf-8"))

    def test_a_note_removed_from_the_vault_leaves_the_mirror(self):
        with tempfile.TemporaryDirectory() as root:
            mirror, vault, repo = self.setup(root)
            mirror.sync()
            (vault / "kubernetes.md").unlink()
            (vault / "voice.md").write_text("# Voice\nPiper\n", encoding="utf-8")
            self.assertEqual(["kubernetes.md", "voice.md"], mirror.sync())
            self.assertFalse((repo / "kubernetes.md").exists(), "the mirror kept a deleted note")
            self.assertTrue((repo / "voice.md").exists())

    def test_nothing_but_published_notes_reaches_the_mirror(self):
        with tempfile.TemporaryDirectory() as root:
            mirror, vault, repo = self.setup(root)
            (vault / "draft.txt").write_text("not a note", encoding="utf-8")
            mirror.sync()
            self.assertFalse((repo / "draft.txt").exists())

    def test_changes_are_pushed_to_the_configured_remote(self):
        with tempfile.TemporaryDirectory() as root:
            remote = Path(root) / "remote.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            mirror, _, repo = self.setup(root)
            mirror.remote = str(remote)
            mirror.sync()
            self.assertIn("kubernetes.md", git(remote, "ls-tree", "--name-only", "main"))


if __name__ == "__main__":
    unittest.main()
