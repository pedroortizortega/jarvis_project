# Design: knowledge-vault-restructure

## Technical Approach

One git working tree at `/opt/knowledge-vault/tree` with exactly two consumed folders, `pending/` and `knowledge/`, pushed directly to the existing bare repo at `/srv/git/knowledge-vault.git` on branch `main`. Every module that enumerates notes stops touching the vault root and goes through a single new choke point, `layout.py`, whose only enumeration primitive is `published_notes()` = `knowledge/**/*.md`. The scope is therefore an **allowlist by construction**: no module ever names `pending/` — or any other folder — to exclude it, so a folder added later is invisible without a code change, and that is asserted by a fixture folder whose name appears nowhere in the source.

The JSON staging pipeline (`proposals/` → `pending/` → `decisions/` → `approved/`) collapses: `propose` renders the OKF note and writes `pending/<id>.md` directly (minting the Zettelkasten id it used to defer to the publisher), `decide` fills `reviewer`/`decision`/`rationale` in place as it already does, and a new `knowledge-vault-promote` performs the audited `git mv` into `knowledge/`. `publisher.py`, `review_sync.py`, `review.py`'s projector/importer, `outbox.py`, `approve_locally.py` and `mirror.py`'s scratch-worktree copy are deleted.

## Verified Findings (read from this repo this session, not assumed)

- **F-1 — the index walk is recursive and unscoped.** `_signature()` (`retrieval.py:29`), `vault_revision()` (`:50`) and `build_index()` (`:92`) all use `vault.rglob("*.md")`. Putting `pending/` under the vault root without scoping would index unapproved notes *and* churn the revision on every reviewer keystroke, invalidating the index on every search. This is the single load-bearing change.
- **F-2 — three other enumerations are flat `glob("*.md")`**, so they break the moment notes move one level down: `search.py:39` (empty-vault guard), `publisher.py:99` (id-collision `taken` set), `mirror.py:65` (`_mirror_files`). `decide.py:35` and `review.py:140` glob `pending/` flatly, which stays correct.
- **F-3 — review fields are merged into the note's own single frontmatter block** (`review.py:33-44`), and `_reviewed_note()` (`:72-83`) already implements the exact strip promotion needs, keyed on `REVIEW_FIELDS = ("proposal_id","version","reviewer","decision","rationale")` (`:69`). Promotion reuses this function; it is not rewritten.
- **F-4 — `search-serve` can never rebuild the index today.** `knowledge-vault-search.service` sets `ProtectSystem=strict`, lists the index under `ReadOnlyPaths=` and declares **no `ReadWritePaths=` at all** (`:27`), while `search_vault()` calls `build_index()` on a stale index (`search.py:44`) and the resulting `OSError` is uncaught in both `SingleFlightSearcher` and `do_POST`. Migration guarantees a stale index on the first request, so this latent bug becomes certain. See D-07.
- **F-5 — `install-host.sh` never creates the `knowledge-vault-search` user** the search unit runs as (`install-host.sh:32-44` creates only publisher/review/mirror). The new users (`-promote`, `-sync`) plus `-search` must all be added, or the units fail to start.
- **F-6 — the bare repo is already group-shared and both writer units deliberately omit `RestrictSUIDSGID`** (`install-host.sh:87-91`, `mirror.service:33-35`), because `core.sharedRepository=group` makes git set setgid on directories. Two accounts writing one worktree's `.git` is an existing, working pattern here, not a new risk shape.
- **F-7 — `propose.py` dedupes on a `sha256` of the note text scanned across the spool** (`:45-52`). With the spool gone the key needs a new home; the pending note's own frontmatter is the only one left.
- **F-8 — the vault is not a git repo today.** `/opt/knowledge-vault/vault` is a plain directory; git lives only in `mirror/repo` and `review/repo` scratch worktrees. So "the vault tree becomes the repo" is a new path, not an in-place conversion — which is what makes the proposal's rollback promise (old flat vault survives untouched) mechanically true.

