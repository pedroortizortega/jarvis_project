#!/usr/bin/env bash
# Give memory-router its own Traefik entryPoint (:8444 by default), isolated
# from `websecure` (used by Engram). This alone does NOT isolate the mTLS
# certificate — see 02-generate-pki.sh and memory-router-ingress.yaml for
# why a dedicated hostname is also required (live-verified 2026-08-21,
# specs/014_memory_router.md §8).
#
# This is a HelmChartConfig overlay on the cluster-wide, shared kube-system
# Traefik — applying it restarts that Traefik pod (brief interruption to
# every ingress on the cluster, including Engram). Confirm before running
# on a live cluster with real traffic.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./00-config.sh

MANIFEST="$MR_MANIFESTS_DIR/traefik-memoryrouter-entrypoint.yaml"
if [ "$MR_ENTRYPOINT_PORT" != "8444" ]; then
  log "MR_ENTRYPOINT_PORT=$MR_ENTRYPOINT_PORT differs from the committed manifest's :8444 — edit $MANIFEST to match before applying"
  exit 1
fi

log "Applying $MANIFEST (restarts the shared kube-system Traefik pod)"
kubectl apply -f "$MANIFEST"

log "Waiting for Traefik rollout"
kubectl -n kube-system rollout status deployment/traefik --timeout=90s

log "Verifying the entryPoint is live"
kubectl -n kube-system get deploy traefik -o jsonpath='{.spec.template.spec.containers[0].args}' \
  | tr ',' '\n' | grep -q "entryPoints.memoryrouter" \
  && log "OK: memoryrouter entryPoint present" \
  || { log "memoryrouter entryPoint NOT found in Traefik args"; exit 1; }
