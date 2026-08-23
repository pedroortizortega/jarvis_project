#!/usr/bin/env bash
# Apply the 9 memory-router + hindsight manifests in dependency order.
# Secrets referenced by name (memory-router-engram-credentials,
# memory-router-client-bearers, memory-router-server-tls,
# memory-router-client-ca, hindsight-tenant-key, hindsight-codex-shim-key)
# must already exist — run 03-create-secrets.sh first, or the Deployments
# land in CreateContainerConfigError.
#
# memory-router-ingress.yaml hardcodes Host(`memory-router.trantor.tail07dff9.ts.net`)
# and memory-router-deployment.yaml hardcodes image `memory-router:local` —
# if MR_HOST or MR_IMAGE_TAG differ from the defaults on this machine, edit
# those two files before applying (kept as plain manifests on purpose, not
# templated, to stay reviewable — see spec 014 §8).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./00-config.sh

for f in memory-router-pvc.yaml \
         memory-router-configmap.yaml \
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
