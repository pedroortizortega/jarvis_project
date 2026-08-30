#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s mini|daily|large\n' "$0" >&2
  exit 2
}

# "daily" used to mean the small qwen3.5-9b (moved to "mini" below). It now
# means the 27B IQ2_S quant, which is the default model in day-to-day use.
case "${1:-}" in
  mini) MODEL=qwen3.5-9b ;;
  daily) MODEL=qwen3.8-27b-iq2s ;;
  large) MODEL=qwen3.6-27b-q3 ;;
  *) usage ;;
esac

for deployment in \
  vllm vllm-big-model vllm-small-model \
  llama-server llama-server-q3 llama-server-q6 llama-server-q3-8; do
  replicas="$(kubectl -n llms get deployment/$deployment \
    -o jsonpath='{.spec.replicas}' 2>/dev/null || true)"
  test -z "$replicas" || test "$replicas" = 0
done
test "$(kubectl -n llms get deployment/llama-router \
  -o jsonpath='{.spec.replicas}')" = 1

LITELLM_REPLICAS="$(kubectl -n llms get deployment/litellm \
  -o jsonpath='{.spec.replicas}')"
GATEWAY_WAS_ACTIVE=false
if systemctl is-active --quiet hermes-gateway.service; then
  GATEWAY_WAS_ACTIVE=true
fi
SUCCESS=false

restore_on_exit() {
  if [[ "$SUCCESS" == true ]]; then
    return
  fi
  kubectl -n llms scale deployment/litellm \
    --replicas="$LITELLM_REPLICAS" >/dev/null || true
  if [[ "$GATEWAY_WAS_ACTIVE" == true ]]; then
    sudo systemctl start hermes-gateway.service || true
  fi
}
trap restore_on_exit EXIT

if [[ "$GATEWAY_WAS_ACTIVE" == true ]]; then
  sudo systemctl stop hermes-gateway.service
fi
kubectl -n llms scale deployment/litellm --replicas=0
kubectl -n llms wait --for=delete pod -l app=litellm --timeout=5m

# This request blocks until the router has fully loaded the selected model.
kubectl -n llms exec deployment/llama-router -- sh -c '
  model="$1"
  key="$(cat /run/secrets/llama/api-key)"
  curl --fail --silent --show-error \
    -H "Authorization: Bearer $key" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"OK\"}],\"max_tokens\":1,\"chat_template_kwargs\":{\"enable_thinking\":false}}" \
    http://127.0.0.1:8080/v1/chat/completions >/dev/null
' sh "$MODEL"

hermes config set model.default "$MODEL"
kubectl -n llms scale deployment/litellm --replicas="$LITELLM_REPLICAS"
if (( LITELLM_REPLICAS > 0 )); then
  kubectl -n llms rollout status deployment/litellm --timeout=5m
fi
if [[ "$GATEWAY_WAS_ACTIVE" == true ]]; then
  sudo systemctl start hermes-gateway.service
fi

SUCCESS=true
printf 'Active model: %s\n' "$MODEL"