## Architecture Decisions

| # | Decision | Options / tradeoff | Choice and rationale |
|---|---|---|---|
| D-01 | Where the `knowledge/` scope lives | Point `KNOWLEDGE_VAULT_DIR` at `<tree>/knowledge` in the units (config-level) vs derive the scope in code from the vault root | **In code.** New `layout.py` exports `KNOWLEDGE_DIRNAME`/`PENDING_DIRNAME`, `knowledge_root(vault)`, `pending_root(vault)` and `published_notes(vault)`; `KNOWLEDGE_VAULT_DIR` keeps meaning "the vault tree". Config-level scoping puts the whole safety property one typo away from re-indexing `pending/`, and makes the invariant untestable in the suite. In code it is one choke point with one test. |
| D-02 | Allowlist vs denylist | `rglob` from the root minus known folders vs enumerate only `knowledge/` | **Allowlist, enforced structurally.** `published_notes()` is the only enumeration; the strings `pending`, `drafts`, etc. never appear in `retrieval.py`/`search.py`. Proven twice: a behavioural test using a fixture folder named `quarantine-2026` (never `pending`), and a source guard asserting the retrieval/search modules reference `KNOWLEDGE_DIRNAME` and never `PENDING_DIRNAME`. |
| D-03 | Recursion inside `knowledge/` | Flat `glob` vs keep `rglob` under the scoped root | **Keep `rglob`, rooted at `knowledge/`.** Obsidian users foldering published notes is legitimate; `path.relative_to(vault)` keeps producing unique keys. The bug was never recursion, it was the root. |
| D-04 | Promotion mechanism | Human-triggered `systemctl start knowledge-vault-promote@<id>` per note vs an unattended `knowledge-vault-promote.timer` scanning `pending/` for any note already carrying `reviewer`+`decision: approved`+`rationale` | **Timer-driven, per explicit user request.** `knowledge-vault-promote.service`/`.timer` (`User=knowledge-vault-promote`, dedicated system account — same actor as originally designed, just no longer manually triggered), interval `KNOWLEDGE_VAULT_PROMOTE_INTERVAL` (default `5min`, configurable, matching the old `publisher`/`approve` timer pattern). Each run calls `promote_all(vault)`: enumerates `pending/*.md`, promotes every note that already passes D-05's refusal contract, skips (does not error on) every note that doesn't. This drops the earlier per-id manual trigger, which was the human's last explicit checkpoint — **see the new security note below, flagged not silently accepted.** |
| D-05 | Promotion refusal contract (D-02 of the proposal) | Warn and strip vs refuse | **Refuse, non-zero, no write.** Missing/empty `reviewer` or `rationale`, or `decision != "approved"`, aborts before any git call. Fields are stripped with the existing `_reviewed_note()` and re-recorded in the commit message. Git history becomes the audit trail exactly because the fields leave the file. |
| D-06 | Guarding the escape hatch | Trust the CLI vs audit `knowledge/` | **Audit.** `promote --check` (also run at the start of every promote) reports any note under `knowledge/` still carrying a `REVIEW_FIELDS` key and exits non-zero. It catches an unstripped hand-`mv`; it cannot catch a hand-`mv` of a note that never had review fields — stated, not papered over. |
| D-07 | Who writes the index (F-4) | Give `search-serve` `ReadWritePaths=` on the index vs let the promote actor rebuild | **Promote rebuilds; the search bridge keeps zero write paths.** Promote is the only actor that can change `knowledge/`, so it is the only actor whose action can invalidate the index — rebuilding there is both correct and free. The docs' strongest safety claim ("no `ReadWritePaths=` at all") survives. `search_vault()` gains a typed `IndexUnavailable` instead of leaking `OSError`: the CLI (running as a group member with `2770` on the index dir) still rebuilds inline; the server answers `503 {"error":"index_unavailable"}`. |
| D-08 | Concurrency between promote and sync | Trust git's `index.lock` vs an explicit fence | **Explicit fence.** Both units take `fcntl.flock(LOCK_EX)` on `/var/lib/knowledge-vault/state/vault.lock` (mode `0660`, group-owned), reusing `Publisher._fence()`'s pattern verbatim as `layout.vault_lock()`. Git's own lock surfaces as an opaque `CalledProcessError` a timer would retry blindly; an flock makes the contention explicit and ordered. |
| D-09 | Revisions / `predecessor_id` | Port `Publisher._write()`'s alias-merge into promote vs defer | **Defer, loudly.** Promote **refuses** when `knowledge/<id>.md` already exists; it never overwrites. Deleting `publisher.py` drops the alias-preserving retitle path, which is a real capability regression and is recorded as such (Open Questions), not silently lost. Porting it would roughly double the promote surface for a path the proposal does not require. |
| D-10 | Dedupe after the spool (F-7) | Digest-scan both folders vs keep the key in pending frontmatter | **`idempotency_key` in the pending note's frontmatter**, added to the stripped `REVIEW_FIELDS` set (renamed `PENDING_FIELDS`). Dedupe therefore covers `pending/` only: re-proposing text already promoted creates a new pending note a human rejects in one word. Cheap, honest, and the failure mode is a human saying no — not a silent duplicate in `knowledge/`. |
| D-11 | Bare-repo `pending` branch | Delete during migration vs leave frozen | **Leave frozen.** Nothing writes it after `review-sync` retires. Deleting it is a separate, explicit cleanup follow-up, consistent with the proposal's "never delete the old vault until a follow-up". |
| D-12 | New tree path | Convert `/opt/knowledge-vault/vault` in place vs a new path | **New path `/opt/knowledge-vault/tree`.** In-place conversion destroys the rollback promise (F-8): the old flat vault must remain a byte-identical, untouched fallback. Cost: the human's Obsidian vault path changes once, at migration. |
| D-13 | Self-approval risk introduced by D-04's timer (flagged, confirmed by user) | Accept as-is (JARVIS is a single trusted operator's own agent) vs add a technical gate beyond the field check | **Accepted, confirmed directly by the user.** Today's manual per-id trigger meant a human explicitly acted before every promotion, even if `reviewer`/`decision`/`rationale` were somehow already filled. A scanning timer removes that checkpoint: `pending/` is group-writable `2770` by JARVIS's own system user, and nothing at the filesystem layer stops that same process from writing `reviewer: pedro` / `decision: approved` / `rationale: ...` into a note it creates itself — the `propose-note` SKILL.md's imperative instruction not to do this is a behavioral constraint on the agent, not a kernel-enforced one, unlike the `pending/`→`knowledge/` boundary (D-04/ownership, still kernel-enforced). Accepted because this repo already trusts JARVIS not to violate skill instructions elsewhere (e.g. never claiming a note is saved before human approval), and because the alternative — splitting review-verdict fields out of the note's own frontmatter into a file JARVIS cannot write — was already rejected once (F-3: two frontmatter blocks confused Obsidian) and reintroducing that split just to gate self-approval is a disproportionate response to a single-operator threat model. **Recorded so it is a real decision, not an oversight** — revisit if this ever runs multi-tenant or with an agent whose trust level is lower than "the operator's own". |

