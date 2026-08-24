#!/usr/bin/env bash
# Install the knowledge vault host services on the coordinator node.
#
# Idempotent: safe to re-run. It creates nothing that already exists and
# rewrites no note. It deliberately does NOT enable or start sync/promote —
# the design keeps them disabled until scripts/migrate-to-tree.sh has
# populated /opt/knowledge-vault/tree (see design.md "Migration / Rollout").
#
#   sudo ./scripts/install-host.sh [reviewer-username]
#
# Configurable via environment: KNOWLEDGE_VAULT_PROMOTE_INTERVAL (default
# 5min) sets how often knowledge-vault-promote.timer scans pending/ (D-04).
#
set -euo pipefail

PREFIX=/opt/knowledge-vault
STATE=/var/lib/knowledge-vault
GROUP=knowledge-vault
PROMOTE_USER=knowledge-vault-promote
SYNC_USER=knowledge-vault-sync
SEARCH_USER=knowledge-vault-search
PROMOTE_INTERVAL="${KNOWLEDGE_VAULT_PROMOTE_INTERVAL:-5min}"
REVIEWER="${1:-${SUDO_USER:-}}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "This script must run as root: sudo $0" >&2
  exit 1
fi

say() { printf '  %s\n' "$*"; }

echo "Accounts"
getent group "$GROUP" >/dev/null || groupadd --system "$GROUP"
say "group $GROUP"
# F-5: knowledge-vault-search was never created by this script, even before
# this change — the search unit failed to start until an operator noticed
# and created it by hand. All three system accounts are created here now.
for user in "$PROMOTE_USER" "$SYNC_USER" "$SEARCH_USER"; do
  getent passwd "$user" >/dev/null || useradd --system --no-create-home \
    --shell /usr/sbin/nologin --gid "$GROUP" "$user"
  say "user $user"
done

if [[ -n "$REVIEWER" ]]; then
  usermod -aG "$GROUP" "$REVIEWER"
  say "reviewer $REVIEWER added to $GROUP (re-login required for it to apply)"
else
  say "WARNING: no reviewer user given; nobody can write decisions yet"
fi

echo "Directories"
# The tree itself is created here so ownership/mode is right from the start;
# scripts/migrate-to-tree.sh clones the bare repo's contents into it (an
# empty, correctly-owned directory is a valid git-clone target). D-04:
# promote is the sole writer to knowledge/ (0750, promote-owned); pending/ is
# group-writable (2770) because JARVIS (this repo's own system user, outside
# this package's scope) and the human reviewer both write there.
install -d -o "$PROMOTE_USER" -g "$GROUP" -m 0750 "$PREFIX" "$PREFIX/tree"
install -d -o "$PROMOTE_USER" -g "$GROUP" -m 0750 "$PREFIX/tree/knowledge"
install -d -o "$PROMOTE_USER" -g "$GROUP" -m 2770 "$PREFIX/tree/pending"
# The tree root itself is 0750 (read-write only to promote:group, not
# world) and neither promote.service's nor sync.service's ReadWritePaths=
# lists the tree root — only .git/knowledge/pending/.vault.lock under it —
# so under ProtectSystem=strict the unit's own sandbox can never CREATE
# .vault.lock, only write to it once it exists. Created here, once, so
# layout.vault_lock()'s first touch() ever needed is a no-op instead of a
# PermissionError against a read-only sandboxed parent.
install -m 0660 -o "$PROMOTE_USER" -g "$GROUP" /dev/null "$PREFIX/tree/.vault.lock"
# Derived, disposable data any group member may rebuild while searching.
install -d -o "$PROMOTE_USER" -g "$GROUP" -m 2770 "$STATE/index"
# Reserved for future writer state; unused by promote/sync today (both take
# their lock at <tree>/.vault.lock, inside the tree itself — layout.vault_lock()).
install -d -o "$PROMOTE_USER" -g "$GROUP" -m 2770 "$STATE/state"
# Bare repository that Working Copy and other VPN clients clone over SSH.
install -d -o "$PROMOTE_USER" -g "$GROUP" -m 0750 /srv/git
if [[ ! -d /srv/git/knowledge-vault.git ]]; then
  git init --bare -q -b main /srv/git/knowledge-vault.git
fi
# git init leaves the repository world-readable. The parent directory already
# blocks traversal, but the repository must not depend on that to stay
# private. Two accounts write here — promote and sync — so new objects must
# stay writable by the group that owns both (F-6). This runs BEFORE the
# chown: git rewrites the config file rather than editing it, so under sudo
# it would leave a root-owned config that neither account reads.
git --git-dir=/srv/git/knowledge-vault.git \
  -c safe.directory=/srv/git/knowledge-vault.git \
  config core.sharedRepository group
chown -R "$PROMOTE_USER:$GROUP" /srv/git/knowledge-vault.git
chmod -R o=,g+rwX /srv/git/knowledge-vault.git
say "/srv/git/knowledge-vault.git"
say "$PREFIX/tree/{knowledge,pending}, $STATE/{index,state}"

echo "Package"
if [[ ! -x "$PREFIX/.venv/bin/python" ]]; then
  python3 -m venv "$PREFIX/.venv"
