"""Smoke checks for the knowledge vault's host-local behaviour.

Unit tests cover the contracts; this exercises the pieces as an operator
meets them: a full propose -> decide -> promote -> search cycle, run against
a real `git init` tree (the actual git plumbing promote/sync shell out to),
not a mock.

    cd hermes-native/knowledge-vault
    PYTHONPATH=src python smoke/verify_vault.py

Every path below is a temporary directory. This never touches a real vault.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from knowledge_vault.decide import decide
from knowledge_vault.promote import PromotionRefused, check_published, promote_all
from knowledge_vault.propose import propose
from knowledge_vault.retrieval import build_index
from knowledge_vault.search import search_vault

FAILURES = []


def check(label, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
    if not condition:
        FAILURES.append(label)


def _git_init(vault):
    """A real repo, not a mock — promote/sync both shell out to git."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(vault)], check=True)


def propose_decide_promote_search_cycle():
    print("\nA note moves pending/ -> knowledge/ only after a decision, and only")
    print("promotion makes it searchable")
    with tempfile.TemporaryDirectory() as root:
        vault = Path(root) / "tree"
        _git_init(vault)
        index = Path(root) / "index.json"

        pending_path = propose(
            "---\ntype: fact\n---\n# Trantor has no Longhorn\nOnly local-path exists.",
            {"agent": "smoke-test"},
            vault,
        )
        check("propose() writes only under pending/", pending_path.parent.name == "pending")
        note_id = pending_path.stem

        build_index(vault, index)
        hits_before = search_vault("Longhorn", vault, index)
        check(
            "a proposed-but-undecided note is never a search hit",
            not any(hit.note == f"{note_id}.md" for hit in hits_before),
        )

        promoted_before_decision = promote_all(vault, index_path=index)
        check(
            "promote_all() skips a note with no decision yet, without raising",
            promoted_before_decision == [],
        )
        check(
            "an undecided note is still in pending/, not knowledge/",
            pending_path.exists() and not (vault / "knowledge" / f"{note_id}.md").exists(),
        )

        decide(note_id, "approved", "Verified against the same source twice.", vault / "pending", reviewer="pedro")
        # sync.py normally commits pending/ before promote runs; promote
        # refuses an uncommitted pending edit (it would race a concurrent
        # sync). Reproduce that commit here without pulling sync.py in, to
        # keep this check scoped to propose/decide/promote/search.
        subprocess.run(["git", "-C", str(vault), "add", "pending"], check=True)
        subprocess.run(["git", "-C", str(vault), "commit", "-q", "-m", "Sync 1 pending note"], check=True)

        promoted = promote_all(vault, index_path=index)
        check("promote_all() promotes the now-decided note", promoted == [note_id])

        published_path = vault / "knowledge" / f"{note_id}.md"
        check("the note landed in knowledge/ with the same id", published_path.exists())
        check("the note left pending/", not pending_path.exists())

        published_text = published_path.read_text(encoding="utf-8")
        check(
            "review fields are stripped from the published note",
            "reviewer:" not in published_text and "rationale:" not in published_text,
        )

        log = subprocess.run(
            ["git", "-C", str(vault), "log", "-1", "--format=%B"], capture_output=True, text=True, check=True
        ).stdout
        check("reviewer and rationale are recorded in the promoting commit", "pedro" in log and "Verified" in log)

        hits_after = search_vault("Longhorn", vault, index)
        check(
            "the promoted note is now a search hit",
            any(hit.note == f"{note_id}.md" for hit in hits_after),
        )

        offenders = check_published(vault)
        check("promote --check reports nothing wrong with a clean knowledge/", offenders == [])


def promote_refuses_a_note_missing_review_fields():
    print("\npromote() refuses a note directly (not via promote_all's skip) when")
    print("reviewer/rationale/decision are missing")
    with tempfile.TemporaryDirectory() as root:
        vault = Path(root) / "tree"
        _git_init(vault)

        pending_path = propose("---\ntype: fact\n---\n# Draft\nNot reviewed yet.", {}, vault)
        note_id = pending_path.stem
        subprocess.run(["git", "-C", str(vault), "add", "pending"], check=True)
        subprocess.run(["git", "-C", str(vault), "commit", "-q", "-m", "Sync 1 pending note"], check=True)

        from knowledge_vault.promote import promote

        try:
            promote(vault, note_id)
            refused = False
        except PromotionRefused:
            refused = True
        check("promote() raises PromotionRefused for a note missing review fields", refused)
        check("nothing was moved by the refused promotion", pending_path.exists())


def a_hand_moved_note_with_leaked_fields_is_caught():
    print("\ncheck_published() catches an unaudited hand git-mv that leaked review fields")
    with tempfile.TemporaryDirectory() as root:
        vault = Path(root) / "tree"
        _git_init(vault)
        (vault / "knowledge").mkdir(parents=True)
        (vault / "knowledge" / "20260101000000.md").write_text(
            "---\ntype: fact\nreviewer: pedro\ndecision: approved\nrationale: ok\n---\n# Hand-moved\nBody.\n",
            encoding="utf-8",
        )
        offenders = check_published(vault)
        check("the hand-moved note is flagged", offenders == ["20260101000000"])


propose_decide_promote_search_cycle()
promote_refuses_a_note_missing_review_fields()
a_hand_moved_note_with_leaked_fields_is_caught()

print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILED: ' + ', '.join(FAILURES)}")
sys.exit(1 if FAILURES else 0)
