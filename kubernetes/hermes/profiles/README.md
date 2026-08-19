# Hermes Codex Profiles

`profiles.yaml` is the versioned source of truth for the nine Codex profiles.
It contains no credentials. The high profiles use the `-pro` model variants,
which Hermes identifies as the Codex high-effort modes.

## Bootstrap

The default Hermes profile must exist first. The script clones it so every
named profile keeps the same terminal settings, skills, compression policy,
effort, and routing description.

```bash
chmod +x kubernetes/hermes/profiles/bootstrap-profiles.sh
kubernetes/hermes/profiles/bootstrap-profiles.sh
```

It is idempotent: an existing profile is not recreated, but its model,
Codex endpoint, reasoning effort, and description are reconciled with this
matrix.

## Authentication

The script deliberately does not authenticate against Codex and does not copy
or create `.env` files. Hermes stores the successful OAuth credential once in
the root auth store (`/opt/data/auth.json`) and named profiles reuse it; they
do not need copied API keys or copied `auth.json` files. Authenticate after
bootstrap with `hermes auth` or `hermes model` inside the pod, then verify the
account exposes the selected model IDs. Do not commit `auth.json`, `.env`, API
keys, or OAuth tokens.

The bundled Hermes image includes a profile credential-pool fallback fix. It
allows the Codex runtime to use the root OAuth entry rather than treating a
named profile as an API-key-only configuration.

After changing Hermes source, rebuild and deploy the local image before
bootstrapping profiles:

```bash
cd kubernetes/docker/hermes-agent
docker build -t hermes-agent:local .
docker save hermes-agent:local | sudo k3s ctr images import -
kubectl -n hermes-agents rollout restart deployment/hermes-agent-master
kubectl -n hermes-agents rollout status deployment/hermes-agent-master
```

Verify a named profile without consuming Codex quota:

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

## Profile Selection

| Profile | Use it for |
| --- | --- |
| `luna-low` | Classification, summaries, simple questions, and low-risk edits. |
| `luna-medium` | Routine focused changes and straightforward debugging. |
| `luna-high` | Bounded investigation when a little more reasoning is needed. |
| `terra-low` | Normal development questions needing reliable implementation. |
| `terra-medium` | Default development profile: multi-file work, tests, and review. |
| `terra-high` | Difficult bugs, careful refactors, and security-sensitive changes. |
| `sol-low` | A time-bounded expert second opinion, not a normal default. |
| `sol-medium` | Architecture and cross-system technical trade-offs. |
| `sol-high` | Critical, ambiguous, high-impact work after cheaper options fail. |

For cost and quota efficiency, start with `luna-low`, move to
`terra-medium` for normal coding, and reserve `sol-high` for genuinely hard
work. A high effort should be an escalation after the lower profile has shown
insufficient evidence, not the default setting.
