#!/usr/bin/env bash
# Create the 7 memory-router / hindsight / knowledge-vault secrets in
# $MR_NAMESPACE. None of these are
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

# 5) hindsight-tenant-key — the shared bearer between memory-router
# (HINDSIGHT_TOKEN) and the Hindsight pod itself
# (HINDSIGHT_API_TENANT_API_KEY). Generated once and cached under
# $MR_PKI_DIR/hindsight/ so re-runs reuse rather than rotate — same shape as
# the bearers/ loop above (D-10).
mkdir -p "$MR_PKI_DIR/hindsight"
HINDSIGHT_TENANT_KEY_FILE="$MR_PKI_DIR/hindsight/tenant-api-key"
if [ ! -f "$HINDSIGHT_TENANT_KEY_FILE" ]; then
  openssl rand -hex 32 > "$HINDSIGHT_TENANT_KEY_FILE"
  chmod 600 "$HINDSIGHT_TENANT_KEY_FILE"
fi
log "Creating hindsight-tenant-key"
kubectl -n "$MR_NAMESPACE" create secret generic hindsight-tenant-key \
  --from-literal=tenant-api-key="$(cat "$HINDSIGHT_TENANT_KEY_FILE")" \
  --dry-run=client -o yaml | apply_secret

# 6) hindsight-codex-shim-key — a deliberate mirror of llms/codex-shim-key's
# `internal-key`, duplicated (never regenerated) into mcps because k8s
# Secrets are namespace-scoped (design.md D-09/D-10, F-1). Copied from the
# source of truth on every run so the two copies stay in sync; aborts loudly
# if the source secret/key is missing rather than creating an empty one.
HINDSIGHT_CODEX_SHIM_KEY=$(kubectl -n llms get secret codex-shim-key \
  -o jsonpath='{.data.internal-key}' 2>/dev/null | base64 -d || true)
if [ -z "$HINDSIGHT_CODEX_SHIM_KEY" ]; then
  log "No internal-key found on llms/codex-shim-key — aborting"
  exit 1
fi
log "Creating hindsight-codex-shim-key (mirrored from llms/codex-shim-key)"
kubectl -n "$MR_NAMESPACE" create secret generic hindsight-codex-shim-key \
  --from-literal=internal-key="$HINDSIGHT_CODEX_SHIM_KEY" \
  --dry-run=client -o yaml | apply_secret

# 7) knowledge-vault-search-token — the shared bearer between memory-router
# (KNOWLEDGE_VAULT_TOKEN) and the knowledge-vault search bridge on trantor.
# design.md D-02/F-3: the host file is the single source of truth
# (written once by install-host.sh); this script only mirrors it into the
# Secret and never generates a token itself — aborts loudly if the host
# file is missing/unreadable/empty rather than creating an empty Secret.
: "${KV_SEARCH_TOKEN_FILE:=/etc/knowledge-vault/search-token}"
KV_SEARCH_TOKEN=$(cat "$KV_SEARCH_TOKEN_FILE" 2>/dev/null || true)
if [ -z "$KV_SEARCH_TOKEN" ]; then
  log "No token at $KV_SEARCH_TOKEN_FILE (run install-host.sh on trantor first) — aborting"
  exit 1
fi
log "Creating knowledge-vault-search-token (mirrored from $KV_SEARCH_TOKEN_FILE)"
kubectl -n "$MR_NAMESPACE" create secret generic knowledge-vault-search-token \
  --from-literal=search-token="$KV_SEARCH_TOKEN" \
  --dry-run=client -o yaml | apply_secret

log "All 7 secrets applied in namespace $MR_NAMESPACE"
