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

git_() {
  git -C "$TREE" -c safe.directory="$TREE" "$@"
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
  git clone --branch "$BRANCH" "$REMOTE" "$TREE"
  say "cloned $REMOTE -> $TREE"
else
  say "$TREE/.git already exists, not re-cloning"
fi
git_ config core.sharedRepository group
say "core.sharedRepository=group"

echo "Step 3: knowledge/ + pending/, move existing notes"
mkdir -p "$TREE/knowledge" "$TREE/pending"
say "$TREE/{knowledge,pending}"
# Only root-level *.md files that are not already inside knowledge/ or
# pending/ — re-running after a previous partial move must not fail on
# "no such file" for notes already moved.
shopt -s nullglob
root_notes=("$TREE"/*.md)
shopt -u nullglob
if [[ ${#root_notes[@]} -gt 0 ]]; then
  # git mv only — no id is ever re-minted, file names stay byte-for-byte,
  # so every intra-vault link keeps resolving (design.md step 3). One file
  # at a time, not a single `git mv -- *.md knowledge/`, so a re-run after
  # a partial previous move never fails on a name already present in
  # knowledge/.
  moved=0
  for path in "${root_notes[@]}"; do
    name="$(basename "$path")"
    if [[ -f "$TREE/knowledge/$name" ]]; then
      say "skip $name: already in knowledge/"
      continue
    fi
    git_ mv -- "$name" "knowledge/$name"
    moved=$((moved + 1))
  done
  if [[ "$moved" -gt 0 ]]; then
    git_ commit -q -m "Migrate: move $moved published note(s) into knowledge/"
  fi
  say "moved $moved note(s) into knowledge/"
else
  say "no root-level *.md left to move"
fi

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
  if [[ "$missing" -gt 0 ]]; then
    git_ commit -q -m "Migrate: copy $missing note(s) the mirror had not pushed yet"
    say "copied $missing note(s) not yet reflected in $TREE"
  fi
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
if git_ show-ref --verify --quiet "refs/heads/pending" || \
   git_ show-ref --verify --quiet "refs/remotes/origin/pending"; then
  pending_ref="pending"
  git_ show-ref --verify --quiet "refs/heads/pending" || pending_ref="origin/pending"
  reconciled=0
  while IFS= read -r name; do
    [[ "$name" == *.md ]] || continue
    [[ "$name" == "README.md" ]] && continue
    id="${name%.md}"
    # Already promoted, or already reconciled by a previous run of this
    # script — either way there is nothing left to do for this id.
    if [[ -f "$TREE/knowledge/$name" || -f "$TREE/pending/$name" ]]; then
      continue
    fi
    git_ show "$pending_ref:$name" > "$TREE/pending/$name"
    git_ add -- "pending/$name"
    reconciled=$((reconciled + 1))
    say "reconciled pending/$name (id $id) from the frozen pending branch"
  done < <(git_ ls-tree -r --name-only "$pending_ref" 2>/dev/null || true)
  if [[ "$reconciled" -gt 0 ]]; then
    git_ commit -q -m "Migrate: reconcile $reconciled note(s) from the frozen pending branch"
  fi
  say "$reconciled note(s) reconciled; refs/heads/pending left frozen, not deleted (D-11)"
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
