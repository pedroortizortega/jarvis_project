"""Stand-in for the control plane while the proposal API does not exist.

The review runner exports a decision; the publisher consumes an approved record
that pairs a proposal with its approval. Joining the two is the control plane's
job. Until that service exists, this joins them locally so a first cycle can be
run and observed end to end.

Delete this script once the proposal API owns approval records.

    python scripts/approve_locally.py PROPOSAL_SPOOL DECISIONS_DIR APPROVED_DIR
"""

import json
import sys
from pathlib import Path


def main(spool, decisions, approved):
    spool, decisions, approved = Path(spool), Path(decisions), Path(approved)
    approved.mkdir(parents=True, exist_ok=True)
    proposals = {}
    for path in spool.glob("*.json"):
        proposal = json.loads(path.read_text(encoding="utf-8"))["proposal"]
        proposals[proposal["id"]] = proposal

    joined = 0
    for path in sorted(decisions.glob("*.json")):
        decision = json.loads(path.read_text(encoding="utf-8"))
        proposal = proposals.get(decision["proposal_id"])
        if proposal is None:
            print(f"no proposal for decision {path.name}", file=sys.stderr)
            continue
        if decision["decision"] != "approved":
            print(f"{decision['proposal_id']}: {decision['decision']}, nothing to publish")
            continue
        target = approved / f"{decision['proposal_id']}.json"
        target.write_text(
            json.dumps({"proposal": proposal, "decision": decision}), encoding="utf-8"
        )
        joined += 1
        print(f"approved record ready: {target}")
    return 0 if joined or not any(decisions.glob("*.json")) else 1


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:]))
