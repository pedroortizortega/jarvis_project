#!/usr/bin/env bash
# Build the memory-router image and import it into k3s' containerd.
#
# No registry exists for this project — same local-image workflow already
# used by model-panel/hermes-agent (docs/services/model-panel.md).
#
# The `k3s ctr images import` step needs root and a real TTY for the sudo
# password prompt, which an automated/non-interactive shell cannot supply.
# If sudo can't get a password non-interactively, this script prints the
# exact command and stops instead of hanging.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./00-config.sh

log "Building $MR_IMAGE_TAG from $MR_ROUTER_SRC_DIR"
docker build -t "$MR_IMAGE_TAG" "$MR_ROUTER_SRC_DIR"

log "Smoke-testing the image under prod-like constraints (non-root, read-only rootfs)"
CID=$(docker run -d --user 10001:10001 --read-only \
  -e MEMORY_ROUTER_HOST=0.0.0.0 -e MEMORY_ROUTER_PORT=8080 \
  -e MEMORY_ROUTER_JOURNAL_PATH=/data/journal.ndjson \
  --tmpfs /data --tmpfs /tmp \
  -p 18080:8080 "$MR_IMAGE_TAG")
sleep 2
CODE=$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/healthz || echo "000")
docker rm -f "$CID" >/dev/null
if [ "$CODE" != "200" ]; then
  log "Smoke test failed: /healthz returned $CODE (expected 200)"
  exit 1
fi
log "Smoke test OK (/healthz 200)"

log "Importing $MR_IMAGE_TAG into k3s containerd (needs sudo)"
if sudo -n true 2>/dev/null; then
  docker save "$MR_IMAGE_TAG" | sudo k3s ctr images import -
else
  cat >&2 <<EOF

[memory-router-bootstrap] sudo needs an interactive password here — run this
yourself, then re-run the rest of the bootstrap:

  docker save $MR_IMAGE_TAG | sudo k3s ctr images import -

EOF
  exit 2
fi

log "Verifying the image landed in containerd"
sudo k3s ctr images list | grep -q "$MR_IMAGE_TAG" \
  && log "OK: $MR_IMAGE_TAG present in containerd" \
  || { log "$MR_IMAGE_TAG NOT found in containerd import list"; exit 1; }
