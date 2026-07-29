# Hermes on Kubernetes

This directory contains the versioned, non-secret configuration required to
deploy Hermes reproducibly. Runtime state, sessions, OAuth credentials, API
keys, and `.env` files remain outside Git.

## Deployment Files

| Path | Purpose |
| --- | --- |
| `config/config.yaml` | Default Hermes configuration seeded into a new PVC. |
| `config/SOUL.md` | Versioned Hermes persona. |
| `hermes-agent-master.yaml` | Deployment for the primary Hermes instance. |
| `hermes-master-pvc.yaml` | Persistent home volume for Hermes. |
| `profiles/` | Declarative Codex profile matrix and bootstrap script. |

## Resolved Incidents

### Context window exhaustion

**Symptom:** LiteLLM rejected requests because Qwen has a 98,304-token total
window while Hermes requested 65,536 output tokens. A prompt with only 32,769
input tokens was enough to exceed the provider limit. Repeated compaction then
ended with `max compression attempts (3) reached`.

**Resolution:** `config/config.yaml` now sets:

```yaml
model:
  context_length: 98304
  max_tokens: 32768

compression:
  threshold_tokens: 50000
  protect_first_n: 1
  protect_last_n: 8
  proactive_prune_tokens: 40000
  max_attempts: 5
```

`protect_last_n: 8` preserves eight recent messages; it does not compact every
eight messages. The absolute `threshold_tokens` is required because Hermes
raises percentage thresholds to 75% for models below 512K context.

**Verification:**

```bash
kubectl -n hermes-agents exec deployment/hermes-agent-master -- hermes config get model
kubectl -n hermes-agents exec deployment/hermes-agent-master -- hermes config get compression
```

Start an affected conversation again with `/new`; an already oversized session
is not repaired by a configuration change.

### Codex OAuth DNS failure

**Symptom:** `hermes auth` failed during device-code polling with
`Temporary failure in name resolution` for `auth.openai.com`.

**Cause:** CoreDNS forwarded external DNS by UDP through the node resolvers.
CoreDNS logged timeouts to `1.1.1.1:53` and `8.8.8.8:53`. Kubernetes DNS and
the Hermes pod were healthy; upstream UDP DNS was intermittent or blocked.

**Resolution:** CoreDNS now forwards external requests over TCP to Cloudflare
and Google DNS. The cluster-wide change is documented in spec 001.

**Verification:**

```bash
kubectl -n hermes-agents exec deployment/hermes-agent-master -- \
  python -c 'import socket; print(socket.getaddrinfo("auth.openai.com", 443))'
kubectl -n kube-system logs deployment/coredns --since=2m
```

The device code created before the failure expires. Generate a new one with:

```bash
kubectl exec -it -n hermes-agents deployment/hermes-agent-master -- hermes auth
```

### Reproducible configuration versus PVC state

**Symptom:** Editing the `hermes-config-seed` ConfigMap did not update an
already running Hermes installation.

**Cause:** The init container intentionally copies `config.yaml` and `SOUL.md`
only when the PVC is empty, so live changes survive a restart.

**Resolution:** Apply the seed ConfigMap for future PVCs, copy the desired
configuration to `/opt/data/config.yaml` for the current PVC, then restart the
Deployment. The complete procedure is in spec 002.

### Named Codex profiles reported OAuth but could not execute requests

**Symptom:** A named profile showed `openai-codex` and `hermes auth status`
reported a logged-in account, but the Codex runtime failed with `No Codex
credentials stored`. The profile also inherited the LiteLLM URL from the
default profile, which obscured the intended OAuth route.

**Cause:** OAuth was stored in the root credential pool at
`/opt/data/auth.json`. Hermes's Codex runtime read the active profile's
credential pool directly instead of using the existing profile-to-root fallback.

**Resolution:** The local Hermes image now reads Codex credentials through the
profile-aware `read_credential_pool("openai-codex")` path. The profile bootstrap
also explicitly configures `model.base_url` as
`https://chatgpt.com/backend-api/codex`.

**Build, import, and rollout:**

```bash
cd kubernetes/docker/hermes-agent
docker build -t hermes-agent:local .
docker save hermes-agent:local | sudo k3s ctr images import -
kubectl -n hermes-agents rollout restart deployment/hermes-agent-master
kubectl -n hermes-agents rollout status deployment/hermes-agent-master
```

**Verification without calling a model:**

```bash
kubectl -n hermes-agents exec deployment/hermes-agent-master -- bash -c \
  'HERMES_HOME=/opt/data/profiles/sol-high /opt/hermes/.venv/bin/python - <<"PY"
from hermes_cli.auth import resolve_codex_runtime_credentials
creds = resolve_codex_runtime_credentials()
assert creds["provider"] == "openai-codex"
assert creds["base_url"] == "https://chatgpt.com/backend-api/codex"
assert creds["source"] == "credential_pool"
assert creds["api_key"]
print("Codex OAuth runtime: valid")
PY'
```

This proves the profile can resolve OAuth credentials without sending a model
request or consuming Codex quota.

## Security

Never commit `.env`, `auth.json`, API keys, private SSH keys, or session data.
Use Kubernetes Secrets for deployment credentials. A key written directly in a
Deployment manifest must be rotated and replaced with `secretKeyRef`.
