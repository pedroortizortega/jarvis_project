import fcntl
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from .atomic import write_atomic
from .models import ApprovedRecord, Decision, Proposal, PublicationFailure


def load_approved(directory, on_failure=None):
    """Read approved records the control plane spooled for the local publisher.

    An unreadable entry is never silently dropped: it is reported through
    `on_failure` so an operator can see why a note was not published.
    """
    records = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            records.append(
                ApprovedRecord(Proposal(**payload["proposal"]), Decision(**payload["decision"]))
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            if on_failure:
                on_failure(PublicationFailure(str(path), f"unreadable approved record: {error}"))
    return records


def main():
    vault = os.environ["KNOWLEDGE_VAULT_DIR"]
    state = os.environ["KNOWLEDGE_VAULT_STATE_DIR"]
    spool = os.environ["KNOWLEDGE_VAULT_APPROVED_DIR"]
    rejected = []
    publisher = Publisher(vault, lambda: load_approved(spool, rejected.append), state)
    publisher.publish()
    for failure in rejected + publisher.failures:
        print(f"knowledge-vault publisher: {failure.proposal_id}: {failure.reason}", file=sys.stderr)
    return 1 if rejected or publisher.failures else 0


class PublisherLocked(RuntimeError):
    """Raised when another publisher already owns the canonical vault."""


class Publisher:
    """Single host-local writer of the canonical vault."""

    def __init__(self, vault_directory, source, state_directory):
        self.vault_directory = Path(vault_directory)
        self.state_directory = Path(state_directory)
        self.source = source
        self.state_directory.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.state_directory / "publisher.lock"
        self.failures = []

    @contextmanager
    def _fence(self):
        with self.lock_path.open("a+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise PublisherLocked("another publisher owns the canonical vault") from error
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def publish(self):
        self.failures = []
        published = []
        with self._fence():
            self.vault_directory.mkdir(parents=True, exist_ok=True)
            for record in self.source():
                failure = self._validate(record)
                if failure:
                    self.failures.append(failure)
                    continue
                try:
                    published.append(self._write(record))
                except OSError as error:
                    self.failures.append(PublicationFailure(record.proposal.id, str(error)))
        return published

    def _validate(self, record):
        if record.decision.decision != "approved":
            return PublicationFailure(record.proposal.id, "proposal has no recorded approval")
        if not record.proposal.markdown.strip():
            return PublicationFailure(record.proposal.id, "proposal markdown is empty")
        return None

    def _write(self, record):
        target = self.vault_directory / f"{record.proposal.id}.md"
        # 0640: the publisher owns the vault, read-only consumers share its group.
        return write_atomic(target, f"{record.proposal.markdown.strip()}\n", 0o640)
