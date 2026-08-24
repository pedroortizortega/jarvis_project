"""Submit a proposal from an agent.

A proposal carries no authority: it only asks a human to look. JARVIS may
write freely under `pending/` — and nowhere else — and nothing it writes
reaches `knowledge/` without a human-run promotion (see design.md D-01/D-02
of knowledge-vault-restructure).
"""

import hashlib
import os
import sys

from . import layout
from .atomic import write_atomic
from .note import new_note_id, parse_frontmatter, render

# Empty and ready to fill: a reviewer who already sees the key only needs to
# type a value, the same lesson `review.py`'s PENDING_FIELDS records.
_EMPTY_REVIEW_FIELDS = {"reviewer": "", "decision": "", "rationale": ""}


def _existing_by_key(pending_directory, key):
    """A pending note already carrying this idempotency key, if any (F-7/D-10)."""
    if not pending_directory.is_dir():
        return None
    for path in sorted(pending_directory.glob("*.md")):
        try:
            fields = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if fields.get("idempotency_key") == key:
            return path
    return None


def _taken_ids(vault_directory, pending_directory):
    taken = {path.stem for path in layout.published_notes(vault_directory)}
    if pending_directory.is_dir():
        taken |= {path.stem for path in pending_directory.glob("*.md")}
    return taken


def propose(markdown, provenance, vault_directory):
    """Propose a note, once. Identical knowledge is never proposed twice.

    Writes `pending/<id>.md` directly — no JSON spool intermediary. This
    function exposes no way to target anything other than `pending/`.
    """
    markdown = markdown.strip()
    if not markdown:
        raise ValueError("a proposal needs content")
    if not parse_frontmatter(markdown).get("type"):
        # Refused here rather than at promotion, so the agent learns while it
        # still has the context to fix it.
        raise ValueError("a note needs OKF frontmatter with a type")

    pending = layout.pending_root(vault_directory)
    key = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    existing = _existing_by_key(pending, key)
    if existing is not None:
        return existing

    note_id = new_note_id(_taken_ids(vault_directory, pending))
    fields = dict(provenance or {})
    fields.update(parse_frontmatter(markdown))
    fields.update(_EMPTY_REVIEW_FIELDS)
    fields["idempotency_key"] = key
    note = render(markdown, fields, note_id)
    pending.mkdir(parents=True, exist_ok=True)
    # 0660: JARVIS owns pending/, the human reviewer shares its group.
    return write_atomic(pending / f"{note_id}.md", note, 0o660)


def main():
    """Read the note on stdin so the agent never has to quote-escape it."""
    markdown = sys.stdin.read()
    provenance = {"agent": os.environ.get("KNOWLEDGE_VAULT_AGENT", "unknown")}
    if len(sys.argv) > 1:
        provenance["source"] = sys.argv[1]
    try:
        path = propose(markdown, provenance, os.environ["KNOWLEDGE_VAULT_DIR"])
    except (ValueError, KeyError, OSError) as error:
        print(f"knowledge-vault propose: {error}", file=sys.stderr)
        return 1
    print(path.stem)
    return 0
