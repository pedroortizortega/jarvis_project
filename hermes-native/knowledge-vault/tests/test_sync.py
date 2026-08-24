import subprocess
import tempfile
import unittest
from pathlib import Path

from knowledge_vault import layout
from knowledge_vault.sync import IDENTITY, GitSync


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


class SyncTests(unittest.TestCase):
    def setup(self, root):
        tree = Path(root) / "tree"
        layout.pending_root(tree).mkdir(parents=True)
        layout.knowledge_root(tree).mkdir(parents=True)
        (layout.pending_root(tree) / "p1.md").write_text("# Draft\nFirst\n", encoding="utf-8")
        return GitSync(tree), tree

    def test_first_run_initialises_the_repository_and_commits_pending(self):
        with tempfile.TemporaryDirectory() as root:
            sync, tree = self.setup(root)
            changed = sync.sync()
            self.assertEqual(["p1.md"], changed)
            self.assertIn(
                f"{layout.PENDING_DIRNAME}/p1.md",
                git(tree, "ls-tree", "--name-only", "-r", "HEAD"),
            )

    def test_an_unchanged_pending_creates_no_commit(self):
        with tempfile.TemporaryDirectory() as root:
            sync, tree = self.setup(root)
            sync.sync()
            before = git(tree, "rev-parse", "HEAD")
            self.assertEqual([], sync.sync())
            self.assertEqual(before, git(tree, "rev-parse", "HEAD"))

    def test_a_revised_pending_note_is_synced(self):
        with tempfile.TemporaryDirectory() as root:
            sync, tree = self.setup(root)
            sync.sync()
            (layout.pending_root(tree) / "p1.md").write_text("# Draft\nSecond\n", encoding="utf-8")
            self.assertEqual(["p1.md"], sync.sync())

    def test_a_dirty_file_under_knowledge_is_never_committed_by_sync(self):
        """The core scope guarantee: knowledge/ is effectively read-only from
        sync's perspective — only promote may write there."""
        with tempfile.TemporaryDirectory() as root:
            sync, tree = self.setup(root)
            sync.sync()
            (layout.knowledge_root(tree) / "20260805090133.md").write_text(
                "---\ntype: fact\n---\n# Published by hand\n", encoding="utf-8"
            )
            changed = sync.sync()
            self.assertEqual([], changed, "sync must not stage/commit files under knowledge/")
            tracked = git(tree, "ls-tree", "--name-only", "-r", "HEAD")
            self.assertNotIn(f"{layout.KNOWLEDGE_DIRNAME}/20260805090133.md", tracked)
            status = git(tree, "status", "--porcelain", "--untracked-files=all")
            self.assertIn("knowledge/20260805090133.md", status, "the dirty file should remain uncommitted")

    def test_it_commits_without_any_git_identity_configured(self):
        with tempfile.TemporaryDirectory() as root:
            sync, tree = self.setup(root)
            sync.sync()
            self.assertIn("p1.md", git(tree, "show", "--name-only", "--format="))

    def test_a_commit_that_failed_earlier_is_retried(self):
        """The push-retry-preserves-commit lesson (mirror.py's _pending):
        a failed commit must not become permanently invisible."""
        with tempfile.TemporaryDirectory() as root:
            sync, tree = self.setup(root)
            sync._commit = lambda count: (_ for _ in ()).throw(RuntimeError("git down"))
            with self.assertRaises(RuntimeError):
                sync.sync()
            head = subprocess.run(
                ["git", "rev-parse", "--verify", "HEAD"], cwd=tree, capture_output=True
            )
            self.assertNotEqual(0, head.returncode, "the failed commit was recorded anyway")

            del sync._commit
            self.assertEqual(["p1.md"], sync.sync())

    def test_a_failing_push_does_not_lose_the_commit(self):
        with tempfile.TemporaryDirectory() as root:
            sync, tree = self.setup(root)
            sync.remote = "origin"
            with self.assertRaises(subprocess.CalledProcessError):
                sync.sync()
            head = subprocess.run(
                ["git", "rev-parse", "--verify", "HEAD"], cwd=tree, capture_output=True
            )
            self.assertEqual(0, head.returncode, "a failed push must not lose the commit")
            self.assertIn("p1.md", git(tree, "show", "--name-only", "--format="))

    def test_changes_are_pushed_to_the_configured_remote(self):
        with tempfile.TemporaryDirectory() as root:
            remote = Path(root) / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
            sync, tree = self.setup(root)
            sync.remote = str(remote)
            sync.sync()
            self.assertIn("p1.md", git(remote, "ls-tree", "--name-only", "-r", "main"))

    def test_it_adopts_a_remote_that_already_has_history(self):
        with tempfile.TemporaryDirectory() as root:
            remote = Path(root) / "remote.git"
            subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)

            seed, seed_tree = self.setup(str(Path(root) / "seed"))
            seed.remote = str(remote)
            seed.sync()

            fresh, fresh_tree = self.setup(str(Path(root) / "fresh"))
            fresh.remote = str(remote)
            (layout.pending_root(fresh_tree) / "p2.md").write_text("# Draft 2\n", encoding="utf-8")
            fresh.sync()

            names = git(remote, "ls-tree", "--name-only", "-r", "main")
            self.assertIn("p2.md", names)
            self.assertIn("p1.md", names, "the remote history was discarded")

    def test_promote_and_sync_share_the_vault_lock(self):
        with tempfile.TemporaryDirectory() as root:
            sync, tree = self.setup(root)
            with layout.vault_lock(tree):
                with self.assertRaises(layout.VaultLocked):
                    sync.sync()


class IdentityTests(unittest.TestCase):
    def test_identity_is_exported_for_reuse(self):
        self.assertIn("GIT_AUTHOR_NAME", IDENTITY)
        self.assertIn("GIT_COMMITTER_EMAIL", IDENTITY)


if __name__ == "__main__":
    unittest.main()
