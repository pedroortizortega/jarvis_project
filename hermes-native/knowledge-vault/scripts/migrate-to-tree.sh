#!/usr/bin/env bash
# One-time (idempotent) migration from the old flat vault + JSON staging
# pipeline to the single-branch tree of pending/ + knowledge/.
#
# Follows design.md's "Migration / Rollout" section step for step. Never
# deletes the old flat vault (/opt/knowledge-vault/vault) or the old local
# staging directories (/var/lib/knowledge-vault/{proposals,pending,decisions,
# approved,publisher,review,mirror}) — that cleanup is an explicit follow-up,
# out of scope here, so a rollback never loses a note (F-8/D-12).
#
# Safe to re-run: every step is a no-op if its work is already done.
#
#   sudo -u knowledge-vault-promote ./scripts/migrate-to-tree.sh
#
set -euo pipefail

OLD_VAULT="${KNOWLEDGE_VAULT_OLD_VAULT_DIR:-/opt/knowledge-vault/vault}"
TREE="${KNOWLEDGE_VAULT_DIR:-/opt/knowledge-vault/tree}"
REMOTE="${KNOWLEDGE_VAULT_REMOTE:-/srv/git/knowledge-vault.git}"
BRANCH="${KNOWLEDGE_VAULT_BRANCH:-main}"

say() { printf '  %s\n' "$*"; }

# Same identity layout.GIT_IDENTITY sets for promote.py/sync.py's commits —
# the promote system account has no HOME (useradd --no-create-home,
# install-host.sh) and so no git config of its own; without an explicit
# identity every commit in this script fails with "Please tell me who you
# are" the first time it's run.
git_() {
  GIT_AUTHOR_NAME=knowledge-vault GIT_AUTHOR_EMAIL=knowledge-vault@localhost \
  GIT_COMMITTER_NAME=knowledge-vault GIT_COMMITTER_EMAIL=knowledge-vault@localhost \
  GIT_TERMINAL_PROMPT=0 \
  git -C "$TREE" -c safe.directory="$TREE" "$@"
}

# Steps 3/4/5 each call this after their own work, so an interrupted prior
# run's staged-but-uncommitted content (git mv/add succeeded, the commit
# right after it didn't) gets picked up on the next run instead of sitting
# invisible forever — see Step 3's comment for how this was actually found
# live. One helper, not three copies, so a future fix to this logic can't
# land in two call sites and miss the third.
#
# Known, accepted edge case: if a run crashes between `git_ add` and this
# commit in one step, and a LATER run resumes at an EARLIER step, that
# earlier step's commit message describes its own step, not the leftover
# content it happens to also commit — the content is never lost or
# mislabeled as the wrong TYPE of change, only attributed to the wrong
# step name in a rare compound-failure ordering. Not engineered around: it
# would cost real complexity (per-step staging areas) for a narrow window
# this repo's own single-writer-at-a-time model (vault_lock in promote.py/
# sync.py; this script runs standalone, before either is enabled) makes
# unlikely to ever actually hit.
commit_staged() {
  git_ diff --cached --quiet || git_ commit -q -m "$1"
}

echo "Step 1: old timers"
# The old units (publisher/review/review-sync/approve/mirror) were removed
# from this package by this same change (systemd/*.service, *.timer). On a
# host still running an older install, disable them by hand before running
# this script — nothing here can safely do it for you, since a partially
# upgraded package tree may not even carry those unit files any more.
say "skipped: disable any knowledge-vault-{review,review-sync,approve,publisher,mirror}.timer by hand first"

echo "Step 2: clone the tree"
if [[ ! -d "$TREE/.git" ]]; then
  # `git clone` refuses a non-empty target directory — and $TREE is never
  # empty by the time this script runs: install-host.sh already created
  # knowledge/, pending/ and .vault.lock in it (deliberately, so those
  # exist with correct ownership/mode before anything else touches the
  # tree). init + fetch + reset --hard populates the working tree from the
  # remote's history the same way `git clone` would, without requiring an
  # empty starting directory; a hard reset only ever touches tracked
  # paths, so the pre-existing untracked knowledge/pending/.vault.lock
  # survive it untouched. Same pattern sync.py's GitSync._ensure_repo()
  # already uses in code, mirrored here in bash.
  mkdir -p "$TREE"
  git_ init -q -b "$BRANCH"
  git_ remote add origin "$REMOTE"
  git_ fetch -q origin "$BRANCH"
  git_ reset -q --hard "origin/$BRANCH"
  say "initialized $TREE from $REMOTE (clone would have refused the non-empty target)"
