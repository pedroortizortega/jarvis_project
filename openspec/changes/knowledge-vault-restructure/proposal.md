# Proposal: knowledge-vault-restructure

## Intent

Today unapproved content never enters the vault's git history: notes cross four local JSON staging directories (`proposals/` → `pending/` → `decisions/` → `approved/`) before `publisher.py` writes the single canonical output into a flat `/opt/knowledge-vault/vault/`, and a separate `pending` branch of the bare repo exists only so a phone can review over SSH. The result is a pipeline nobody can explain in one sentence, one temporary stand-in (`approve_locally.py`) already documented as deletable, and no shared history between what was proposed and what was approved.

Target state: **one branch, two folders**. JARVIS may create/modify notes only in `pending/`. A human promotion moves a note to `knowledge/`. `knowledge-vault-search` indexes `knowledge/` only — never `pending/`, never any other folder added later.

## Scope

### In Scope

- Single-branch vault tree with exactly two consumed folders: `pending/` and `knowledge/`.
- Imperative agent boundary: `propose-note` SKILL.md states JARVIS writes only `pending/`, using the existing note template.
- Promotion path `pending/<id>.md` → `knowledge/<id>.md` preserving the note id (links must not break).
- Search/index scope restricted to `knowledge/` **by construction** (allowlist root, not a denylist).
- Retirement/reshaping of the staging directories and units the new model makes redundant.
- Migration of existing published notes into `knowledge/`.

### Out of Scope

- Changing note format (OKF frontmatter, `type` requirement, Zettelkasten id scheme).
- Replacing the search ranking, embeddings, or the memory-router adapter contract.
- Any real control-plane approval API (still absent; this change removes its stand-in, not builds it).
- Obsidian plugin/UI work.

## Capabilities

### New Capabilities

- `knowledge-vault-note-lifecycle`: single-branch `pending/` → `knowledge/` lifecycle, the agent write boundary, and the promotion act.

### Modified Capabilities

- `knowledge-vault-search-bridge`: index and search scope becomes `knowledge/` only; folders outside `knowledge/` are invisible by construction.

## Approach

The vault working tree becomes the canonical git repository with one branch. `pending/` and `knowledge/` are siblings inside it. Search reads a `knowledge/` root, not the vault root. Promotion is an explicit, audited command rather than an implicit timer stage.

**Verified code facts this must handle** (read this session, not assumed):

| Fact | File | Consequence |
|---|---|---|
| `_signature()`/`vault_revision()`/`build_index()` walk `vault.rglob("*.md")` — recursive | `retrieval.py:24,47,92` | Placing `pending/` under the vault root today would index pending notes and churn the revision on every keystroke. Scoping is mandatory, not cosmetic. |
| `search_vault()` guards on `vault.glob("*.md")` — flat | `search.py:39` | Returns zero hits once notes live in `knowledge/`. |
| `Publisher._target()` computes taken ids from flat `glob("*.md")` | `publisher.py:99` | Id-collision check must follow the notes into `knowledge/`. |
| `GitMirror._mirror_files()` is flat `glob("*.md")` | `mirror.py:65` | Mirror copies nothing after the move. |
| Review fields live in the note's own frontmatter, single block | `review.py:33-44` | The promotion act must strip `REVIEW_FIELDS` (`review.py:69`) or they leak into published notes. |

## Decisions (defaults — open for the question round)

