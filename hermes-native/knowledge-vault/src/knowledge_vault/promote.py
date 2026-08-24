"""The audited act that moves a reviewed note from `pending/` to `knowledge/`.

This is the only actor allowed to write `knowledge/`. It refuses to run
without a reviewer, a rationale, and `decision: approved` already present in
the note's own frontmatter (D-05); it never overwrites an existing published
note (D-09); it strips the review fields before publish and records them in
the git commit message instead, so the audit trail lives in git history, not
in the published note (D-02 of the proposal).

D-04: promotion is unattended — `knowledge-vault-promote.timer` calls
`promote_all()` on an interval, not a human triggering a single id. A bare
hand `git mv` still works as an unaudited escape hatch; `check_published()`
is how that escape hatch gets caught (D-06).
"""

import os
import re
import subprocess
import sys
from pathlib import Path

from . import layout
from .atomic import write_atomic
from .layout import run_git as _git
from .note import parse_frontmatter, title_of
from .retrieval import build_index
from .review import PENDING_FIELDS, _reviewed_note

# `new_note_id()` (note.py) renders `datetime.strftime("%Y%m%d%H%M%S")`:
# exactly 14 digits, nothing else. Validated before any path join or git
# call — an id is untrusted input the moment it crosses the CLI/argv
# boundary (design.md Threat Matrix: "Path traversal via argv").
NOTE_ID = re.compile(r"\A[0-9]{14}\Z")


class PromotionRefused(RuntimeError):
    """The note failed D-05's contract, or `../etc/passwd` tried to be an id.

    Raised before any git call and before any file is touched.
    """


def _validate(vault_directory, note_id):
    """Refuse before any path join or git call (path traversal defense)."""
    if not NOTE_ID.match(note_id):
        raise PromotionRefused(f"{note_id!r} is not a valid note id")

    pending_path = layout.pending_root(vault_directory) / f"{note_id}.md"
    if not pending_path.is_file():
        raise PromotionRefused(f"no pending note {note_id}")

    knowledge_path = layout.knowledge_root(vault_directory) / f"{note_id}.md"
    if knowledge_path.exists():
        # D-09: promote never overwrites and never alias-merges. Porting the
        # old publisher's retitle/alias-merge path is deferred, loudly.
        raise PromotionRefused(
            f"knowledge/{note_id}.md already exists; promotion never overwrites (D-09)"
        )

    fields = parse_frontmatter(pending_path.read_text(encoding="utf-8"))
    reviewer = (fields.get("reviewer") or "").strip()
    rationale = (fields.get("rationale") or "").strip()
    decision = fields.get("decision")
    if not reviewer:
        raise PromotionRefused(f"{note_id}: missing reviewer")
    if not rationale:
        raise PromotionRefused(f"{note_id}: missing rationale")
    if decision != "approved":
        raise PromotionRefused(f"{note_id}: decision is {decision!r}, not 'approved'")

    return fields, pending_path, knowledge_path


def promote(vault_directory, note_id, remote=None, branch="main", index_path=None, _rebuild_index=True):
    """pending/<id>.md -> knowledge/<id>.md. Same id, stripped fields, audited commit.

    Ordered so the riskiest, most failure-prone step (rendering/writing the
    stripped note) happens BEFORE any git call, not between two of them: if
    `_reviewed_note()`/`write_atomic()` raises, neither `pending/` nor `git`
    has been touched yet, so the note is untouched and simply retried on the
    next `promote_all()` pass. If a git call fails after the file write, the
    `except` below rolls the write back so the same holds for that window
    too — this never leaves a note moved-but-uncommitted for
    `check_published()` to misdiagnose as an unaudited hand `git mv`.
    """
    vault_directory = Path(vault_directory)

    with layout.vault_lock(vault_directory):
        fields, pending_path, knowledge_path = _validate(vault_directory, note_id)

        relative_pending = f"{layout.PENDING_DIRNAME}/{note_id}.md"
        relative_knowledge = f"{layout.KNOWLEDGE_DIRNAME}/{note_id}.md"
        layout.knowledge_root(vault_directory).mkdir(parents=True, exist_ok=True)

        # review._reviewed_note() already implements the exact strip (F-3);
        # promotion reuses it rather than reimplementing frontmatter surgery.
        # Reads the still-in-`pending/` file — nothing has moved yet, so a
        # failure here (a malformed note, a disk error) leaves `pending/`
        # exactly as it was.
        published = _reviewed_note(pending_path)
        title = title_of(published) or note_id

        try:
            write_atomic(knowledge_path, published, 0o640)
            # `--` separates options from the path: an id that somehow
            # looked like a flag can never be interpreted as one.
            _git(vault_directory, "rm", "-q", "--", relative_pending)
            _git(vault_directory, "add", "--", relative_knowledge)

            rationale = fields.get("rationale", "")
            reviewer = fields.get("reviewer", "")
            decision = fields.get("decision", "")
            message = (
                f"Promote {note_id}: {title}\n\n"
                f"Reviewer: {reviewer}\n"
                f"Decision: {decision}\n"
                # One argv item to `git commit -m`, never a shell string: a
                # rationale holding a newline, a quote, `$(...)` or
                # `--force` can never become a second command or an option.
                f"Rationale: {rationale}\n"
            )
            _git(vault_directory, "commit", "-m", message)
        except Exception:
            # Roll back whatever landed on disk/in the index so the next
            # promote_all() pass sees `pending/<id>.md` untouched again,
            # not a half-promoted note stuck forever.
            _git(vault_directory, "reset", "--hard", "HEAD", check=False)
            knowledge_path.unlink(missing_ok=True)
            raise

        if _rebuild_index and index_path is not None:
            # D-07: promote is the only actor that can invalidate knowledge/,
            # so it is the only actor whose action needs to rebuild the
            # index; search-serve keeps zero write paths.
            build_index(vault_directory, index_path)

        if remote:
            _git(vault_directory, "push", remote, branch)

    return knowledge_path


