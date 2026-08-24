"""The single choke point for where notes live in the vault tree.

Every other module that needs to enumerate published notes goes through
`published_notes()` here instead of walking the vault directory itself. This
makes the search/index scope an **allowlist by construction**: no module
outside this one is ever allowed to spell "pending" (or any other folder
name) to exclude it, so a folder added to the vault tree later is invisible
to search without anyone remembering to update an exclusion list.

See design.md D-01/D-02 (knowledge-vault-restructure).
"""

import contextlib
import fcntl
import os
import subprocess
from pathlib import Path

KNOWLEDGE_DIRNAME = "knowledge"
PENDING_DIRNAME = "pending"

# git refuses to commit without an identity, and a system user has no
# gitconfig, so every writer (promote, sync) supplies its own. Shared here
# rather than duplicated per module, so a future change to it (or to the
# subprocess-safety env vars) lands in one place, not two that can drift.
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "knowledge-vault",
    "GIT_AUTHOR_EMAIL": "knowledge-vault@localhost",
    "GIT_COMMITTER_NAME": "knowledge-vault",
    "GIT_COMMITTER_EMAIL": "knowledge-vault@localhost",
}


def run_git(vault_directory, *args, check=True):
    """The one place `git` is ever invoked from this package.

    Argument list only, never `shell=True`: a rationale containing `\\n`,
    `"`, `$(...)` or `--force` must never become a shell command or a git
    option (design.md Threat Matrix: "Shell / subprocess",
    "Commit-message injection").
    """
    return subprocess.run(
        ["git", *args],
        cwd=vault_directory,
        capture_output=True,
        text=True,
        check=check,
        env={**os.environ, **GIT_IDENTITY, "GIT_TERMINAL_PROMPT": "0"},
    )


def knowledge_root(vault_directory):
    """The only root `published_notes()` ever scans."""
    return Path(vault_directory) / KNOWLEDGE_DIRNAME


def pending_root(vault_directory):
    """Where JARVIS may write; never read by search."""
    return Path(vault_directory) / PENDING_DIRNAME


def published_notes(vault_directory):
    """Every published note, and nothing else that happens to live in the tree.

    An allowlist: this never enumerates the vault root, so a folder added
    later is invisible without anyone remembering to exclude it. A vault
    tree that has no `knowledge/` yet simply publishes nothing, rather than
    raising — the same "empty vault" steady state the old flat layout had.
    """
    root = knowledge_root(vault_directory)
    if not root.is_dir():
        return iter(())
    return root.rglob("*.md")


class VaultLocked(RuntimeError):
    """Another writer (promote/sync) already holds the vault lock."""


@contextlib.contextmanager
def vault_lock(vault_directory):
    """Serialize writers (promote/sync) on one exclusive filesystem lock.

    Reuses `Publisher._fence()`'s pattern (design.md D-08) verbatim,
    including its non-blocking `LOCK_NB` + typed-exception shape, not just
    its use of `flock`. A contended lock fails fast and observably
    (`VaultLocked`), the same way `_fence()` raises `PublisherLocked`,
    rather than hanging a timer-triggered run indefinitely — that ordered,
    observable failure is the entire reason D-08 chose an explicit fence
    over trusting git's own lock in the first place.
    """
    lock_path = Path(vault_directory) / ".vault.lock"
    lock_path.touch(mode=0o660, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise VaultLocked("another writer owns the vault lock") from error
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
