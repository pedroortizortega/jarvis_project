"""Commit and push `pending/`. Never touch `knowledge/`.

The vault tree is the git repository now (design.md F-8/D-12), so there is no
scratch-worktree copy left to make (`mirror.py`'s `_mirror_files()` is gone,
design.md D-05 of the proposal). `sync` is the only actor allowed to write
under `.git` besides `promote`, and it is scoped so that `knowledge/` stays
effectively read-only from its perspective — a dirty file there is
`promote`'s business, never sync's (systemd enforces this too, with
`ReadOnlyPaths=<tree>/knowledge` on the sync unit; this module enforces it in
code so the property holds under test, not just under a specific unit file).
"""

import os
import subprocess
import sys
from pathlib import Path

from . import layout


class AdoptRemoteRefused(RuntimeError):
    """Refused to converge on the remote because doing so would discard
    uncommitted local changes (`git reset --hard` is destructive)."""


class GitSync:
    """Stage, commit and push `pending/` in the vault tree's own git repo."""

    def __init__(self, vault_directory, remote=None, branch="main"):
        self.vault_directory = Path(vault_directory)
        self.remote = remote
        self.branch = branch

    def _git(self, *args, check=True):
        return layout.run_git(self.vault_directory, *args, check=check)

    def _ensure_repo(self):
        """Idempotent: the migration script already clones the vault tree
        from the bare repo, but initialising here too keeps this module
        usable on its own (tests, a fresh tree) without duplicating that
        logic elsewhere."""
        self.vault_directory.mkdir(parents=True, exist_ok=True)
        if (self.vault_directory / ".git").exists():
            return
        self._git("init", "-q", "-b", self.branch)
        if not self.remote:
            return
        self._git("remote", "add", "origin", self.remote)
        if self._git("fetch", "-q", "origin", self.branch, check=False).returncode == 0:
            self._git("reset", "-q", "--hard", f"origin/{self.branch}")

    def _adopt_remote(self):
        """Converge on the remote's history before syncing (same lesson as
        the old mirror.py's `_adopt_remote`): anything else that pushes to
        the bare repo leaves this tree behind, and every later push is
        rejected until someone intervenes.

        Unlike the old mirror.py, this tree IS the canonical vault — a
        reviewer's just-decided note can be sitting uncommitted in
        `pending/` (decide.py writes straight to disk, no commit; only
        `sync()` commits it, and only after this check). `reset --hard`
        would discard that edit silently. Refuse instead: the next sync
        run tries again, and a genuinely fresh/clean tree converges as
        before.
        """
        if not self.remote:
            return
        self._git("remote", "remove", "origin", check=False)
        self._git("remote", "add", "origin", self.remote)
        if self._git("fetch", "-q", "origin", self.branch, check=False).returncode != 0:
            return
        behind = self._git(
            "rev-list", "--count", f"HEAD..origin/{self.branch}", check=False
        )
        if behind.returncode == 0 and behind.stdout.strip() not in ("", "0"):
            dirty = self._git("status", "--porcelain", check=False).stdout.strip()
            if dirty:
                raise AdoptRemoteRefused(
                    "local tree is behind the remote and has uncommitted changes; "
                    "refusing to reset --hard and discard them"
                )
            self._git("reset", "-q", "--hard", f"origin/{self.branch}")

    def _pending(self):
        """What still needs committing, scoped to `pending/` only.

        A dirty file under `knowledge/` never enters this list — only
        `promote` writes there, and `git status` scoped with a pathspec
        never even looks.
        """
        lines = self._git(
            "status", "--porcelain", "--untracked-files=all", "--", layout.PENDING_DIRNAME
        ).stdout.splitlines()
        return sorted(Path(line[3:].strip().strip('"')).name for line in lines if line)

    def _commit(self, count):
        self._git("add", "--", layout.PENDING_DIRNAME)
        self._git("commit", "-q", "-m", f"Sync {count} pending note{'s' if count != 1 else ''}")

    def sync(self):
        # vault_lock() touches a file inside vault_directory — it must exist
        # before the lock is acquired, or a genuinely fresh tree (no prior
        # _ensure_repo() call) raises FileNotFoundError here instead of
        # being initialized, contradicting this module's own claim of being
        # usable standalone on a fresh tree.
        self.vault_directory.mkdir(parents=True, exist_ok=True)
        with layout.vault_lock(self.vault_directory):
            self._ensure_repo()
            self._adopt_remote()
            layout.pending_root(self.vault_directory).mkdir(parents=True, exist_ok=True)
            changed = self._pending()
            if not changed:
                return []
            self._commit(len(changed))
            if self.remote:
                self._git("push", "-q", "--set-upstream", "origin", self.branch)
            return changed


def main():
    sync = GitSync(
        os.environ["KNOWLEDGE_VAULT_DIR"],
        remote=os.environ.get("KNOWLEDGE_VAULT_REMOTE"),
        branch=os.environ.get("KNOWLEDGE_VAULT_BRANCH", "main"),
    )
    try:
        changed = sync.sync()
    except (layout.VaultLocked, AdoptRemoteRefused) as error:
        print(f"knowledge-vault sync: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"knowledge-vault sync: {(error.stderr or '').strip()}", file=sys.stderr)
        return 1
    print(f"knowledge-vault sync: {len(changed)} note(s) synced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
