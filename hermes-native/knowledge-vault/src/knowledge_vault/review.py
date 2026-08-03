import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from .models import Decision, Proposal, PublicationFailure
class PendingProjector:
    def __init__(self, directory):
        self.directory = Path(directory)

    def project(self, proposal):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{proposal.id}.md"
        path.write_text(f"---\nproposal_id: {proposal.id}\nversion: 1\n---\n{proposal.markdown}\n", encoding="utf-8")
        return path


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


def _write_json(path, payload):
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    ) as temporary:
        json.dump(payload, temporary)
        temporary.flush()
        os.fsync(temporary.fileno())
    os.replace(temporary.name, path)


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
        _write_json(decisions / f"{decision.proposal_id}.json", asdict(decision))
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