## Data Flow

    JARVIS ── knowledge-vault-propose ──► <tree>/pending/<id>.md      (renders OKF + empty
      │        (group-writable 2770)        reviewer/decision/rationale + idempotency_key)
      │
      │      human ── knowledge-vault-decide (or an editor / Obsidian) ──► same file, fields filled
      │
      │      knowledge-vault-sync.timer ──► git add pending/ ; commit ; push origin main
      │        (RW: .git + pending/ only — knowledge/ is ReadOnlyPaths)
      ▼
    knowledge-vault-promote.timer (default 5min, KNOWLEDGE_VAULT_PROMOTE_INTERVAL) ──┐
                                                                                     ▼
      for each pending/*.md: validate(reviewer, rationale, decision==approved)  ──refuse──► skip, next timer run
      (D-13: nothing technical stops JARVIS from pre-filling these fields itself —
       flagged, accepted risk, not mitigated)
                       │
                       ├─► git mv -- pending/<id>.md knowledge/<id>.md      (id byte-for-byte)
                       ├─► strip PENDING_FIELDS via review._reviewed_note()  (write_atomic 0640)
                       ├─► git commit -m "Promote <id>: <title>\n\nReviewer/Decision/Rationale"
                       ├─► build_index(<tree>, /var/lib/knowledge-vault/index/index.json)   (D-07)
                       └─► git push origin main ─────────────► /srv/git/knowledge-vault.git
                                                                 (one branch: main)
    knowledge-vault-search(-serve) ── published_notes(<tree>) = knowledge/**/*.md ──► hits
      <tree>/pending/, <tree>/README.md, <tree>/anything-else/  ── never enumerated, never named

