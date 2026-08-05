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
        fields = {"proposal_id": proposal.id, "version": 1, **parse_frontmatter(proposal.markdown)}
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


def _frontmatter(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        return {}
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}
    return dict(line.split(": ", 1) for line in lines[1:end] if ": " in line)


def run_review(spool_directory, pending_directory, decisions_directory, on_failure=None):
    """Project spooled proposals for Obsidian and export the decisions humans made.

    Projection never overwrites an existing pending file, because a reviewer may
    already be editing it. A file the human has not decided yet is left alone,
    and a malformed decision stays in place so it can be corrected.
    """
    spool, pending = Path(spool_directory), Path(pending_directory)
    decisions = Path(decisions_directory)
    for directory in (pending, decisions):
        directory.mkdir(parents=True, exist_ok=True)
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
        projected.append(projector.project(proposal))

    recorded = []
    importer = DecisionImporter(recorded.append)
    for path in sorted(pending.glob("*.md")):
        if "decision" not in _frontmatter(path):
            continue
        try:
            decision = importer.import_file(path)
        except ValueError as error:
            if on_failure:
                on_failure(PublicationFailure(str(path), f"invalid decision: {error}"))
            continue
        # 0640: the control plane reads exported decisions as another user.
        write_atomic(decisions / f"{decision.proposal_id}.json", json.dumps(asdict(decision)), 0o640)
        path.unlink()
    return projected, recorded


def main():
    failures = []
    projected, recorded = run_review(
        os.environ["KNOWLEDGE_VAULT_PROPOSAL_SPOOL"],
        os.environ["KNOWLEDGE_VAULT_PENDING_DIR"],
        os.environ["KNOWLEDGE_VAULT_DECISIONS_DIR"],
        on_failure=failures.append,
    )
    print(f"knowledge-vault review: projected {len(projected)}, recorded {len(recorded)}")
    for failure in failures:
        print(f"knowledge-vault review: {failure.proposal_id}: {failure.reason}", file=sys.stderr)
    return 1 if failures else 0
