# Tasks: knowledge-vault-restructure

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1880 total (260/420/450/350/400 per PR) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 → PR 4 → PR 5 |
| Delivery strategy | ask-on-risk |
| Chain strategy | 5 chained PRs, each targeting the previous branch (as delivered: PR1 search scope, PR2 propose/decide lifecycle, PR3 promote/sync, PR4 units/installer/migration, PR5 docs/skill/spec sync) |

Decision needed before apply: No — resolved; all 5 PRs landed
Chained PRs recommended: Yes
Chain strategy: resolved (5 chained PRs, delivered)
400-line budget risk: High (materialized on PRs 2-3; absorbed via the chain, no further split needed)

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | Scope index/search to `knowledge/` | PR 1 | `python -m unittest tests.test_retrieval tests.test_search tests.test_serve tests.test_layout` | N/A — pure unit, no host deps | Revert `layout.py`+3 edits; old `rglob` behavior returns |
| 2 | `propose`/`decide` write into `pending/` | PR 2 | `python -m unittest tests.test_propose tests.test_decide tests.test_pending_list` | N/A — filesystem-only | Revert; `publisher.py` deletion is the only irreversible file loss, restorable via git |
| 3 | `promote`/`sync` lifecycle | PR 3 | `python -m unittest tests.test_promote tests.test_sync` | Real temp `git init` repo (per `test_mirror.py` precedent) | Revert; `mirror.py`→`sync.py` rename reversible |
| 4 | Units, installer, migration script | PR 4 | Unit-file assertions + `bash -n scripts/migrate-to-tree.sh` | Manual: `sudo` migration dry-run on staging host | Re-enable old timers, point `KNOWLEDGE_VAULT_DIR` back |
| 5 | Docs, skill, spec sync | PR 5 | `python -m unittest discover -s tests` (full suite) | `smoke/verify_vault.py` propose→decide→promote cycle | Docs-only; trivially revertible |

## Phase 1: Scoped search/index root (PR 1, load-bearing — must land first, alone)

- [x] 1.1 Create `layout.py`: `KNOWLEDGE_DIRNAME`/`PENDING_DIRNAME`, `knowledge_root()`, `pending_root()`, `published_notes()`, `vault_lock()`.
- [x] 1.2 RED `test_layout.py`: `quarantine-2026` fixture folder never appears in index/search (allowlist, behavioral).
- [x] 1.3 RED `test_layout.py`: `retrieval.py`/`search.py` source has no `"pending"` literal via `inspect.getsource` (allowlist, structural).
- [x] 1.4 RED `test_retrieval.py`: note in `pending/` never changes `vault_revision()`, never a hit (F-1).
- [x] 1.5 GREEN `retrieval.py`: `_signature()`/`vault_revision()`/`build_index()` use `published_notes(vault)`.
- [x] 1.6 GREEN `search.py`: empty-vault guard uses `published_notes()` (F-2).
- [x] 1.7 Add `IndexUnavailable`; `build_index()` failure raises it instead of leaking `OSError` (D-07).
- [x] 1.8 GREEN `serve.py`: map `IndexUnavailable` → `503 {"error":"index_unavailable"}`.
- [x] 1.9 RED `test_search.py`/`test_serve.py`: read-only index dir (`chmod 0500`) → `IndexUnavailable`/503 (F-4).
- [x] 1.10 Update `test_retrieval/search/serve.py` fixtures to add `knowledge/` root.
- [x] 1.11 Run Unit 1 focused command; confirm PR 1 green in isolation.

## Phase 2: Lifecycle — propose/decide into `pending/` (PR 2)

- [x] 2.1 RED `test_propose.py`: `propose()` writes only under `pending/`; no param retargets `knowledge/`.
- [x] 2.2 GREEN `propose.py`: render OKF, mint id vs `published_notes() ∪ pending/*.md`, write `pending/<id>.md` with empty review fields + `idempotency_key` (F-7/D-10).
- [x] 2.3 `decide.py`: derive path from `pending_root(KNOWLEDGE_VAULT_DIR)`; logic unchanged.
- [x] 2.4 `review.py`: keep only `PENDING_FIELDS`, `_reviewed_note()`, `_render()`; delete `PendingProjector`/`DecisionImporter`/`run_review`/`main`.
- [x] 2.5 Delete `publisher.py` (move `new_note_id()` to `note.py`), delete `outbox.py`.
- [x] 2.6 `models.py`: drop `Proposal`/`Decision`/`ApprovedRecord`; keep `RetrievalHit`/`RetrievalResult`/`PublicationFailure`.
- [x] 2.7 RED `test_propose.py`: id collision checked against `knowledge/** ∪ pending/*`.
- [x] 2.8 Delete `test_publisher.py`/`test_review_run.py`/`test_outbox.py`; update `test_decide/pending_list/note.py` fixtures.
- [x] 2.9 Run Unit 2 focused command.

## Phase 3: Promote + sync (PR 3)

