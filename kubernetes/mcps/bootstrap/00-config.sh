#!/usr/bin/env bash
# Shared configuration for the memory-router bootstrap scripts. Sourced by
# every 0N-*.sh script in this directory — never run directly.
#
# Every value here has a default matching the `trantor` cluster this was
# first deployed and verified against (2026-08-21, spec 014_memory_router.md
# §8-9). Override via environment variables when bootstrapping on a
# different machine/cluster.

set -euo pipefail

: "${MR_NAMESPACE:=mcps}"
: "${MR_HOST:=memory-router.trantor.tail07dff9.ts.net}"
: "${MR_TAILNET_ADMIN_HOST:=trantor.tail07dff9.ts.net}" # existing engram-tailnet host, for reference only
: "${MR_LB_VIP:=192.168.1.240}"                          # Traefik LoadBalancer external IP (kubectl -n kube-system get svc traefik)
: "${MR_ENTRYPOINT_PORT:=8444}"
: "${MR_IMAGE_TAG:=memory-router:local}"
: "${MR_PKI_DIR:=$HOME/.config/memory-router/pki}"
: "${MR_IDENTITIES:=pedro-claude-code codex opencode hermes-gateway}"
# Hindsight image: unlike MR_IMAGE_TAG (consumed by 01-build-image.sh),
# there is no local build step — the image is pulled straight from
# ghcr.io/vectorize-io/hindsight:latest, hardcoded in
# hindsight-deployment.yaml. No env var here on purpose: a var with no
# consumer would look configurable without being so.

# Repo-relative paths — resolved from this script's location so it works
# regardless of the caller's cwd.
MR_BOOTSTRAP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MR_MANIFESTS_DIR="$(cd "$MR_BOOTSTRAP_DIR/.." && pwd)"
MR_REPO_ROOT="$(cd "$MR_MANIFESTS_DIR/../.." && pwd)"
MR_ROUTER_SRC_DIR="$MR_REPO_ROOT/hermes-native/memory-router"

log() { printf '[memory-router-bootstrap] %s\n' "$*" >&2; }