def promote_all(vault_directory, index_path=None, **kwargs):
    """Timer entry point (D-04).

    Promotes every pending/*.md that passes validation and silently skips
    (never raises for) every one that doesn't — an unreviewed note sitting
    in pending/ between timer runs is the normal, expected steady state, not
    an error. One promotion failure (e.g. a git error on one note) must not
    abort the batch for the rest.

    Rebuilds the index once after the whole batch, not once per note: each
    `build_index()` call rescans every published note, so doing it inside
    the per-note loop is an O(batch size x knowledge/ size) full rescan for
    what should be one O(knowledge/ size) rescan per timer run.
    """
    pending = layout.pending_root(vault_directory)
    if not pending.is_dir():
        return []

    promoted = []
    for path in sorted(pending.glob("*.md")):
        try:
            promote(vault_directory, path.stem, index_path=None, _rebuild_index=False, **kwargs)
        except PromotionRefused:
            continue
        except (subprocess.CalledProcessError, OSError):
            continue
        else:
            promoted.append(path.stem)

    if promoted and index_path is not None:
        build_index(vault_directory, index_path)

    return promoted


def check_published(vault_directory):
    """Ids of published notes still carrying a `PENDING_FIELDS` key (D-06).

    Catches an unstripped hand `git mv`. It cannot catch a hand `mv` of a
    note that never had review fields to begin with — stated, not papered
    over.
    """
    offenders = []
    for path in layout.published_notes(vault_directory):
        fields = parse_frontmatter(path.read_text(encoding="utf-8"))
        if any(field in fields for field in PENDING_FIELDS):
            offenders.append(path.stem)
    return sorted(offenders)


def main():
    """The unattended entry point: `knowledge-vault-promote.timer` runs this.

    D-04: no per-id argument. The audited path is `promote_all()`; a bare
    hand `git mv` is the (unaudited) escape hatch for a single note.
    """
    try:
        vault_directory = os.environ["KNOWLEDGE_VAULT_DIR"]
    except KeyError as error:
        print(f"knowledge-vault promote: {error}", file=sys.stderr)
        return 1
    index_path = os.environ.get("KNOWLEDGE_VAULT_INDEX")
    remote = os.environ.get("KNOWLEDGE_VAULT_REMOTE")
    branch = os.environ.get("KNOWLEDGE_VAULT_BRANCH", "main")
    try:
        promoted = promote_all(vault_directory, remote=remote, branch=branch, index_path=index_path)
    except (layout.VaultLocked, OSError) as error:
        print(f"knowledge-vault promote: {error}", file=sys.stderr)
        return 1
    print(f"knowledge-vault promote: {len(promoted)} note(s) promoted")
    return 0


def check_main():
    try:
        offenders = check_published(os.environ["KNOWLEDGE_VAULT_DIR"])
    except (KeyError, OSError) as error:
        print(f"knowledge-vault promote-check: {error}", file=sys.stderr)
        return 1
    if not offenders:
        print("knowledge/ is clean: no published note carries review fields")
        return 0
    for note_id in offenders:
        print(f"{note_id}: still carries review fields (unstripped hand git mv?)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
