# Hermes Native Installer Specification

## Purpose

Define the correctness, pinned-vendoring, and fail-closed contract of
`hermes-native/scripts/install-hermes.sh`, the primary-node bootstrap entry
point for spec 004 (`specs/004_hermes_native_clone_systemd.md`). This is a new
OpenSpec capability capturing script-quality and supply-chain requirements
implicit in spec 004 Fase 3 but never formalized. It does not change spec
004's architecture.

## Requirements

### Requirement: Script must parse

`hermes-native/scripts/install-hermes.sh` MUST be syntactically valid Bash.
`bash -n hermes-native/scripts/install-hermes.sh` MUST exit 0 before any
release or merge.

#### Scenario: Static syntax check

- GIVEN the committed script
- WHEN `bash -n hermes-native/scripts/install-hermes.sh` is run
- THEN it exits 0 with no syntax errors

#### Scenario: Regression guard

- GIVEN a future edit to the script
- WHEN CI or a pre-commit check runs `bash -n`
- THEN a non-zero exit blocks the change from merging

### Requirement: AGENTS.md config-copy conditional

When `$SOURCE_CONFIG_DIR/AGENTS.md` exists, the script MUST copy it into
`$HERMES_HOME` using the same atomic-copy and permission handling already
applied to `SOUL.md`. The conditional guarding this copy MUST be valid Bash
(`bash -n` clean) — no missing whitespace before `]`, no stray tokens.

#### Scenario: AGENTS.md present in source config

- GIVEN `$SOURCE_CONFIG_DIR/AGENTS.md` exists (e.g.
  `kubernetes/hermes/config/AGENTS.md`)
- WHEN the config-copy phase runs
- THEN `AGENTS.md` is installed into `$HERMES_HOME`

#### Scenario: AGENTS.md absent from source config

- GIVEN `$SOURCE_CONFIG_DIR/AGENTS.md` does not exist
- WHEN the config-copy phase runs
- THEN the script skips the copy without error and continues

### Requirement: Pinned auto-vendoring on first run

When `hermes-native/vendor/hermes-agent/scripts/install.sh` is absent, the
script MUST auto-vendor the official installer from `NousResearch/hermes-agent`
pinned to the exact `$HERMES_COMMIT` SHA, using: `git init`, `remote add
origin`, `git fetch --depth 1 origin $HERMES_COMMIT`, `checkout FETCH_HEAD`,
into `hermes-native/vendor/hermes-agent/`. The installer is used directly from
its real path inside that checkout — it MUST NOT be copied elsewhere. The
script MUST then write `hermes-native/vendor/hermes-agent/scripts/install.sh.sha256`
in place (TOFU pin, consumed by the script's existing `sha256sum -c`
verification step). The script MUST NEVER fetch an unpinned branch or `HEAD`
without a SHA (no `main`, no bare `HEAD`).

#### Scenario: Clean checkout, no vendored installer

- GIVEN `hermes-native/vendor/hermes-agent/scripts/install.sh` does not exist
- WHEN `install-hermes.sh` reaches the runtime-install phase (Fase 3)
- THEN it shallow-fetches `NousResearch/hermes-agent` at the exact
  `$HERMES_COMMIT` SHA into `hermes-native/vendor/hermes-agent/`
- AND it writes `hermes-native/vendor/hermes-agent/scripts/install.sh.sha256`
  next to the installer, without copying the installer anywhere
- AND it proceeds to invoke the vendored installer without requiring any
  manual pre-step

#### Scenario: Fetch never targets an unpinned ref

- GIVEN the vendoring step is about to run
- WHEN the script constructs its `git fetch` invocation
- THEN the ref fetched is always the literal `$HERMES_COMMIT` SHA
- AND no invocation fetches `main`, any other branch name, or bare `HEAD`

### Requirement: Verify-only on subsequent runs

When `hermes-native/vendor/hermes-agent/scripts/install.sh` and a matching
`.sha256` already exist, the script MUST NOT re-fetch from the network. It
MUST instead verify the existing file against the recorded checksum via the
existing `sha256sum -c` step.

#### Scenario: Second run reuses vendored installer

