import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_vault import layout
from knowledge_vault.note import parse_frontmatter
from knowledge_vault.promote import (
    NOTE_ID,
    PromotionRefused,
    PushFailed,
    check_published,
    promote,
    promote_all,
)
from knowledge_vault.review import PENDING_FIELDS

NOTE_ID_VALUE = "20260805090133"


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def pending_note(reviewer="pedro", decision="approved", rationale="ok", note_id=NOTE_ID_VALUE, extra=""):
    return (
        "---\n"
        "type: fact\n"
        f"id: {note_id}\n"
        "title: Por que SQLite en el control plane\n"
        f"reviewer: {reviewer}\n"
        f"decision: {decision}\n"
        f'rationale: "{rationale}"\n'
        f"idempotency_key: abc123\n"
        f"{extra}"
        "---\n"
        "# Por que SQLite en el control plane\n\nCuerpo verificado.\n"
    )


class VaultRepo:
    """A real git repo shaped like the vault tree, per test_mirror.py's
    precedent — the point is proving the actual subprocess argv is safe,
    not mocking it away."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        layout.knowledge_root(self.root).mkdir(parents=True, exist_ok=True)
        layout.pending_root(self.root).mkdir(parents=True, exist_ok=True)
        (self.root / "README.md").write_text("# Vault\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(
            self.root,
            "-c",
            "user.email=knowledge-vault@localhost",
            "-c",
            "user.name=knowledge-vault",
            "commit",
            "-q",
            "-m",
            "init",
        )

    def write_pending(self, text, note_id=NOTE_ID_VALUE):
        """Write and commit a pending note — by the time promote runs,
        knowledge-vault-sync has already committed it (Data Flow, design.md).
        `git mv` requires the source to be tracked."""
        path = layout.pending_root(self.root) / f"{note_id}.md"
        path.write_text(text, encoding="utf-8")
        git(self.root, "add", "-A")
        git(
            self.root,
            "-c",
            "user.email=knowledge-vault@localhost",
            "-c",
            "user.name=knowledge-vault",
            "commit",
            "-q",
            "-m",
            f"sync {note_id}",
        )
        return path


class PromoteRefusalTests(unittest.TestCase):
    def test_refuses_missing_reviewer(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note(reviewer=""))
            with self.assertRaises(PromotionRefused):
                promote(repo.root, NOTE_ID_VALUE)
            self.assertTrue((layout.pending_root(repo.root) / f"{NOTE_ID_VALUE}.md").exists())
            self.assertFalse((layout.knowledge_root(repo.root) / f"{NOTE_ID_VALUE}.md").exists())

    def test_refuses_missing_rationale(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note(rationale=""))
            with self.assertRaises(PromotionRefused):
                promote(repo.root, NOTE_ID_VALUE)

    def test_refuses_a_rejected_decision(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note(decision="rejected"))
            with self.assertRaises(PromotionRefused):
                promote(repo.root, NOTE_ID_VALUE)

    def test_refuses_when_knowledge_already_has_this_id(self):
        """D-09: never overwrites, never alias-merges."""
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note())
            (layout.knowledge_root(repo.root) / f"{NOTE_ID_VALUE}.md").write_text(
                "---\ntype: fact\n---\n# Already there\n", encoding="utf-8"
            )
            with self.assertRaises(PromotionRefused):
                promote(repo.root, NOTE_ID_VALUE)
            self.assertIn(
                "Already there",
                (layout.knowledge_root(repo.root) / f"{NOTE_ID_VALUE}.md").read_text(encoding="utf-8"),
            )

    def test_a_refusal_touches_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note(reviewer=""))
            before = git(repo.root, "rev-parse", "HEAD").strip()
            with self.assertRaises(PromotionRefused):
                promote(repo.root, NOTE_ID_VALUE)
            self.assertEqual(before, git(repo.root, "rev-parse", "HEAD").strip())


class PathTraversalTests(unittest.TestCase):
    """design.md Threat Matrix: 'Path traversal via argv' — the id must be
    rejected before it ever becomes part of a path or reaches git."""

    def _refused_before_any_git_call(self, note_id):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            with patch("knowledge_vault.layout.subprocess.run") as run:
                with self.assertRaises(PromotionRefused):
                    promote(repo.root, note_id)
                run.assert_not_called()

    def test_rejects_dot_dot_traversal(self):
        self._refused_before_any_git_call("../../etc/passwd")

    def test_rejects_an_absolute_path(self):
        self._refused_before_any_git_call("/abs/id")

    def test_rejects_an_id_with_a_space(self):
        self._refused_before_any_git_call("a b")

    def test_note_id_pattern_matches_new_note_id_format(self):
        """new_note_id() renders `%Y%m%d%H%M%S` — 14 digits, nothing else."""
        self.assertTrue(NOTE_ID.match("20260805090133"))
        self.assertFalse(NOTE_ID.match("2026080509013"))
        self.assertFalse(NOTE_ID.match("202608050901333"))
        self.assertFalse(NOTE_ID.match("2026080509013a"))


class SubprocessSafetyTests(unittest.TestCase):
    """design.md Threat Matrix: 'Shell / subprocess'."""

    def test_no_call_ever_uses_shell_true(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note())
            with patch("knowledge_vault.layout.subprocess.run", wraps=subprocess.run) as run:
                promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")
            self.assertTrue(run.call_args_list, "no git call was observed — patch target is dead")
            for call in run.call_args_list:
                self.assertNotIn("shell", call.kwargs)
                self.assertIsInstance(call.args[0], list)

    def test_git_rm_and_add_pass_the_id_after_a_separator(self):
        """promote() writes the stripped note to knowledge/ directly (via
        write_atomic, before any git call — see promote()'s docstring on
        crash-atomicity), then stages the move as `git rm pending/<id>.md`
        + `git add knowledge/<id>.md` rather than a single `git mv`. Both
        still need `--` so an id that looked like a flag is never
        interpreted as one."""
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note())
            with patch("knowledge_vault.layout.subprocess.run", wraps=subprocess.run) as run:
                promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")
            rm_calls = [call for call in run.call_args_list if "rm" in call.args[0]]
            add_calls = [call for call in run.call_args_list if "add" in call.args[0]]
            self.assertTrue(rm_calls, "git rm was never called")
            self.assertTrue(add_calls, "git add was never called")
            rm_argv, add_argv = rm_calls[0].args[0], add_calls[0].args[0]
            self.assertIn("--", rm_argv)
            self.assertIn("--", add_argv)
            self.assertGreater(
                rm_argv.index(f"{layout.PENDING_DIRNAME}/{NOTE_ID_VALUE}.md"), rm_argv.index("--")
            )
            self.assertGreater(
                add_argv.index(f"{layout.KNOWLEDGE_DIRNAME}/{NOTE_ID_VALUE}.md"), add_argv.index("--")
            )


class CommitMessageInjectionTests(unittest.TestCase):
    """design.md Threat Matrix: 'Commit-message injection' — the rationale is
    one argv item to `git commit -m`, never concatenated into a shell string.

    The note's own frontmatter is single-line (note.py has no multi-line YAML
    support), so a raw newline character cannot survive round-tripping
    through a pending note at all — that is an existing format limitation,
    not something this suite works around. The literal two-character
    sequence backslash-n is what a rationale can actually carry, and it is
    exactly the shape a naive `os.system(f"...{rationale}...")` or
    `shell=True` build would be tempted to reinterpret (e.g. via `echo -e`)
    into a real newline. Proving it stays two inert characters is the
    meaningful assertion here.
    """

    def test_a_backslash_n_sequence_in_the_rationale_is_recorded_literally(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note(rationale="linea uno\\nlinea dos"))
            promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")
            log = git(repo.root, "log", "-1", "--format=%B")
            self.assertIn("linea uno\\nlinea dos", log)

    def test_a_double_quote_in_the_rationale_is_recorded_literally(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note(rationale='dice "hola"'))
            promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")
            log = git(repo.root, "log", "-1", "--format=%B")
            self.assertIn('dice "hola"', log)

    def test_command_substitution_syntax_is_recorded_literally_never_executed(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note(rationale="$(touch /tmp/pwned-$$)"))
            promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")
            log = git(repo.root, "log", "-1", "--format=%B")
            self.assertIn("$(touch /tmp/pwned-$$)", log)
            # git log has exactly the promotion commit plus the two setup
            # commits (init, sync-of-pending) — never an injected extra one.
            self.assertEqual(3, len(git(repo.root, "log", "--format=%H").strip().splitlines()))

    def test_a_value_shaped_like_a_git_option_never_becomes_one(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note(rationale="--force"))
            promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")
            log = git(repo.root, "log", "-1", "--format=%B")
            self.assertIn("--force", log)
            # a real --force would not have changed commit safety here, but
            # the regression this guards is rationale reaching git as
            # anything other than the literal argument to -m.
            self.assertEqual(3, len(git(repo.root, "log", "--format=%H").strip().splitlines()))


class PromoteGreenPathTests(unittest.TestCase):
    def test_promotion_moves_the_note_preserving_the_id(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note())
            path = promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")
            self.assertEqual(f"{NOTE_ID_VALUE}.md", path.name)
            self.assertTrue(path.exists())
            self.assertFalse((layout.pending_root(repo.root) / f"{NOTE_ID_VALUE}.md").exists())

    def test_pending_fields_are_stripped_from_the_published_note(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note())
            path = promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")
            fields = parse_frontmatter(path.read_text(encoding="utf-8"))
            for field in PENDING_FIELDS:
                self.assertNotIn(field, fields)
            self.assertEqual("fact", fields["type"])

    def test_reviewer_and_rationale_are_recorded_in_the_commit_message(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note(reviewer="pedro", rationale="verificado en produccion"))
            promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")
            log = git(repo.root, "log", "-1", "--format=%B")
            self.assertIn("pedro", log)
            self.assertIn("verificado en produccion", log)

    def test_the_note_is_committed_to_the_repository(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note())
            promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")
            tracked = git(repo.root, "ls-tree", "--name-only", "-r", "HEAD")
            self.assertIn(f"{layout.KNOWLEDGE_DIRNAME}/{NOTE_ID_VALUE}.md", tracked)
            self.assertNotIn(f"{layout.PENDING_DIRNAME}/{NOTE_ID_VALUE}.md", tracked)

    def test_the_index_is_rebuilt_after_promotion(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note())
            index_path = Path(root) / "index.json"
            promote(repo.root, NOTE_ID_VALUE, index_path=index_path)
            self.assertTrue(index_path.exists())
            self.assertIn(NOTE_ID_VALUE, index_path.read_text(encoding="utf-8"))

    def test_promotion_pushes_to_the_configured_remote(self):
        with tempfile.TemporaryDirectory() as root:
            remote = Path(root) / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
            repo = VaultRepo(root + "/tree")
            git(repo.root, "remote", "add", "origin", str(remote))
            repo.write_pending(pending_note())
            promote(repo.root, NOTE_ID_VALUE, remote="origin", index_path=Path(root) / "index.json")
            names = git(remote, "ls-tree", "--name-only", "-r", "main")
            self.assertIn(f"{layout.KNOWLEDGE_DIRNAME}/{NOTE_ID_VALUE}.md", names)

    def test_promotion_is_wrapped_in_the_vault_lock(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note())
            with layout.vault_lock(repo.root):
                with self.assertRaises(layout.VaultLocked):
                    promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")
            self.assertTrue((layout.pending_root(repo.root) / f"{NOTE_ID_VALUE}.md").exists())


class CrashAtomicityTests(unittest.TestCase):
    """A failure mid-promotion must never leave a note moved-but-uncommitted
    — that state is indistinguishable from an unaudited hand `git mv` to
    `check_published()`, and `promote_all()` would never retry it since it
    only scans `pending/`."""

    def test_a_git_failure_after_the_write_rolls_back_and_stays_retryable(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note())
            knowledge_path = layout.knowledge_root(repo.root) / f"{NOTE_ID_VALUE}.md"
            real_git = layout.run_git

            def failing_commit(vault_directory, *args, **kwargs):
                if args and args[0] == "commit":
                    raise subprocess.CalledProcessError(1, ["git", *args])
                return real_git(vault_directory, *args, **kwargs)

            with patch("knowledge_vault.promote._git", side_effect=failing_commit):
                with self.assertRaises(subprocess.CalledProcessError):
                    promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")

            self.assertTrue(
                (layout.pending_root(repo.root) / f"{NOTE_ID_VALUE}.md").exists(),
                "pending note must survive a mid-promotion failure for the next retry",
            )
            self.assertFalse(
                knowledge_path.exists(), "a failed promotion must not leave an orphan knowledge/ file"
            )
            self.assertEqual([], check_published(repo.root), "no half-published note should exist")

    def test_a_write_atomic_failure_touches_neither_pending_nor_knowledge(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note())
            before = git(repo.root, "rev-parse", "HEAD").strip()
            with patch("knowledge_vault.promote.write_atomic", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")
            self.assertTrue((layout.pending_root(repo.root) / f"{NOTE_ID_VALUE}.md").exists())
            self.assertFalse((layout.knowledge_root(repo.root) / f"{NOTE_ID_VALUE}.md").exists())
            self.assertEqual(before, git(repo.root, "rev-parse", "HEAD").strip())


class UncommittedDecisionRaceTests(unittest.TestCase):
    """decide.py writes reviewer/decision/rationale straight to disk with no
    commit — only sync()'s own timer commits pending/. If promote reaches a
    note before that commit lands, `git rm` would fail and (pre-fix) the
    rollback discarded the reviewer's just-made edit."""

    def test_refuses_a_pending_note_with_uncommitted_local_changes(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            # Committed first with no decision — the state sync() leaves a
            # freshly-proposed note in.
            path = repo.write_pending(pending_note(reviewer="", decision="", rationale=""))
            # decide.py's write: straight to disk, no commit.
            path.write_text(pending_note(), encoding="utf-8")

            with self.assertRaises(PromotionRefused):
                promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")

            self.assertTrue(path.exists(), "the reviewer's uncommitted decision must survive")
            self.assertIn("decision: approved", path.read_text(encoding="utf-8"))
            self.assertFalse((layout.knowledge_root(repo.root) / f"{NOTE_ID_VALUE}.md").exists())

    def test_promotes_once_sync_has_committed_the_decision(self):
        """Same scenario, but sync() (simulated by a plain commit) has run
        first — promotion proceeds normally."""
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            path = repo.write_pending(pending_note(reviewer="", decision="", rationale=""))
            path.write_text(pending_note(), encoding="utf-8")
            git(repo.root, "add", "-A")
            git(
                repo.root, "-c", "user.email=x@x", "-c", "user.name=x", "commit", "-q", "-m", "sync"
            )
            promoted = promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")
            self.assertTrue(promoted.exists())


class PushFailureTests(unittest.TestCase):
    """A push failure must never be indistinguishable from a promotion
    failure — the commit already landed locally (D-05/D-09 satisfied)."""

    def test_a_failed_push_raises_push_failed_not_a_rollback(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note())
            real_git = layout.run_git

            def failing_push(vault_directory, *args, **kwargs):
                if args and args[0] == "push":
                    raise subprocess.CalledProcessError(1, ["git", *args])
                return real_git(vault_directory, *args, **kwargs)

            with patch("knowledge_vault.promote._git", side_effect=failing_push):
                with self.assertRaises(PushFailed):
                    promote(
                        repo.root,
                        NOTE_ID_VALUE,
                        remote="origin",
                        index_path=Path(root) / "index.json",
                    )
            # The commit is real and local, unlike CrashAtomicityTests above.
            self.assertTrue((layout.knowledge_root(repo.root) / f"{NOTE_ID_VALUE}.md").exists())
            self.assertFalse((layout.pending_root(repo.root) / f"{NOTE_ID_VALUE}.md").exists())

    def test_promote_all_still_counts_a_push_failed_note_and_rebuilds_the_index(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note())
            real_git = layout.run_git

            def failing_push(vault_directory, *args, **kwargs):
                if args and args[0] == "push":
                    raise subprocess.CalledProcessError(1, ["git", *args])
                return real_git(vault_directory, *args, **kwargs)

            index_path = Path(root) / "index.json"
            with patch("knowledge_vault.promote._git", side_effect=failing_push):
                promoted = promote_all(repo.root, remote="origin", index_path=index_path)
            self.assertEqual([NOTE_ID_VALUE], promoted)
            self.assertTrue(index_path.exists())


class ConcurrentWriterTests(unittest.TestCase):
    def test_a_contended_note_is_skipped_not_batch_aborting(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note(note_id="20260101000000"), note_id="20260101000000")
            repo.write_pending(pending_note(note_id="20260102000000"), note_id="20260102000000")
            with layout.vault_lock(repo.root):
                promoted = promote_all(repo.root, index_path=Path(root) / "index.json")
            self.assertEqual([], promoted, "every note contends on the same held lock")
            # Both notes are still there, untouched, ready for the next run.
            self.assertTrue((layout.pending_root(repo.root) / "20260101000000.md").exists())
            self.assertTrue((layout.pending_root(repo.root) / "20260102000000.md").exists())


class PromoteAllTests(unittest.TestCase):
    def test_promotes_every_eligible_note_and_skips_the_rest(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note(note_id="20260101000000"), note_id="20260101000000")
            repo.write_pending(
                pending_note(reviewer="", note_id="20260101000001"), note_id="20260101000001"
            )
            repo.write_pending(
                pending_note(decision="rejected", note_id="20260101000002"), note_id="20260101000002"
            )
            promoted = promote_all(repo.root, index_path=Path(root) / "index.json")
            self.assertEqual(["20260101000000"], promoted)
            self.assertTrue((layout.knowledge_root(repo.root) / "20260101000000.md").exists())
            self.assertTrue((layout.pending_root(repo.root) / "20260101000001.md").exists())
            self.assertTrue((layout.pending_root(repo.root) / "20260101000002.md").exists())

    def test_an_unreviewed_note_never_raises(self):
        """An unreviewed note sitting in pending/ is the normal steady state
        between timer runs, not an error."""
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note(reviewer="", decision="", rationale=""))
            promoted = promote_all(repo.root, index_path=Path(root) / "index.json")
            self.assertEqual([], promoted)

    def test_one_failure_does_not_abort_the_rest_of_the_batch(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note(note_id="20260101000000"), note_id="20260101000000")
            repo.write_pending(pending_note(note_id="20260101000003"), note_id="20260101000003")

            real_promote = promote

            def flaky(vault_directory, note_id, **kwargs):
                if note_id == "20260101000000":
                    raise subprocess.CalledProcessError(1, ["git", "mv"])
                return real_promote(vault_directory, note_id, **kwargs)

            with patch("knowledge_vault.promote.promote", side_effect=flaky):
                # promote_all must call the *module-level* promote so the patch
                # takes effect the same way an external caller would see it.
                from knowledge_vault import promote as promote_module

                promoted = promote_module.promote_all(repo.root, index_path=Path(root) / "index.json")
            self.assertEqual(["20260101000003"], promoted)


class CheckPublishedTests(unittest.TestCase):
    def test_flags_a_hand_moved_note_that_still_carries_review_fields(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            (layout.knowledge_root(repo.root) / f"{NOTE_ID_VALUE}.md").write_text(
                pending_note(), encoding="utf-8"
            )
            offenders = check_published(repo.root)
            self.assertEqual([NOTE_ID_VALUE], offenders)

    def test_a_clean_published_note_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            repo = VaultRepo(root)
            repo.write_pending(pending_note())
            promote(repo.root, NOTE_ID_VALUE, index_path=Path(root) / "index.json")
            self.assertEqual([], check_published(repo.root))


if __name__ == "__main__":
    unittest.main()
