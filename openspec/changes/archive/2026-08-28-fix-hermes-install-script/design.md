# Design: Repair and auto-vendor install-hermes.sh

## Technical Approach

Three localized edits in `hermes-native/scripts/install-hermes.sh` plus one `.gitignore` line.
Fase 3 stops being a manual dead end: when `vendor/install-hermes.sh` is absent the script
materializes it itself from a **SHA-pinned** shallow fetch, writes the `.sha256` the existing
verification block already consumes (TOFU), and then falls through to the unchanged
`chmod 700` + `bash "$VENDORED_INSTALLER" ...` invocation. Every later run sees the file,
skips the network entirely, and verifies the recorded hash.

## Edit 1 — `HERMES_COMMIT` default (line 44)

Before: `HERMES_COMMIT="${HERMES_COMMIT:-v0.20.6}"`
After: `HERMES_COMMIT="${HERMES_COMMIT:-5fc308a70719a83cccdbba4c0e39c23f5a8239d5}"`
plus an adjacent comment: this SHA is upstream tag `v2026.8.27` (latest stable 2026-08-28;
upstream uses date-based tags, not semver — the `v0.x` tags in spec 004 do not exist).
The stale `--hermes-commit` help text at line 59 (`default: v0.20.1`) is inside the touched
concern and must be corrected to match.

## Edit 2 — Fase 3 rewrite (lines 169-221)

Delete lines 172-175 entirely (`else`, `echo`, `git clone ... -o ...`, `echo`) — they were
pasted **inside** the `cat >&2 <<EOF` body, which is what breaks the phase. `-o` names a
remote, not a destination; no `git clone` form survives the edit.

**Path decision (owner-confirmed after review of a prior copy-based draft):** the vendored
installer is never copied out of the clone. `VENDORED_INSTALLER` is redefined to point directly
at the file's real path inside the cloned working copy —
`hermes-native/vendor/hermes-agent/scripts/install.sh` — so the whole `vendor/hermes-agent/`
directory IS the vendored artifact, not a source the installer gets extracted from. Verified: the
upstream `scripts/install.sh` has no `dirname "$0"`/`SCRIPT_DIR`/`source` reference to sibling
files, so nothing about its own location constrains where it may live — this simplification is
purely about not maintaining a second copy, not a correctness requirement of the installer
itself. This also means the existing checksum machinery needs zero new logic: it already derives
`INSTALLER_SHA256_FILE="${VENDORED_INSTALLER}.sha256"`, so once `VENDORED_INSTALLER` points inside
the clone, that checksum file naturally becomes `.../scripts/install.sh.sha256` with no separate
copy step to hash.

Target shape for the missing-vendor branch:

```
VENDOR_ROOT="$(dirname "$0")/../vendor"
HERMES_AGENT_SRC="$VENDOR_ROOT/hermes-agent"
VENDORED_INSTALLER="$HERMES_AGENT_SRC/scripts/install.sh"
UPSTREAM_REPO="https://github.com/NousResearch/hermes-agent.git"

if [ ! -f "$VENDORED_INSTALLER" ]; then
  # invariant: refuse to touch the network unless $HERMES_COMMIT is a full 40-hex SHA
  # (post-verify fix: regex validates all 40 characters, not just the first)
  if ! [[ "$HERMES_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then <fail 20>; fi
  log "no vendored installer at $VENDORED_INSTALLER — auto-vendoring $UPSTREAM_REPO @ $HERMES_COMMIT"
  install -d -m 755 "$HERMES_AGENT_SRC"
  ( cd "$HERMES_AGENT_SRC" \
    && git init -q \
    && { git remote remove origin >/dev/null 2>&1 || true; } \
    && git remote add origin "$UPSTREAM_REPO" \
    && git fetch --quiet --depth 1 origin "$HERMES_COMMIT" \
    && git checkout --quiet FETCH_HEAD ) || { <fetch-failure heredoc>; exit 20; }
  [ -f "$VENDORED_INSTALLER" ] || { <path heredoc>; exit 20; }
  chmod 700 "$VENDORED_INSTALLER"
  ( cd "$(dirname "$VENDORED_INSTALLER")" && sha256sum "$(basename "$VENDORED_INSTALLER")" > "$(basename "$VENDORED_INSTALLER").sha256" )
  log "vendored installer recorded at ${VENDORED_INSTALLER}.sha256"
fi
```

Note: `VENDORED_INSTALLER`'s declaration moves from its old standalone line (169) up to sit
alongside `VENDOR_ROOT`/`HERMES_AGENT_SRC` above, since it is now derived from them rather than
being an independent literal path. No other code below this block references the old
`$(dirname "$0")/../vendor/install-hermes.sh` literal — confirm no other match exists before
removing it.

Rules for apply:
- Explicit `&&` chaining inside the subshell is mandatory. Bash suppresses `set -e` inside a
  compound command on the left of `||`, so newline-separated statements there would not abort.
