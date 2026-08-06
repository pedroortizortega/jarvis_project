import subprocess
import tempfile
import unittest
from pathlib import Path

from knowledge_vault.mirror import IDENTITY
from knowledge_vault.review_sync import DirectoryUnusable, ReviewSync

NOTE = (
    "---\nproposal_id: p1\nversion: 1\ntype: infra-fact\n"
    "reviewer: \ndecision: \nrationale: \n---\n# Un solo storage class\nCuerpo.\n"
)


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
        env={**IDENTITY, "PATH": "/usr/bin:/bin", "HOME": str(repo)},
    ).stdout.strip()


class ReviewSyncTests(unittest.TestCase):
    def setup(self, root):
        root = Path(root)
        pending, repo, remote = root / "pending", root / "repo", root / "remote.git"
        pending.mkdir(parents=True)
        subprocess.run(["git", "init", "--bare", "-q", "-b", "pending", str(remote)], check=True)
        return ReviewSync(pending, repo, str(remote)), pending, repo, remote

    def clone(self, remote, where):
        subprocess.run(["git", "clone", "-q", str(remote), str(where)], check=True)
        return Path(where)

    def test_a_pending_note_reaches_the_phone(self):
        with tempfile.TemporaryDirectory() as root:
            sync, pending, repo, remote = self.setup(root)
            (pending / "p1.md").write_text(NOTE, encoding="utf-8")
            imported, published = sync.sync()
            self.assertEqual([], imported)
            self.assertEqual(["p1.md"], published)
            self.assertIn("p1.md", git(remote, "ls-tree", "--name-only", "pending"))

    def test_a_decision_made_on_the_phone_reaches_the_host(self):
        """The phone is the authority on decisions; that is the whole point."""
        with tempfile.TemporaryDirectory() as root:
            sync, pending, repo, remote = self.setup(root)
            (pending / "p1.md").write_text(NOTE, encoding="utf-8")
            sync.sync()

            phone = self.clone(remote, Path(root) / "phone")
            (phone / "p1.md").write_text(
                NOTE.replace("reviewer: \ndecision: \nrationale: ",
                             "reviewer: pedro\ndecision: approved\nrationale: verificado"),
                encoding="utf-8",
            )
            for args in (["add", "-A"], ["commit", "-q", "-m", "decide"], ["push", "-q"]):
                subprocess.run(["git", *args], cwd=phone, check=True,
                               env={**IDENTITY, "PATH": "/usr/bin:/bin", "HOME": str(phone)})

            imported, _ = sync.sync()
            self.assertEqual(["p1.md"], imported)
            landed = (pending / "p1.md").read_text(encoding="utf-8")
            self.assertIn("decision: approved", landed)
            self.assertIn("rationale: verificado", landed)

    def test_a_note_that_left_the_queue_leaves_the_phone(self):
        with tempfile.TemporaryDirectory() as root:
            sync, pending, repo, remote = self.setup(root)
            (pending / "p1.md").write_text(NOTE, encoding="utf-8")
            sync.sync()
            (pending / "p1.md").unlink()
            imported, published = sync.sync()
            self.assertEqual(["p1.md"], published)
            self.assertEqual("README.md", git(remote, "ls-tree", "--name-only", "pending"))

    def test_an_unchanged_queue_makes_no_commit(self):
        with tempfile.TemporaryDirectory() as root:
            sync, pending, repo, remote = self.setup(root)
            (pending / "p1.md").write_text(NOTE, encoding="utf-8")
            sync.sync()
            before = git(remote, "rev-parse", "pending")
            self.assertEqual(([], []), sync.sync())
            self.assertEqual(before, git(remote, "rev-parse", "pending"))

    def test_the_branch_exists_before_anything_is_waiting(self):
        """The phone has to be set up at some point, and that point is rarely
        the moment a note happens to be in the queue."""
        with tempfile.TemporaryDirectory() as root:
            sync, pending, repo, remote = self.setup(root)
            sync.sync()
            self.assertIn("pending", git(remote, "branch", "--list", "pending"))
            self.assertIn("README", git(remote, "ls-tree", "--name-only", "pending"))

    def test_it_refuses_to_create_the_pending_directory(self):
        with tempfile.TemporaryDirectory() as root:
            sync = ReviewSync(Path(root) / "absent", Path(root) / "repo", None)
            with self.assertRaises(DirectoryUnusable):
                sync.sync()


if __name__ == "__main__":
    unittest.main()