## File Changes

| File | Action | Description |
|---|---|---|
| `src/knowledge_vault/layout.py` | Create | The choke point: `KNOWLEDGE_DIRNAME`/`PENDING_DIRNAME`, `knowledge_root()`, `pending_root()`, `published_notes()`, `vault_lock()`. |
| `src/knowledge_vault/retrieval.py` | Modify | `_signature()`, `vault_revision()`, `build_index()` iterate `published_notes(vault)` instead of `vault.rglob` (F-1). No other change. |
| `src/knowledge_vault/search.py` | Modify | Empty-vault guard uses `published_notes()` (F-2); `build_index()` failure raises `IndexUnavailable` (D-07). |
| `src/knowledge_vault/serve.py` | Modify | Map `IndexUnavailable` → `503 {"error": "index_unavailable"}`. |
| `src/knowledge_vault/promote.py` | Create | `promote(vault, note_id, ...)`, `promote_all(vault)` (scans `pending/*.md`, promotes each note that passes validation, skips the rest without erroring — the timer entry point), `check_published(vault)`, `main()`/`check_main()`. Validation, `git mv`, strip, commit, index rebuild, push, under `vault_lock()`. |
| `src/knowledge_vault/propose.py` | Modify | Writes `pending/<id>.md` (rendered OKF + empty review fields + `idempotency_key`), mints the id against `published_notes() ∪ pending/*.md`. Loses the spool and `Proposal`. |
| `src/knowledge_vault/decide.py` | Modify | `KNOWLEDGE_VAULT_PENDING_DIR` → derived `pending_root(KNOWLEDGE_VAULT_DIR)`. Logic unchanged. |
| `src/knowledge_vault/review.py` | Modify | Keeps only `PENDING_FIELDS` (was `REVIEW_FIELDS`), `_reviewed_note()`, `_render()`. `PendingProjector`, `DecisionImporter`, `run_review`, `main` deleted. |
| `src/knowledge_vault/publisher.py` | Delete | `new_note_id()` moves to `note.py`; the approved-record path, manifest, and alias merge go (D-09). |
| `src/knowledge_vault/mirror.py` → `sync.py` | Modify | `_mirror_files()` (the scratch-worktree copy, F-2) deleted; `IDENTITY`, `_git`, `_adopt_remote`, `_pending`, push survive, operating on the vault tree and staging `pending/` only. |
| `src/knowledge_vault/review_sync.py` | Delete | D-01 of the proposal: phone review over the `pending` branch is retired. |
| `src/knowledge_vault/outbox.py` | Delete | Its only sender was the JSON spool; never wired into the CLI. |
| `src/knowledge_vault/models.py` | Modify | Drop `Proposal`, `Decision`, `ApprovedRecord`. Keep `RetrievalHit`, `RetrievalResult`, `PublicationFailure`. |
| `scripts/approve_locally.py` | Delete | Its own docstring calls it a stand-in. |
| `scripts/migrate-to-tree.sh` | Create | The Migration section below, idempotent, never deleting the old vault. |
| `pyproject.toml` | Modify | `-promote`/`-promote-check`/`-sync` in, `-publisher`/`-review`/`-review-sync`/`-mirror` out. |
| `systemd/knowledge-vault-{publisher,review,review-sync,approve,mirror}.{service,timer}` | Delete | 5 units + their timers. |
| `systemd/knowledge-vault-promote.{service,timer}` | Create | Oneshot + timer (per explicit user request — unattended, not per-id triggered), `User=knowledge-vault-promote`, `ReadWritePaths=<tree>/.git <tree>/knowledge <tree>/pending /var/lib/knowledge-vault/{index,state}`, `RestrictSUIDSGID` absent (F-6). Timer interval `OnUnitActiveSec=%i` sourced from `KNOWLEDGE_VAULT_PROMOTE_INTERVAL` (default `5min`) via a drop-in or `EnvironmentFile=`, so it's configurable without editing the shipped unit. Calls `promote_all()` (D-04). |
| `systemd/knowledge-vault-sync.{service,timer}` | Create | `User=knowledge-vault-sync`, `ReadWritePaths=<tree>/.git <tree>/pending /srv/git/knowledge-vault.git`, **`ReadOnlyPaths=<tree>/knowledge`**. |
| `systemd/knowledge-vault-search.service` | Modify | `KNOWLEDGE_VAULT_DIR` → `/opt/knowledge-vault/tree`; still no `ReadWritePaths=` (D-07). |
| `scripts/install-host.sh` | Modify | Creates `-promote`, `-sync`, `-search` users (F-5); `<tree>/knowledge` `0750 promote:group`, `<tree>/pending` `2770 promote:group`, `/var/lib/knowledge-vault/state` `2770`; installs the new unit set; the printed walkthrough becomes propose → decide → promote. |
| `skills/propose-note/SKILL.md` | Modify | Imperative: JARVIS writes `pending/` only, never `knowledge/`, never promotes. Submission block updated to `KNOWLEDGE_VAULT_DIR`. |
| `smoke/verify_vault.py` | Modify | Publisher/review-cycle checks replaced by a propose → decide → promote cycle. |
| `tests/test_{publisher,review,review_run,review_sync,outbox,mirror}.py` | Delete | Their subjects are gone. |
| `tests/test_{retrieval,search,serve,propose,decide,pending_list,note}.py` | Modify | Vault fixtures gain the `knowledge/` root. |
| `tests/test_layout.py`, `tests/test_promote.py`, `tests/test_sync.py` | Create | See Testing Strategy. |
| `docs/services/knowledge-vault.md` | Modify | Pipeline diagram, unit table (6 → 3), safety model, env table rewritten. |
| `specs/023_knowledge_vault_restructure.md` | Create | Owned by `sdd-spec`, running in parallel. |

