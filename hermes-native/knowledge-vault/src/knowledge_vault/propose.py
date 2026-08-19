"""Submit a proposal from an agent.

A proposal carries no authority: it only asks a human to look. That is why an
agent may write here freely, and why nothing it writes can reach the vault
without a recorded approval.
"""

import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from .atomic import write_atomic
from .models import Proposal
from .note import parse_frontmatter


def spool_sender(directory):
    """Deliver a proposal by spooling it where the review runner will find it."""
    directory = Path(directory)

    def send(proposal):
        # 0640: the review user reads this as another account.
        write_atomic(
            directory / f"{proposal.id}.json",
            json.dumps({"proposal": asdict(proposal)}),
            0o640,
        )
        return proposal

    return send


def propose(markdown, provenance, spool_directory):
    """Propose a note, once. Identical knowledge is never proposed twice."""
    if not markdown.strip():
        raise ValueError("a proposal needs content")
    if not parse_frontmatter(markdown).get("type"):
        # Refused here rather than at publication, so the agent learns while it
        # still has the context to fix it.
        raise ValueError("a note needs OKF frontmatter with a type")
    spool = Path(spool_directory)
    key = hashlib.sha256(markdown.strip().encode("utf-8")).hexdigest()
    for path in sorted(spool.glob("*.json")):
        try:
            existing = Proposal(**json.loads(path.read_text(encoding="utf-8"))["proposal"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if existing.idempotency_key == key:
            return existing
    proposal = Proposal.create(markdown.strip(), key, provenance)
    spool_sender(spool)(proposal)
    return proposal


def main():
    """Read the note on stdin so the agent never has to quote-escape it."""
    markdown = sys.stdin.read()
    provenance = {"agent": os.environ.get("KNOWLEDGE_VAULT_AGENT", "unknown")}
    if len(sys.argv) > 1:
        provenance["source"] = sys.argv[1]
    try:
        proposal = propose(markdown, provenance, os.environ["KNOWLEDGE_VAULT_PROPOSAL_SPOOL"])
    except (ValueError, KeyError, OSError) as error:
        print(f"knowledge-vault propose: {error}", file=sys.stderr)
        return 1
    print(proposal.id)
    return 0
