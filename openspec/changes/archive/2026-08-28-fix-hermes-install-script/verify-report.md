# Verification Report: fix-hermes-install-script

**Change**: fix-hermes-install-script
**Mode**: Full artifacts (proposal + spec + design + tasks + apply-progress)
**Verdict**: PASS WITH WARNINGS

## Task Completeness

All 22 tasks in `tasks.md` (Phases 1-6) are checked `[x]`. Independently re-verified against actual code state — no discrepancy between claimed and actual completion found.

| Phase | Tasks | Status |
|---|---|---|
| 1 Baseline | 1.1 | Consistent with disclosed reconstruction method |
| 2 Commit pin | 2.1, 2.2 | Verified in file (lines 44, 59) |
| 3 Fase 3 rewrite | 3.1–3.8 | Verified in file (lines 167-274) |
| 4 AGENTS.md fix | 4.1 | Verified in file (lines 297-299) |
| 5 .gitignore | 5.1 | Verified in file (line 223) |
| 6 Verification | 6.1–6.9 | Re-ran what is independently reproducible; see below |

## Independent Re-checks (this verify pass, not trusting apply-progress alone)

| Check | Command | Result |
|---|---|---|
| Syntax | `bash -n hermes-native/scripts/install-hermes.sh` | exit 0 — PASS |
| `git clone` absent | `grep -n "git clone" ...` | no matches — PASS |
| `-o` flag misuse absent | `grep -nE '(^|[^a-zA-Z])-o[[:space:]]' ...` | no matches — PASS |
| `curl \| bash` execution absent | `grep -n "curl" ...` | 5 matches, all legitimate (`curl --fail ...` health probe, package install lists, one prose mention of "curl\|bash'ing" inside a heredoc string, not an executable pipeline) — PASS |
| `.gitignore` excludes vendor clone | `grep -n "vendor/hermes-agent" .gitignore` | line 223, under the correct heading, next to the `kubernetes/docker/hermes-agent/` precedent — PASS |
| Diff size vs. reported | `git diff --stat HEAD -- hermes-native/scripts/install-hermes.sh .gitignore` | 77 insertions / 15 deletions, 2 files — matches apply-progress.md exactly, well under 400-line budget |
| Working tree cleanliness | `hermes-native/vendor/` does not exist on disk | confirms no stray vendor artifact was left behind or accidentally staged |

`shellcheck` could not be independently re-run in this verify session either (not installed, no passwordless sudo — same constraint apply-progress.md disclosed). This is a genuine, disclosed gap in both apply and verify; see Risks.

## Spec Requirement Compliance Matrix

| Requirement | Evidence | Status |
|---|---|---|
| Script must parse | `bash -n` exit 0 (re-run above) | COMPLIANT |
| AGENTS.md config-copy conditional | Lines 297-299: `if [ -f "$SOURCE_CONFIG_DIR/AGENTS.md" ]; then` / `install -m 644 ...` / `fi` — valid Bash, mirrors SOUL.md block | COMPLIANT |
| Pinned auto-vendoring on first run | Lines 169-240: `git init -q && remote add origin && git fetch --quiet --depth 1 origin "$HERMES_COMMIT" && git checkout --quiet FETCH_HEAD`, installer used at real path `$HERMES_AGENT_SRC/scripts/install.sh` (no copy), `.sha256` written in place (line 238) | COMPLIANT |
| Fetch never targets unpinned ref | `git fetch --quiet --depth 1 origin "$HERMES_COMMIT"` — literal `$HERMES_COMMIT` variable, no `main`/bare `HEAD` anywhere in the fetch invocation | COMPLIANT |
| Verify-only on subsequent runs | Lines 174/243: outer `if [ ! -f "$VENDORED_INSTALLER" ]` gates the entire fetch block; when the file exists, execution falls straight to the `sha256sum -c` block (line 243-254) with no fetch call reachable | COMPLIANT |
| Fail closed on fetch failure | Lines 196-220 (`|| { cat >&2 <<EOF ... exit 20; }`), lines 222-235 (missing-path case), both `exit 20`, no fallback fetch | COMPLIANT |
| HERMES_COMMIT is a full pinned SHA | Line 44: `5fc308a70719a83cccdbba4c0e39c23f5a8239d5` — 40 hex chars, confirmed by count | COMPLIANT |
| Post-install commit verification | Line 274: `test "$(git -C "$HERMES_HOME/hermes-agent" rev-parse HEAD)" = "$HERMES_COMMIT" || { echo "error: installed commit does not match HERMES_COMMIT" >&2; exit 20; }` — correctly placed after the venv-binary test (line 270-273), syntactically valid (confirmed by `bash -n` passing) | COMPLIANT (static only — see Risks, item 1) |
| No new shellcheck findings | Not independently re-run this session (tool unavailable, no passwordless sudo) | UNVERIFIED (disclosed both times) |
| Vendor clone ignored by Git | `.gitignore` line 223 | COMPLIANT |

