import json
import os
import tempfile
import threading
from pathlib import Path
from uuid import uuid4


class Journal:
    """Append-only, fsync'd NDJSON durable write queue.

    Used when a target backend is unavailable for `store`: the write is
    appended here (never dropped, never silently committed) and the caller
    is told its status is "pending". A drainer later replays entries and
    `ack`s each one once the backend confirms the write.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def append(self, entry: dict) -> str:
        entry_id = str(uuid4())
        record = {"id": entry_id, **entry}
        line = json.dumps(record) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            self._fsync_dir()
        return entry_id

    def replay(self) -> list[dict]:
        with self._lock:
            return self._read()

    def ack(self, entry_id: str) -> None:
        with self._lock:
            remaining = [entry for entry in self._read() if entry["id"] != entry_id]
            self._rewrite(remaining)

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        entries = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    # A truncated trailing line from a crash mid-write must
                    # not take down previously committed entries.
                    break
        return entries

    def _rewrite(self, entries: list[dict]) -> None:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, delete=False
        ) as temporary:
            for entry in entries:
                temporary.write(json.dumps(entry) + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary.name, self.path)
        self._fsync_dir()

    def _fsync_dir(self) -> None:
        directory_fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
