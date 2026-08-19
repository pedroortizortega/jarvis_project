import hashlib
import json
import math
import re
from pathlib import Path

from .atomic import write_atomic
from .note import body_of
from .models import RetrievalHit, RetrievalResult

TOKEN = re.compile(r"[a-z0-9]+")
HEADING = re.compile(r"^#{1,6}\s+(.*)$")
LEXICAL_WEIGHT = 0.5


def _tokens(text):
    return TOKEN.findall(text.lower())


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _signature(vault):
    """Cheap stat-only fingerprint used to decide whether to re-read notes."""
    return tuple(
        sorted(
            (str(path.relative_to(vault)), path.stat().st_mtime_ns, path.stat().st_size)
            for path in vault.rglob("*.md")
        )
    )


def vault_revision(vault_directory, cache=None):
    """Revision of the published vault; any note change produces a new value.

    Hashing every note on every query is the first thing that gets expensive as
    the vault grows, so an optional cache short-circuits on an unchanged
    stat signature. A note rewritten with the same size and a restored mtime
    would evade the signature; that requires deliberately forged timestamps,
    and `build_index` always recomputes the revision from content.
    """
    vault = Path(vault_directory)
    signature = _signature(vault)
    if cache is not None and cache.get("signature") == signature:
        return cache["revision"]
    entries = sorted(
        f"{path.relative_to(vault)}:{_digest(path.read_text(encoding='utf-8'))}"
        for path in vault.rglob("*.md")
    )
    revision = _digest("\n".join(entries))
    if cache is not None:
        cache.update(signature=signature, revision=revision)
    return revision


def _fragments(path):
    """Split a note into heading-scoped fragments with deterministic ids.

    The OKF frontmatter is metadata for agents, not prose: indexing it would
    have every note match its own field names.
    """
    lines = body_of(path.read_text(encoding="utf-8")).splitlines()
    sections, heading, body = [], None, []
    for line in lines:
        match = HEADING.match(line)
        if match:
            if heading is not None or body:
                sections.append((heading, body))
            heading, body = match.group(1), []
        else:
            body.append(line)
    if heading is not None or body:
        sections.append((heading, body))
    fragments = []
    for title, content in sections:
        text = "\n".join(([f"# {title}"] if title else []) + content).strip()
        if not text:
            continue
        slug = "-".join(_tokens(title or path.stem)) or "fragment"
        fragments.append(
            {"note_path": str(path), "fragment_id": f"{slug}-{_digest(text)[:12]}", "text": text}
        )
    return fragments


def build_index(vault_directory, index_path):
    """Build the current index from published notes only. It is disposable."""
    vault = Path(vault_directory)
    index = {
        "revision": vault_revision(vault),
        "fragments": [fragment for path in sorted(vault.rglob("*.md")) for fragment in _fragments(path)],
    }
    index_path = Path(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    # 0640: the indexer builds it, read-only consumers query it.
    write_atomic(index_path, json.dumps(index), 0o640)
    return index


def _cosine(left, right):
    magnitude = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return sum(a * b for a, b in zip(left, right)) / magnitude if magnitude else 0.0


class Retriever:
    """Query published notes locally, or report the index unavailable."""

    def __init__(self, vault_directory, index_path, embedder=None):
        self.vault_directory = Path(vault_directory)
        self.index_path = Path(index_path)
        self.embedder = embedder
        self._revision_cache = {}

    def search(self, query, limit=5):
        try:
            index = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return RetrievalResult((), False, "index is unavailable")
        if index.get("revision") != vault_revision(self.vault_directory, self._revision_cache):
            return RetrievalResult((), False, "index revision does not match the published vault")
        scored = [
            (self._score(query, fragment["text"]), fragment) for fragment in index["fragments"]
        ]
        ranked = sorted(
            (item for item in scored if item[0] > 0), key=lambda item: (-item[0], item[1]["fragment_id"])
        )
        hits = tuple(
            RetrievalHit(fragment["note_path"], fragment["fragment_id"], fragment["text"], score)
            for score, fragment in ranked[:limit]
        )
        return RetrievalResult(hits, True, "")

    def _score(self, query, text):
        lexical = self._lexical(query, text)
        if self.embedder is None:
            return lexical
        semantic = max(0.0, _cosine(self.embedder(query), self.embedder(text)))
        return LEXICAL_WEIGHT * lexical + (1 - LEXICAL_WEIGHT) * semantic

    def _lexical(self, query, text):
        wanted = set(_tokens(query))
        if not wanted:
            return 0.0
        return len(wanted & set(_tokens(text))) / len(wanted)
