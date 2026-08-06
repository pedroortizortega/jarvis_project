#!/usr/bin/env bash
# Install the knowledge vault host services on the coordinator node.
#
# Idempotent: safe to re-run. It creates nothing that already exists and
# rewrites no note. It deliberately does NOT enable or start the publisher —
# the design keeps it disabled until a reviewed test proposal publishes
# correctly (see specs/004_hermes_native_clone_systemd.md).
#
#   sudo ./scripts/install-host.sh [reviewer-username]
#
set -euo pipefail

PREFIX=/opt/knowledge-vault
STATE=/var/lib/knowledge-vault
GROUP=knowledge-vault
PUBLISHER_USER=knowledge-vault-publisher
MIRROR_USER=knowledge-vault-mirror
REVIEW_USER=knowledge-vault-review
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
for user in "$PUBLISHER_USER" "$REVIEW_USER"; do
  getent passwd "$user" >/dev/null || useradd --system --no-create-home \
    --shell /usr/sbin/nologin --gid "$GROUP" "$user"
  say "user $user"
done

# The mirror user is reachable over SSH so VPN clients can clone, so it gets a
# home for authorized_keys and git-shell instead of nologin: git commands work,
# an interactive shell does not.
getent passwd "$MIRROR_USER" >/dev/null || useradd --system \
  --home-dir "$STATE/mirror" --shell /usr/bin/git-shell --gid "$GROUP" "$MIRROR_USER"
usermod --home "$STATE/mirror" --shell /usr/bin/git-shell "$MIRROR_USER"
say "user $MIRROR_USER (git-shell over SSH)"

if [[ -n "$REVIEWER" ]]; then
  usermod -aG "$GROUP" "$REVIEWER"
  say "reviewer $REVIEWER added to $GROUP (re-login required for it to apply)"
else
  say "WARNING: no reviewer user given; nobody can write decisions yet"
fi

echo "Directories"
install -d -o "$PUBLISHER_USER" -g "$GROUP" -m 0750 "$PREFIX" "$PREFIX/vault"
install -d -o "$PUBLISHER_USER" -g "$GROUP" -m 0700 "$STATE/publisher"
install -d -o "$REVIEW_USER" -g "$GROUP" -m 0750 "$STATE/approved" "$STATE/decisions"
# Agents write proposals here, so the group may write. A proposal carries no
# authority: it still needs a recorded human approval to reach the vault.
install -d -o "$REVIEW_USER" -g "$GROUP" -m 2770 "$STATE/proposals"
# Derived, disposable data that any group member may rebuild while searching.
install -d -o "$REVIEW_USER" -g "$GROUP" -m 2770 "$STATE/index"
# Only pending is group-writable: the human edits the projected file in place.
install -d -o "$REVIEW_USER" -g "$GROUP" -m 2770 "$STATE/pending"
# The mirror serves private-network clients: vault read-only, its own dir.
install -d -o "$MIRROR_USER" -g "$GROUP" -m 0750 "$STATE/mirror"
# SSH refuses a home, a .ssh or an authorized_keys that the group can write,
# so these stay owner-only even though the group owns the rest.
install -d -o "$MIRROR_USER" -g "$GROUP" -m 0700 "$STATE/mirror/.ssh"
# Never truncate: a re-run must not discard keys the operator already added.
AUTHORIZED_KEYS="$STATE/mirror/.ssh/authorized_keys"
[[ -e "$AUTHORIZED_KEYS" ]] || : > "$AUTHORIZED_KEYS"
chown "$MIRROR_USER:$GROUP" "$AUTHORIZED_KEYS"
chmod 0600 "$AUTHORIZED_KEYS"
# Bare repository that Working Copy and other VPN clients clone over SSH.
install -d -o "$MIRROR_USER" -g "$GROUP" -m 0750 /srv/git
if [[ ! -d /srv/git/knowledge-vault.git ]]; then
  git init --bare -q -b main /srv/git/knowledge-vault.git
fi
# git init leaves the repository world-readable. The parent directory already
# blocks traversal, but the repository must not depend on that to stay private.
chown -R "$MIRROR_USER:$GROUP" /srv/git/knowledge-vault.git
chmod -R o= /srv/git/knowledge-vault.git
say "/srv/git/knowledge-vault.git"
say "$PREFIX/vault, $STATE/{proposals,pending,decisions,approved,publisher}"

