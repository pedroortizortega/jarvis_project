#!/usr/bin/env bash
# Create the 4 memory-router secrets in $MR_NAMESPACE. None of these are
# ever committed to the repo (kubernetes/mcps/memory-router-*.yaml only
# references them by name) — this script is the reproducible record of how
# they were built, not a store of their values.
#
# Idempotent via `kubectl ... --dry-run=client -o yaml | kubectl apply -f -`
# so re-running rotates the secret in place instead of failing on "already
# exists".
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./00-config.sh
source ./02-generate-pki.sh # ensure PKI exists before wiring secrets from it

apply_secret() {
  kubectl -n "$MR_NAMESPACE" apply -f - <<< "$(cat)"
}

# 1) memory-router-engram-credentials
# Reuses the existing shared Engram bootstrap bearer (spec 011 §"deuda de
# tokens por identidad" — all local Engram clients still share one bearer,
# known upstream limitation, not something to solve here). Override with
# MR_ENGRAM_TOKEN if bootstrapping where engram-cloud-config doesn't exist
# yet or you want a distinct token.
if [ -z "${MR_ENGRAM_TOKEN:-}" ]; then
  MR_ENGRAM_TOKEN=$(kubectl -n "$MR_NAMESPACE" get secret engram-cloud-config \
    -o jsonpath='{.data.ENGRAM_CLOUD_TOKEN}' 2>/dev/null | base64 -d || true)
fi
if [ -z "$MR_ENGRAM_TOKEN" ]; then
  log "No ENGRAM_CLOUD_TOKEN found (engram-cloud-config missing?) and MR_ENGRAM_TOKEN not set — aborting"
  exit 1
fi
log "Creating memory-router-engram-credentials"
kubectl -n "$MR_NAMESPACE" create secret generic memory-router-engram-credentials \
  --from-literal=ENGRAM_CLOUD_TOKEN="$MR_ENGRAM_TOKEN" \
  --dry-run=client -o yaml | apply_secret

# 2) memory-router-client-ca (Opaque, key `ca.crt` — same shape as the
# existing engram-client-ca, verified against the live cluster 2026-08-20)
log "Creating memory-router-client-ca"
kubectl -n "$MR_NAMESPACE" create secret generic memory-router-client-ca \
  --from-file=ca.crt="$MR_PKI_DIR/ca/ca.crt" \
  --dry-run=client -o yaml | apply_secret

# 3) memory-router-server-tls (kubernetes.io/tls — same shape as engram-server-tls)
log "Creating memory-router-server-tls"
kubectl -n "$MR_NAMESPACE" create secret tls memory-router-server-tls \
  --cert="$MR_PKI_DIR/server/server.crt" --key="$MR_PKI_DIR/server/server.key" \
  --dry-run=client -o yaml | apply_secret

# 4) memory-router-client-bearers — second factor on top of mTLS, checked by
# identity.py::resolve_identity with hmac.compare_digest. One random token
# per identity, generated once and cached under $MR_PKI_DIR/bearers/ so
# re-runs are idempotent (reused, not rotated) unless you delete that dir.
mkdir -p "$MR_PKI_DIR/bearers"
BEARER_ARGS=()
for NAME in $MR_IDENTITIES; do
  TOKEN_FILE="$MR_PKI_DIR/bearers/$NAME"
  if [ ! -f "$TOKEN_FILE" ]; then
    openssl rand -hex 32 > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
  fi
  BEARER_ARGS+=(--from-literal="$NAME=$(cat "$TOKEN_FILE")")
done
log "Creating memory-router-client-bearers (${MR_IDENTITIES})"
kubectl -n "$MR_NAMESPACE" create secret generic memory-router-client-bearers \
  "${BEARER_ARGS[@]}" \
  --dry-run=client -o yaml | apply_secret

log "All 4 secrets applied in namespace $MR_NAMESPACE"
