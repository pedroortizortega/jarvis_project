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

from dataclasses import dataclass

from . import layout
from .atomic import write_atomic
from .note import body_of, parse_frontmatter, title_of

DECISIONS = ("approved", "rejected")


@dataclass(frozen=True)
class Waiting:
    proposal_id: str
    title: str


def awaiting_decision(pending_directory):
    """What is waiting for a human, with the id a decision needs.

    Deciding takes a proposal id. Without a way to read the queue that id is
    unknowable, and the decide command cannot be used at all.
    """
    waiting = []
    for path in sorted(Path(pending_directory).glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fields = parse_frontmatter(text)
        if fields.get("decision"):
            continue
        waiting.append(Waiting(fields.get("proposal_id") or path.stem, title_of(text) or path.stem))
    return waiting


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


def list_main():
    try:
        pending = layout.pending_root(os.environ["KNOWLEDGE_VAULT_DIR"])
        waiting = awaiting_decision(pending)
    except (KeyError, OSError) as error:
        print(f"knowledge-vault pending: {error}", file=sys.stderr)
        return 1
    if not waiting:
        print("nothing is waiting for a decision")
        return 0
    for item in waiting:
        print(f"{item.proposal_id}  {item.title}")
    return 0


def main():
    """The reason arrives on stdin, never as an argument.

    A reason is a sentence a person wrote, with colons and quotes in it.
    Passing it through a shell argument meant the caller had to escape it, and
    the command died on the quoting before it ever ran.
    """
    if len(sys.argv) != 3:
        print(
            "usage: <reason on stdin> | knowledge-vault-decide <proposal-id> <approved|rejected>",
            file=sys.stderr,
        )
        return 2
    try:
        path = decide(
            sys.argv[1],
            sys.argv[2],
            sys.stdin.read(),
            layout.pending_root(os.environ["KNOWLEDGE_VAULT_DIR"]),
            reviewer=os.environ.get("KNOWLEDGE_VAULT_REVIEWER"),
            source=os.environ.get("KNOWLEDGE_VAULT_DECISION_SOURCE"),
        )
    except (ValueError, FileNotFoundError, KeyError, OSError) as error:
        print(f"knowledge-vault decide: {error}", file=sys.stderr)
        return 1
    print(f"{sys.argv[2]}: {path.name}")
    return 0