echo "Package"
if [[ ! -x "$PREFIX/.venv/bin/python" ]]; then
  python3 -m venv "$PREFIX/.venv"
fi
"$PREFIX/.venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/.venv/bin/pip" install --quiet "$SOURCE_DIR"
chown -R "$PUBLISHER_USER:$GROUP" "$PREFIX/.venv"
chmod -R g+rX "$PREFIX/.venv"
install -m 0750 -o "$REVIEW_USER" -g "$GROUP" "$SOURCE_DIR/scripts/approve_locally.py" "$PREFIX/approve_locally.py"
test -x "$PREFIX/.venv/bin/knowledge-vault-review"
test -x "$PREFIX/.venv/bin/knowledge-vault-publisher"
test -x "$PREFIX/.venv/bin/knowledge-vault-mirror"
test -x "$PREFIX/.venv/bin/knowledge-vault-propose"
test -x "$PREFIX/.venv/bin/knowledge-vault-search"
say "entry points installed"

if [[ -n "$REVIEWER" && -d "/home/$REVIEWER/.hermes/skills" ]]; then
  install -d -o "$REVIEWER" -g "$REVIEWER" -m 0755 "/home/$REVIEWER/.hermes/skills/propose-note"
  install -o "$REVIEWER" -g "$REVIEWER" -m 0644 "$SOURCE_DIR/skills/propose-note/SKILL.md" \
    "/home/$REVIEWER/.hermes/skills/propose-note/SKILL.md"
  say "hermes skill propose-note installed for $REVIEWER"
fi

echo "Units"
install -m 0644 "$SOURCE_DIR/systemd/knowledge-vault-review.service" /etc/systemd/system/
install -m 0644 "$SOURCE_DIR/systemd/knowledge-vault-publisher.service" /etc/systemd/system/
install -m 0644 "$SOURCE_DIR/systemd/knowledge-vault-mirror.service" /etc/systemd/system/
systemctl daemon-reload
say "installed, both left disabled on purpose"

cat <<EOF

Installed. Neither unit is enabled: the publisher stays off until a reviewed
test proposal publishes correctly.

Run one cycle by hand. The runners read their paths from the environment, which
systemd normally supplies:

  # 1. queue a test proposal (the control plane would normally do this).
  #    Written as a single -c so it works in bash, zsh and fish alike.
  sudo -u $REVIEW_USER $PREFIX/.venv/bin/python -c 'import json, pathlib; from knowledge_vault.models import Proposal; p = Proposal.create("# Test note\\nFirst real cycle.", "first-cycle", {"agent": "manual"}); pathlib.Path("$STATE/proposals/test.json").write_text(json.dumps({"proposal": p.__dict__})); print(p.id)'

  # 2. project it for review
  sudo -u $REVIEW_USER \\
    KNOWLEDGE_VAULT_PROPOSAL_SPOOL=$STATE/proposals \\
    KNOWLEDGE_VAULT_PENDING_DIR=$STATE/pending \\
    KNOWLEDGE_VAULT_DECISIONS_DIR=$STATE/decisions \\
    $PREFIX/.venv/bin/knowledge-vault-review

  # 3. decide as a human, then project again to export the decision
  \$EDITOR $STATE/pending/<id>.md    # add reviewer:, decision:, rationale:
  # ...repeat step 2...

  # 4. join proposal and decision (stand-in for the missing control plane).
  #    Runs as $REVIEW_USER, which owns the approved spool; the publisher only
  #    ever reads it.
  sudo -u $REVIEW_USER $PREFIX/.venv/bin/python $PREFIX/approve_locally.py \\
    $STATE/proposals $STATE/decisions $STATE/approved

  # 5. publish, then look at the vault
  sudo -u $PUBLISHER_USER \\
    KNOWLEDGE_VAULT_DIR=$PREFIX/vault \\
    KNOWLEDGE_VAULT_STATE_DIR=$STATE/publisher \\
    KNOWLEDGE_VAULT_APPROVED_DIR=$STATE/approved \\
    $PREFIX/.venv/bin/knowledge-vault-publisher
  ls -l $PREFIX/vault
EOF