- No `install -m 700`/copy step exists anymore — the file is chmod'd and hashed in place inside
  the clone. The pre-existing `chmod 700 "$VENDORED_INSTALLER"` a few lines below (line ~203 in
  the original) becomes redundant with the one shown above when the auto-vendor branch just ran;
  keep the original line so the already-vendored (second-run) path still gets it too — chmod is
  idempotent, calling it twice is harmless. Do not delete the original line 203 `chmod 700`.
- `.sha256` must be written from **inside** the installer's own directory with the bare basename,
  producing `<hash>  install.sh` (NOT `install-hermes.sh` — the real upstream filename is
  `install.sh`). That is exactly the format the existing checksum-verify block (lines 191-201,
  `cd "$(dirname "$VENDORED_INSTALLER")" && sha256sum -c "$(basename "$INSTALLER_SHA256_FILE")"`)
  already expects — it needs no changes at all now that `VENDORED_INSTALLER` resolves inside the
  clone.
- Lines 191-201 (existing checksum verify block) and 205-214 (`BROWSER_FLAG`, the
  `bash "$VENDORED_INSTALLER"` invocation with its `# shellcheck disable=SC2086`) stay
  byte-identical — they already work correctly once `VENDORED_INSTALLER` is redefined.
- Heredocs keep the unquoted `<<EOF` delimiter — the messages must expand `$VENDORED_INSTALLER`,
  `$UPSTREAM_REPO`, `$HERMES_COMMIT`. Reword the retained spec-004 rationale so auto-vendoring
  is the documented primary path and manual pre-population of
  `hermes-native/vendor/hermes-agent/` (a hand-placed checkout containing `scripts/install.sh`
  and its `.sha256`) is the **offline/air-gapped override**. Failure text must name the repo, the
  commit, the likely causes (no network, commit not present on the remote / rewritten upstream),
  and for the missing-path case the expected `scripts/install.sh`.
- After line 217-220 (venv-binary test), add the spec-004 commit assertion:
  `test "$(git -C "$HERMES_HOME/hermes-agent" rev-parse HEAD)" = "$HERMES_COMMIT" || { echo "error: installed commit does not match HERMES_COMMIT" >&2; exit 20; }`

## Edit 3 — Fase 5 `AGENTS.md` conditional (lines 243-245)

`if [ -f "$SOURCE_CONFIG_DIR/AGENTS.md"]: then` → space before `]`, `;` for `:`, body
re-indented to the file's 2-space standard. Structurally identical to the SOUL.md block at
239-241. Behavior (`install -m 644`) unchanged.

## Edit 4 — `.gitignore`

Append `hermes-native/vendor/hermes-agent/` under the existing
`# Local project credentials and embedded source checkouts` heading, next to the precedent
`kubernetes/docker/hermes-agent/`.

**Simplified by the path decision above:** since `VENDORED_INSTALLER` and its `.sha256` now live
*inside* `hermes-native/vendor/hermes-agent/` rather than as separate flat files under
`hermes-native/vendor/`, this single directory entry covers everything — the clone, the installer
script, and its checksum are all one gitignored tree. No separate `install-hermes.sh*` pattern is
needed.

## Architecture Decisions

| Decision | Choice | Alternatives rejected | Rationale |
|---|---|---|---|
| Missing vendor | Pinned auto-vendor, then fall through | Keep `exit 20` manual dead end | Nobody could bootstrap a primary node; owner decision |
| Fetch mechanism | `git init` + `remote add` + `fetch --depth 1 <sha>` + `checkout FETCH_HEAD` into `vendor/hermes-agent/` | `git clone` (cannot clone a bare SHA); single-file `curl` TOFU (owner rejected) | Only form that pins a full SHA with one shallow round trip |
| Pin enforcement | 40-hex guard before any network call | Trust the operator's `--hermes-commit` | Proposal capability: MUST fail closed rather than fetch anything unpinned |
| Trust model | TOFU: hash recorded on first vendor, verified on every later run | Ship a committed expected hash | Out of scope; upstream content is not reviewed here |
| Failure policy | `exit 20`, never `main`/`HEAD` fallback | Warn and continue unpinned | An unpinned fallback is the exact supply-chain hole the pin exists to close |
| Checkout path for assertion | `$HERMES_HOME/hermes-agent` | New literal path; `$SERVICE_HOME/.hermes/...` | `$HERMES_HOME` is what is passed as `--hermes-home`; identical expansion to the adjacent venv test, matches spec 004 line 424 |
| Clean-up radius | Touched region only | File-wide shellcheck sweep | Reviewable diff, atomic revert |

## Data Flow

