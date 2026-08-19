#!/usr/bin/env bash
set -euo pipefail

namespace="${NAMESPACE:-hermes-agents}"
deployment="${DEPLOYMENT:-hermes-agent-master}"
codex_base_url="https://chatgpt.com/backend-api/codex"

configure_profile() {
  local name="$1"
  local model="$2"
  local effort="$3"
  local description="$4"
  local created=false

  if ! kubectl exec -n "$namespace" "deployment/$deployment" -- \
    sh -c "test -d /opt/data/profiles/$name"; then
    kubectl exec -n "$namespace" "deployment/$deployment" -- \
      hermes profile create "$name" --clone-from default --no-alias
    created=true
  fi

  # A clone includes the default .env. Profiles share configuration, not secrets.
  if [ "$created" = true ]; then
    kubectl exec -n "$namespace" "deployment/$deployment" -- \
      sh -c "rm -f /opt/data/profiles/$name/.env"
  fi

  kubectl exec -n "$namespace" "deployment/$deployment" -- \
    sh -c "HERMES_HOME=/opt/data/profiles/$name hermes config set model.provider openai-codex"
  kubectl exec -n "$namespace" "deployment/$deployment" -- \
    sh -c "HERMES_HOME=/opt/data/profiles/$name hermes config set model.base_url $codex_base_url"
  kubectl exec -n "$namespace" "deployment/$deployment" -- \
    sh -c "HERMES_HOME=/opt/data/profiles/$name hermes config set model.default $model"
  kubectl exec -n "$namespace" "deployment/$deployment" -- \
    sh -c "HERMES_HOME=/opt/data/profiles/$name hermes config set --force agent.reasoning_effort $effort"
  kubectl exec -n "$namespace" "deployment/$deployment" -- \
    hermes profile describe "$name" --text "$description"
}

configure_profile luna-low gpt-5.6-luna low "Fast classification, short summaries, simple lookups, and low-risk edits."
configure_profile luna-medium gpt-5.6-luna medium "Routine single-file changes, focused debugging, and concise implementation plans."
configure_profile luna-high gpt-5.6-luna-pro high "Bounded investigations that need more care but do not justify Terra."
configure_profile terra-low gpt-5.6-terra low "Standard development questions where a stronger model improves reliability."
configure_profile terra-medium gpt-5.6-terra medium "Default coding profile for multi-file features, test failures, and code review."
configure_profile terra-high gpt-5.6-terra-pro high "Complex debugging, refactors with invariants, and security-sensitive implementation."
configure_profile sol-low gpt-5.6-sol low "High-quality but time-bounded second opinion; use only when Luna or Terra is insufficient."
configure_profile sol-medium gpt-5.6-sol medium "Architecture trade-offs, difficult reviews, and cross-system design proposals."
configure_profile sol-high gpt-5.6-sol-pro high "Last-resort profile for critical, ambiguous, or high-impact engineering decisions."

kubectl exec -n "$namespace" "deployment/$deployment" -- hermes profile list