else
  say "$TREE/.git already exists, not re-initializing"
fi
git_ config core.sharedRepository group
say "core.sharedRepository=group"
# Belt-and-suspenders for a host where install-host.sh ran before this file
# existed there: promote/sync's systemd sandbox can write TO .vault.lock but
# not CREATE it (their ReadWritePaths= never lists the tree root itself), so
# layout.vault_lock()'s first touch() would otherwise fail. Idempotent.
[[ -f "$TREE/.vault.lock" ]] || : > "$TREE/.vault.lock"

echo "Step 3: knowledge/ + pending/, move existing notes"
mkdir -p "$TREE/knowledge" "$TREE/pending"
say "$TREE/{knowledge,pending}"
# Only root-level *.md files that are not already inside knowledge/ or
# pending/ — re-running after a previous partial move must not fail on
# "no such file" for notes already moved.
shopt -s nullglob
root_notes=("$TREE"/*.md)
shopt -u nullglob
moved=0
if [[ ${#root_notes[@]} -gt 0 ]]; then
  # git mv only — no id is ever re-minted, file names stay byte-for-byte,
  # so every intra-vault link keeps resolving (design.md step 3). One file
  # at a time, not a single `git mv -- *.md knowledge/`, so a re-run after
  # a partial previous move never fails on a name already present in
  # knowledge/.
  for path in "${root_notes[@]}"; do
    name="$(basename "$path")"
    if [[ -f "$TREE/knowledge/$name" ]]; then
      say "skip $name: already in knowledge/"
      continue
    fi
    git_ mv -- "$name" "knowledge/$name"
    moved=$((moved + 1))
  done
  say "moved $moved note(s) into knowledge/"
else
  say "no root-level *.md left to move"
fi
# `git mv` and `git commit` are two separate steps (not atomic): a run that
# gets interrupted between them — or, as found live on trantor, one whose
# commit failed for an unrelated reason (no git identity, before the fix
# in this same file) — leaves a rename staged but never committed. Because
# root_notes above only globs the *working tree*, a later run sees the
# file already physically in knowledge/ and reports "nothing to move",
# silently leaving that staged rename uncommitted forever. Committing
# whatever is staged here, unconditionally (not gated on $moved > 0 from
# *this* run), picks up exactly that resumed case as well as the normal
# one.
commit_staged "Migrate: move $moved published note(s) into knowledge/ (includes any staged from an interrupted prior run)"

echo "Step 4: drift check against the old flat vault"
if [[ -d "$OLD_VAULT" ]]; then
  shopt -s nullglob
  old_notes=("$OLD_VAULT"/*.md)
  shopt -u nullglob
  missing=0
  for path in "${old_notes[@]}"; do
    name="$(basename "$path")"
    if [[ ! -f "$TREE/knowledge/$name" ]]; then
      cp -- "$path" "$TREE/knowledge/$name"
      git_ add -- "knowledge/$name"
      missing=$((missing + 1))
    fi
  done
  say "copied $missing note(s) not yet reflected in $TREE"
  commit_staged "Migrate: copy $missing note(s) the mirror had not pushed yet (includes any staged from an interrupted prior run)"
  # Gate: every note the old flat vault has must now exist, byte-identical,
  # in knowledge/ — "$TREE/knowledge" may contain more (subfolders, notes
  # reconciled from the pending branch in step 5), so this is a one-way,
  # per-file comparison, not a symmetric directory diff (design.md step 4:
  # "modulo subfolders").
  failed=0
  for path in "${old_notes[@]}"; do
    name="$(basename "$path")"
    if ! diff -q "$path" "$TREE/knowledge/$name" >&2; then
      failed=1
    fi
  done
  if [[ "$failed" -ne 0 ]]; then
    echo "drift check failed: $OLD_VAULT and $TREE/knowledge disagree on content" >&2
    exit 1
  fi
  say "drift check passed: $OLD_VAULT is a byte-identical subset of $TREE/knowledge"
else
  say "no old flat vault at $OLD_VAULT, nothing to diff"
fi

echo "Step 5: reconcile the frozen pending branch"
# The pending branch is a read-then-copy, not a merge — it is left frozen,
# never deleted (D-11). Nothing writes it after review-sync retired (D-01).
#
# Step 2 only ever fetches $BRANCH (main) — a local clone that has never
# been told about the remote's `pending` branch has no
# refs/remotes/origin/pending to check, so the existence test below would
# always report "nothing to reconcile" even when the remote genuinely has
# one. Fetch it explicitly here, tolerating its absence (a remote that was
# never used for phone review, or one already past D-11 cleanup, has none).
git_ fetch -q origin pending 2>/dev/null || true
if git_ show-ref --verify --quiet "refs/heads/pending" || \
   git_ show-ref --verify --quiet "refs/remotes/origin/pending"; then
  pending_ref="pending"
  git_ show-ref --verify --quiet "refs/heads/pending" || pending_ref="origin/pending"
  reconciled=0
  # promote.py's NOTE_ID only ever accepts 14 digits (design.md Threat
  # Matrix: "Path traversal via argv") — but the old JSON-pipeline's
  # proposal_id was a UUID. A note reconciled under its original filename
  # would sit in pending/ forever: promote_all() catches its NOTE_ID
  # rejection as PromotionRefused and silently skips it every cycle,
  # indistinguishable from "not reviewed yet" — found live when a UUID-id
  # note that already had reviewer/decision/rationale filled in still
  # never promoted. Mint a compliant id for any name that isn't already
  # 14 digits; the note is still unpublished (never lived in knowledge/,
  # nothing links to it yet), so renaming it here is safe, unlike Step 3's
  # git mv of already-published notes which must never re-mint an id.
  next_epoch="$(date -u +%s)"
  mint_compliant_id() {
    local candidate
    while :; do
      candidate="$(date -u -d "@$next_epoch" +%Y%m%d%H%M%S)"
      next_epoch=$((next_epoch + 1))
      [[ -f "$TREE/knowledge/$candidate.md" || -f "$TREE/pending/$candidate.md" ]] || break
    done
    printf '%s' "$candidate"
  }
  while IFS= read -r name; do
    [[ "$name" == *.md ]] || continue
    [[ "$name" == "README.md" ]] && continue
    id="${name%.md}"
    # Already reconciled by a previous run: either still under its
    # original name (a compliant id needs no renaming and was matched by
    # the check below in an earlier iteration/run), or already carrying
    # a legacy_proposal_id marker recording this id was renamed once
    # already — either way, nothing left to do.
    if [[ -f "$TREE/knowledge/$name" || -f "$TREE/pending/$name" ]] || \
       grep -qlr "^legacy_proposal_id: $id\$" "$TREE/pending" "$TREE/knowledge" 2>/dev/null; then
      continue
    fi
    target_name="$name"
    content="$(git_ show "$pending_ref:$name")"
    if [[ ! "$id" =~ ^[0-9]{14}$ ]]; then
      new_id="$(mint_compliant_id)"
      target_name="$new_id.md"
      # proposal_id is always the first frontmatter line (note.render()'s
      # FIELD_ORDER) — swap it for the new id and record the old one so a
      # future run recognizes this id was already reconciled (see the
      # skip check above), and so the audit trail keeps the origin
      # traceable rather than silently discarding it. `0,/re/{...}` (GNU
      # sed) applies the substitution only to the FIRST matching line, in
      # case a hostile or malformed note repeats the key deeper in the
      # body.
      content="$(printf '%s\n' "$content" | sed "0,/^proposal_id: /{s/^proposal_id: .*/proposal_id: $new_id\nlegacy_proposal_id: $id/}")"
      say "renamed pending/$name -> pending/$target_name (legacy UUID id, promote.py only accepts 14 digits)"
    fi
    printf '%s\n' "$content" > "$TREE/pending/$target_name"
    git_ add -- "pending/$target_name"
    reconciled=$((reconciled + 1))
    say "reconciled pending/$target_name (id $id) from the frozen pending branch"
  done < <(git_ ls-tree -r --name-only "$pending_ref" 2>/dev/null || true)
  say "$reconciled note(s) reconciled; refs/heads/pending left frozen, not deleted (D-11)"
  commit_staged "Migrate: reconcile $reconciled note(s) from the frozen pending branch (includes any staged from an interrupted prior run)"
else
  say "no pending branch on the remote, nothing to reconcile"
fi

echo "Step 6: push"
git_ push "$REMOTE" "$BRANCH"
say "pushed $BRANCH -> $REMOTE"

cat <<EOF

Migration complete.

Next (manual, per design.md steps 7-8):
  sudo systemctl enable --now knowledge-vault-sync.timer knowledge-vault-promote.timer
  # run one propose -> decide cycle by hand, wait one
  # KNOWLEDGE_VAULT_PROMOTE_INTERVAL, confirm the note landed in
  # $TREE/knowledge and in a fresh clone without any manual promote trigger.

Cleanup of $OLD_VAULT and the old local staging directories is an explicit
follow-up, not part of this script.
EOF
