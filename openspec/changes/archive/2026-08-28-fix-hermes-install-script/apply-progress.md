# Apply Progress: Repair and auto-vendor install-hermes.sh

**Status**: All 22 tasks complete (Phase 1–6). No prior apply-progress existed; this is the
single/only apply batch. Session was interrupted once mid-way through Phase 6 verification and
resumed; all edits and the tasks.md checkbox state were confirmed intact on disk before
continuing (no rework, no duplicate edits).

## Mode

Standard mode. `strict_tdd` is not applicable — this is a bash installer script with no test
runner in this repo. Per the phase prompt, `tasks.md` Phase 6 (verification) was treated as the
required verification suite in place of an automated test command, and was executed for real
wherever safely possible (see Work Unit Evidence below).

## Completed Tasks

- [x] 1.1 Baseline shellcheck captured (reconstructed exact pre-apply broken file from HEAD +
      the injected-bug diff, since the broken state was only ever an uncommitted working-tree
      diff, not a separate file)
- [x] 2.1 `HERMES_COMMIT` default → `5fc308a70719a83cccdbba4c0e39c23f5a8239d5` + adjacent comment
- [x] 2.2 `--hermes-commit` help text default updated to match
- [x] 3.1 Corrupted `else`/`echo`/`git clone -o`/`echo` block deleted from inside the heredoc
- [x] 3.2 40-hex `HERMES_COMMIT` guard (`case` statement) added before any network call
- [x] 3.3 Pinned fetch subshell implemented, fully `&&`-chained in one compound command
- [x] 3.4 `VENDORED_INSTALLER` redefined to `$HERMES_AGENT_SRC/scripts/install.sh` (real path
      inside the clone, no copy); confirmed no other line references the old
      `$(dirname "$0")/../vendor/install-hermes.sh` literal
- [x] 3.5 Existence check + chmod 700 + `sha256sum install.sh > install.sh.sha256` written in
      place from inside the installer's own directory
- [x] 3.6 Heredocs reworded: auto-vendoring documented as primary path, manual pre-population as
      offline/air-gapped override; `rm -rf $HERMES_AGENT_SRC` re-vendor instruction included in
      **both** the fetch-failure heredoc and the checksum-verification-failure message (see
      Deviations — this required a small, deliberate departure from the "byte-identical" line in
      design.md's Edit 2 prose, reconciled in favor of the more specific and repeated
      requirement in design.md's own Migration/Rollout section, the proposal's Risks table, and
      tasks.md 3.6)
- [x] 3.7 Lines covering checksum verify / `chmod 700` / `BROWSER_FLAG` / installer invocation
      left otherwise byte-identical (only the one error string above was touched, per 3.6)
- [x] 3.8 Post-install commit assertion added after the venv-binary test
- [x] 4.1 `AGENTS.md` conditional fixed (`]` spacing, `;`, 2-space indent) — structurally
      identical to the SOUL.md block
- [x] 5.1 `.gitignore`: `hermes-native/vendor/hermes-agent/` added under the existing
      "Local project credentials and embedded source checkouts" heading
- [x] 6.1 `bash -n` — exit 0 (confirmed)
- [x] 6.2 `shellcheck` diff vs. baseline — see Work Unit Evidence (shellcheck was not
      preinstalled; a static binary was fetched read-only into scratch space and used)
- [x] 6.3 First-run test — executed for real (see evidence)
- [x] 6.4 Second-run test — executed for real (see evidence)
- [x] 6.5 Fetch-failure test — executed for real (see evidence)
- [x] 6.6 Unpinned-guard test — executed for real (see evidence)
- [x] 6.7 Post-install commit-match test — **partially** executed; see Risks (full end-to-end
      `bash "$VENDORED_INSTALLER" ...` was not run — see rationale in Risks)
- [x] 6.8 Negative grep — executed for real (see evidence)
- [x] 6.9 Static diff — executed for real (see evidence)

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `hermes-native/scripts/install-hermes.sh` | Modified | Edit 1 (commit pin + help text), Edit 2 (Fase 3 pinned auto-vendor rewrite + post-install commit assertion), Edit 3 (AGENTS.md conditional fix) |
| `.gitignore` | Modified | Added `hermes-native/vendor/hermes-agent/` |
| `openspec/changes/fix-hermes-install-script/tasks.md` | Modified | All 22 tasks marked `[x]` |

