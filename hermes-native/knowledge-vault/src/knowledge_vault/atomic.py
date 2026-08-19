import os
import tempfile
from pathlib import Path


def write_atomic(path, text, mode):
    """Replace `path` with `text` atomically, at an explicit mode.

    Every file here is written by one system user and read by another, and a
    temporary file is created 0600 while `os.replace` preserves that mode. The
    mode is therefore always explicit and never inherited from the caller's
    umask, which differs between systemd and a manual run.

    On failure the target keeps its previous content and no temporary is left
    behind.
    """
    path = Path(path)
    temporary = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    )
    try:
        with temporary:
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary.name, mode)
        os.replace(temporary.name, path)
    except OSError:
        Path(temporary.name).unlink(missing_ok=True)
        raise
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return path