## Interfaces / Contracts

```python
# layout.py — the only place a folder name is ever spelled.
KNOWLEDGE_DIRNAME = "knowledge"
PENDING_DIRNAME = "pending"

def knowledge_root(vault_directory):   return Path(vault_directory) / KNOWLEDGE_DIRNAME
def pending_root(vault_directory):     return Path(vault_directory) / PENDING_DIRNAME

def published_notes(vault_directory):
    """Every published note, and nothing else that happens to live in the tree.

    An allowlist: this never enumerates the vault root, so a folder added
    later is invisible without anyone remembering to exclude it.
    """
    return knowledge_root(vault_directory).rglob("*.md")
```

```python
# promote.py — the whole contract
class PromotionRefused(RuntimeError): ...

NOTE_ID = re.compile(r"\A[0-9]{14}\Z")   # argv reaches a path join; ../ never does

def promote(vault_directory, note_id, remote=None, branch="main", index_path=None):
    """pending/<id>.md -> knowledge/<id>.md. Same id, stripped fields, audited commit."""

def promote_all(vault_directory, **kwargs):
    """Timer entry point (D-04): promote() every pending/*.md that passes
    validation, silently skip (never raise) every one that doesn't — an
    unreviewed note sitting in pending/ is the normal, expected state
    between timer runs, not an error."""
```

