"""Let an agent read the vault before it proposes.

A Zettelkasten is worth what its links are worth, and an agent that cannot see
what is already there will never link to it. This answers with the note id an
agent must write into a link, never with prose it could paraphrase as its own.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .note import parse_frontmatter
from .retrieval import Retriever, build_index

EXCERPT = 200
# At least half the query terms must appear. Without it a single common word
# ("en", "de") is enough to match, and the agent would link a note to something
# it has nothing to do with.
MIN_RELEVANCE = 0.5


@dataclass(frozen=True)
class VaultHit:
    note: str
    title: str
    excerpt: str
    score: float = 0.0


def search_vault(query, vault_directory, index_path, limit=5):
    """Search published notes, rebuilding the index when it has fallen behind.

    Retrieval refuses a stale index on purpose, because serving stale knowledge
    is worse than serving none. For an agent about to write a link, though, the
    honest answer is a fresh index rather than "unavailable".
    """
    vault, index_path = Path(vault_directory), Path(index_path)
    if not any(vault.glob("*.md")):
        return []
    retriever = Retriever(vault, index_path)
    result = retriever.search(query, limit=limit)
    if not result.available:
        build_index(vault, index_path)
        result = Retriever(vault, index_path).search(query, limit=limit)
    if not result.available:
        return []

    seen, hits = set(), []
    for hit in result.hits:
        if hit.score < MIN_RELEVANCE:
            continue
        note = Path(hit.note_path)
        if note.name in seen:
            continue
        seen.add(note.name)
        fields = parse_frontmatter(note.read_text(encoding="utf-8"))
        excerpt = " ".join(hit.text.split())
        hits.append(
            VaultHit(
                note.name,
                fields.get("title") or note.stem,
                excerpt[:EXCERPT] + ("…" if len(excerpt) > EXCERPT else ""),
                hit.score,
            )
        )
    return hits


def main():
    if len(sys.argv) < 2:
        print("usage: knowledge-vault-search <query>", file=sys.stderr)
        return 2
    try:
        hits = search_vault(
            " ".join(sys.argv[1:]),
            os.environ["KNOWLEDGE_VAULT_DIR"],
            os.environ["KNOWLEDGE_VAULT_INDEX"],
        )
    except (KeyError, OSError) as error:
        print(f"knowledge-vault search: {error}", file=sys.stderr)
        return 1
    if not hits:
        print("no matching notes; nothing to link to yet")
        return 0
    for hit in hits:
        print(f"{hit.note}  {hit.title}")
        print(f"    {hit.excerpt}")
    return 0
