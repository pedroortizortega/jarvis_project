import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from .atomic import write_atomic
from .models import Decision, Proposal, PublicationFailure
from .note import body_of, parse_frontmatter


def _render(value):
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(str(item) for item in value) + "]"
    text = str(value)
    return f'"{text}"' if ": " in text or text.endswith(":") else text
class PendingProjector:
    def __init__(self, directory):
        self.directory = Path(directory)

    def project(self, proposal):
        """Merge the review fields into the note's own frontmatter.

        Wrapping the note instead produced two frontmatter blocks: Obsidian
        parses only the first, so the note's real fields rendered as body text
        and the reviewer edited the wrong block.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{proposal.id}.md"
        # The review fields ship empty and ready to fill: typing the key from
        # memory is how `devision` happened, and a field already present only
        # needs a value.
        fields = {
            "proposal_id": proposal.id,
            "version": 1,
            **parse_frontmatter(proposal.markdown),
            "reviewer": "",
            "decision": "",
            "rationale": "",
        }
        lines = [f"{key}: {_render(value)}" for key, value in fields.items()]
        note = "---\n" + "\n".join(lines) + "\n---\n" + body_of(proposal.markdown).strip() + "\n"
        # 0660: the human reviewer writes the decision into this very file.
        return write_atomic(path, note, 0o660)


class DecisionImporter:
    def __init__(self, record):
        self.record = record

    def import_file(self, path):
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        try:
            end = lines.index("---", 1)
        except ValueError as error:
            raise ValueError("decision frontmatter is required") from error
        fields = dict(line.split(": ", 1) for line in lines[1:end] if ": " in line)
        required = ("proposal_id", "reviewer", "decision", "rationale")
        if fields.get("version") != "1" or any(not fields.get(key) for key in required):
            raise ValueError("decision requires version 1, reviewer, decision, and rationale")
        if fields["decision"] not in {"approved", "rejected"}:
            raise ValueError(f"decision must be approved or rejected, got {fields['decision']!r}")
        decision = Decision(fields["proposal_id"], 1, fields["reviewer"], fields["decision"], fields["rationale"])
        self.record(decision)
        return decision


# Fields the review flow adds; they belong to the decision, not to the note.
REVIEW_FIELDS = ("proposal_id", "version", "reviewer", "decision", "rationale")


def _reviewed_note(path):
    """The note exactly as the reviewer approved it, minus the review fields.

    What a reviewer approves is the text in front of them. Publishing the
    original proposal instead would discard their corrections in silence.
    """
    text = path.read_text(encoding="utf-8")
    fields = {k: v for k, v in parse_frontmatter(text).items() if k not in REVIEW_FIELDS}
    if not fields:
        return body_of(text).strip() + "\n"
    lines = [f"{key}: {_render(value)}" for key, value in fields.items()]
    return "---\n" + "\n".join(lines) + "\n---\n" + body_of(text).strip() + "\n"


def _frontmatter(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        return {}
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}
    return dict(line.split(": ", 1) for line in lines[1:end] if ": " in line)


class DirectoryUnusable(RuntimeError):
    """Raised when a state directory is missing or not writable by this user."""


def _require_writable(path):
    if not path.is_dir():
        raise DirectoryUnusable(f"{path} does not exist; run the installer")
    if not os.access(path, os.W_OK | os.X_OK):
        raise DirectoryUnusable(f"{path} is not writable by this user")


def run_review(spool_directory, pending_directory, decisions_directory, on_failure=None):
    """Project spooled proposals for Obsidian and export the decisions humans made.

    Projection never overwrites an existing pending file, because a reviewer may
    already be editing it. A file the human has not decided yet is left alone,
    and a malformed decision stays in place so it can be corrected.
    """
    spool, pending = Path(spool_directory), Path(pending_directory)
    decisions = Path(decisions_directory)
    # Never create these: a silent mkdir leaves the directory owned by whoever
    # ran the command first, and the service then fails on every later run.
    for directory in (pending, decisions):
        _require_writable(directory)
    projector, projected = PendingProjector(pending), []

    for path in sorted(spool.glob("*.json")):
        try:
            proposal = Proposal(**json.loads(path.read_text(encoding="utf-8"))["proposal"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            if on_failure:
                on_failure(PublicationFailure(str(path), f"unreadable proposal: {error}"))
            continue
        if (pending / f"{proposal.id}.md").exists():
            continue
        if (decisions / f"{proposal.id}.json").exists():
            # Already decided. Projecting it again would put a rejection back in
            # front of the reviewer on every run.
            continue
        projected.append(projector.project(proposal))

    recorded = []
    importer = DecisionImporter(recorded.append)
    for path in sorted(pending.glob("*.md")):
        fields = _frontmatter(path)
        if not fields.get("decision"):
            # Every pending note carries the fields empty, so an empty decision
            # simply means nobody has decided yet. A reason without a decision
            # is different: it is half an answer, and saying nothing would
            # leave the reviewer believing they had finished.
            if on_failure and (fields.get("rationale") or fields.get("reviewer")):
                on_failure(
                    PublicationFailure(
                        str(path),
                        "a reason is written but 'decision' is empty; fill it with approved or rejected",
                    )
                )
            continue
        try:
            decision = importer.import_file(path)
        except ValueError as error:
            if on_failure:
                on_failure(PublicationFailure(str(path), f"invalid decision: {error}"))
            continue
        # 0640: the control plane reads exported decisions as another user.
        payload = {**asdict(decision), "markdown": _reviewed_note(path)}
        write_atomic(decisions / f"{decision.proposal_id}.json", json.dumps(payload), 0o640)
        path.unlink()
    return projected, recorded


def main():
    failures = []
    try:
        projected, recorded = run_review(
            os.environ["KNOWLEDGE_VAULT_PROPOSAL_SPOOL"],
            os.environ["KNOWLEDGE_VAULT_PENDING_DIR"],
            os.environ["KNOWLEDGE_VAULT_DECISIONS_DIR"],
            on_failure=failures.append,
        )
    except DirectoryUnusable as error:
        print(f"knowledge-vault review: {error}", file=sys.stderr)
        return 1
    print(f"knowledge-vault review: projected {len(projected)}, recorded {len(recorded)}")
    for failure in failures:
        print(f"knowledge-vault review: {failure.proposal_id}: {failure.reason}", file=sys.stderr)
    return 1 if failures else 0