Net diff: 77 insertions / 15 deletions across 2 files (well under the 400-line budget; forecast
was Low risk, no chaining needed).

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `bash -n hermes-native/scripts/install-hermes.sh` → exit 0 (was exit 2 with a syntax error on the pre-apply broken state, confirmed by reconstructing that exact state from HEAD + the injected-bug diff before editing) |
| Runtime harness command/scenario and exact result | Extracted the real Fase 3 block (`VENDOR_ROOT=...` through the checksum-verify block, byte-identical text pulled from the actual file via `sed`) into a scratch wrapper and ran it against a scratch `vendor/` directory with live network access to `github.com`. All of the following passed as real executions, not reasoning: (1) first-run — empty vendor dir → `git init`/`fetch --depth 1`/`checkout FETCH_HEAD` succeeded, `vendor/hermes-agent/scripts/install.sh` (mode 700) and `install.sh.sha256` created in place, `sha256sum -c` passed inline (`install.sh: La suma coincide`), no copy step, no error; (2) second-run — same wrapper re-run against the now-populated vendor dir: no "auto-vendoring" log line emitted, `vendor/hermes-agent/.git/HEAD` mtime unchanged before/after (proves no fetch/checkout ran), checksum verified instead; (3) fetch-failure — `HERMES_COMMIT` set to a well-formed-but-nonexistent 40-hex SHA (`000...dead`) against an empty vendor dir: `git fetch` failed with `not our ref`, script printed the reworded failure heredoc naming the repo/commit/likely-causes/re-vendor command, exited 20, and left no `install.sh` behind; (4) unpinned-guard — `HERMES_COMMIT=v2026.8.27` (a tag) against an empty vendor dir: script exited 20 on the case-guard *before* any network call, no vendor dir was even created; (5) checksum-tamper — appended a byte to the already-vendored `install.sh` and re-ran: `sha256sum -c` failed (`La suma no coincide`), script printed the updated checksum-failure message including the `rm -rf` re-vendor instruction, exited 20. Full end-to-end `bash "$VENDORED_INSTALLER" --skip-setup ...` (which installs system packages via `sudo pacman`/`apt-get`, writes to a real user's home, and touches systemd) was **not** run — this sandbox has no passwordless sudo and running it would install real system state; task 6.7's post-install `rev-parse HEAD == $HERMES_COMMIT` assertion is therefore verified only by static code review (the line was added correctly, matches the exact literal from design.md/tasks.md, and sits after the existing venv-binary test) — flagged as a risk below |
| Rollback boundary | `git checkout HEAD~1 -- hermes-native/scripts/install-hermes.sh .gitignore` reverts both files atomically; no migrations, no deployed state; operators additionally `rm -rf hermes-native/vendor/hermes-agent/` if a partial vendor checkout was created |

### Shellcheck evidence (task 1.1 / 6.2)

`shellcheck` was not installed in this environment and there is no passwordless `sudo` to
install it via the system package manager. A static `shellcheck` 0.11.0 binary was downloaded
from the upstream GitHub release into ephemeral scratch space (not the repo, not `/tmp` outside
the assigned scratchpad) and used read-only, then discarded.

- **Baseline**: the pre-apply broken state was only ever an *uncommitted working-tree diff* on
  top of the committed `HEAD` version (not a separate committed file), so it could not be
  re-read after editing. It was reconstructed byte-for-byte by applying the exact diff hunks
  observed at the start of this session on top of `git show HEAD:hermes-native/scripts/install-hermes.sh`,
  confirmed identical to the pre-edit file content read earlier in this session. `shellcheck`
  against that reconstruction fails to parse (`SC1072`/`SC1073`/`SC1009` on the malformed
  `AGENTS.md` conditional at line 243), matching the `bash -n` failure described in the proposal.
- **Fixed**: `shellcheck hermes-native/scripts/install-hermes.sh` on the current file → **zero
  findings**, exit 0.
- Since the baseline could not even be parsed, "zero new findings" is trivially and strictly
  satisfied — the fixed script introduces none at all.

### Negative grep / static diff (6.8 / 6.9)

- `grep -n "git clone"` → no matches.
- `grep -nE '\-o[[:space:]]'` → no matches (no `-o` flag misuse).
- `grep -n "curl.*|.*bash"` → one match, but it is inside prose text in the reworded heredoc
  (`"...rather than curl|bash'ing an upstream script..."`), not an executable pipeline. No
  `curl | bash` execution pattern exists anywhere in the script.
- Static diff of lines ~293-299 (AGENTS.md block) vs. ~$SOUL.md block confirmed structurally and
  indentation-identical (`if [ -f "$SOURCE_CONFIG_DIR/<file>" ]; then` / 2-space-indented
  `install -m 644 ...` / `fi`).

## TDD Cycle Evidence

Not applicable — Strict TDD Mode is not active for this change (bash installer script, no test
runner recorded for bash scripts in this project's SDD config). The Work Unit Evidence table
above (real script execution against scratch vendor directories) substitutes for automated
test-first cycles, per the phase prompt's explicit guidance.

## Deviations from Design

- **Checksum-failure message reworded** (tasks 3.6/3.7 tension): design.md's Edit 2 prose says
  lines 191-201 (checksum verify block) "stay byte-identical," but design.md's own
  Migration/Rollout section, the proposal's Risks table, and tasks.md task 3.6 all explicitly
  require the `rm -rf hermes-native/vendor/hermes-agent/` re-vendor instruction to appear in
  **both** the fetch-failure and the checksum-failure messages. These two statements inside
  design.md conflict with each other. Resolved in favor of the more specific, repeated,
  cross-referenced requirement: the checksum-failure `echo` line was extended by one clause to
  include the re-vendor command, while everything else in that block (the `sha256sum -c`
  invocation, the `chmod 700`, `BROWSER_FLAG`, and the installer invocation itself) was left
  untouched. This is flagged here rather than silently applied.
- No other deviations — the fetch subshell, the 40-hex guard, the heredoc rewording, the
  `VENDORED_INSTALLER` path resolution, and the post-install assertion all match design.md's
  literal blueprint.

## Issues Found

None in the design. One environment limitation: no `shellcheck` and no passwordless `sudo`
preinstalled — worked around by fetching a static shellcheck binary into scratch space; the
`sudo`-gated real installer run (Fase 3's `bash "$VENDORED_INSTALLER" ...` and everything after
it) could not be exercised end-to-end for the same reason (see Risks).

## Remaining Tasks

None. 22/22 complete.

## Workload / PR Boundary

- Mode: single PR (forecast: Low risk, no chaining recommended)
- Current work unit: Unit 1 — "Repair + auto-vendor `install-hermes.sh`, ignore vendor clone dir"
- Boundary: starts from the corrupted committed... (uncommitted, injected-bug) state and ends
  with all 4 design edits + `.gitignore` applied and verified
- Estimated review budget impact: 77 insertions + 15 deletions across 2 files — well under the
  400-line budget

## Status

22/22 tasks complete. Ready for verify.
