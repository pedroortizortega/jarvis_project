# Exploration: fix-hermes-install-script

## Current State

`hermes-native/scripts/install-hermes.sh` implements the `role=primary`/`mode=bootstrap` path
of the installer contract in `specs/004_hermes_native_clone_systemd.md`. It currently has two
independent hard syntax errors that make the script fail to parse at all (`bash -n` fails),
plus a design contradiction in the vendoring logic. There is no `hermes-native/vendor/`
directory in the repo at all — spec 004's own "Trabajo pendiente" item 5 ("Vendorizar y revisar
el instalador oficial, y versionar su SHA-256") is still unaddressed.

## Affected Areas

- `hermes-native/scripts/install-hermes.sh:169-189` — the `cat >&2 <<EOF ... EOF` heredoc that
  should print a fail-closed error message ("no vendored installer, do it manually") has had an
  `else` branch, a `git clone` call, and two `echo` statements inserted **inside** the heredoc
  body. Bash treats heredoc content as literal text until the terminating `EOF`, so:
  - The inserted `else`, `git clone`, and `echo` lines are printed verbatim to stderr as
    documentation text, never executed.
  - The heredoc's own closing `EOF` on line 187 is followed by an orphan `exit 20` / `fi`
    (lines 188-189) that no longer matches a valid `if/then/fi` structure once the `else` got
    swallowed into the heredoc — this is the confirmed `bash -n` parse failure.
- `hermes-native/scripts/install-hermes.sh:174` — `git clone "https://github.com/NousResearch/hermes-agent.git -o $(dirname "$0")/../vendor"`:
  the URL and a `-o <path>` flag are both inside one quoted string, so even outside the heredoc
  this would be passed as a single malformed positional argument to `git clone`, not two tokens.
  Separately, `-o` is git's flag for naming the remote (`--origin`), **not** the destination
  directory — the destination is normally the second positional argument. The intended command
  is doubly wrong: wrong quoting *and* wrong flag semantics.
- `hermes-native/scripts/install-hermes.sh:164-189` vs. `specs/004_hermes_native_clone_systemd.md`
  ("Trabajo pendiente" item 5) — the script's own comment at line 164 says "vendored +
  checksum-pinned per spec 004," and the (correctly non-corrupted) heredoc text at lines 177-186
  explicitly restates spec 004's rationale: vendor the installer and pin its SHA-256 rather than
  fetching an unpinned upstream script at install time, because that reintroduces exactly the
  `curl|bash` supply-chain risk spec 004 was written to avoid. The inserted `git clone` (even if
  fixed) directly contradicts this documented intent by fetching `hermes-agent.git` unpinned at
  install time with no checksum step afterward.
- `hermes-native/scripts/install-hermes.sh:243` — `if [ -f "$SOURCE_CONFIG_DIR/AGENTS.md"]: then`
  has no space before `]` (`"AGENTS.md"]` is parsed as one invalid token) and a stray `:` before
  `then`. Confirmed second independent `bash -n` failure, unrelated to the heredoc issue. Line
  244's `install -m 644 ...` is also indented with 1 space instead of the file's 2-space
  convention (cosmetic only).
- `hermes-native/vendor/` — does not exist. Nothing is vendored yet, so even after fixing the
  syntax, the fail-closed branch (once repaired) will always trigger on a clean checkout today.
  That's correct/expected per spec 004's current state, not a bug — but "vendor something" is a
  real, separate prerequisite for anyone running this script successfully.

## Additional issues found (not in the original confirmed list)

1. **Missing post-install commit verification** — spec 004 Fase 3 explicitly requires
   `test "$(git -C "$HERMES_HOME/hermes-agent" rev-parse HEAD)" = "$HERMES_COMMIT"` after running
   the vendored installer. The script verifies `--version` and the venv binary path but never
   checks that the installed commit actually matches `$HERMES_COMMIT`. Spec-compliance gap, not a
   syntax bug.