```
run 1: vendor/hermes-agent/scripts/install.sh missing
  SHA guard ─→ git fetch --depth 1 <sha> ─→ vendor/hermes-agent/ (gitignored, whole tree)
             └─fail→ exit 20
       └─→ chmod 700 + sha256sum in place ─→ vendor/hermes-agent/scripts/install.sh.sha256
run 2+: file present ─→ (no network) ─→ sha256sum -c ─→ chmod 700 ─→ bash installer
                     ─→ hermes --version, venv test, rev-parse HEAD == $HERMES_COMMIT
```

## File Changes

| File | Action | Description |
|---|---|---|
| `hermes-native/scripts/install-hermes.sh` | Modify | Commit default + help text (44, 59); Fase 3 auto-vendor + commit assertion (169-221); AGENTS.md conditional (243-245) |
| `.gitignore` | Modify | Add `hermes-native/vendor/hermes-agent/` |

## Threat Matrix

| Boundary | Applicability | Design response | RED test |
|---|---|---|---|
| Documentation-like paths | N/A — no file-classification logic | — | — |
| Git repository selection | **Applicable** — a network fetch is being *added* | Hardcoded `https://github.com/NousResearch/hermes-agent.git`, never operator-supplied; fetched into a dedicated `vendor/hermes-agent/` subdir, never over the repo or `$HERMES_HOME`; `git -C` in the assertion targets the installer's own checkout | Fetch failure ⇒ exit 20, no `vendor/hermes-agent/scripts/install.sh` produced; assertion mismatch ⇒ exit 20 |
| Commit state | **Applicable** — `git init`/`checkout` run inside the vendored subdir | All VCS commands are `cd`-scoped to `$HERMES_AGENT_SRC`; script stages/commits nothing in the project repo | Project repo `git status` unchanged after a run |
| Push state | N/A — no push path | — | — |
| PR commands | N/A — no PR automation | — | — |

**Invariant that must never be violated:** the script must never issue a fetch unless
`$HERMES_COMMIT` is already fixed to a full 40-hex SHA at that point. Safety rests on two
independent legs — (a) the pin means the fetched bytes are content-addressed, so a compromised
or rewritten upstream branch cannot change what is retrieved, and (b) TOFU hashing means the
network is consulted exactly once per vendored artifact, and every later run verifies bytes on
disk instead. Any branch/tag fetch, any `main` fallback, or any fetch on a run where the file
already exists breaks both legs and must be treated as a blocking defect.

## Testing / Verification Strategy

| Layer | Check | Pass condition |
|---|---|---|
| Parse | `bash -n hermes-native/scripts/install-hermes.sh` | exit 0 |
| Lint | `shellcheck` vs. a baseline captured **before** editing | no new findings |
| First run | Empty `hermes-native/vendor/`, run Fase 3 | `vendor/hermes-agent/scripts/install.sh` + matching `.sha256` created in place; `sha256sum -c` passes; no manual pre-step |
| Second run | Re-run with the file present | no network call (assert no fetch in trace); checksum verified; installer executed |
| Fetch failure | `HERMES_COMMIT=<40 hex, nonexistent>` or network denied | exit 20, message naming repo+commit+causes, no `vendor/hermes-agent/scripts/install.sh` left behind |
| Unpinned guard | `--hermes-commit v2026.8.27` with vendor empty | exit 20 before any network call |
| Post-install | `rev-parse HEAD` assertion | equals `$HERMES_COMMIT`; mismatch ⇒ exit 20 |
| Negative | grep the script | no `git clone`, no `-o` misuse, no `curl \| bash` |
| Static | diff 243-245 against 239-241 | identical structure and indent |

## Migration / Rollout

No data migration, no deployed state. Revert = `git revert` or
`git checkout HEAD~1 -- hermes-native/scripts/install-hermes.sh .gitignore`.

Operators bumping `HERMES_COMMIT` must force re-vendoring, because a present
`vendor/hermes-agent/scripts/install.sh` short-circuits the fetch and its `.sha256` goes stale
relative to the new commit:

```
rm -rf hermes-native/vendor/hermes-agent/
```

The fetch-failure and checksum-failure messages must both say this (one directory to remove now,
simpler than the prior two-path form). Air-gapped hosts keep the manual path: place a checkout at
`hermes-native/vendor/hermes-agent/` containing `scripts/install.sh` and its `.sha256` by hand,
and the script never reaches the fetch.

## Open Questions

- [x] Should the vendored installer live at a flat `vendor/install-hermes.sh` (copied out) or at
      its real path inside the clone? **Resolved by the owner**: real path inside the clone,
      `hermes-native/vendor/hermes-agent/scripts/install.sh` — no copy step. This also resolves
      the prior "should the blob also be gitignored" question: there is no separate blob anymore,
      one directory entry in `.gitignore` covers everything.
- [ ] Spec 004 "Trabajo pendiente" items 5 and 6 become satisfied by this change and should be
      marked done at archive (raised non-blocking in the proposal).
