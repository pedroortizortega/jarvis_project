#!/usr/bin/env bash
# install-hermes.sh — install a persistent Hermes gateway on a fresh host.
#
# Implements the `role=primary`, `mode=bootstrap` path of the installer
# contract defined in specs/004_hermes_native_clone_systemd.md: a brand-new
# host with no existing ~/.hermes state, installed as the one and only
# Telegram-bot-holding gateway.
#
# NOT IMPLEMENTED (spec 004 defines these; this script fails closed if you
# ask for them, rather than pretending to support them):
#   - mode=memory-seed / mode=primary-restore (state restore from a backup
#     archive — spec 004 Fase 4; too risky to ship untested against real
#     state, do this by hand following spec 004 until it's built and
#     verified)
#   - role=standby / role=worker (spec 004 Fase 10's non-primary paths)
#   - Fase 6 (custom skill sync) — hermes-native/skills/ doesn't exist yet
#     in this repo (spec 004's own "trabajo pendiente" item #2); nothing to
#     sync until it does
#   - Fase 7 (Codex profile reconciliation) IS implemented — see below
#
# This script never starts the gateway automatically (spec 004 rule 13).
# `--start` is accepted but requires you to type the literal word "yes" at
# an interactive confirmation after the fencing checklist prints, precisely
# because starting a second gateway against the same Telegram bot token
# breaks the running one (spec 004, "Un solo gateway por identidad de
# mensajeria").
#
# Usage:
#   install-hermes.sh --litellm-url <url> [--hermes-commit <sha>] \
#     [--user <user>] [--enable-browser] [--allow-insecure-http] [--start]
#
# Exit codes (spec 004):
#   0 ok  10 preflight  20 runtime-install  30 config  50 litellm
#   60 systemd-state  80 missing-secret

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

HERMES_USER="${HERMES_USER:-$(id -un)}"
HERMES_LITELLM_URL="${HERMES_LITELLM_URL:-}"
HERMES_COMMIT="${HERMES_COMMIT:-v0.20.1}"
ENABLE_BROWSER=0
ALLOW_INSECURE_HTTP=0
DO_START=0

usage() {
  cat >&2 <<'EOF'
Usage: install-hermes.sh --litellm-url <url> [options]

Required:
  --litellm-url <url>       LiteLLM endpoint, e.g. https://litellm.home.arpa/v1
                             (or http:// with --allow-insecure-http)

Options:
  --user <name>             Service user to install as (default: current user)
  --hermes-commit <sha>     Hermes version/tag to install (default: v0.20.1)
  --enable-browser          Install Playwright/Chromium (skipped by default)
  --allow-insecure-http     Permit an http:// --litellm-url (LAN-only transition)
  --start                   After fencing confirmation, enable+start the unit
                             (primary role only; asks for interactive "yes")
  -h, --help                Show this help
EOF
  exit 10
}

while [ $# -gt 0 ]; do
  case "$1" in
    --litellm-url) HERMES_LITELLM_URL="$2"; shift 2 ;;
    --user) HERMES_USER="$2"; shift 2 ;;
    --hermes-commit) HERMES_COMMIT="$2"; shift 2 ;;
    --enable-browser) ENABLE_BROWSER=1; shift ;;
    --allow-insecure-http) ALLOW_INSECURE_HTTP=1; shift ;;
    --start) DO_START=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

[ -n "$HERMES_LITELLM_URL" ] || { echo "--litellm-url is required" >&2; usage; }

case "$HERMES_LITELLM_URL" in
  https://*) : ;;
  http://*)
    [ "$ALLOW_INSECURE_HTTP" -eq 1 ] || {
      echo "error: --litellm-url is http:// — pass --allow-insecure-http" \
           "to accept this for a LAN-only transition (spec 004 preflight rule)" >&2
      exit 10
    }
    ;;
  *) echo "error: --litellm-url must start with http:// or https://" >&2; exit 10 ;;
esac