2. **`HERMES_COMMIT` default is a tag, not a full SHA** — `HERMES_COMMIT="${HERMES_COMMIT:-v0.20.1}"`
   defaults to a tag. Spec 004 rule 8 requires a full Git SHA and states the reference version is
   `v0.19.0` (not `v0.20.1`). Flagged as a discrepancy for the proposal phase.
3. **Redundant path derivation** — line 217 re-derives
   `$SERVICE_HOME/.hermes/hermes-agent/venv/bin/hermes` instead of reusing `$HERMES_HOME` (they're
   equal today, not a bug, but duplicated-path pattern spec 004 rule 11 warns against). Low
   priority cleanup.

No other `bash -n`-level syntax errors were found elsewhere in the file (argument parsing, Fase
0/1/5/7/8/9/10 blocks, and the embedded `python3 - <<'PYEOF'` block all look structurally sound).

## Approaches considered

1. **Remove the corrupted insertion; restore original fail-closed behavior.** Delete the
   erroneously-inserted `else`/`git clone`/`echo` lines and keep the heredoc exactly as
   originally documented. Zero new automation surface, matches spec 004's documented rationale,
   smallest diff. Con: still fully manual vendoring. Effort: Low. **Recommended.**
2. **Add a separate one-time vendoring/update helper script** (e.g.
   `hermes-native/scripts/vendor-hermes-installer.sh`), matching planned sibling scripts already
   listed in spec 004. Clones/fetches the official installer, lets a human review it, then
   computes and writes `install-hermes.sh.sha256` (TOFU — trust-on-first-use, pin thereafter).
   Gives a documented, repeatable path to close "Trabajo pendiente" item 5. Con: new script,
   deserves its own OpenSpec delta rather than folding into this fix. Effort: Medium.
   **Recommended as a separate follow-up change, not part of this one.**
3. **Inline fetch-and-pin flow inside `install-hermes.sh`** (fix the `git clone` line and
   auto-generate `.sha256` right after cloning, run automatically when vendoring is missing).
   Fully automates the empty-repo case but directly contradicts the security intent already
   documented in the script's own error message and in spec 004 — reintroduces an unpinned,
   unreviewed fetch-and-execute step at install time. **Rejected.**

## Recommendation

Fix the two confirmed syntax errors as approach 1 (strip the corrupted insertion, restore the
original fail-closed heredoc; fix the `[ -f ... ]` spacing/stray colon at line 243) as the
immediate, low-risk `fix-hermes-install-script` change. Treat approach 2 (a dedicated vendoring
helper script) as a separate, follow-up OpenSpec change against spec 004's "Trabajo pendiente"
item 5. Explicitly reject approach 3 for this or any future change. Fold in the two low-risk
cleanups (post-install commit verification, `HERMES_COMMIT` default/spec-reference-version
discrepancy) into the same spec/tasks pass since they're small, spec-traceable gaps in the same
function area.

## Risks

- Fixing only the syntax without addressing "nothing is vendored" leaves the script
  non-functional end-to-end for a fresh checkout — expected/by-design (fail closed), but the
  proposal/tasks phase should make this explicit so it isn't read as "the install now works."
- If a future change picks approach 3 instead of 2, it silently regresses the security posture
  spec 004 established.
- `HERMES_COMMIT` default (`v0.20.1`) vs. spec 004's stated reference version (`v0.19.0`,
  requiring a full Git SHA) is a pre-existing inconsistency independent of this bug fix; scope
  creep risk if the fix pass tries to resolve this by guessing rather than confirming the correct
  commit with the user/spec owner.

## Ready for Proposal

Yes. Scope: (a) remove the corrupted heredoc insertion and restore the fail-closed
vendoring-missing message, (b) fix the `AGENTS.md` conditional syntax at line 243, (c) optionally
add the two spec-compliance gaps (commit verification, `HERMES_COMMIT` default reconciliation) as
stretch items, (d) explicitly scope out any new vendoring automation into a separate future
change.