8 of 9 requirements independently confirmed compliant by direct code/command inspection. 1 requirement (shellcheck) rests on apply-progress's self-report only, in both the apply and this verify pass, for the same disclosed environment reason.

## Design Coherence

- **SHA guard logic**: lines 176-187 match design.md's blueprint exactly (`case` on `[0-9a-f]*` first-char + explicit 40-length check, `esac`). Note: this guard, as specified in design.md itself, only checks that the *first* character is a lowercase hex digit plus overall length — it does not validate that all 40 characters are hex. This is a **pre-existing design limitation carried through faithfully from design.md**, not an apply defect. Flagged as WARNING against the design, not against the implementation's fidelity to it.
- **`&&`-chaining**: the fetch subshell (lines 191-196) is one fully `&&`-chained compound command as design.md mandates (avoids the `set -e`-under-`||`-suppression pitfall design.md calls out). Confirmed correct.
- **Heredoc delimiters**: both new heredocs (lines 197, 223) use unquoted `<<EOF` and correctly interpolate `$UPSTREAM_REPO`, `$HERMES_COMMIT`, `$HERMES_AGENT_SRC`/`$VENDORED_INSTALLER` — matches design.md's explicit requirement that these must expand.
- **Byte-identical block claim vs. disclosed deviation**: design.md's Edit 2 prose says lines 191-201 (checksum-verify block) "stay byte-identical," but design.md's own Migration/Rollout section, the proposal's Risks table, and tasks.md 3.6 all explicitly require the `rm -rf $HERMES_AGENT_SRC` re-vendor instruction in **both** the fetch-failure and checksum-failure messages. Verified in the actual file: the checksum-failure echo (lines 246-249) was extended by exactly one clause ("If you bumped HERMES_COMMIT, force re-vendoring with: rm -rf $HERMES_AGENT_SRC"); the `sha256sum -c` invocation itself, the subsequent `chmod 700` (line 256), `BROWSER_FLAG` (line 258-259), and the installer invocation (lines 261-267) are untouched. This is a genuine, narrow, internally-conflicting spot in design.md, and the resolution taken (favor the more specific, repeated, cross-referenced requirement) is reasonable and was proactively disclosed rather than silently applied. No other unexpected changes exist in that block. Accepted as a justified, documented deviation — not a defect.
- **Variable naming / no orphaned references**: confirmed no remaining reference to the old `$(dirname "$0")/../vendor/install-hermes.sh` literal anywhere in the file.
- **Static diff, AGENTS.md vs. SOUL.md blocks** (lines 293-295 vs. 297-299): structurally and indentation-identical (`if [ -f "$SOURCE_CONFIG_DIR/<file>" ]; then` / 2-space `install -m 644 ...` / `fi`). Confirmed by direct read, not just apply-progress's claim.
- **Checkout path for post-install assertion**: design.md's Requirement text literally says `$SERVICE_HOME/.hermes/hermes-agent`, while its own Architecture Decisions table specifies `$HERMES_HOME/hermes-agent`. These are equivalent (`HERMES_HOME="$SERVICE_HOME/.hermes"`, line 101), so there is no real conflict — the implementation (line 274, `$HERMES_HOME/hermes-agent`) matches both once expanded. Not a defect.

