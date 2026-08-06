"""Record a decision on a pending note.

An agent may run this on the reviewer's behalf, but only when the reviewer said
so in words. It cannot be prevented at the filesystem level — the agent runs as
the same user — so the defence is that a decision is deliberate and auditable:
the reason is required and never invented, and `source` says through which
channel it arrived.
"""

import os
import sys
from pathlib import Path

from .atomic import write_atomic
from .note import body_of, parse_frontmatter

DECISIONS = ("approved", "rejected")


def _render(value):
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(str(item) for item in value) + "]"
    text = str(value)
    return f'"{text}"' if ": " in text or text.endswith(":") else text


def decide(proposal_id, decision, rationale, pending_directory, reviewer=None, source=None):
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}, got {decision!r}")
    if not rationale.strip():
        raise ValueError("a decision needs a reason; it is what makes it auditable later")

    path = Path(pending_directory) / f"{proposal_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"no proposal {proposal_id} is waiting for review")

    text = path.read_text(encoding="utf-8")
    fields = parse_frontmatter(text)
    if fields.get("decision"):
        raise ValueError(f"proposal {proposal_id} was already {fields['decision']}")

    fields["reviewer"] = reviewer or os.environ.get("KNOWLEDGE_VAULT_REVIEWER", "unknown")
    fields["decision"] = decision
    fields["rationale"] = rationale.strip()
    if source:
        fields["source"] = source
    lines = [f"{key}: {_render(value)}" for key, value in fields.items()]
    note = "---\n" + "\n".join(lines) + "\n---\n" + body_of(text).strip() + "\n"
    # 0660: the pending area is where the human writes, so it stays writable.
    return write_atomic(path, note, 0o660)


def main():
    if len(sys.argv) < 4:
        print(
            "usage: knowledge-vault-decide <proposal-id> <approved|rejected> <reason>",
            file=sys.stderr,
        )
        return 2
    try:
        path = decide(
            sys.argv[1],
            sys.argv[2],
            " ".join(sys.argv[3:]),
            os.environ["KNOWLEDGE_VAULT_PENDING_DIR"],
            reviewer=os.environ.get("KNOWLEDGE_VAULT_REVIEWER"),
            source=os.environ.get("KNOWLEDGE_VAULT_DECISION_SOURCE"),
        )
    except (ValueError, FileNotFoundError, KeyError, OSError) as error:
        print(f"knowledge-vault decide: {error}", file=sys.stderr)
        return 1
    print(f"{sys.argv[2]}: {path.name}")
    return 0
