#!/usr/bin/env bash
# Registers memory-router-mcp as an MCP server in Claude Code, Codex, and
# OpenCode -- on this machine, or on a remote one reachable over SSH (e.g.
# a work laptop on the same LAN/tailnet as the memory-router deployment).
#
# Each harness gets its own mTLS identity from 02-generate-pki.sh, matched
# by name (pedro-claude-code -> claude, codex -> codex, opencode ->
# opencode) so identity.py's per-identity role map on the server side stays
# meaningful -- override with --identity only if you deliberately want a
# harness to authenticate as a different identity.
#
# Deliberately standalone (does NOT source 00-config.sh): a remote run only
# ships this one file + the PKI + the memory-router source, not the whole
# bootstrap dir, so it can't rely on this repo's directory layout existing
# on the far end. Defaults below mirror 00-config.sh's; override via env.
set -euo pipefail

: "${MR_HOST:=memory-router.trantor.tail07dff9.ts.net}"
: "${MR_ENTRYPOINT_PORT:=8444}"
: "${MR_PKI_DIR:=$HOME/.config/memory-router/pki}"

declare -A HARNESS_IDENTITY=(
  [claude]=pedro-claude-code
  [codex]=codex
  [opencode]=opencode
)

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Only meaningful locally (needed to `uv tool install` from source); a
# remote run gets this via staging instead, see below.
: "${MR_ROUTER_SRC_DIR:=$(cd "$SELF_DIR/../../../hermes-native/memory-router" 2>/dev/null && pwd || true)}"

log() { printf '[install-mcp-clients] %s\n' "$*" >&2; }

usage() {
  cat <<EOF
Usage: $0 [--harness claude|codex|opencode|all] [--identity <name>] [--host <ssh-target>] [--remote-path <path>]

  --harness       Which harness to register (default: all)
  --identity      Override the identity used (default per harness:
                  claude->pedro-claude-code, codex->codex, opencode->opencode)
                  -- only meaningful with a single --harness, must match an
                  existing client cert from 02-generate-pki.sh
  --host          Install on this SSH target instead of the local machine
                  (e.g. pedro@192.168.1.50) -- stages source + PKI over rsync
                  first, then re-runs this script there.
  --remote-path   Where to stage source+PKI on --host (default: ~/.config/memory-router)

Requires the target machine to resolve $MR_HOST -- if it's not on the same
tailnet, add "\$MR_LB_VIP $MR_HOST" to its /etc/hosts (see 06-verify.sh).
EOF
}

HARNESS=all
HOST=""
REMOTE_PATH='$HOME/.config/memory-router'
IDENTITY_OVERRIDE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --harness) HARNESS="$2"; shift 2 ;;
    --identity) IDENTITY_OVERRIDE="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --remote-path) REMOTE_PATH="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

case " claude codex opencode all " in
  *" $HARNESS "*) ;;
  *) echo "Unknown --harness '$HARNESS' (claude|codex|opencode|all)" >&2; exit 1 ;;
esac
if [ -n "$IDENTITY_OVERRIDE" ] && [ "$HARNESS" = all ]; then
  echo "--identity only applies with a single --harness, not 'all'" >&2; exit 1
fi

harnesses_to_run() {
  if [ "$HARNESS" = all ]; then echo claude codex opencode; else echo "$HARNESS"; fi
}

identity_for() {
  local h="$1"
  if [ -n "$IDENTITY_OVERRIDE" ]; then echo "$IDENTITY_OVERRIDE"; else echo "${HARNESS_IDENTITY[$h]}"; fi
}

require_identity_files() {
  local identity="$1"
  local client_dir="$MR_PKI_DIR/clients/$identity"
  for f in "$client_dir/$identity.crt" "$client_dir/$identity.key" "$MR_PKI_DIR/bearers/$identity" "$MR_PKI_DIR/ca/ca.crt"; do
    [ -f "$f" ] || { echo "Missing $f -- run 02-generate-pki.sh / 03-create-secrets.sh first (or stage them, see --host)" >&2; return 1; }
  done
}

# ---------------------------------------------------------------------------
# Remote mode: stage source + PKI (for every identity this run needs) on
# --host, then re-invoke ourselves there.
# ---------------------------------------------------------------------------
if [ -n "$HOST" ]; then
  [ -n "$MR_ROUTER_SRC_DIR" ] || { echo "Can't find hermes-native/memory-router locally to stage -- run from inside the repo" >&2; exit 1; }

  IDENTITIES=()
  for h in $(harnesses_to_run); do IDENTITIES+=("$(identity_for "$h")"); done
  for identity in "${IDENTITIES[@]}"; do require_identity_files "$identity" || exit 1; done

  log "Staging memory-router (source + PKI for: ${IDENTITIES[*]}) on $HOST:$REMOTE_PATH"
  ssh "$HOST" "mkdir -p '$REMOTE_PATH/pki/bearers' '$REMOTE_PATH/pki/ca' '$REMOTE_PATH/src/memory-router'"
  rsync -az --delete "$MR_ROUTER_SRC_DIR/" "$HOST:$REMOTE_PATH/src/memory-router/"
  rsync -az "$MR_PKI_DIR/ca/ca.crt" "$HOST:$REMOTE_PATH/pki/ca/ca.crt"
  for identity in "${IDENTITIES[@]}"; do
    ssh "$HOST" "mkdir -p '$REMOTE_PATH/pki/clients/$identity'"
    rsync -az "$MR_PKI_DIR/clients/$identity/" "$HOST:$REMOTE_PATH/pki/clients/$identity/"
    rsync -az "$MR_PKI_DIR/bearers/$identity" "$HOST:$REMOTE_PATH/pki/bearers/$identity"
  done
  scp -q "$SELF_DIR/$(basename "${BASH_SOURCE[0]}")" "$HOST:$REMOTE_PATH/install.sh"

  log "Running install remotely on $HOST"
  REMOTE_ARGS=(--harness "$HARNESS")
  [ -n "$IDENTITY_OVERRIDE" ] && REMOTE_ARGS+=(--identity "$IDENTITY_OVERRIDE")
  # shellcheck disable=SC2029  # expansion on the local side is intentional
  ssh "$HOST" "chmod +x '$REMOTE_PATH/install.sh' && \
    MR_HOST='$MR_HOST' MR_ENTRYPOINT_PORT='$MR_ENTRYPOINT_PORT' \
    MR_PKI_DIR='$REMOTE_PATH/pki' MR_ROUTER_SRC_DIR='$REMOTE_PATH/src/memory-router' \
    '$REMOTE_PATH/install.sh' ${REMOTE_ARGS[*]@Q}"

  cat >&2 <<EOF