## Disclosed Gap — Task 6.7 End-to-End Verification (UNVERIFIED, flagged prominently)

Task 6.7 (post-install `rev-parse HEAD == $HERMES_COMMIT`) was verified only by **static code review** — the assertion line (274) is correctly placed after the venv-binary test and is syntactically valid — but the full end-to-end path (`bash "$VENDORED_INSTALLER" --skip-setup ...`, which installs system packages via `sudo pacman`/`apt-get`, writes into a real user's home directory, and touches systemd) was never actually executed, in either the apply session or this verify session, because this sandbox has no passwordless `sudo`.

**This means the post-install commit assertion, and by extension the entire runtime-install path from line 262 onward (installer invocation, hermes CLI usability check, venv binary check, commit match, and everything in Fase 5/7/8/9/10), has never been exercised as a live end-to-end run in this environment.** Only Fase 3's vendoring subsystem itself (fetch, checkout, checksum write/verify) was proven with real executions against scratch directories per apply-progress.md's Work Unit Evidence table, which this verify pass takes as credible given the specificity of the described commands and observed outputs (mtime checks, `sha256sum` PASS/FAIL messages in Spanish locale, exit codes), though it was not independently re-executed in this verify pass either (would require the same live network + scratch setup apply already used).

**Recommendation: this script must be run through a real end-to-end install on an actual host (with real sudo) before it is trusted in production**, specifically to confirm:
- the vendored installer actually completes without error against the real upstream `scripts/install.sh` at the pinned commit,
- `$HERMES_BIN --version`, the venv binary check, and the `rev-parse HEAD` assertion all pass against a real installed checkout,
- Fase 5 onward (config, profiles, `.env`, systemd) does not regress.

## Issues

### CRITICAL
None found.

### WARNING
1. ~~**SHA guard only checks the first character is hex, not all 40**~~ — **RESOLVED post-verify**: the orchestrator replaced the `case`-based guard with `if ! [[ "$HERMES_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then ...; fi`, which validates all 40 characters. Confirmed live: a 40-char string with a non-hex tail is now correctly rejected, and the real pinned SHA is still correctly accepted. `bash -n` re-confirmed clean after the edit.
2. **Task 6.7 / post-install assertion is unverified end-to-end** (see dedicated section above) — do not treat this script as production-trusted until a real host run with sudo confirms Fase 3 onward.
3. **Shellcheck "no new findings" requirement is unverified in this verify session** as well as in apply (tool unavailable, no passwordless sudo in this sandbox both times). Apply's baseline-was-unparseable argument ("zero new findings is trivially satisfied since the baseline could not even parse") is logically sound but rests on a reconstructed baseline file, not a captured artifact — reasonable, not ironclad.

### SUGGESTION
1. Consider tightening the `case` guard to validate all 40 characters are `[0-9a-f]` (e.g. via a full pattern match) rather than only the first character, for a more precise error message and stricter enforcement of the design's stated invariant.
2. When shellcheck becomes available in CI/a real host, capture and commit its baseline+diff as durable evidence for this requirement, since both the apply and verify sessions here could only assert this indirectly.

## Final Verdict

**PASS WITH WARNINGS**

All spec requirements are code-confirmed except the shellcheck "no new findings" requirement, which is asserted but not independently re-verifiable in this sandbox (disclosed, non-blocking, low risk given the reconstructed-unparseable-baseline argument). The one disclosed design deviation (checksum-failure message wording) is justified by design.md's own internally conflicting sections and was correctly scoped to a single clause. The one disclosed test gap (task 6.7 end-to-end) is real, correctly flagged, and does not block merge of this diff, but **must be closed with a real end-to-end run on an actual host before this script is trusted to bootstrap a production primary node.**

No CRITICAL issues block archive.
