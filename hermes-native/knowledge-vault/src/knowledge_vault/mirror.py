import os
import subprocess
import sys
from pathlib import Path

# The mirror is a copy, never the canonical vault: a broken git state must
# never be able to damage published notes.
COMMIT_AUTHOR = "knowledge-vault <knowledge-vault@localhost>"


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
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )

    def _ensure_repo(self):
        self.repo_directory.mkdir(parents=True, exist_ok=True)
        if not (self.repo_directory / ".git").exists():
            self._git("init", "-q", "-b", self.branch)

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

    def sync(self):
        self._ensure_repo()
        changed = self._mirror_files()
        if not changed:
            return []
        self._git("add", "-A")
        self._git(
            "commit", "-q", "--author", COMMIT_AUTHOR,
            "-m", f"Publish {len(changed)} note{'s' if len(changed) != 1 else ''}",
        )
        if self.remote:
            self._git("remote", "remove", "origin", check=False)
            self._git("remote", "add", "origin", self.remote)
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
    except subprocess.CalledProcessError as error:
        print(f"knowledge-vault mirror: {(error.stderr or '').strip()}", file=sys.stderr)
        return 1
    print(f"knowledge-vault mirror: {len(changed)} note(s) synced")
    return 0