- GIVEN `hermes-native/vendor/hermes-agent/scripts/install.sh` and its
  `.sha256` already exist from a prior run
- WHEN `install-hermes.sh` reaches Fase 3 again
- THEN it verifies the checksum of the existing file
- AND it performs no `git fetch` or `git clone` of `NousResearch/hermes-agent`

### Requirement: Fail closed on fetch failure

If the pinned fetch fails for any reason (network unreachable, commit SHA
not found on the remote, or `scripts/install.sh` missing at that commit), the
script MUST exit non-zero with a clear error message. It MUST NOT fall back
to fetching an unpinned branch or partially proceed with a missing installer.

#### Scenario: Network unreachable during vendoring

- GIVEN the vendoring step is triggered and the network is unreachable
- WHEN `git fetch --depth 1 origin $HERMES_COMMIT` fails
- THEN the script exits non-zero with a clear error message
- AND it does not attempt an unpinned fetch as a fallback

#### Scenario: Pinned commit does not exist on the remote

- GIVEN `$HERMES_COMMIT` does not exist on `NousResearch/hermes-agent`
- WHEN the fetch step runs
- THEN the script exits non-zero with a clear error naming the missing commit

#### Scenario: install.sh missing at the pinned commit

- GIVEN the fetch and checkout succeed but `scripts/install.sh` does not
  exist in the checked-out tree at the expected path
- WHEN the script checks for it before proceeding
- THEN the script exits non-zero with a clear error message

### Requirement: HERMES_COMMIT is a full pinned SHA

The default value of `HERMES_COMMIT` MUST be a full 40-character Git commit
SHA, not a tag or branch name. The current pinned default is
`5fc308a70719a83cccdbba4c0e39c23f5a8239d5` (corresponding to upstream tag
`v2026.8.27`).

#### Scenario: Default commit format

- GIVEN the script's default `HERMES_COMMIT` value
- WHEN its length and character set are checked
- THEN it is exactly 40 hexadecimal characters

### Requirement: Post-install commit verification

After the vendored installer runs, the script MUST verify that the installed
runtime checkout's `HEAD` equals `$HERMES_COMMIT`, per spec 004 Fase 3:
`git -C "$SERVICE_HOME/.hermes/hermes-agent" rev-parse HEAD` MUST equal
`$HERMES_COMMIT`.

#### Scenario: Installed HEAD matches pinned commit

- GIVEN the vendored installer has completed successfully
- WHEN the script runs
  `git -C "$SERVICE_HOME/.hermes/hermes-agent" rev-parse HEAD`
- THEN the output equals `$HERMES_COMMIT`

#### Scenario: Installed HEAD mismatch fails the install

- GIVEN the installed runtime checkout's `HEAD` does not equal
  `$HERMES_COMMIT`
- WHEN the post-install verification step runs
- THEN the script exits non-zero rather than reporting success

### Requirement: No new shellcheck findings on touched lines

Lines modified by this change MUST pass `shellcheck` with no findings that
were not already present in the pre-change baseline for the same file.

#### Scenario: Shellcheck comparison

- GIVEN a `shellcheck` run against the pre-change baseline script and a
  `shellcheck` run against the fixed script
- WHEN the two finding sets are diffed
- THEN the fixed script introduces zero new findings on the touched lines

### Requirement: Vendor clone working copy is ignored by Git

`.gitignore` MUST exclude `hermes-native/vendor/hermes-agent/`, since it is a
transient clone working copy, not a vendored artifact to commit.

#### Scenario: Vendor clone is untracked

- GIVEN a fresh vendoring run has created `hermes-native/vendor/hermes-agent/`
- WHEN `git status` is run in the repository
- THEN `hermes-native/vendor/hermes-agent/` does not appear as untracked or
  stageable content

## Out of Scope (deferred, not requirements of this spec)

- Reviewing or auditing the content of the fetched `scripts/install.sh`
  beyond SHA-256 pinning (TOFU, not manual review).
- Committing the vendored installer or its checksum
  (`vendor/hermes-agent/scripts/install.sh` or its `.sha256`) to Git — the
  whole `vendor/hermes-agent/` tree is gitignored.
- Multi-arch/browser policy or other spec 004 pending items unrelated to this
  fix.
