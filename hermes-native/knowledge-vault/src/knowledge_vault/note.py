"""The note format: Zettelkasten identity inside an Open Knowledge Format envelope.

A note is Markdown with YAML frontmatter. OKF requires `type` and makes
`title`, `description`, `tags` and `timestamp` queryable; Zettelkasten
contributes the immutable `id` that file names and links are built from, and
`aliases`, which keeps a retitled note findable under every name it has had.

Only the small YAML subset the format actually uses is parsed here — scalars
and inline lists — so the package keeps no dependencies.
"""

import re
from datetime import datetime, timedelta, timezone

HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
FIELD_ORDER = ("type", "id", "title", "description", "aliases", "tags", "timestamp")


def new_note_id(taken):
    """A Zettelkasten id: the second the note was created, made unique.

    The id is the file name and every link is built from it, so it must never
    change. Two notes born in the same second would collide, so the later one
    borrows the next free second.
    """
    stamp = datetime.now(timezone.utc)
    while stamp.strftime("%Y%m%d%H%M%S") in taken:
        stamp += timedelta(seconds=1)
    return stamp.strftime("%Y%m%d%H%M%S")


class MissingType(ValueError):
    """OKF requires a type on every concept; a note without one is not a note."""


def _split(text):
    """Return (frontmatter lines, body) for a note."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], text
    try:
        end = lines.index("---", 1)
    except ValueError:
        return [], text
    return lines[1:end], "\n".join(lines[end + 1 :]).lstrip("\n")


def _value(raw):
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [item.strip() for item in inner.split(",") if item.strip()] if inner else []
    if len(raw) > 1 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def parse_frontmatter(text):
    fields = {}
    for line in _split(text)[0]:
        key, separator, raw = line.partition(":")
        if separator and not key.startswith(" "):
            fields[key.strip()] = _value(raw)
    return fields


def body_of(text):
    return _split(text)[1]


def title_of(text):
    heading = HEADING.search(body_of(text))
    return heading.group(1).strip() if heading else None


def _render_value(value):
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(str(item) for item in value) + "]"
    text = str(value)
    # A title like "Storage: solo local-path" would otherwise parse as a nested
    # mapping, so any value holding a colon is quoted.
    return f'"{text}"' if ": " in text or text.endswith(":") else text


def render(markdown, fields, note_id, timestamp=None):
    """Render a note with its OKF envelope, keeping the author's body intact."""
    fields = dict(fields)
    if not fields.get("type"):
        raise MissingType("a note needs an OKF type")
    fields["id"] = note_id
    fields.setdefault("title", title_of(markdown) or note_id)
    if timestamp:
        fields["timestamp"] = timestamp
    fields.setdefault("aliases", [])
    if fields["title"] not in fields["aliases"]:
        fields["aliases"] = [*fields["aliases"], fields["title"]]

    ordered = [key for key in FIELD_ORDER if key in fields]
    ordered += [key for key in fields if key not in FIELD_ORDER]
    lines = [f"{key}: {_render_value(fields[key])}" for key in ordered]
    return "---\n" + "\n".join(lines) + "\n---\n" + body_of(markdown).strip() + "\n"