SERVICE_HOME="$(getent passwd "$HERMES_USER" | cut -d: -f6)"
[ -n "$SERVICE_HOME" ] && [ -d "$SERVICE_HOME" ] || {
  echo "error: cannot resolve home directory for user '$HERMES_USER'" >&2
  exit 10
}
HERMES_HOME="$SERVICE_HOME/.hermes"
HERMES_BIN="$SERVICE_HOME/.local/bin/hermes"

log() { printf '[install-hermes] %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# Fase 0 — Preflight (spec 004)
# ---------------------------------------------------------------------------

log "Fase 0: preflight"

[ "$(id -u)" -ne 0 ] || {
  echo "error: run as the service user ($HERMES_USER), not root — this" \
       "script uses sudo internally only for system packages and systemd" >&2
  exit 10
}

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|aarch64|arm64) : ;;
  *) echo "error: unsupported architecture: $ARCH" >&2; exit 10 ;;
esac
log "architecture: $ARCH"

command -v getent >/dev/null || { echo "error: getent not found" >&2; exit 10; }
getent hosts github.com >/dev/null || {
  echo "error: cannot resolve github.com — check DNS" >&2
  exit 10
}

if ! curl --fail --silent --show-error --max-time 10 \
    "${HERMES_LITELLM_URL%/v1}/health/readiness" >/dev/null 2>&1; then
  echo "warning: could not reach ${HERMES_LITELLM_URL%/v1}/health/readiness" \
       "— continuing, but Hermes will not be able to reach a model until" \
       "this is fixed (spec 004 Fase 0)" >&2
fi

avail_kib="$(df --output=avail "$SERVICE_HOME" | tail -1)"
if [ "$avail_kib" -lt $((4 * 1024 * 1024)) ]; then
  echo "error: less than 4 GiB free in $SERVICE_HOME (spec 004 Fase 0 minimum)" >&2
  exit 10
fi

log "preflight OK — arch=$ARCH litellm=$HERMES_LITELLM_URL user=$HERMES_USER home=$HERMES_HOME"

# ---------------------------------------------------------------------------
# Fase 1 — System dependencies (spec 004)
# ---------------------------------------------------------------------------

log "Fase 1: system dependencies"

if command -v pacman >/dev/null 2>&1; then
  sudo pacman -S --needed --noconfirm ca-certificates curl git jq unzip sqlite ffmpeg ripgrep
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl git jq unzip sqlite3 ffmpeg ripgrep
else
  echo "error: neither pacman nor apt-get found — install ca-certificates" \
       "curl git jq unzip sqlite3 ffmpeg ripgrep manually, then re-run" >&2
  exit 20
fi

# ---------------------------------------------------------------------------
# Fase 3 — Official installer (vendored + checksum-pinned per spec 004)
# ---------------------------------------------------------------------------

log "Fase 3: install Hermes runtime ($HERMES_COMMIT)"

VENDORED_INSTALLER="$(dirname "$0")/../vendor/install-hermes.sh"
if [ ! -f "$VENDORED_INSTALLER" ]; then
  cat >&2 <<EOF
error: no vendored installer at $VENDORED_INSTALLER

spec 004 requires vendoring the official Hermes installer and pinning its
SHA-256, rather than curl|bash'ing an upstream script whose content can
change out from under a pinned --commit. This repo does not yet vendor one
(see specs/004_hermes_native_clone_systemd.md, "Trabajo pendiente" item 5).

To proceed manually for now:
  1. Download the real installer from the Hermes project's own instructions.
  2. Save it to hermes-native/vendor/install-hermes.sh
  3. sha256sum hermes-native/vendor/install-hermes.sh   # record this
  4. Re-run this script.
EOF
  exit 20
fi

INSTALLER_SHA256_FILE="${VENDORED_INSTALLER}.sha256"
if [ -f "$INSTALLER_SHA256_FILE" ]; then
  ( cd "$(dirname "$VENDORED_INSTALLER")" && \
    sha256sum -c "$(basename "$INSTALLER_SHA256_FILE")" ) || {
    echo "error: vendored installer failed checksum verification" >&2
    exit 20
  }