- [x] 3.1 RED `test_promote.py`: refuses missing `reviewer`/`rationale`, `decision != approved`, existing `knowledge/<id>.md` (D-09); rejects `../`, absolute ids, no git call.
- [x] 3.2 RED `test_promote.py`: no `shell=True`; id passed after `--`.
- [x] 3.3 RED `test_promote.py`: rationale with `\n`, `"`, `$(...)`, `--force` never escapes argv.
- [x] 3.4 GREEN `promote.py`: `promote()` — validate, `git mv --`, strip via `review._reviewed_note()`, commit with reviewer+rationale, rebuild index, push, under `vault_lock()`.
- [x] 3.5 RED `test_promote.py`: `promote_all()` promotes eligible notes, skips ineligible without raising, one failure doesn't abort the batch.
- [x] 3.6 GREEN `promote.py`: `promote_all(vault)` scanning `pending/*.md`.
- [x] 3.7 RED `test_promote.py`: `check_published()` flags hand-`git mv`'d note still carrying `REVIEW_FIELDS`.
- [x] 3.8 GREEN `promote.py`: `check_published()`, `main()`, `check_main()`.
- [x] 3.9 Create `sync.py` from `mirror.py`: drop `_mirror_files()`; keep `IDENTITY`/`_git`/`_adopt_remote`/`_pending`/push; stage `pending/` only.
- [x] 3.10 RED `test_sync.py`: dirty file under `knowledge/` never committed; failing push doesn't lose the commit.
- [x] 3.11 Delete `mirror.py`, `review_sync.py`, `test_mirror.py`, `test_review_sync.py`.
- [x] 3.12 `pyproject.toml`: add `-promote`/`-promote-check`/`-sync`; remove `-publisher`/`-review`/`-review-sync`/`-mirror`.
- [x] 3.13 Add `vault_lock()` (`fcntl.flock(LOCK_EX)`, D-08) to `layout.py`; reuse in `promote.py`/`sync.py`. (already present from PR1; reused, not reimplemented)
- [x] 3.14 Run Unit 3 focused command against real temp git repo.

## Phase 4: Units, installer, migration (PR 4)

- [x] 4.1 Delete `systemd/knowledge-vault-{publisher,review,review-sync,approve,mirror}.{service,timer}`.
- [x] 4.2 Create `knowledge-vault-promote.{service,timer}`: `User=knowledge-vault-promote`, `ReadWritePaths=` tree `.git`/`knowledge`/`pending` + state/index; no `RestrictSUIDSGID`; interval from `KNOWLEDGE_VAULT_PROMOTE_INTERVAL` (default `5min`).
- [x] 4.3 Create `knowledge-vault-sync.{service,timer}`: `User=knowledge-vault-sync`, RW `.git`+`pending`, `ReadOnlyPaths=<tree>/knowledge`.
- [x] 4.4 Modify `knowledge-vault-search.service`: `KNOWLEDGE_VAULT_DIR` → `/opt/knowledge-vault/tree`; still no RW paths.
- [x] 4.5 `install-host.sh`: add `-promote`/`-sync`/`-search` users (F-5); set `knowledge/` `0750`, `pending/` `2770`, `state/` `2770`; install new units; walkthrough → propose→decide→promote.
- [x] 4.6 Delete `scripts/approve_locally.py`.
- [x] 4.7 Create `scripts/migrate-to-tree.sh`: idempotent clone, `mkdir knowledge pending`, `git mv *.md knowledge/`, drift-check gate, reconcile frozen `pending` branch, push; never deletes old vault.
- [ ] 4.8 Manual E2E: run migration on staging host, full propose→decide cycle, wait one timer interval, confirm promotion with no manual trigger. (Out of scope for this PR — requires a real host; not automatable in this environment.)

## Phase 5: Docs, skill, spec sync (PR 5)

- [x] 5.1 `skills/propose-note/SKILL.md`: imperative `pending/`-only rule; submission block uses `KNOWLEDGE_VAULT_DIR`.
- [x] 5.2 `docs/services/knowledge-vault.md`: rewrite pipeline diagram, unit table (6→3), safety model, env table.
- [x] 5.3 `smoke/verify_vault.py`: replace publisher/review-cycle checks with propose→decide→promote.
- [x] 5.4 Sync `specs/023_knowledge_vault_restructure.md` §2 D-03/D-04 wording to match design.md's amended unattended-timer promotion (currently still says human-triggered per-id).
- [x] 5.5 Run full suite (`python -m unittest discover -s tests`); confirm green end-to-end.
- [x] 5.6 Sync `openspec/changes/knowledge-vault-restructure/specs/knowledge-vault-note-lifecycle/spec.md`'s promotion-trigger requirement to the unattended-timer model and add the D-13 self-approval-risk requirement (search-bridge delta spec did not describe promotion mechanics; left as-is).
- [x] 5.7 Fix stale `publisher.py` reference in `serve.py`'s module docstring found during the final dangling-reference grep.