```
Promote 20260805090133: Por que SQLite en el control plane

Reviewer: pedro
Decision: approved
Rationale: <verbatim frontmatter value, one argv item, never a shell string>
```

## Testing Strategy

| Layer | What to test | Approach |
|---|---|---|
| Unit — scope (RED first, the load-bearing one) | A note in `pending/` is never a hit and never changes `vault_revision()` | `test_retrieval.py`: build index, write `pending/x.md`, assert revision identical and no hit — fails today against `rglob` |
| Unit — allowlist, behavioural | **A folder the code never names is invisible.** Fixture `<tree>/quarantine-2026/note.md` + `<tree>/README.md`: absent from `build_index()` fragments, `vault_revision()` unchanged after writing and after rewriting them, no hit from `search_vault()` | `test_layout.py` — the name `quarantine-2026` is arbitrary and appears in no source file, so passing cannot mean "we excluded it" |
| Unit — allowlist, structural | `retrieval.py` and `search.py` source contains no `"pending"` literal and no `PENDING_DIRNAME` reference; `published_notes` is the only enumerator | `test_layout.py` via `inspect.getsource` — turns "allowlist not denylist" from prose into an assertion |
| Unit — agent boundary | `propose()` given the vault root writes under `pending/` and leaves `knowledge/` empty; it exposes no parameter that can target another folder | `test_propose.py` |
| Unit — promotion | Refuses missing `reviewer`; refuses missing `rationale`; refuses `decision: rejected`; refuses an existing `knowledge/<id>.md` (D-09); on success the file name is byte-identical, `PENDING_FIELDS` are absent from the published note, present in the commit message, and `pending/<id>.md` is gone; `../` and absolute ids rejected before any git call | `test_promote.py` against a real `git init` temp repo (the suite already shells out to git in `test_mirror.py`) |
| Unit — promote_all (D-04) | Given a mix of eligible and ineligible pending notes, `promote_all()` promotes every eligible one and raises nothing for the ineligible ones — a `pending/` note with empty `reviewer` is expected steady state, not an error; one promotion failure (e.g. a git error) does not abort the rest of the batch | `test_promote.py` |
| Unit — audit | `check_published()` flags a note hand-moved into `knowledge/` with `reviewer:` still set, exit non-zero | `test_promote.py` |
| Unit — id collision | A new id is minted against `knowledge/**` ∪ `pending/*` (F-2 follow-through) | `test_propose.py` |
| Unit — index availability | `search_vault()` on a read-only index dir raises `IndexUnavailable`, and `serve` answers `503 index_unavailable` (F-4) | `test_search.py`, `test_serve.py` with a `chmod 0500` index dir |
| Unit — sync scope | The sync path stages `pending/` only: a dirty file under `knowledge/` is never committed by it | `test_sync.py` |
| Integration | Full propose → decide → promote → search cycle in one temp tree | `smoke/verify_vault.py` |
| E2E | Real host: migration script, `sudo systemctl start knowledge-vault-promote@<id>`, note appears in `knowledge/` and in a fresh clone of the bare repo | Manual, per the proposal's success criteria |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED test |
|---|---|---|---|
| Shell / subprocess | **Applicable** — `promote.py` and `sync.py` run `git` | `subprocess.run` with an argument list, never `shell=True`, inherited from `mirror.py`; `git mv --` separator; `GIT_TERMINAL_PROMPT=0`; `cwd` is the vault tree, never caller-supplied | `test_promote.py` asserts no `shell=True` and that a note id is passed after `--` |
| Path traversal via argv | **Applicable** — `promote <id>` becomes a path | `NOTE_ID` regex validated before any join; a rejected id exits non-zero with nothing touched | `test_promote.py`: `../../etc/passwd`, `/abs/id`, `a b` all refused pre-git |
| VCS automation | Applicable | Only three write verbs (`add`, `mv`, `commit`) plus `push` to a fixed local bare path; no `--force`, no history rewrite, no branch deletion (D-11); a failed push leaves the commit for the next run, matching `mirror._pending()`'s existing lesson | `test_sync.py`: a failing push does not lose the commit |
| Commit-message injection | Applicable | `rationale` is one argv item to `git commit -m`, never a shell string; a value containing newlines or `--` cannot become an option or a command | `test_promote.py` with a rationale containing `\n`, `"`, `$(...)`, `--force` |
| Process integration / privilege boundary | **Applicable** — D-04/D-13 | JARVIS: group write on `pending/` (`2770`) only. Promote actor: sole owner of `knowledge/` (`0750`), runs unattended on `knowledge-vault-promote.timer` — no per-promotion human trigger. Sync: `ReadOnlyPaths=<tree>/knowledge`. `pending/`→`knowledge/` boundary enforced twice (ownership + `ReadWritePaths=`/`ReadOnlyPaths=`), as today. **What is NOT enforced (D-13, accepted risk):** nothing stops the JARVIS-owned process from writing `reviewer`/`decision: approved`/`rationale` into its own pending note before the timer runs — the human-review gate is a skill-level behavioral instruction, not a kernel one | Unit-file assertions in `test_promote.py`-adjacent installer review; runtime proof is manual (`sudo -u jarvis touch <tree>/knowledge/x` → EACCES) |
| Unapproved content served as approved | **Applicable** — the reason this change exists | Allowlist scope (D-01/D-02); `pending/` is not merely excluded, it is never enumerated | The two allowlist tests above |
| Audit-trail loss | Applicable | D-05 refusal + commit message; D-06 audit over `knowledge/` | `test_promote.py` refusal cases |
| Secret handling | N/A | The search bearer token's `LoadCredential=` path is untouched | None |
| Routing / network | N/A | `POST /search` + `GET /healthz` contract unchanged; one new `503` body on a path that previously raised | `test_serve.py` |
| Executable-file classification | N/A | No executable bits change beyond the existing `install -m 0750` for scripts | None |