else
  log "warning: no $INSTALLER_SHA256_FILE to verify against — proceeding" \
      "unverified (fix by recording one, see spec 004 Fase 3)"
fi

chmod 700 "$VENDORED_INSTALLER"

BROWSER_FLAG="--skip-browser"
[ "$ENABLE_BROWSER" -eq 1 ] && BROWSER_FLAG=""

# shellcheck disable=SC2086
bash "$VENDORED_INSTALLER" \
  --skip-setup \
  --non-interactive \
  $BROWSER_FLAG \
  --hermes-home "$HERMES_HOME" \
  --commit "$HERMES_COMMIT"

"$HERMES_BIN" --version >/dev/null || { echo "error: hermes CLI not usable after install" >&2; exit 20; }
test -x "$SERVICE_HOME/.hermes/hermes-agent/venv/bin/hermes" || {
  echo "error: expected venv binary missing after install" >&2
  exit 20
}
log "runtime installed: $("$HERMES_BIN" --version)"

# ---------------------------------------------------------------------------
# Fase 5 — Declarative config (spec 004; restore modes not implemented)
# ---------------------------------------------------------------------------

log "Fase 5: applying declarative config"

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SOURCE_CONFIG_DIR="$REPO_ROOT/kubernetes/hermes/config"

if [ -f "$HERMES_HOME/config.yaml" ]; then
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  backup_dir="$HERMES_HOME/backups-before-install"
  install -d -m 700 "$backup_dir"
  cp "$HERMES_HOME/config.yaml" "$backup_dir/config.yaml.$ts" 2>/dev/null || true
fi

if [ -f "$SOURCE_CONFIG_DIR/SOUL.md" ]; then
  install -m 644 "$SOURCE_CONFIG_DIR/SOUL.md" "$HERMES_HOME/SOUL.md"
fi

HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" config set model.base_url "$HERMES_LITELLM_URL" \
  || { echo "error: failed to set model.base_url" >&2; exit 30; }
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" config set terminal.backend local \
  || { echo "error: failed to set terminal.backend" >&2; exit 30; }

# ---------------------------------------------------------------------------
# Fase 7 — Codex profile reconciliation (the nine Luna/Terra/Sol profiles)
# ---------------------------------------------------------------------------

log "Fase 7: reconciling Codex profiles"

PROFILES_YAML="$REPO_ROOT/kubernetes/hermes/profiles/profiles.yaml"
if [ ! -f "$PROFILES_YAML" ]; then
  log "warning: $PROFILES_YAML not found — skipping profile reconciliation"
else
  python3 - "$PROFILES_YAML" "$HERMES_BIN" "$HERMES_HOME" <<'PYEOF'
import subprocess
import sys
import yaml

profiles_path, hermes_bin, hermes_home = sys.argv[1:4]
with open(profiles_path) as f:
    doc = yaml.safe_load(f)

provider = doc["provider"]
base_url = doc["base_url"]

existing = subprocess.run(
    [hermes_bin, "profile", "list", "--plain"],
    capture_output=True, text=True, env={"HERMES_HOME": hermes_home, "PATH": "/usr/bin:/bin"},
).stdout

for name, cfg in doc["profiles"].items():
    if name not in existing:
        subprocess.run(
            [hermes_bin, "profile", "create", name, "--clone-from", "default", "--no-alias"],
            check=True, env={"HERMES_HOME": hermes_home, "PATH": "/usr/bin:/bin"},
        )
    profile_home = f"{hermes_home}/profiles/{name}"
    for key, value in (
        ("model.provider", provider),
        ("model.base_url", base_url),
        ("model.default", cfg["model"]),
        ("terminal.backend", "local"),
    ):
        subprocess.run(
            [hermes_bin, "config", "set", key, str(value)],
            check=True, env={"HERMES_HOME": profile_home, "PATH": "/usr/bin:/bin"},
        )
    subprocess.run(
        [hermes_bin, "config", "set", "--force", "agent.reasoning_effort", cfg["reasoning_effort"]],
        check=True, env={"HERMES_HOME": profile_home, "PATH": "/usr/bin:/bin"},
    )
    print(f"reconciled profile: {name}", file=sys.stderr)
