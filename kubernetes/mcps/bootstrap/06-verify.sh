#!/usr/bin/env bash
# End-to-end verification, mirroring exactly what was run by hand and
# recorded in specs/014_memory_router.md §8-9 (2026-08-21):
#   1. internal /healthz via port-forward
#   2. /etc/hosts check for MR_HOST (can't be automated — needs sudo)
#   3. one positive mTLS+bearer request per identity
#   4. one negative control (no client cert -> must be rejected at TLS level)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./00-config.sh

log "1/4: internal /healthz via port-forward"
kubectl -n "$MR_NAMESPACE" port-forward svc/memory-router 18080:8080 >/dev/null 2>&1 &
PF_PID=$!
trap 'kill $PF_PID 2>/dev/null || true' EXIT
sleep 2
CODE=$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/healthz || echo "000")
kill "$PF_PID" 2>/dev/null || true
trap - EXIT
[ "$CODE" = "200" ] && log "OK: internal /healthz 200" || { log "internal /healthz returned $CODE"; exit 1; }

log "2/4: checking /etc/hosts for $MR_HOST"
if getent hosts "$MR_HOST" >/dev/null 2>&1 || grep -q "$MR_HOST" /etc/hosts 2>/dev/null; then
  log "OK: $MR_HOST resolves"
else
  cat >&2 <<EOF

[memory-router-bootstrap] $MR_HOST does not resolve yet. MagicDNS does not
serve subdomains, so this needs a manual /etc/hosts entry (same pattern
spec 011 uses for remote Engram clients). Run:

  echo "$MR_LB_VIP $MR_HOST" | sudo tee -a /etc/hosts

then re-run this script.
EOF
  exit 2
fi

log "3/4: positive mTLS+bearer check per identity"
for NAME in $MR_IDENTITIES; do
  BEARER=$(kubectl -n "$MR_NAMESPACE" get secret memory-router-client-bearers \
    -o jsonpath="{.data.$NAME}" | base64 -d)
  CODE=$(curl -sS -o /dev/null -w '%{http_code}' \
    --cacert "$MR_PKI_DIR/ca/ca.crt" \
    --cert "$MR_PKI_DIR/clients/$NAME/$NAME.crt" \
    --key "$MR_PKI_DIR/clients/$NAME/$NAME.key" \
    -H "Authorization: Bearer $BEARER" \
    "https://$MR_HOST:$MR_ENTRYPOINT_PORT/healthz")
  [ "$CODE" = "200" ] && log "OK: $NAME -> 200" || { log "$NAME -> $CODE (expected 200)"; exit 1; }
done

log "4/4: negative control (no client cert must be rejected at TLS level)"
set +e
OUT=$(curl -sS --cacert "$MR_PKI_DIR/ca/ca.crt" "https://$MR_HOST:$MR_ENTRYPOINT_PORT/healthz" 2>&1)
RC=$?
set -e
if [ $RC -ne 0 ] && echo "$OUT" | grep -qi "certificate required"; then
  log "OK: connection without a client cert was rejected at the TLS layer"
else
  log "Negative control did NOT fail as expected (rc=$RC): $OUT"
  exit 1
fi

log "All checks passed."
