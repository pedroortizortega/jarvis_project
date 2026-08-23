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
from pathlib import Path

KNOWLEDGE_DIRNAME = "knowledge"
PENDING_DIRNAME = "pending"


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


@contextlib.contextmanager
def vault_lock(vault_directory):
    """Serialize writers (promote/sync) on one exclusive filesystem lock.

    Reuses `Publisher._fence()`'s pattern (design.md D-08): an explicit
    `flock` makes contention ordered and observable instead of surfacing as
    an opaque `CalledProcessError` from git's own lock.
    """
    lock_path = Path(vault_directory) / ".vault.lock"
    lock_path.touch(mode=0o660, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