[install-mcp-clients] Remote install on $HOST finished.
Before it actually connects: confirm $HOST resolves $MR_HOST. If it's not
joined to the same tailnet, run there:
  echo "\$MR_LB_VIP $MR_HOST" | sudo tee -a /etc/hosts
(same as 06-verify.sh step 2/4 -- get MR_LB_VIP from: kubectl -n kube-system get svc traefik)
EOF
  exit 0
fi

# ---------------------------------------------------------------------------
# Local mode
# ---------------------------------------------------------------------------
command -v uv >/dev/null 2>&1 || { echo "uv is required (https://docs.astral.sh/uv/)" >&2; exit 1; }
[ -n "${MR_ROUTER_SRC_DIR:-}" ] && [ -d "$MR_ROUTER_SRC_DIR" ] || { echo "MR_ROUTER_SRC_DIR ('${MR_ROUTER_SRC_DIR:-}') not found" >&2; exit 1; }

log "Installing memory-router-mcp from $MR_ROUTER_SRC_DIR via uv tool"
uv tool install --force --reinstall "$MR_ROUTER_SRC_DIR" >&2
MCP_BIN="$(command -v memory-router-mcp || true)"
if [ -z "$MCP_BIN" ]; then
  log "memory-router-mcp not on PATH yet -- run 'uv tool update-shell' and re-open your shell, then re-run this script"
  exit 1
fi
log "memory-router-mcp -> $MCP_BIN"

env_args_for() {
  # Prints NAME=VALUE lines for the given identity's env, one per line.
  local identity="$1"
  local client_dir="$MR_PKI_DIR/clients/$identity"
  cat <<EOF
MEMORY_ROUTER_URL=https://$MR_HOST:$MR_ENTRYPOINT_PORT
MEMORY_ROUTER_CLIENT_CN=$identity
MEMORY_ROUTER_CLIENT_BEARER=$(cat "$MR_PKI_DIR/bearers/$identity")
MEMORY_ROUTER_CLIENT_CERT=$client_dir/$identity.crt
MEMORY_ROUTER_CLIENT_KEY=$client_dir/$identity.key
MEMORY_ROUTER_CA_CERT=$MR_PKI_DIR/ca/ca.crt
EOF
}

install_claude() {
  local identity="$1"
  command -v claude >/dev/null 2>&1 || { log "claude CLI not found, skipping"; return; }
  claude mcp remove memory-router -s user >/dev/null 2>&1 || true
  local env_args=()
  while IFS= read -r kv; do env_args+=(-e "$kv"); done < <(env_args_for "$identity")
  claude mcp add memory-router -s user "${env_args[@]}" -- "$MCP_BIN"
  log "Registered in Claude Code (scope: user, identity: $identity)"
}

install_codex() {
  local identity="$1"
  command -v codex >/dev/null 2>&1 || { log "codex CLI not found, skipping"; return; }
  codex mcp remove memory-router >/dev/null 2>&1 || true
  local env_args=()
  while IFS= read -r kv; do env_args+=(--env "$kv"); done < <(env_args_for "$identity")
  codex mcp add memory-router "${env_args[@]}" -- "$MCP_BIN"
  log "Registered in Codex (identity: $identity)"
}

install_opencode() {
  local identity="$1"
  command -v jq >/dev/null 2>&1 || { log "jq not found, skipping opencode (needed to edit opencode.json safely)"; return; }
  local cfg="$HOME/.config/opencode/opencode.json"
  [ -f "$cfg" ] || { mkdir -p "$(dirname "$cfg")"; echo '{}' > "$cfg"; }
  local env_json='{}'
  while IFS='=' read -r k v; do
    env_json=$(jq --arg k "$k" --arg v "$v" '. + {($k): $v}' <<< "$env_json")
  done < <(env_args_for "$identity")
  local tmp
  tmp=$(mktemp)
  jq --arg bin "$MCP_BIN" --argjson env "$env_json" \
    '.mcp = (.mcp // {}) | .mcp["memory-router"] = {command: [$bin], enabled: true, type: "local", environment: $env}' \
    "$cfg" > "$tmp" && mv "$tmp" "$cfg"
  log "Registered in OpenCode ($cfg, identity: $identity)"
}

for h in $(harnesses_to_run); do
  identity="$(identity_for "$h")"
  require_identity_files "$identity" || exit 1
  case "$h" in
    claude) install_claude "$identity" ;;
    codex) install_codex "$identity" ;;
    opencode) install_opencode "$identity" ;;
  esac
done

log "Done."
