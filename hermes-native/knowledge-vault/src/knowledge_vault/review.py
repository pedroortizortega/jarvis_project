from pathlib import Path

from .models import Decision
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
            raise ValueError("decision must be approved or rejected")
        decision = Decision(fields["proposal_id"], 1, fields["reviewer"], fields["decision"], fields["rationale"])
        self.record(decision)
        return decision
