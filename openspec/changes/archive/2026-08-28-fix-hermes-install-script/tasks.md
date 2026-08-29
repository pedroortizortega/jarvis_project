# Tasks: Repair and auto-vendor install-hermes.sh

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~75-95 (additions+deletions) across 2 files |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Repair + auto-vendor `install-hermes.sh`, ignore vendor clone dir | PR 1 (single) | `bash -n hermes-native/scripts/install-hermes.sh` | Run script Fase 3 twice against empty `hermes-native/vendor/` (see Phase 4 tasks) | `git checkout HEAD~1 -- hermes-native/scripts/install-hermes.sh .gitignore` |

## Phase 1: Baseline

- [x] 1.1 Run `shellcheck hermes-native/scripts/install-hermes.sh` on the current (unmodified) file and save output as the pre-change baseline for later diffing (satisfies "no new findings" requirement).

## Phase 2: Edit 1 — Commit pin + help text

- [x] 2.1 In `hermes-native/scripts/install-hermes.sh` line ~44, change `HERMES_COMMIT="${HERMES_COMMIT:-v0.20.6}"` to `HERMES_COMMIT="${HERMES_COMMIT:-5fc308a70719a83cccdbba4c0e39c23f5a8239d5}"`; add adjacent comment noting this SHA = upstream tag `v2026.8.27`.
- [x] 2.2 In the `--hermes-commit` help text (line ~59, `default: v0.20.1`), update the shown default to match the new SHA/tag.

## Phase 3: Edit 2 — Fase 3 rewrite (pinned auto-vendoring)

- [x] 3.1 Delete the corrupted lines 172-175 (`else` / `echo` / unpinned `git clone ... -o ...` / `echo`) that were pasted inside the `cat >&2 <<EOF` heredoc body.
- [x] 3.2 Add the 40-hex `HERMES_COMMIT` guard (`case` statement) before any network call inside the missing-vendor `if [ ! -f "$VENDORED_INSTALLER" ]; then` branch; on failure `exit 20`.
- [x] 3.3 Implement the pinned fetch subshell per design: `install -d -m 755 "$HERMES_AGENT_SRC"`, then `cd "$HERMES_AGENT_SRC" && git init -q && git remote remove origin (best-effort) && git remote add origin "$UPSTREAM_REPO" && git fetch --quiet --depth 1 origin "$HERMES_COMMIT" && git checkout --quiet FETCH_HEAD`, all `&&`-chained on one compound command; on failure emit the fetch-failure heredoc (names repo, commit, likely causes) and `exit 20`.
- [x] 3.4 Redefine `VENDORED_INSTALLER="$HERMES_AGENT_SRC/scripts/install.sh"` (real path inside the clone — no copy, no flat `vendor/install-hermes.sh`) and confirm no other line still references the old `$(dirname "$0")/../vendor/install-hermes.sh` literal.
- [x] 3.5 Add the existence check `[ -f "$VENDORED_INSTALLER" ]` on the checked-out tree; on missing path emit the path-heredoc (naming `scripts/install.sh`) and `exit 20`. Then `chmod 700 "$VENDORED_INSTALLER"` and write `install.sh.sha256` in place from inside `$(dirname "$VENDORED_INSTALLER")` using the bare basename (`sha256sum install.sh > install.sh.sha256`) — matches the existing verify block's expected format with no changes needed there.
- [x] 3.6 Reword the retained heredoc text so auto-vendoring is documented as the primary path and manual pre-population of `hermes-native/vendor/hermes-agent/` (a hand-placed checkout with `scripts/install.sh` + `.sha256`) is the offline/air-gapped override; include the `rm -rf hermes-native/vendor/hermes-agent/` re-vendor instruction in both the fetch-failure and checksum-failure messages.
- [x] 3.7 Leave lines 191-214 (checksum verify, `chmod 700`, `BROWSER_FLAG`, installer invocation with its `# shellcheck disable=SC2086`) byte-identical.
- [x] 3.8 After the venv-binary test (post line ~217-220), add the post-install commit assertion: `test "$(git -C "$HERMES_HOME/hermes-agent" rev-parse HEAD)" = "$HERMES_COMMIT" || { echo "error: installed commit does not match HERMES_COMMIT" >&2; exit 20; }`.

## Phase 4: Edit 3 — AGENTS.md conditional fix

- [x] 4.1 Fix `if [ -f "$SOURCE_CONFIG_DIR/AGENTS.md"]: then` (lines ~243-245) to `if [ -f "$SOURCE_CONFIG_DIR/AGENTS.md" ]; then`, re-indent body to 2 spaces, matching the SOUL.md block at 239-241. Behavior (`install -m 644`) unchanged.

## Phase 5: Edit 4 — .gitignore

- [x] 5.1 Add `hermes-native/vendor/hermes-agent/` to `.gitignore` under the "Local project credentials and embedded source checkouts" heading, next to `kubernetes/docker/hermes-agent/`. One entry is sufficient — the installer and its `.sha256` live inside this same directory now, no separate pattern needed.

## Phase 6: Verification

- [x] 6.1 Run `bash -n hermes-native/scripts/install-hermes.sh` — must exit 0.
- [x] 6.2 Run `shellcheck hermes-native/scripts/install-hermes.sh` again and diff against the Phase 1 baseline — zero new findings.
- [x] 6.3 First-run test: with an empty `hermes-native/vendor/`, run the script through Fase 3 and confirm `vendor/hermes-agent/scripts/install.sh` and a matching `install.sh.sha256` are created in place with no manual pre-step and no copy, and `sha256sum -c` passes.
- [x] 6.4 Second-run test: re-run with the vendored file present and confirm no `git fetch`/`git clone` occurs (trace/log check) and the checksum verification path executes instead.
- [x] 6.5 Fetch-failure test: set `HERMES_COMMIT` to a 40-hex value that does not exist on the remote (or deny network) and confirm the script exits non-zero with a message naming the repo, commit, and likely causes, with no `vendor/hermes-agent/scripts/install.sh` left behind.
- [x] 6.6 Unpinned-guard test: pass `--hermes-commit v2026.8.27` (a tag, not a SHA) with an empty vendor dir and confirm the script exits 20 before any network call.
- [x] 6.7 Post-install commit-match test: after a successful vendored install, confirm `git -C "$HERMES_HOME/hermes-agent" rev-parse HEAD` equals `$HERMES_COMMIT`; simulate a mismatch and confirm `exit 20`.
- [x] 6.8 Negative grep: confirm the script contains no `git clone`, no `-o` flag misuse, and no `curl | bash` pattern anywhere.
- [x] 6.9 Static diff: confirm lines 243-245 (AGENTS.md block) are structurally and indentation-identical to lines 239-241 (SOUL.md block).
