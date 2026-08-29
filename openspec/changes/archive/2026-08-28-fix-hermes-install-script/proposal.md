# Proposal: Repair and auto-vendor install-hermes.sh

## Intent

`hermes-native/scripts/install-hermes.sh` does not parse (`bash -n` fails on the malformed
`AGENTS.md` conditional, line 243). Even once it parses, spec 004's Fase 3 is a dead end on a
clean checkout: `hermes-native/vendor/` is empty, so the script exits 20 and asks an operator to
vendor an installer by hand. Nobody can bootstrap a primary node. The owner has decided the
script must vendor the official installer itself, pinned, instead of leaving a manual pre-step.

## Scope

### In Scope
- Fix `if [ -f "$SOURCE_CONFIG_DIR/AGENTS.md"]: then` → `[ ... ]; then`; normalize line 244 to
  2-space indent. Behavior (`install -m 644`) unchanged, mirrors the adjacent SOUL.md block.
  This part was never in dispute.
- Remove the corrupted `else` + unpinned `git clone -o ...` + `echo` block wrongly inserted
  inside the `cat >&2 <<EOF` heredoc (lines 172-176). `-o` names a Git remote, not a destination.
- Replace the fail-closed dead end with **pinned auto-vendoring** when
  `vendor/hermes-agent/scripts/install.sh` is absent: `git init` → `remote add origin` →
  `git fetch --depth 1 origin $HERMES_COMMIT` → `checkout FETCH_HEAD` into
  `hermes-native/vendor/hermes-agent/`, then use `scripts/install.sh` **directly from that
  checkout** (owner decision: no copy, no flat `vendor/install-hermes.sh` — the installer is
  used from its real path inside the clone so nothing about its location needs to move) and
  write `vendor/hermes-agent/scripts/install.sh.sha256` in place (TOFU pin consumed by the
  existing `sha256sum -c` step, which needs no changes since it already derives the checksum
  path from wherever `VENDORED_INSTALLER` points). Fail closed on any fetch error; never fall
  back to an unpinned branch.
- `HERMES_COMMIT` default `v0.20.6` → `5fc308a70719a83cccdbba4c0e39c23f5a8239d5` (tag
  `v2026.8.27`). Upstream uses date tags; `v0.19.0`/`v0.20.x` do not exist on the remote.
- Reword the heredoc so automatic vendoring is the documented primary path and manual
  pre-population of `hermes-native/vendor/hermes-agent/` (a hand-placed checkout) remains an
  offline override.
- Add spec 004 Fase 3 post-install check:
  `git -C "$SERVICE_HOME/.hermes/hermes-agent" rev-parse HEAD` = `$HERMES_COMMIT`. **Moved in
  scope**: it was deferred only because `HERMES_COMMIT` was a tag; with a full SHA and a working
  install path it is a 3-line assertion required by spec 004, next to the existing venv test.
- Ignore `hermes-native/vendor/hermes-agent/` (clone working copy, not vendored artifact).
- Shellcheck/indent clean-up limited to touched lines; `bash -n` must pass.

### Out of Scope
- Checksum-pinning or reviewing upstream `scripts/install.sh` content itself (TOFU only).
- Committing the vendored installer blob to Git.
- Multi-arch / browser policy, backup/restore, or other spec 004 pending items.

## Capabilities

### New Capabilities
- `hermes-native-installer`: script MUST parse under `bash -n`; MUST obtain the official
  installer only from a SHA-pinned fetch of `NousResearch/hermes-agent`; MUST record and
  thereafter verify its SHA-256; MUST fail closed rather than fetch anything unpinned; MUST
  verify the installed checkout's `HEAD` equals `HERMES_COMMIT`.

### Modified Capabilities
- None.

## Approach

Delete the corrupted insertion, then replace the fail-closed branch with a pinned shallow fetch
into a dedicated subdirectory, and point the existing `VENDORED_INSTALLER` variable at the
installer's real path inside that checkout instead of a copied flat file. Trust boundary is the
pinned commit SHA on first run; every later run verifies the recorded hash instead of the
network.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `hermes-native/scripts/install-hermes.sh` | Modified | Commit default (44), Fase 3 vendoring + verification (167-221), AGENTS.md conditional (243-244) |
| `.gitignore` | Modified | Ignore `hermes-native/vendor/hermes-agent/` |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Network fetch at install time is read as `curl \| bash` | Med | Fetch is pinned to a full commit SHA and TOFU-hashed; no branch, no unpinned URL execution |
| Upstream deletes/rewrites the pinned commit | Low | Fail closed with explicit error; manual vendoring override documented |
| `scripts/install.sh` path moves in a future commit | Med | Bumping `HERMES_COMMIT` requires re-verifying the path; error message names it |
| Stale `.sha256` after a commit bump blocks installs | Med | Error message instructs `rm -rf vendor/hermes-agent/` to re-vendor |
| Clone directory (installer + checksum included) committed by accident | Low | Single `.gitignore` entry covers the whole tree |

## Rollback Plan

`git revert` the commit, or `git checkout HEAD~1 -- hermes-native/scripts/install-hermes.sh`.
Operators additionally remove `hermes-native/vendor/hermes-agent/` (one directory holds the
clone, the installer, and its checksum). No migrations, no deployed state.

## Dependencies

- Network access to `https://github.com/NousResearch/hermes-agent.git` on first install.
- Git supporting `fetch --depth 1 <sha>` (server-side `uploadpack.allowReachableSHA1InWant`,
  enabled on GitHub).

## Success Criteria

- [ ] `bash -n hermes-native/scripts/install-hermes.sh` exits 0
- [ ] With an empty `hermes-native/vendor/`, one run produces
      `vendor/hermes-agent/scripts/install.sh` and a matching `.sha256` in place, with no manual
      pre-step and no copy to a separate file
- [ ] Second run verifies the checksum and performs no fetch
- [ ] Fetch failure exits non-zero with a clear message; no unpinned fallback
- [ ] `HERMES_COMMIT` default is a full 40-char SHA; no `-o` flag misuse remains
- [ ] Post-install `rev-parse HEAD` equals `HERMES_COMMIT`
- [ ] `AGENTS.md` is installed when present in `kubernetes/hermes/config`
- [ ] `shellcheck` reports no new findings vs. baseline

## Proposal question round — resolved

1. `HERMES_COMMIT` → full SHA `5fc308a70719a83cccdbba4c0e39c23f5a8239d5` (tag `v2026.8.27`,
   latest stable). Upstream uses date-based tags; the semver tags in spec 004 do not exist.
2. Vendoring style → full pinned repo clone into `vendor/hermes-agent/`, installer used
   **directly from its real path inside the clone** (`scripts/install.sh`, no copy). Single-file
   TOFU fetch was explicitly rejected by the owner; a copy-out-to-flat-file draft was also
   rejected in favor of using the clone in place, so nothing about the installer's location ever
   needs to move.
3. Shellcheck/indent clean-up on touched lines → included.

Open for owner review (non-blocking): spec 004's "Trabajo pendiente" items 5 and 6 are satisfied
by this change and should be marked done during archive.
