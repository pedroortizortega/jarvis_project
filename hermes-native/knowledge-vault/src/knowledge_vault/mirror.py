import os
import subprocess
import sys
from pathlib import Path

# The mirror is a copy, never the canonical vault: a broken git state must
# never be able to damage published notes.
# git refuses to commit without an identity, and a system user has no
# gitconfig, so the mirror always supplies its own.
IDENTITY = {
    "GIT_AUTHOR_NAME": "knowledge-vault",
    "GIT_AUTHOR_EMAIL": "knowledge-vault@localhost",
    "GIT_COMMITTER_NAME": "knowledge-vault",
    "GIT_COMMITTER_EMAIL": "knowledge-vault@localhost",
}


class VaultUnreadable(RuntimeError):
    """Raised when the vault cannot be listed, instead of mirroring nothing."""


class GitMirror:
    """Mirror published notes into a git working tree and push them.

    The remote is a bare repository on this host, reachable only over the
    private network, so notes are mirrored in the clear: nothing leaves the
    network and a third party never stores them.
    """

    def __init__(self, vault_directory, repo_directory, remote=None, branch="main"):
        self.vault_directory = Path(vault_directory)
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

    def _ensure_repo(self):
        """Adopt the remote's history when starting a fresh working tree.

        Initialising beside a remote that already has commits produced two
        unrelated histories, and every push from then on was rejected as a
        non-fast-forward — permanently, since the mirror never fetches.
        """
        self.repo_directory.mkdir(parents=True, exist_ok=True)
        if (self.repo_directory / ".git").exists():
            return
        self._git("init", "-q", "-b", self.branch)
        if not self.remote:
            return
        self._git("remote", "add", "origin", self.remote)
        if self._git("fetch", "-q", "origin", self.branch, check=False).returncode == 0:
            self._git("reset", "-q", "--hard", f"origin/{self.branch}")

    def _mirror_files(self):
        """Make the mirror match the vault exactly, and report what moved."""
        published = {path.name: path for path in self.vault_directory.glob("*.md")}
        mirrored = {path.name for path in self.repo_directory.glob("*.md")}
        changed = []

        for name, source in published.items():
            target = self.repo_directory / name
            content = source.read_bytes()
            if name not in mirrored or target.read_bytes() != content:
                target.write_bytes(content)
                changed.append(name)

        for name in mirrored - set(published):
            (self.repo_directory / name).unlink()
            changed.append(name)

        return sorted(changed)

    def _check_vault(self):
        """Path.glob swallows permission errors, so an unreadable vault would
        look like an empty one and the mirror would report success having
        copied nothing. Fail loudly instead."""
        if not self.vault_directory.is_dir():
            raise VaultUnreadable(f"{self.vault_directory} is not a directory")
        if not os.access(self.vault_directory, os.R_OK | os.X_OK):
            raise VaultUnreadable(f"{self.vault_directory} is not readable by this user")

    def _commit(self, count):
        self._git("add", "-A")
        self._git("commit", "-q", "-m", f"Publish {count} note{'s' if count != 1 else ''}")

    def _pending(self):
        """Ask git, not the file contents, what still needs committing.

        Comparing files alone made a failed commit permanent: the notes already
        matched on the next run, so the mirror reported nothing to do and never
        committed what it had copied.
        """
        lines = self._git("status", "--porcelain").stdout.splitlines()
        return sorted(Path(line[3:].strip().strip('"')).name for line in lines if line)

    def _adopt_remote(self):
        """Converge on the remote's history, keeping the vault as the content.

        Anything that pushes to the remote — a phone syncing the vault, a hand
        run — leaves it ahead, and every later push is rejected until someone
        intervenes. The vault is the source of truth for CONTENT and the remote
        is the source of truth for HISTORY, so take both: adopt the history and
        put the vault state back on top.
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
            self._git("reset", "-q", "--hard", f"origin/{self.branch}")

    def sync(self):
        self._check_vault()
        self._ensure_repo()
        self._adopt_remote()
        changed = self._mirror_files() or self._pending()
        if not changed:
            return []
        self._commit(len(changed))
        if self.remote:
            self._git("push", "-q", "--set-upstream", "origin", self.branch)
        return changed


def main():
    mirror = GitMirror(
        os.environ["KNOWLEDGE_VAULT_DIR"],
        os.environ["KNOWLEDGE_VAULT_MIRROR_DIR"],
        remote=os.environ.get("KNOWLEDGE_VAULT_MIRROR_REMOTE"),
    )
    try:
        changed = mirror.sync()
    except VaultUnreadable as error:
        print(f"knowledge-vault mirror: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"knowledge-vault mirror: {(error.stderr or '').strip()}", file=sys.stderr)
        return 1
    print(f"knowledge-vault mirror: {len(changed)} note(s) synced")
    return 0