| ID | Question | Proposed default | Rationale |
|---|---|---|---|
| D-01 | Does phone review survive? | **Retire `review-sync` and the separate `pending` branch.** The phone clones the one vault repo over the same `git-shell` SSH account and sees `pending/` directly. | The `pending` branch exists *only* because pending content was outside vault git. Collapsing to one branch removes its reason to exist. **Real fork — confirm before design.** |
| D-02 | Is rationale still recorded? | **Yes — do not silently drop it.** `pending/<id>.md` keeps `reviewer`/`decision`/`rationale` frontmatter; promotion refuses to run without them, strips them from the published note, and records them in the promote commit message. Git history becomes the audit trail. | "Just `git mv` it" would delete the recorded-rationale property. Removing an audit property must be an explicit decision, never a side effect. |
| D-03 | How does promotion happen mechanically? | **A `knowledge-vault-promote` command the human runs** (git mv + strip + commit + push). A bare hand `git mv` still works but is explicitly an unaudited escape hatch. | Keeps the act human-triggered (JARVIS never promotes) while keeping the audit trail machine-enforced. |
| D-04 | Does the "only one writer" invariant survive? | **Re-scoped, not deleted.** New invariant: JARVIS writes only `pending/`; only the promote actor writes `knowledge/`. Enforced twice as today — ownership/mode plus per-unit `ReadWritePaths=`/`InaccessiblePaths=`. | Two actors now legitimately write the tree; the guarantee that matters ("nothing reaches `knowledge/` without a human") is preserved, not weakened. |
| D-05 | Does `knowledge-vault-mirror` still exist? | **Collapses.** The vault tree is the git repo; the bare repo stays as its remote. One sync unit commits+pushes the vault tree; the scratch-worktree copy step is deleted. | No separate local staging left to mirror away from. |
| D-06 | What happens to `proposals/`/`decisions/`/`approved/` + `approve_locally.py`? | **Deleted**, per D-02/D-05. `propose` writes `pending/<id>.md` directly. | Their own docstrings call them temporary stand-ins. |

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/knowledge_vault/retrieval.py` | Modified | Index root becomes `knowledge/`; scoped walk replaces vault-root `rglob`. |
| `src/knowledge_vault/search.py`, `serve.py` | Modified | Empty-vault guard and search root follow `knowledge/`. |
| `src/knowledge_vault/publisher.py` | Modified/Removed | Rendering + id reuse move into promotion; approved-record path deleted. |
| `src/knowledge_vault/review.py` | Modified | Projection targets `pending/` in the vault tree; decision import becomes promotion. |
| `src/knowledge_vault/mirror.py`, `review_sync.py` | Modified/Removed | Per D-01/D-05. |
| `src/knowledge_vault/propose.py` | Modified | Writes `pending/` notes, not JSON spool. |
| `skills/propose-note/SKILL.md` | Modified | Imperative `pending/`-only rule; submission section rewritten. |
| `systemd/*.service`,`*.timer`, `scripts/install-host.sh` | Modified/Removed | Unit set shrinks; new path/ownership layout. |
| `tests/` (113 tests, 13 files) | Modified | Large rewrite follows the structural change. |
| `docs/services/knowledge-vault.md`, `specs/023_*.md` | Modified/New | Pipeline diagram and safety model rewritten. |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `pending/` notes become searchable/retrievable as if approved | Med | Allowlist a `knowledge/` root; add an explicit test asserting a note in `pending/` is never returned and never affects the index revision. |
| Existing note ids/links break during migration | Med | Migration preserves file names byte-for-byte; `git mv` only, never re-mint an id. |
| Audit trail silently lost with the staging JSON | Med | D-02 makes rationale mandatory and moves it into commit history; call it out in the spec, never as an implementation detail. |
| Phone review dropped without the user noticing | Med | D-01 is escalated as a blocking question, not a default applied quietly. |
| Human `git mv` bypasses validation (unstripped review fields published) | Med | Promotion validation also runs as a check over `knowledge/`; a note carrying `REVIEW_FIELDS` is reported, never silently published. |
| Change exceeds the 400-line review budget | High | Slice into chained PRs: (1) scoped search/index root, (2) lifecycle + promote, (3) unit/installer/migration, (4) docs+skill. |

## Rollback Plan

Per-slice `git revert` of the chained PRs, newest first. Runtime rollback: the previous host layout is a *separate* path tree (`/var/lib/knowledge-vault/*` + `/opt/knowledge-vault/vault`), so restoring means re-enabling the old timers and pointing `KNOWLEDGE_VAULT_DIR` back — the migration copies notes into `knowledge/` and never deletes the old flat vault until a follow-up cleanup, so no note is destroyed by a rollback.

## Dependencies

- Confirmed answers to D-01 and D-02 before `sdd-design` (D-01 changes the unit topology).
- `/srv/git/knowledge-vault.git` remains the SSH-reachable remote for whatever review surface D-01 selects.
- Existing bare-repo `main`/`pending` branch contents must be reconciled into the single branch during migration.

## Success Criteria

- [ ] The vault is one branch containing exactly `pending/` and `knowledge/`.
- [ ] `propose-note` SKILL.md imperatively forbids JARVIS from writing anywhere but `pending/`, and a test asserts the agent-facing write path can only target `pending/`.
- [ ] A test proves a note in `pending/` is never returned by `knowledge-vault-search` and never changes the index revision.
- [ ] A test proves an arbitrary third folder (e.g. `drafts/`) added to the vault is invisible to search without any code change.
- [ ] Promotion moves `pending/<id>.md` to `knowledge/<id>.md` preserving the id, stripping review fields, recording reviewer+rationale.
- [ ] No promotion path exists that JARVIS can trigger.
- [ ] Existing published notes migrate into `knowledge/` with unchanged file names and no broken intra-vault links.
- [ ] The full test suite passes; the docs pipeline diagram matches reality.

## Proposal question round — resolved

1. **(D-01, blocking) — RESOLVED: retire.** Confirmed by the user directly (not via a defaulted timeout): `review-sync` and the separate `pending` branch are retired. Phone-based offline review has no replacement mechanism in this change; if wanted later it is a separate, explicitly scoped follow-up, not something silently preserved here.
2. **(D-02) — RESOLVED: keep reviewer+rationale mandatory.** Confirmed by the user directly. Promotion refuses to run without both fields in the note's frontmatter; they are stripped before publish and recorded in the promotion commit message. Git history is the audit trail.
3. **(D-03)** Not separately asked — the recommended default (a `knowledge-vault-promote` command, hand `git mv` as an unaudited escape hatch) stands, consistent with D-02's requirement that promotion enforce the reviewer/rationale check somewhere.
4. **(D-04)** Not separately asked — the recommended default stands: only the promote actor writes `knowledge/`, JARVIS never writes there, enforced by ownership + systemd `ReadWritePaths=`/`InaccessiblePaths=`.
5. **(D-05)** Not separately asked — the recommended default stands: the bare repo at `/srv/git/knowledge-vault.git` remains the remote; `mirror.py`'s scratch-worktree copy step is deleted since the vault tree pushes to it directly.
6. **Scope check:** not separately asked — `sdd-tasks` decides the PR slicing per the review-budget guard; the whole restructure is in scope for this change (not split into a separate future change), only its *delivery* is sliced into chained PRs.
