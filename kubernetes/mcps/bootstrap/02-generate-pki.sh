#!/usr/bin/env bash
# Generate memory-router's own PKI: root CA, server cert (SAN = MR_HOST),
# and one client cert per identity (CN = identity name exactly — required
# by identity.py's cn_to_identity resolution, see app.py::_load_role_map_from_env).
#
# memory-router NEVER reuses Engram's CA (kept isolated on purpose — see
# memory-router-tlsoption.yaml header). Idempotent: skips anything that
# already exists on disk.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./00-config.sh

mkdir -p "$MR_PKI_DIR/ca" "$MR_PKI_DIR/server" "$MR_PKI_DIR/clients"

if [ -f "$MR_PKI_DIR/ca/ca.crt" ]; then
  log "CA already exists at $MR_PKI_DIR/ca/ca.crt, skipping"
else
  log "Generating root CA"
  openssl genrsa -out "$MR_PKI_DIR/ca/ca.key" 4096
  openssl req -x509 -new -key "$MR_PKI_DIR/ca/ca.key" -sha256 -days 3650 \
    -out "$MR_PKI_DIR/ca/ca.crt" -subj "/CN=memory-router CA/O=jarvis_project"
fi

if [ -f "$MR_PKI_DIR/server/server.crt" ]; then
  log "Server cert already exists at $MR_PKI_DIR/server/server.crt, skipping"
else
  # SAN MUST be memory-router's own hostname, distinct from Engram's. Traefik
  # resolves the TLS server cert by SNI against ONE global store shared by
  # every entryPoint on the instance — sharing a hostname with engram-tailnet
  # means whichever cert wins the SNI slot gets served regardless of which
  # port/entryPoint the client connected to, silently breaking mTLS
  # isolation. Verified live 2026-08-21 (specs/014_memory_router.md §8).
  log "Generating server cert for $MR_HOST"
  openssl genrsa -out "$MR_PKI_DIR/server/server.key" 2048
  openssl req -new -key "$MR_PKI_DIR/server/server.key" \
    -out "$MR_PKI_DIR/server/server.csr" -subj "/CN=$MR_HOST/O=jarvis_project"
  printf 'subjectAltName=DNS:%s\n' "$MR_HOST" > "$MR_PKI_DIR/server/server.ext"
  openssl x509 -req -in "$MR_PKI_DIR/server/server.csr" \
    -CA "$MR_PKI_DIR/ca/ca.crt" -CAkey "$MR_PKI_DIR/ca/ca.key" -CAcreateserial \
    -out "$MR_PKI_DIR/server/server.crt" -days 730 -sha256 \
    -extfile "$MR_PKI_DIR/server/server.ext"
fi

for NAME in $MR_IDENTITIES; do
  if [ -f "$MR_PKI_DIR/clients/$NAME/$NAME.crt" ]; then
    log "Client cert for '$NAME' already exists, skipping"
    continue
  fi
  log "Generating client cert for identity '$NAME'"
  mkdir -p "$MR_PKI_DIR/clients/$NAME"
  openssl genrsa -out "$MR_PKI_DIR/clients/$NAME/$NAME.key" 2048
  openssl req -new -key "$MR_PKI_DIR/clients/$NAME/$NAME.key" \
    -out "$MR_PKI_DIR/clients/$NAME/$NAME.csr" -subj "/CN=$NAME/O=jarvis_project"
  printf 'extendedKeyUsage=clientAuth\n' > "$MR_PKI_DIR/clients/$NAME/$NAME.ext"
  openssl x509 -req -in "$MR_PKI_DIR/clients/$NAME/$NAME.csr" \
    -CA "$MR_PKI_DIR/ca/ca.crt" -CAkey "$MR_PKI_DIR/ca/ca.key" -CAcreateserial \
    -out "$MR_PKI_DIR/clients/$NAME/$NAME.crt" -days 730 -sha256 \
    -extfile "$MR_PKI_DIR/clients/$NAME/$NAME.ext"
done

log "PKI ready under $MR_PKI_DIR"
