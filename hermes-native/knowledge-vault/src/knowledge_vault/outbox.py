import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from .models import Proposal
class DurableOutbox:
    def __init__(self, directory, sender):
        self.directory, self.sender = Path(directory), sender
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "pending.json"
        self.lock_path = self.directory / "pending.lock"
        self._submitted = {}
        self._lock = threading.RLock()

    def pending(self):
        with self._lock, self._file_lock():
            return self._read()

    @contextmanager
    def _file_lock(self):
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _read(self):
        if not self.path.exists():
            return []
        return [Proposal(**item) for item in json.loads(self.path.read_text(encoding="utf-8"))]

    def _save(self, proposals):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.directory, delete=False) as temporary:
            json.dump([item.__dict__ for item in proposals], temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary.name, self.path)
        directory = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def submit(self, proposal):
        with self._lock:
            if proposal.idempotency_key in self._submitted:
                return self._submitted[proposal.idempotency_key]
            for queued in self.pending():
                if queued.idempotency_key == proposal.idempotency_key:
                    return queued
            try:
                delivered = self.sender(proposal)
                self._submitted[proposal.idempotency_key] = delivered
                return delivered
            except OSError:
                with self._file_lock():
                    queued = self._read()
                    for existing in queued:
                        if existing.idempotency_key == proposal.idempotency_key:
                            return existing
                    self._save([*queued, proposal])
                return proposal

    def drain(self):
        with self._lock, self._file_lock():
            delivered = []
            queued = self._read()
            while queued:
                proposal = queued[0]
                try:
                    sent = self.sender(proposal)
                except OSError:
                    break
                self._submitted[proposal.idempotency_key] = sent
                delivered.append(sent)
                queued.pop(0)
                self._save(queued)
            return delivered
