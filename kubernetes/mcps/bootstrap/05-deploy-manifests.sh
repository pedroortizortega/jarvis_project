#!/usr/bin/env bash
# Apply the 10 memory-router + hindsight + knowledge-vault-search manifests
# in dependency order. Secrets referenced by name
# (memory-router-engram-credentials, memory-router-client-bearers,
# memory-router-server-tls, memory-router-client-ca, hindsight-tenant-key,
# hindsight-codex-shim-key, knowledge-vault-search-token) must already
# exist — run 03-create-secrets.sh first, or the Deployments land in
# CreateContainerConfigError.
#
# knowledge-vault-search-endpoints.yaml is applied before
# memory-router-deployment.yaml on purpose (design.md D-07/D-01 ordering):
# the host bridge must be provably reachable before the router is told the
# token, so a later router-side 401 can only mean token mismatch, never
# "nothing is listening".
#
# memory-router-ingress.yaml hardcodes Host(`memory-router.trantor.tail07dff9.ts.net`)
# and memory-router-deployment.yaml hardcodes image `memory-router:local` —
# if MR_HOST or MR_IMAGE_TAG differ from the defaults on this machine, edit
# those two files before applying (kept as plain manifests on purpose, not
# templated, to stay reviewable — see spec 014 §8).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./00-config.sh

# Staleness guard (specs/022 §8.1 Bug 2): `kubectl apply` only propagates
# manifest changes — it never rebuilds memory-router:local. If the source
# has changed since the last successful 01-build-image.sh run, the pod
# would silently keep running old code. Fail loudly here instead.
if [ ! -f "$MR_IMAGE_HASH_MARKER" ]; then
  log "No record of $MR_IMAGE_TAG ever being built (missing $MR_IMAGE_HASH_MARKER)."
  log "Run ./01-build-image.sh first, then re-run this script."
  exit 3
fi
CURRENT_HASH="$(mr_source_hash)"
BUILT_HASH="$(cat "$MR_IMAGE_HASH_MARKER")"
if [ "$CURRENT_HASH" != "$BUILT_HASH" ]; then
  log "$MR_IMAGE_TAG is STALE: hermes-native/memory-router source has changed"
  log "since the last successful ./01-build-image.sh run."
  log "Run ./01-build-image.sh to rebuild + reimport, then re-run this script."
  exit 3
fi
log "$MR_IMAGE_TAG is up to date with the current source (hash $CURRENT_HASH)"

for f in memory-router-pvc.yaml \
         memory-router-configmap.yaml \
         knowledge-vault-search-endpoints.yaml \
         memory-router-deployment.yaml \
         memory-router-service.yaml \
         memory-router-tlsoption.yaml \
         memory-router-ingress.yaml \
         hindsight-pvc.yaml \
         hindsight-deployment.yaml \
         hindsight-service.yaml; do
  log "Applying $f"
  kubectl -n "$MR_NAMESPACE" apply -f "$MR_MANIFESTS_DIR/$f"
done

log "Waiting for the memory-router rollout"
kubectl -n "$MR_NAMESPACE" rollout status deployment/memory-router --timeout=90s

# hindsight-deployment (SDD change): D-11's startup budget is ~10 minutes
# (embedded Postgres initdb + first-run model download), much longer than
# memory-router's own rollout.
log "Waiting for the hindsight rollout"
kubectl -n "$MR_NAMESPACE" rollout status deployment/hindsight --timeout=600s