## Migration / Rollout

Idempotent, and the old layout is never destroyed (F-8, D-12):

1. `sudo systemctl disable --now knowledge-vault-{review,review-sync,approve,publisher,mirror}.timer`.
2. `git clone --branch main /srv/git/knowledge-vault.git /opt/knowledge-vault/tree`; `chown -R knowledge-vault-promote:knowledge-vault`; `git config core.sharedRepository group`.
3. `mkdir knowledge pending`; `git mv -- *.md knowledge/`; commit. **`git mv` only — no id is ever re-minted, file names stay byte-for-byte**, so every intra-vault link keeps resolving (links are relative `<id>.md`, and both endpoints move together).
4. Drift check: every `*.md` in the old flat `/opt/knowledge-vault/vault` that the mirror had not pushed is copied into `knowledge/` and committed. Gate: `diff -r /opt/knowledge-vault/vault /opt/knowledge-vault/tree/knowledge` must be empty (modulo subfolders) before continuing.
5. Reconcile the old `pending` branch: for each `<id>.md` on `refs/heads/pending` whose id is **not** already in `knowledge/`, `git show pending:<name> > pending/<name>`; commit. That branch's `README.md` is dropped — the new tree gets a root `README.md` instead, which nothing enumerates and which is therefore itself a live demonstration of D-02. The `pending` branch is left frozen, not deleted (D-11).
6. `git push origin main`.
7. Install the new package + units; `systemctl enable --now knowledge-vault-sync.timer knowledge-vault-promote.timer`; run one full propose → decide cycle by hand, wait one `KNOWLEDGE_VAULT_PROMOTE_INTERVAL` (default 5min), and confirm the note landed in `knowledge/` and in a fresh clone without any manual promote trigger.
8. Cleanup of `/opt/knowledge-vault/vault` and `/var/lib/knowledge-vault/{proposals,pending,decisions,approved,publisher,review,mirror}` is an **explicit follow-up**, not part of this change.

