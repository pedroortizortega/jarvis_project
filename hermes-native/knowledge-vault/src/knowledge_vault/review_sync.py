"""Carry the review queue to a phone and its decisions back.

Two authorities meet here, and each owns a different thing. The host owns WHICH
notes are waiting: it projects new proposals and removes the ones already
decided. The reviewer owns WHAT THE DECISION IS, wherever they happen to be.
Keeping those separate is what lets both sides write without a merge conflict
being possible in practice.

It talks only to a bare repository on this host, so it needs no network: the
phone reaches that repository over SSH on its own.
"""

import os
import subprocess
import sys
from pathlib import Path

from .atomic import write_atomic
from .mirror import IDENTITY
from .note import parse_frontmatter


class DirectoryUnusable(RuntimeError):
    """Raised when the pending directory is missing or not writable."""


class ReviewSync:
    def __init__(self, pending_directory, repo_directory, remote, branch="pending"):
        self.pending_directory = Path(pending_directory)
        self.repo_directory = Path(repo_directory)
        self.remote = remote
        self.branch = branch

    def _git(self, *args, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_directory,
            capture_output=True,
            text=True,
            check=check,
            env={**os.environ, **IDENTITY, "GIT_TERMINAL_PROMPT": "0"},
        )

    def _check_pending(self):
        # Never create it: a silent mkdir leaves the directory owned by whoever
        # ran the command first, and the service then fails on every later run.
        if not self.pending_directory.is_dir():
            raise DirectoryUnusable(f"{self.pending_directory} does not exist; run the installer")
        if not os.access(self.pending_directory, os.W_OK | os.X_OK):
            raise DirectoryUnusable(f"{self.pending_directory} is not writable by this user")

    def _ensure_repo(self):
        self.repo_directory.mkdir(parents=True, exist_ok=True)
        if (self.repo_directory / ".git").exists():
            return
        self._git("init", "-q", "-b", self.branch)
        if self.remote:
            self._git("remote", "add", "origin", self.remote)

    def _adopt_remote(self):
        """Take the remote's state before writing anything.

        Unlike the published mirror, here the other side is a legitimate
        author: a decision written on the phone is the reviewer's, and it must
        never be overwritten by what the host happened to have.
        """
        if not self.remote:
            return
        self._git("remote", "remove", "origin", check=False)
        self._git("remote", "add", "origin", self.remote)
        if self._git("fetch", "-q", "origin", self.branch, check=False).returncode == 0:
            self._git("reset", "-q", "--hard", f"origin/{self.branch}")

    def _import_decisions(self):
        """Bring back notes the reviewer decided, wherever they decided them."""
        imported = []
        for path in sorted(self.repo_directory.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            if not parse_frontmatter(text).get("decision"):
                continue
            local = self.pending_directory / path.name
            if local.exists() and local.read_text(encoding="utf-8") == text:
                continue
            # 0660: the pending area stays writable by the reviewer.
            write_atomic(local, text, 0o660)
            imported.append(path.name)
        return imported

    def _refresh_queue(self):
        """Make the branch show exactly what is waiting for a decision."""
        waiting = {path.name: path for path in self.pending_directory.glob("*.md")}
        present = {path.name for path in self.repo_directory.glob("*.md")}
        published = []

        for name, source in waiting.items():
            target = self.repo_directory / name
            content = source.read_bytes()
            if name not in present or target.read_bytes() != content:
                target.write_bytes(content)
                published.append(name)

        for name in present - set(waiting):
            (self.repo_directory / name).unlink()
            published.append(name)

        return sorted(published)

    def _dirty(self):
        lines = self._git("status", "--porcelain").stdout.splitlines()
        return [Path(line[3:].strip().strip('"')).name for line in lines if line]

    def sync(self):
        self._check_pending()
        self._ensure_repo()
        self._adopt_remote()
        imported = self._import_decisions()
        published = self._refresh_queue() or self._dirty()
        if published:
            self._git("add", "-A")
            self._git("commit", "-q", "-m", f"Review queue: {len(published)} change(s)")
            if self.remote:
                self._git("push", "-q", "--set-upstream", "origin", self.branch)
        return imported, published


def main():
    sync = ReviewSync(
        os.environ["KNOWLEDGE_VAULT_PENDING_DIR"],
        os.environ["KNOWLEDGE_VAULT_REVIEW_REPO"],
        os.environ.get("KNOWLEDGE_VAULT_REVIEW_REMOTE"),
    )
    try:
        imported, published = sync.sync()
    except DirectoryUnusable as error:
        print(f"knowledge-vault review-sync: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"knowledge-vault review-sync: {(error.stderr or '').strip()}", file=sys.stderr)
        return 1
    print(f"knowledge-vault review-sync: {len(imported)} decided, {len(published)} queued")
    return 0