fi
"$PREFIX/.venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/.venv/bin/pip" install --quiet "$SOURCE_DIR"
chown -R "$PROMOTE_USER:$GROUP" "$PREFIX/.venv"
chmod -R g+rX "$PREFIX/.venv"
# Only the entry points pyproject.toml actually ships today
# ([project.scripts]) — publisher/review/review-sync/mirror are gone.
test -x "$PREFIX/.venv/bin/knowledge-vault-propose"
test -x "$PREFIX/.venv/bin/knowledge-vault-decide"
test -x "$PREFIX/.venv/bin/knowledge-vault-pending"
test -x "$PREFIX/.venv/bin/knowledge-vault-promote"
test -x "$PREFIX/.venv/bin/knowledge-vault-promote-check"
test -x "$PREFIX/.venv/bin/knowledge-vault-sync"
test -x "$PREFIX/.venv/bin/knowledge-vault-search"
test -x "$PREFIX/.venv/bin/knowledge-vault-search-serve"
say "entry points installed"

if [[ -n "$REVIEWER" && -d "/home/$REVIEWER/.hermes/skills" ]]; then
  install -d -o "$REVIEWER" -g "$REVIEWER" -m 0755 "/home/$REVIEWER/.hermes/skills/propose-note"
  install -o "$REVIEWER" -g "$REVIEWER" -m 0644 "$SOURCE_DIR/skills/propose-note/SKILL.md" \
    "/home/$REVIEWER/.hermes/skills/propose-note/SKILL.md"
  say "hermes skill propose-note installed for $REVIEWER"
fi

echo "Units"
install -m 0644 "$SOURCE_DIR"/systemd/*.service /etc/systemd/system/
# knowledge-vault-promote.timer is installed like every other timer, but its
# interval is then overridden by a drop-in below so the shipped file's
# hardcoded default never has to be the only word on it (design.md D-04:
# "genuinely configurable, not hardcoded").
install -m 0644 "$SOURCE_DIR"/systemd/*.timer /etc/systemd/system/
install -d -m 0755 /etc/systemd/system/knowledge-vault-promote.timer.d
cat > /etc/systemd/system/knowledge-vault-promote.timer.d/interval.conf <<EOF
# Generated by install-host.sh from \$KNOWLEDGE_VAULT_PROMOTE_INTERVAL. Re-run
# the installer with a different value (or hand-edit this file, or
# 'systemctl edit knowledge-vault-promote.timer') to change it.
[Timer]
OnUnitActiveSec=
OnUnitActiveSec=$PROMOTE_INTERVAL
EOF
say "promote interval: $PROMOTE_INTERVAL (KNOWLEDGE_VAULT_PROMOTE_INTERVAL=<value> sudo ./scripts/install-host.sh to change it)"
systemctl daemon-reload
# Never enabled from here: starting recurring work on someone's machine is
# their decision, not a script's. It would also be premature before
# scripts/migrate-to-tree.sh has populated the tree these units read/write.
say "units and timers installed, none enabled"

cat <<EOF

Installed. Nothing is enabled yet.

1. Migrate existing notes into the tree (safe to run once the bare repo at
   /srv/git/knowledge-vault.git exists; never touches the old flat vault):

     sudo -u $PROMOTE_USER $SOURCE_DIR/scripts/migrate-to-tree.sh

2. Enable the unattended units:

     sudo systemctl enable --now knowledge-vault-sync.timer knowledge-vault-promote.timer
     systemctl list-timers 'knowledge-vault-*'

Your approval stays manual by design: JARVIS may only write $PREFIX/tree/pending
(propose); only a human filling reviewer/decision/rationale there makes a
note eligible for the next promote run. Nothing JARVIS runs can promote a
note itself (D-04/D-13).

Run one cycle by hand. The CLIs read their paths from the environment, which
systemd normally supplies:

  # 1. propose a note (the agent would normally pipe rendered OKF markdown in).
  echo '---
type: fact
---
First real cycle.' | sudo -u $SYNC_USER \\
    KNOWLEDGE_VAULT_DIR=$PREFIX/tree \\
    $PREFIX/.venv/bin/knowledge-vault-propose

  # 2. see what is waiting
  sudo -u $SYNC_USER KNOWLEDGE_VAULT_DIR=$PREFIX/tree $PREFIX/.venv/bin/knowledge-vault-pending

  # 3. decide as a human (reason arrives on stdin, never as an argument)
  echo 'looks correct, matches the source' | sudo -u $SYNC_USER \\
    KNOWLEDGE_VAULT_DIR=$PREFIX/tree KNOWLEDGE_VAULT_REVIEWER=$REVIEWER \\
    $PREFIX/.venv/bin/knowledge-vault-decide <id> approved

  # 4. sync commits+pushes pending/, then wait for the promote timer (or run
  #    it once by hand) — no manual promote trigger exists by design (D-04).
  sudo systemctl start knowledge-vault-sync.service
  sudo systemctl start knowledge-vault-promote.service
  ls -l $PREFIX/tree/knowledge
EOF