Rollback: revert the PR slices newest-first and re-enable the old timers with `KNOWLEDGE_VAULT_DIR=/opt/knowledge-vault/vault`. Because step 4 copies and never moves, and step 8 never runs here, no note is destroyed by a rollback.

## Delivery Forecast

| Slice | Contents | Authored lines (est.) |
|---|---|---|
| **PR 1 — scoped search/index root** | `layout.py`, `retrieval.py`, `search.py`, `serve.py` (D-07), `test_layout.py`, edits to `test_retrieval/search/serve` | **~260** |
| **PR 2 — lifecycle: propose/decide into `pending/`** | `propose.py`, `decide.py`, `review.py` shrink, `publisher.py`/`outbox.py`/`models.py` deletions, test deletions + rewrites | **~420** |
| **PR 3 — promote + sync** | `promote.py`, `sync.py` (from `mirror.py`), `review_sync.py` deletion, `test_promote.py`, `test_sync.py`, `pyproject.toml` | **~450** |
| **PR 4 — units, installer, migration** | new/deleted units, `install-host.sh`, `scripts/migrate-to-tree.sh`, `approve_locally.py` deletion | **~350** |
| **PR 5 — docs + skill + spec** | `docs/services/knowledge-vault.md` rewrite, `SKILL.md`, `smoke/verify_vault.py`, openspec spec sync | **~400** |

`Decision needed before apply: Yes`
`Chained PRs recommended: Yes`
`400-line budget risk: High`

Five chained PRs, each targeting the previous branch. PR 1 is the one that must land first and alone: it is the load-bearing change, it is independently verifiable (the two allowlist tests go green, the suite stays green against the *old* flat layout only if the fixtures move — so it is also the honest place to absorb the fixture churn), and it fits the budget. PRs 2–5 each exceed or approach 400 and will need either a further split or an explicit `size:exception`; the natural further split points are PR 2 at propose-vs-deletions and PR 5 at docs-vs-spec. Deletions dominate PRs 2–4, which is authored-line cost that reviews far faster than net-new code — worth saying out loud when the exception is requested. `sdd-tasks` owns the final guard lines.

## Open Questions

- [x] **D-09 — RESOLVED by explicit user request: accept the loss.** The retitle/alias-merge capability drops with `publisher.py`; `promote.py` refuses to overwrite an existing `knowledge/<id>.md` instead of merging aliases.
- [x] **D-13 — RESOLVED by explicit user request: accepted.** The self-approval risk introduced by making promotion an unattended timer (see D-13 above) is an accepted risk, not mitigated in this change.
- [ ] **D-10 — dedupe no longer spans published notes.** Re-proposing text already in `knowledge/` produces a pending duplicate. Acceptable? The alternative is keeping `idempotency_key` in published frontmatter, which pollutes the OKF envelope.
- [ ] **Obsidian vault path changes** from `/opt/knowledge-vault/vault` to `/opt/knowledge-vault/tree` (D-12), and the human now sees `pending/` inside their vault. Whether `pending/` should be added to Obsidian's excluded folders is a human-workflow question this design does not decide.
- [x] **Who runs promotion — RESOLVED by explicit user request.** Dedicated `knowledge-vault-promote` system user, unattended on `knowledge-vault-promote.timer` (default 5min, `KNOWLEDGE_VAULT_PROMOTE_INTERVAL`-configurable), not a per-id manual trigger. This changed the design from the earlier "human explicitly promotes each note" model — see D-13 for the resulting tradeoff.
- [ ] **F-4's `503` on a stale index** is a new response the memory-router `KnowledgeVaultBackend` has not been exercised against. Its partial-unavailability contract should cover it; confirm during PR 1 rather than assuming.
- [ ] **Phone review has no replacement.** D-01 of the proposal retires it by explicit user confirmation. Cloning the single repo and editing `pending/` by hand is the manual substitute; a real mobile surface is a separate change.