PYEOF
fi

# ---------------------------------------------------------------------------
# Fase 8 — .env (spec 004; never print secret values)
# ---------------------------------------------------------------------------

log "Fase 8: writing .env"

if [ ! -e "$HERMES_HOME/.env" ]; then
  install -m 600 /dev/null "$HERMES_HOME/.env"
  cat >> "$HERMES_HOME/.env" <<'EOF'
TERMINAL_ENV=local
# OPENAI_API_KEY=          # LiteLLM key or placeholder required by Hermes
# TELEGRAM_BOT_TOKEN=      # primary only — never copy this between hosts
# TELEGRAM_ALLOWED_USERS=  # comma-separated numeric IDs
# BRAVE_API_KEY=           # optional, rotate if ever exposed
EOF
  log ".env created at $HERMES_HOME/.env (0600) — fill in the commented" \
      "values by hand, this script never accepts secrets as arguments"
else
  chmod 600 "$HERMES_HOME/.env"
  log ".env already exists — left untouched (permissions re-applied to 0600)"
fi

# ---------------------------------------------------------------------------
# Fase 9 — Validation before touching systemd (spec 004)
# ---------------------------------------------------------------------------

log "Fase 9: pre-systemd validation"

HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" doctor \
  || { echo "error: hermes doctor failed" >&2; exit 30; }
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" config get model.base_url
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" config get terminal.backend
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" -z 'Responde exactamente: OK' \
  || { echo "error: one-shot smoke query failed — check $HERMES_LITELLM_URL" >&2; exit 50; }

log "Fase 9 OK"

# ---------------------------------------------------------------------------
# Fase 10 — systemd (always installed disabled; --start requires fencing)
# ---------------------------------------------------------------------------

log "Fase 10: installing systemd unit (disabled by default)"

install_output="$(sudo "$HERMES_BIN" gateway install \
  --system \
  --run-as-user "$HERMES_USER" \
  --no-start-now \
  --no-start-on-login)"
echo "$install_output" >&2

HERMES_UNIT="$(printf '%s\n' "$install_output" | grep -oE 'hermes[a-zA-Z0-9_.-]*\.service' | head -1)"
[ -n "$HERMES_UNIT" ] || {
  echo "error: could not determine the installed unit name from installer output" >&2
  exit 60
}
log "unit: $HERMES_UNIT"

sudo systemctl disable --now "$HERMES_UNIT" 2>/dev/null || true
[ "$(systemctl is-enabled "$HERMES_UNIT" 2>/dev/null || echo disabled)" = "disabled" ] || {
  echo "error: $HERMES_UNIT did not end up disabled" >&2
  exit 60
}
[ "$(systemctl is-active "$HERMES_UNIT" 2>/dev/null || echo inactive)" = "inactive" ] || {
  echo "error: $HERMES_UNIT did not end up inactive" >&2
  exit 60
}

if [ "$DO_START" -eq 1 ]; then
  cat >&2 <<EOF

Fencing checklist before starting a PRIMARY gateway (spec 004 Fase 10):
  1. Any Kubernetes hermes-agent-master Deployment is scaled to 0.
  2. No other systemd unit anywhere is holding this same Telegram bot token.
  3. You have confirmed THIS host is the one and only primary.
  4. Secrets in $HERMES_HOME/.env are filled in and validated.

Type exactly "yes" to enable and start $HERMES_UNIT now, anything else aborts:
EOF
  read -r confirmation
  if [ "$confirmation" = "yes" ]; then
    sudo systemctl enable --now "$HERMES_UNIT"
    log "$HERMES_UNIT enabled and started"
  else
    log "not confirmed — leaving $HERMES_UNIT disabled and inactive"
  fi
else
  log "--start not passed — $HERMES_UNIT installed, disabled, and inactive." \
      "Start it yourself once you've completed the fencing checklist in" \
      "specs/004_hermes_native_clone_systemd.md."
fi

log "done."
