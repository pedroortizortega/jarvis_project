"""Smoke checks for the knowledge vault's host-local behaviour.

Unit tests cover the contracts; this exercises the pieces as an operator meets
them: the publisher as a real process with its exit code, retrieval over a
vault large enough to measure, and a full review cycle.

    cd hermes-native/knowledge-vault
    PYTHONPATH=src python smoke/verify_vault.py

Every path below is a temporary directory. This never touches a real vault.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from knowledge_vault.models import Decision, Proposal
from knowledge_vault.retrieval import Retriever, build_index
from knowledge_vault.review import run_review

FAILURES = []


def check(label, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
    if not condition:
        FAILURES.append(label)


def publisher_reports_corrupt_approved_records():
    print("\nA corrupt approved record is reported, not swallowed")
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        spool, vault, state = root / "spool", root / "vault", root / "state"
        spool.mkdir()
        good = Proposal.create("---\ntype: fact\n---\n# Good\nPublished body", "key-good", {"agent": "hermes"})
        approval = Decision(good.id, 1, "reviewer", "approved", "ok")
        (spool / "good.json").write_text(
            json.dumps({"proposal": good.__dict__, "decision": approval.__dict__}), encoding="utf-8"
        )
        (spool / "corrupt.json").write_text("{not json", encoding="utf-8")

        run = subprocess.run(
            [sys.executable, "-c", "import sys; from knowledge_vault.publisher import main; sys.exit(main())"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": "src",
                "KNOWLEDGE_VAULT_DIR": str(vault),
                "KNOWLEDGE_VAULT_STATE_DIR": str(state),
                "KNOWLEDGE_VAULT_APPROVED_DIR": str(spool),
            },
        )
        check("exit code is non-zero so systemd marks the run failed", run.returncode == 1, f"exit={run.returncode}")
        check("stderr names the corrupt file", "corrupt.json" in run.stderr)
        check("the healthy note was still published", len(list(vault.glob("*.md"))) == 1)


def retrieval_stops_rehashing_an_unchanged_vault():
    print("\nRepeat queries stop re-reading an unchanged vault")
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        vault = root / "vault"
        vault.mkdir()
        for number in range(500):
            (vault / f"note-{number:04d}.md").write_text(
                "---\ntype: fact\n---\n"
                + f"# Note {number}\nContent about kubernetes and trantor number {number}.\n" * 20,
                encoding="utf-8",
            )
        index = root / "index.json"
        build_index(vault, index)
        retriever = Retriever(vault, index)

        start = time.perf_counter()
        first = retriever.search("kubernetes")
        cold = time.perf_counter() - start

        start = time.perf_counter()
        retriever.search("kubernetes")
        warm = time.perf_counter() - start

        check("the first query returns cited hits", first.available and bool(first.hits))
        check(
            "the second query is faster over 500 notes",
            warm < cold,
            f"cold={cold * 1000:.1f}ms warm={warm * 1000:.1f}ms ({cold / warm:.1f}x)",
        )
        (vault / "note-0007.md").write_text("# Note 7\nRewritten.\n", encoding="utf-8")
        stale = retriever.search("kubernetes")
        check("an edited note still invalidates the index", not stale.available, stale.reason)


def review_cycle_runs_unattended():
    print("\nThe review flow runs without a human driving each step")
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        spool, pending, decisions = root / "spool", root / "pending", root / "decisions"
        for directory in (spool, pending, decisions):
            directory.mkdir()
        proposal = Proposal.create("---\ntype: fact\n---\n# Draft\nNeeds review", "key-review", {"agent": "hermes"})
        (spool / "p.json").write_text(json.dumps({"proposal": proposal.__dict__}), encoding="utf-8")

        projected, _ = run_review(spool, pending, decisions)
        note = pending / f"{proposal.id}.md"
        check("the proposal is projected for Obsidian", projected == [note] and note.exists())

        note.write_text(note.read_text(encoding="utf-8") + "\nreviewer notes\n", encoding="utf-8")
        edited = note.read_text(encoding="utf-8")
        run_review(spool, pending, decisions)
        check("a second run leaves the review in progress alone", note.read_text(encoding="utf-8") == edited)

        note.write_text(
            f"---\nproposal_id: {proposal.id}\nversion: 1\nreviewer: reviewer\n"
            "decision: approved\nrationale: Checked\n---\n# Draft\nNeeds review\n",
            encoding="utf-8",
        )
        run_review(spool, pending, decisions)
        exported = decisions / f"{proposal.id}.json"
        check(
            "the decision is exported for the control plane",
            exported.exists() and json.loads(exported.read_text(encoding="utf-8"))["decision"] == "approved",
        )
        check("the decided file leaves the pending area", not note.exists())

        broken = pending / "broken.md"
        broken.write_text(
            "---\nproposal_id: p1\nversion: 1\nreviewer: reviewer\ndecision: maybe\nrationale: unsure\n---\n# Draft\n",
            encoding="utf-8",
        )
        problems = []
        run_review(spool, pending, decisions, on_failure=problems.append)
        check("a malformed decision is reported", len(problems) == 1, problems[0].reason if problems else "")
        check("a malformed decision stays for the reviewer to fix", broken.exists())


publisher_reports_corrupt_approved_records()
retrieval_stops_rehashing_an_unchanged_vault()
review_cycle_runs_unattended()

print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILED: ' + ', '.join(FAILURES)}")
sys.exit(1 if FAILURES else 0)
