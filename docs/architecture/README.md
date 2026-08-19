# Architecture overview

This is the map of the whole system: what runs where, how the pieces talk to
each other, and what to read next depending on what you're trying to do. If
you're new to this project, start here before opening any single service's
code — the pieces don't make sense in isolation.

**One sentence:** Jarvis is a personal AI assistant ("Hermes") running as a
host process on a single machine (`trantor`), backed by a single-node
Kubernetes cluster on the same machine that serves local LLMs and a handful
of supporting services (memory, GPU handoff, MCP tools).

## Quick path

1. Read [System map](#system-map) below to see every moving part in one table.
2. Read [Two runtimes, one machine](#two-runtimes-one-machine) to understand
   the host/cluster split — this is the one idea that unlocks everything else.
3. Pick the subsystem you actually need from
   [Subsystems in depth](#subsystems-in-depth) and read that section.
4. Before touching production, read [Known drift and gotchas](#known-drift-and-gotchas) —
   these are real traps that already bit this project once.

## System map

Everything in this project is either a **host process** on `trantor` or a
**Kubernetes workload** in its single-node k3s cluster. Nothing runs
anywhere else.

| Subsystem | Runs as | Where | Purpose |
|---|---|---|---|
| Hermes gateway | systemd service | host | The assistant itself — Telegram, TUI, skills, plugins, MCP client |
| hermes-intent-orchestration | pip plugin, in-process with Hermes | host (Hermes's venv) | Classifies user turns, routes bounded tasks to isolated profiles |
| knowledge-vault | 5 systemd services + timers | host | Human-reviewed note pipeline (propose → review → publish → mirror) |
| llama-service (vLLM + llama.cpp) | Deployments + Jobs | k8s `llms` | Serves local Qwen models |
| LiteLLM | Deployment (LoadBalancer) | k8s `llms` | Single routing layer for all model traffic (local + cloud) |
| model-panel | Deployment | k8s `llms` | Web UI: GPU handoff (Local↔Cloud) + live CPU/RAM/VRAM gauges |
| codex-shim | Deployment | k8s `llms` | Proxies Codex/ChatGPT cloud requests behind an OpenAI-compatible API |
| node-exporter / nvidia-gpu-exporter | DaemonSets | k8s `llms` | Feed model-panel's metrics gauges |
| Engram Cloud | Deployment + Postgres | k8s `mcps` | Centralized, persistent, multi-agent memory backend |
| memory-router | Deployment (**not yet live**, see below) | k8s `mcps` | Namespaced/permissioned memory routing in front of Engram |
| brave-search-mcp | Deployment | k8s `mcps` | Brave Search MCP tool |

## Two runtimes, one machine

The single fact that explains most of this project's shape: **Hermes (the
assistant) and the Kubernetes cluster both run on the same physical machine,
`trantor`, but as two independent runtimes.** Hermes is not a pod. Kubernetes
does not manage Hermes. They talk to each other over the network, the same
way they'd talk to a remote service — just one that happens to be on the
same box.

```
┌─────────────────────────────── trantor ───────────────────────────────┐
│                                                                        │
│  ┌─────────────────────────┐        ┌───────────────────────────┐    │
│  │   HOST (systemd)         │        │   k3s (single node)        │    │
│  │                          │        │                             │    │
│  │  hermes-gateway.service  │──LAN──▶│  namespace llms             │    │
│  │   └─ intent-orchestration│  :4000 │   ├─ litellm (LoadBalancer) │    │
│  │      (plugin, in-proc)   │        │   ├─ llama-router → llama.cpp│   │
│  │                          │        │   ├─ vllm                   │    │
│  │  knowledge-vault-*.service│       │   ├─ model-panel             │    │
│  │   (5 units + timers)     │        │   ├─ codex-shim             │    │
│  │                          │◀──TS──▶│   └─ node/gpu-exporter      │    │
│  │  engram-tailnet-serve /  │  :443  │                             │    │
│  │  port-forward bridges    │        │  namespace mcps             │    │
│  └─────────────────────────┘        │   ├─ engram-cloud + postgres│    │
│                                       │   ├─ memory-router (WIP)    │    │
│                                       │   └─ brave-search-mcp       │    │
│                                       └───────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

Why this split exists (spec 004): Hermes originally ran *inside* k8s
(`hermes-agent-master`, namespace `hermes-agents`) but moved to a native host
systemd install for resilience — a pod restart used to mean losing the
running agent mid-conversation. That old k8s Deployment still exists, scaled
to `replicas: 0`, kept only as an emergency rollback path. **Never scale it
back up while the host systemd unit is also running** — they'd both try to
run the same Telegram bot.

**How the host reaches into the cluster:**

| From | To | How |
|---|---|---|
| Hermes → model traffic | LiteLLM `192.168.1.241:4000` | LAN (MetalLB LoadBalancer IP), no VPN hop needed |
| Hermes → Engram memory | `127.0.0.1:7180` | Local port-forward bridge (systemd unit) → Tailnet → Traefik (mTLS) |
| Hermes → MCP tools | `https://trantor.tail07dff9.ts.net/mcp` | Tailscale, terminating at Traefik so mTLS still applies |
| Hermes → kubectl (fallback) | cluster API | `KUBECONFIG=~/.kube/config` set in Hermes's env |

## Subsystems in depth

### Hermes gateway (host)

The assistant itself — not just an LLM client. Owns Telegram integration,
TUI, skills, plugins, MCP client, its own state DB, kanban, and cron. It's a
vendored project (full source under `kubernetes/docker/hermes-agent/`), not
something built from scratch in this repo.

- **Unit:** `/etc/systemd/system/hermes-gateway.service`
- **Runs:** `~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run`
- **State home** (`HERMES_HOME`): `~/.hermes/` — config, session DB, plugins,
  audit logs. Distinct from the source checkout at `~/.hermes/hermes-agent/`.
- **Config:** `~/.hermes/config.yaml` (model routing, plugins, Telegram allow-list)
- **Env:** `~/.hermes/.env` (Telegram tokens, `HASS_URL`, `KUBECONFIG`, `TERMINAL_ENV`)
- `Restart=always`; the process self-restarts by exiting with code `75`.
- Relevant specs: **002** (resilience/multi-model), **003** (Codex profiles),
  **004** (native systemd install/rollback boundary).

### hermes-intent-orchestration (host, plugin)

Classifies each user turn locally and routes bounded tasks to isolated
Luna/Terra/Sol profiles — without switching Hermes's primary profile or
touching Hermes core. Spec **009**.

- **Not a service** — a pip package installed *into Hermes's own venv*:
  `pip install -e hermes-native/orchestration`, then enabled via
  `plugins.enabled: [intent-orchestration]` in `~/.hermes/config.yaml`.
- **Modes** (least to most autonomous): `disabled` → `shadow` (classify +
  audit, primary still handles everything — the recommended starting point)
  → `explicit` (only routes when the user names an allow-listed profile) →
  `auto` (policy-driven; high-risk/Sol routes stay local unless
  `allow_high_risk_auto` is set).
- Classifier calls LiteLLM directly (`local_base_urls`), single request, hard
  15s timeout — bypasses Hermes's own fallback chain on purpose.
- Routing metadata (never prompt content) is audited to
  `~/.hermes/orchestration/events.sqlite3`.
- Delegated task workers get only policy-selected toolsets, never full
  conversation history; terminal/file/test workers fail closed unless a
  verified sandbox is explicitly opted into.
- **Tests:** `unittest`, needs the package installed first —
  `pip install -e .` then `python -m unittest discover -s tests` from
  `hermes-native/orchestration/`.

### knowledge-vault (host)

A human-reviewed note pipeline, deliberately separate from Hermes's own
memory/Engram tooling — this is for durable, curated knowledge an agent
*proposes* but a human must approve before it reaches the canonical vault.

- **Not k8s** — installed at `/opt/knowledge-vault`, state under
  `/var/lib/knowledge-vault/{proposals,pending,decisions,approved,...}`,
  vault at `/opt/knowledge-vault/vault`.
- **5 systemd units + timers**, each its own hardened, dedicated system user:
  - `knowledge-vault-review` — projects proposals into a pending review area
  - `knowledge-vault-approve` — joins a proposal with its recorded decision
  - `knowledge-vault-publisher` — the **only** unit allowed to write the vault
  - `knowledge-vault-mirror` — mirrors published notes to a private bare git repo
  - `knowledge-vault-review-sync` — carries the review queue to/from mobile over SSH, offline
- The installer does **not** auto-enable the publisher — it must be enabled
  manually once a test proposal has been verified to publish correctly.
- Agents submit proposals via the `propose-note` skill piping into
  `knowledge-vault-propose`.
- **Tests:** `unittest` from `hermes-native/knowledge-vault/` after
  `pip install -e .` (12 test files).

### llama-service — local LLM serving (k8s `llms`)

Serves Qwen GGUF models via llama.cpp's OpenAI-compatible server, alongside
vLLM, on the single RTX 4070 Ti SUPER (16 GiB VRAM). Hybrid inference:
llama.cpp auto-offloads as many layers as fit in VRAM, the rest stays in host
RAM. Specs **001, 005, 006, 007, 008**.

- **Three quantization variants**, each its own zero-replica-by-default
  Deployment, scaled up one at a time:
  - `llama-server` — base
  - `llama-server-q3` — Qwen3.6-27B **Q3_K_S**, 11.5 GiB, fully GPU-offloadable (the "large" profile)
  - `llama-server-q6` — Qwen3.6-27B **UD-Q6_K_XL**, 23.9 GiB, hybrid CPU/GPU
- **`llama-router`** is the recommended path: preloads the small **daily**
  model (Qwen3.5-9B Q6_K) and swaps to **large** (Qwen3.6-27B Q3_K_S) on
  demand, `--models-max 1` so only one model is ever resident.
- `switch-model.sh` — CLI for manual daily/large switching (model-panel's
  profile toggle uses the same router underneath).
- Model downloads are one-shot Jobs with size + SHA-256 verification.
- **LiteLLM** (`kubernetes/proxy/`) is the single external routing layer —
  a LoadBalancer Service on `192.168.1.241:4000`. Aliases: `qwen3` → vLLM,
  `qwen3.6-27b`/`qwen3.5-9b` → `llama-router`, `qwen3.6-27b-q6` →
  `llama-server-q6` directly, `cloud` → `codex-shim`. `router_settings`
  raises `allowed_fails` to 3 — each alias is single-instance, so the
  stock 1-failure cooldown would blackhole a whole model on one blip.
- **Secrets required before applying** (not versioned):
  `llms/litellm-auth` with `master-key` + `llama-api-key`.
- **Tests:** none — pure manifests + shell, verified via
  `kubectl apply --dry-run` and the manual runbooks in specs 005–008.

### model-panel — GPU handoff web panel (k8s `llms`)

Replaces a ~15-step manual `kubectl`/bash runbook with a one-click
Local↔Cloud toggle, plus live host metrics. Spec **012**
(OpenSpec change `gpu-handoff-web-panel`).

**API** (`app/main.py`):

| Route | Does |
|---|---|
| `GET /healthz` | Liveness |
| `GET /api/status` | Current mode, Codex session state, switch phase |
| `GET /api/metrics` | Live `{cpu_pct, ram_pct, vram_pct}` |
| `POST /api/switch` | Toggle Local↔Cloud (fail-closed on invalid Codex session, `202` + background drain) |
| `POST /api/profile` | Switch local profile (`daily`/`large`) without leaving Local |
| `POST /api/repair` | Self-heal state/alias drift |

**"GPU handoff" means, operationally:** drain traffic → scale the local
GPU-consuming Deployment to zero → confirm the GPU is actually free
(`app/handoff/gpu.py`) → rewrite LiteLLM's `qwen3` alias to point at either
`llama-router` or `codex-shim` → restart LiteLLM. Returning to Local always
re-defaults to the `daily` profile.

**Metrics gauges** (added on top of the base panel): `node-exporter`
DaemonSet (read-only `hostPath` mounts, no `hostNetwork`) for CPU/RAM,
`nvidia-gpu-exporter` DaemonSet for VRAM — deliberately **never requests the
`nvidia.com/gpu` Kubernetes resource** (the node has exactly one GPU that
this whole panel exists to hand off between consumers; requesting it here
would starve every other workload). GPU visibility instead comes from
`runtimeClassName: nvidia` + `NVIDIA_VISIBLE_DEVICES=all`. Full design
rationale: `docs/superpowers/specs/2026-08-18-realtime-server-metrics-design.md`.

- Auth: shared bearer via `MODEL_PANEL_AUTH_TOKEN`, fails closed (503) if unset.
- Depends on: `llama-router`, `codex-shim`, the Kubernetes API (in-cluster
  ServiceAccount via `rbac.yaml`), LiteLLM's ConfigMap, both exporters.
- **Tests:** `pytest` from `kubernetes/model-panel/` (~100 tests: handoff
  state machine, drain, GPU checks, metrics client/API, manifest sanity).

### codex-shim (k8s `llms`)

Proxies Codex/ChatGPT cloud requests behind an OpenAI-compatible `/v1`
surface. Owns its **own** OAuth session — deliberately separate from
Hermes's own `hermes auth` session, because the refresh token is single-use
and rotates server-side; sharing one pair across two independent refreshers
would invalidate each other's session.

- When model-panel switches to Cloud, it points LiteLLM's `cloud`/`qwen3`
  alias at `codex-shim.llms.svc.cluster.local:8080/v1`.
- model-panel polls codex-shim's `GET /internal/session` before allowing a
  switch to Cloud, to confirm the session is actually valid first.
- The Codex login itself is bootstrapped manually
  (`scripts/bootstrap_login.md`) — never through the panel.
- **Tests:** `pytest` (`asyncio_mode = auto`), 13 files covering auth,
  refresh/backoff, and streaming/non-streaming translation.

### Engram Cloud (k8s `mcps`)

Centralized, persistent, multi-agent memory backend. Spec **011**. One
shared deployment + Postgres, reachable over two paths that both converge on
the same Traefik router + mTLS + bearer auth:

- **LAN** — `engram.lan` via a dedicated Ingress (applied separately from
  the base kustomization; can be disabled independently)
- **Tailnet** — host-level bridge: `kubectl port-forward` (loopback) →
  `tailscale serve --tcp=443` (raw TCP, deliberately not `--https`, so
  **Traefik terminates TLS and verifies the client cert**, not Tailscale)

Two independent auth factors: mTLS client cert (Traefik, before the request
even reaches the app) + application bearer token (Engram itself,
per-identity). No shared credentials between agents/machines.

- Image: pinned `ghcr.io/gentleman-programming/engram:v1.20.0`
- Single replica, `Recreate` strategy — not HA
- Full docs: `docs/engram-cloud/{architecture,installation,client-setup}.md`
- **Tests:** none — manifests only; verify with `kubectl apply --dry-run`,
  rollout status, and `engram cloud status` from an enrolled client.

### memory-router (k8s `mcps` — code merged, **not yet deployed**)

A single namespaced, permissioned memory-access layer in front of Engram
(today the only real backend; five more are planned but out of scope for
Phase 1). Solves fragmentation: today every agent integrates Engram's own
per-client stdio path directly, with no namespace or per-identity permission
concept. Spec **014**.

⚠️ **The Deployment YAML says explicitly it is not yet applied to the
cluster**, pending resolution of an Engram-manifest-ownership prerequisite
(now fixed — see [Known drift and gotchas](#known-drift-and-gotchas)). Don't
assume `mcps` has this running; check `kubectl get pods -n mcps` first.

- **Architecture:** client (mTLS+bearer) → Traefik Ingress → identity
  resolver → role validator → namespace validator → permission engine →
  backend registry → dispatcher → Engram adapter (subprocess
  `engram mcp --tools=agent`)
- **Dual surface:** REST (`/memory/store`, `/memory/search`,
  `/memory/reflect`, `/agents/context`, `/projects/context`) and a thin
  stdio MCP shim — both call the same dispatcher, guaranteeing parity.
- **Namespaces:** `/global`, `/user/master`, `/projects/{name}`,
  `/agents/{name}`; search falls back project → agent → global.
- **Roles:** `coder`, `scientist`, `jarvis` — namespace+verb rules,
  deny-by-default.
- **Degraded-backend handling:** store failures go to a durable
  single-writer NDJSON journal (hence `replicas: 1` + `Recreate` — a hard
  requirement, not a shortcut) and drain later; search failures are skipped
  with an "unavailable" marker, never silently dropped.
- Stdlib-only Python (no FastAPI) — justified as "4 Tailnet clients don't
  justify a new stack."
- **Tests:** repo-root `tests/test_memory_router_*.py`, run via
  `python -m unittest discover -s tests` from the repo root (no install
  needed — this is the one package tested without its own venv).

### brave-search-mcp (k8s `mcps`)

Wraps the Brave Search API as an MCP tool. Simple by comparison to the
others — stdio transport, `BRAVE_API_KEY` from a Secret. See
`kubernetes/mcps/brave-mcp-activation-guide.md` for activation steps.

### OpenSpec — the planning process, not a runtime component

`openspec/` is **pure workflow/documentation** — it never runs in
production. It's the spec-driven-development process this repo follows:
idea → `openspec/changes/<name>/` (proposal, design, delta specs, tasks) →
implementation → numbered `specs/NNN_*.md` → archived to
`openspec/changes/archive/` with the durable capability specs promoted to
`openspec/specs/`. `openspec/config.yaml` declares `strict_tdd: true` and
the canonical test command (`python -m unittest discover -s tests`).
Dedicated Claude skills (`sdd-new`, `sdd-apply`, `sdd-verify`, `sdd-archive`,
...) operate this workflow — see [Contributing](#where-to-go-next).

## Specs index

Every numbered spec in `specs/`, one line each:

| # | Title | Summary |
|---|---|---|
| 001 | Cluster K8s híbrido para LLMs locales y agentes | Founding spec: k3s cluster to serve local LLMs and agents |
| 002 | Resiliencia de Hermes, multi-modelo, integración dev | Multi-model (vLLM + Codex) support, resilient Hermes |
| 003 | Perfiles Codex de Hermes y convivencia con OpenCode | The nine Codex profiles (Luna/Terra/Sol) |
| 004 | Instalación nativa y clonación segura de Hermes con systemd | Native host systemd install, rollback boundary |
| 005 | llama.cpp híbrido para Qwen3.6-27B en Kubernetes | Hybrid GPU/RAM serving for Qwen3.6-27B |
| 006 | Qwen3.6-27B UD-Q6_K_XL con llama.cpp | The larger, hybrid-offload quantization variant |
| 007 | Qwen3.6-27B Q3_K_S con llama.cpp | Fully GPU-offloadable quantization variant |
| 008 | Qwen3.5-9B Q6_K diario y Qwen3.6-27B bajo demanda | Daily/large router split, single-model-loaded design |
| 009 | Orquestación automática de tareas en Hermes | Intent classification/routing → the orchestration plugin |
| 010 | Voz local estilo JARVIS para Hermes con Piper | Local Piper TTS voice output (implemented) |
| 011 | Engram Cloud: memoria persistente centralizada | Centralized Engram memory, LAN + Tailnet mTLS access |
| 012 | GPU Handoff Web Panel | Web panel replacing the manual GPU handoff runbook |
| 013 | K8s Deployment: Piper TTS API con Ingress | Piper TTS API behind an Ingress (draft) |
| 014 | Memory Router: capa unificada de acceso a memoria | Namespaced/permissioned memory routing (Phase 1) |

## Known drift and gotchas

Real traps found while writing this doc — read before you deploy or debug
anything in production.

- **memory-router is not yet live.** Its own Deployment YAML says so. Check
  `kubectl get pods -n mcps` before assuming it's running.
- **`hermes-agent-master` still exists in k8s** (namespace `hermes-agents`,
  `replicas: 0`) as an emergency rollback. Never scale it up while the host
  systemd `hermes-gateway.service` is also active — same Telegram bot, two
  processes.
- **K3s NetworkPolicy enforcement is disabled cluster-wide.** Any
  `networkpolicy.yaml` in this repo (e.g. under `kubernetes/llama-service/`,
  `kubernetes/policy/`) is documentation of intent, not an active control —
  don't treat it as real isolation.
- **Secrets are never committed.** `litellm-auth`, `engram-*`,
  `memory-router-*`, `brave-api-key-secret`, `codex-shim-auth` must all be
  created by hand before applying the corresponding manifests.
- **Engram's namespace was drifted** (`engram` in the YAML, `mcps` in
  reality) until 2026-08-19 — fixed in PR #16. If you're reading an older
  checkout or a fork, verify with `kubectl get svc -A | grep engram` before
  trusting any doc that says namespace `engram`.
- **Two independent Engram bridges exist on the host** — one for the
  general Tailnet path (`engram-tailnet-serve.service`), one dedicated to
  Hermes (`127.0.0.1:7180`). Don't assume there's only one port-forward to
  manage.

## Where to go next

- **Deploying to production / standing up the cluster from scratch:**
  production runbook (not yet written — see `docs/superpowers/plans/` for
  the closest existing precedent, the real-time-metrics feature plan, as an
  example of this repo's planning depth).
- **Concepts you don't recognize:** a glossary is planned at `docs/glossary.md`
  as the next doc in this series — not written yet as of this doc.
- **Making a change:** this repo follows spec-driven development via
  OpenSpec — see [Specs index](#specs-index) above and the `sdd-*` skills.
  Every PR in this project's history so far went through its own branch +
  PR, one logical change at a time — check `git log --oneline main` for the
  pattern before batching unrelated changes into one PR.
- **A specific service's full detail:** read its own `README.md` in its
  source directory (`kubernetes/<service>/README.md`,
  `hermes-native/<package>/README.md`) — this doc summarizes, the service's
  own README is the source of truth for exact commands.
