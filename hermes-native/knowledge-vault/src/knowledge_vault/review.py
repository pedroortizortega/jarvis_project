"""What promotion strips before a note leaves `pending/` for `knowledge/`.

The reviewer's decision lives in the note's own frontmatter, merged with its
OKF fields in a single block — Obsidian only parses the first frontmatter
block, so a second, wrapping one rendered as body text instead. Promotion
reuses `_reviewed_note()` to strip these fields before publish (F-3).
"""

from .note import _render_value, body_of, parse_frontmatter

# Fields the lifecycle adds to a pending note; they belong to the review
# process, not to the published note (D-10 adds `idempotency_key` to what
# was `REVIEW_FIELDS`, renamed `PENDING_FIELDS`).
PENDING_FIELDS = ("reviewer", "decision", "rationale", "idempotency_key")


def _reviewed_note(path):
    """The note exactly as the reviewer approved it, minus the review fields.

    What a reviewer approves is the text in front of them. Publishing the
    original proposal instead would discard their corrections in silence.
    """
    text = path.read_text(encoding="utf-8")
    fields = {k: v for k, v in parse_frontmatter(text).items() if k not in PENDING_FIELDS}
    if not fields:
        return body_of(text).strip() + "\n"
    lines = [f"{key}: {_render_value(value)}" for key, value in fields.items()]
    return "---\n" + "\n".join(lines) + "\n---\n" + body_of(text).strip() + "\n"
