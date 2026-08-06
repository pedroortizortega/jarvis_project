import fcntl
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path

from .atomic import write_atomic
from .note import MissingType, parse_frontmatter, render, title_of
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


def new_note_id(taken):
    """A Zettelkasten id: the second the note was created, made unique.

    The id is the file name and every link is built from it, so it must never
    change. Two notes born in the same second would collide, so the later one
    borrows the next free second.
    """
    stamp = datetime.now(timezone.utc)
    while stamp.strftime("%Y%m%d%H%M%S") in taken:
        stamp += timedelta(seconds=1)
    return stamp.strftime("%Y%m%d%H%M%S")


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
        # proposal id -> published file name, so a revision replaces the note it
        # supersedes instead of leaving a stale duplicate beside it.
        self.manifest_path = self.state_directory / "notes.json"
        # Records that can never become valid. Reporting them on every run
        # leaves the unit permanently red, and a unit that is always red stops
        # being read.
        self.unpublishable_path = self.state_directory / "unpublishable.json"
        self.failures = []

    def _manifest(self):
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _target(self, record, manifest):
        """A revision reuses the id of the note it supersedes: that is what
        keeps every existing link to it valid."""
        proposal = record.proposal
        # An approved record stays on disk and a timer re-publishes it every
        # few minutes. Filing it under a fresh id each time filled the vault
        # with copies of the same note, so publishing must be idempotent.
        published = manifest.get(proposal.id)
        if published:
            return self.vault_directory / published, published
        superseded = manifest.get(proposal.predecessor_id) if proposal.predecessor_id else None
        if superseded:
            return self.vault_directory / superseded, superseded
        taken = {path.stem for path in self.vault_directory.glob("*.md")} | set(manifest.values())
        return self.vault_directory / f"{new_note_id(taken)}.md", None

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

    def _unpublishable(self):
        try:
            return set(json.loads(self.unpublishable_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return set()

    def publish(self):
        self.failures = []
        published = []
        with self._fence():
            self.vault_directory.mkdir(parents=True, exist_ok=True)
            manifest = self._manifest()
            known_bad = self._unpublishable()
            for record in self.source():
                if record.proposal.id in known_bad:
                    continue
                failure = self._validate(record)
                if failure:
                    # Validation can never pass on a later run, unlike a write
                    # that failed because the disk was full.
                    self.failures.append(failure)
                    known_bad.add(record.proposal.id)
                    write_atomic(self.unpublishable_path, json.dumps(sorted(known_bad)), 0o640)
                    continue
                try:
                    published.append(self._write(record, manifest))
                except OSError as error:
                    self.failures.append(PublicationFailure(record.proposal.id, str(error)))
            if published:
                write_atomic(self.manifest_path, json.dumps(manifest), 0o640)
        return published

    def _validate(self, record):
        if record.decision.decision != "approved":
            return PublicationFailure(record.proposal.id, "proposal has no recorded approval")
        if not record.proposal.markdown.strip():
            return PublicationFailure(record.proposal.id, "proposal markdown is empty")
        if not parse_frontmatter(record.proposal.markdown).get("type"):
            return PublicationFailure(record.proposal.id, "note has no OKF type in its frontmatter")
        return None

    def _write(self, record, manifest):
        target, superseded = self._target(record, manifest)
        fields = parse_frontmatter(record.proposal.markdown)
        if superseded and target.exists():
            # Keep every name the note has been known by, so a link written
            # under the old title still leads a reader to it.
            previous = parse_frontmatter(target.read_text(encoding="utf-8"))
            fields["aliases"] = list(previous.get("aliases") or []) + list(fields.get("aliases") or [])
            if previous.get("title"):
                fields["aliases"] = [*fields["aliases"], previous["title"]]
            fields.setdefault("timestamp", previous.get("timestamp"))
        fields["title"] = title_of(record.proposal.markdown) or fields.get("title")
        fields.setdefault("timestamp", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        fields["aliases"] = list(dict.fromkeys(alias for alias in fields.get("aliases") or [] if alias))
        note = render(record.proposal.markdown, fields, note_id=target.stem)
        # 0640: the publisher owns the vault, read-only consumers share its group.
        write_atomic(target, note, 0o640)
        manifest[record.proposal.id] = target.name
        return target
