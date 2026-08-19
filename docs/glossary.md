# Glossary

Every term used across this project's docs that isn't obvious from context —
alphabetical, so you can jump straight to the word that stopped you. Each
entry explains the concept **and** how it shows up specifically in this
repo, not just the generic definition.

If a term you needed isn't here, that's a gap in this doc — add it.

---

**allowed_fails** — a LiteLLM router setting: how many consecutive failures
a model alias tolerates before LiteLLM stops routing to it ("cooldown").
This project sets it to `3` instead of LiteLLM's default of `1`, because
every alias here is a single instance (no replicas to fall back to) — the
default would blackhole a whole model on one transient blip.

**auto mode** — see [intent-orchestration modes](#intent-orchestration-modes).

**Change proposal** — see [OpenSpec](#openspec).

**ClusterIP** — a Kubernetes Service type reachable only *from inside* the
cluster (no external IP). Most services here (`llama-router`, `codex-shim`,
`engram-cloud`, `node-exporter`) are ClusterIP — only things that need
external access (LiteLLM, Traefik) use `LoadBalancer` instead.

**Codex** — OpenAI's coding-agent product with its own separate OAuth/cloud
session — distinct from a regular ChatGPT session. `codex-shim` owns a
dedicated Codex session so it never collides with Hermes's own separate
`hermes auth` session (see [codex-shim](#codex-shim)).

**codex-shim** — the k8s service that proxies Codex/ChatGPT cloud requests
behind an OpenAI-compatible API, so model-panel's "Cloud" mode can point
LiteLLM at it like any other model backend. See
[Subsystems in depth](architecture/README.md#codex-shim-k8s-llms).

**daily / large (profile)** — the two local-model presets `llama-router`
switches between. **daily** = Qwen3.5-9B Q6_K, small and always-on for
everyday interactive use. **large** = Qwen3.6-27B Q3_K_S, bigger and loaded
on demand for harder tasks. Only one is ever resident in VRAM at a time
(`--models-max 1`). Switched via model-panel's profile toggle or
`switch-model.sh`. See spec **008**.

**DaemonSet** — a Kubernetes workload type that runs exactly one pod **per
node**, automatically, forever (unlike a Deployment, which runs a chosen
*count* of pods anywhere the scheduler picks). Used here for `node-exporter`
and `nvidia-gpu-exporter` — they need to read the specific node's hardware,
so "one per node" is the correct shape even though this cluster only has
one node today.

**Deployment** — the standard Kubernetes workload type for running N
replicas of a stateless-ish app, with rolling updates. Most services in
this repo (`model-panel`, `codex-shim`, `llama-router`, `engram-cloud`,
`memory-router`) are Deployments.

**engram / Engram Cloud** — the centralized, persistent, multi-agent memory
backend (spec **011**). "Engram" is both the product name and, confusingly,
the *old* (now-corrected) namespace it briefly lived under — it actually
runs in namespace `mcps`. See
[Subsystems in depth](architecture/README.md#engram-cloud-k8s-mcps) and
`docs/engram-cloud/architecture.md`.

**explicit mode** — see [intent-orchestration modes](#intent-orchestration-modes).

**FQDN (in-cluster)** — the full DNS name a Kubernetes Service is reachable
at from inside the cluster: `<service>.<namespace>.svc.cluster.local`. e.g.
`llama-router.llms.svc.cluster.local:8080`. Any Service can also be reached
by its short name alone (`llama-router`) from a pod in the *same* namespace.

**GGUF** — the file format llama.cpp loads quantized models from (a single
binary file bundling weights + metadata). When this project's docs say
"the model", for llama.cpp-served variants they usually mean a specific
`.gguf` file.

**GPU handoff** — this project's term for freeing the single physical GPU
from one consumer so another can use it deterministically: drain traffic →
scale the GPU-consuming Deployment to zero → **confirm** the GPU is
actually free (not just "scaled down," which doesn't guarantee VRAM is
released yet) → repoint LiteLLM's model alias at the new target → restart
LiteLLM. Automated by `model-panel`; see spec **012**.

**hermes-agent-master** — the *old*, now-disabled (`replicas: 0`)
Kubernetes Deployment of the Hermes gateway, kept only as an emergency
rollback path after Hermes moved to a native host systemd install (spec
**004**). Never scale it up while the real `hermes-gateway.service` is
running — same Telegram bot, two processes.

**Hermes / Hermes gateway** — the assistant itself ("Jarvis" is its
persona/voice, Hermes is the underlying software). Runs as a host systemd
service on `trantor`, not in Kubernetes. See
[Subsystems in depth](architecture/README.md#hermes-gateway-host).

**HERMES_HOME** — the live-state directory for the Hermes gateway
(`~/.hermes/`): config, session DB, plugin data, audit logs. **Not** the
same as the Hermes source code checkout (`~/.hermes/hermes-agent/`) — one
is state, the other is code.

**hostNetwork** — a pod setting that binds it directly to the host's own
network namespace instead of getting its own pod IP. `node-exporter`
*doesn't* use this in this project (deliberately dropped — see the
real-time-metrics design doc) even though many public examples of
node-exporter default to it; here it's reached via a normal ClusterIP
Service instead, for a smaller privilege footprint.

**hostPath** — a volume type that mounts a path from the *host's*
filesystem directly into a pod (bypassing the normal container filesystem
isolation). `node-exporter` uses read-only hostPath mounts to `/proc`,
`/sys`, `/` to read real host CPU/RAM stats — this is inherently a
privileged operation, which is why it's only ever mounted read-only here.

**Ingress** — the Kubernetes object that routes external HTTP(S) traffic
(by hostname/path) to an internal Service. This project's Ingresses all
route through **Traefik** and are paired with a `TLSOption` requiring a
client certificate (mTLS).

**intent-orchestration** — short for `hermes-intent-orchestration`, the
plugin that classifies user turns and routes bounded tasks to isolated
profiles. See [intent-orchestration modes](#intent-orchestration-modes) and
[Subsystems in depth](architecture/README.md#hermes-intent-orchestration-host-plugin).

**intent-orchestration modes** — the four autonomy levels the
intent-orchestration plugin can run in, least to most autonomous:
`disabled` (off) → `shadow` (classifies and audits, but the primary model
still handles everything — the recommended default) → `explicit` (only
delegates when the user names an allow-listed profile by name) → `auto`
(fully policy-driven; high-risk routes still stay local unless explicitly
allowed).

**journal (memory-router)** — a durable, single-writer, append-only NDJSON
file memory-router writes to when its Engram backend is unavailable, so a
`/memory/store` call never silently loses data — it's drained/replayed once
the backend recovers. This is *why* memory-router's Deployment is pinned
to `replicas: 1` + strategy `Recreate`: two writers to the same journal
file would corrupt it.

**k3s** — a lightweight, single-binary Kubernetes distribution. This
project's entire cluster is **one k3s node** running on `trantor` — not a
multi-node cloud cluster.

**Kustomize / kustomization.yaml** — a Kubernetes-native way to compose a
set of plain YAML manifests into one applyable unit, without a templating
language. Every service directory under `kubernetes/` has a
`kustomization.yaml` listing its own manifests; you apply a whole service
with `kubectl apply -k kubernetes/<service>/`.

**KV cache** — the per-token memory an LLM keeps during generation so it
doesn't have to recompute earlier tokens' attention every step. It scales
with context length and eats into the same VRAM/RAM budget as the model
weights — this is why specs 005–008 talk about reserving VRAM headroom
beyond just the model file's own size.

**large (profile)** — see [daily / large](#daily--large-profile).

**LiteLLM** — the single routing layer all model traffic (local and cloud)
passes through. Exposes an OpenAI-compatible `/v1` API on
`192.168.1.241:4000` (LAN, via MetalLB) and maps friendly **model
aliases** (`qwen3`, `qwen3.6-27b`, `cloud`, ...) to the real backend
Service each one currently points at. Model-panel's whole job is rewriting
these alias mappings.

**LoadBalancer (Service type)** — a Kubernetes Service type that gets a
real, externally-reachable IP. On a bare-metal cluster like this one (no
cloud provider to hand out IPs), that IP comes from **MetalLB** instead.

**local_base_urls** — a Hermes/plugin config key naming which model
`base_url`s count as "local" — used by the intent-orchestration classifier
to decide whether it's safe to call the classifier model directly.

**Luna / Terra / Sol** — the three isolated Hermes profiles the
intent-orchestration plugin can route bounded tasks to (spec **003**). Each
is a separate, sandboxed context so a delegated task can't see the primary
conversation's full history or credentials.

**MCP (Model Context Protocol)** — the protocol that lets an LLM agent call
external tools/data sources through a standard interface, either over
stdio (a local subprocess) or HTTP. Both `memory-router` and
`brave-search-mcp` expose an MCP surface; Hermes is an MCP *client*.

**memory-router** — the namespaced, permissioned memory-routing layer in
front of Engram (spec **014**). **Not yet deployed to the live cluster** as
of this doc — see
[Subsystems in depth](architecture/README.md#memory-router-k8s-mcps--code-merged-not-yet-deployed).

**MetalLB** — the software that hands out real LAN IPs to `LoadBalancer`
Services on a bare-metal cluster (no cloud provider to do it automatically).
This project's `192.168.1.240`–`.250` pool is managed by MetalLB.

**model alias** — see [LiteLLM](#litellm).

**model-panel** — the web UI for GPU handoff and live host metrics. See
[GPU handoff](#gpu-handoff) and
[Subsystems in depth](architecture/README.md#model-panel--gpu-handoff-web-panel-k8s-llms).

**mTLS (mutual TLS)** — TLS where **both sides** present a certificate, not
just the server. Used everywhere this project exposes something over the
Tailnet or LAN (Engram, memory-router, model-panel) as one of two
independent auth factors (the other being an app-level bearer token).
Enforced by Traefik's `TLSOption` CRD with `RequireAndVerifyClientCert`.

**namespace (Kubernetes)** — a way to partition a cluster into isolated
groups of resources (its own Services, Deployments, Secrets, etc.), used
here mainly for organization, not hard multi-tenant security (NetworkPolicy
enforcement is disabled cluster-wide — see
[Known drift and gotchas](architecture/README.md#known-drift-and-gotchas)).
This project uses `llms`, `mcps`, `engram` *(historical, now unused — see
that same section)*, `hermes-agents` *(legacy, scaled to zero)*.

**OpenSpec** — the spec-driven-development (SDD) planning process this repo
follows: an idea becomes a **change proposal** under `openspec/changes/`
(with `proposal.md`, `design.md`, delta specs, `tasks.md`), gets
implemented, gets a numbered `specs/NNN_*.md` file, and once done gets
**archived** to `openspec/changes/archive/` with its durable capability
specs promoted to `openspec/specs/`. It never runs in production — pure
process/documentation. See
[Subsystems in depth](architecture/README.md#openspec--the-planning-process-not-a-runtime-component).

**profile (Hermes)** — see [Luna / Terra / Sol](#luna--terra--sol). (Not to
be confused with **profile** meaning `daily`/`large` — this project reuses
the word for two unrelated concepts; context tells you which.)

**quantization** — compressing an LLM's weights to use fewer bits per
number, trading a little accuracy for much less VRAM/RAM and faster
inference. Names like `Q3_K_S`, `Q6_K`, `UD-Q6_K_XL` describe *how much*
and *how* the compression was done — roughly, more bits (Q6 vs Q3) means
better quality but a bigger file. This project runs several different
quantizations of the same underlying Qwen models specifically to trade off
VRAM budget vs. quality per use case (see specs 005–008).

**RBAC (Role-Based Access Control)** — Kubernetes's permission system for
*what a ServiceAccount is allowed to do against the cluster API itself*
(not app-level auth). `model-panel`'s `rbac.yaml` is what lets its pod
scale Deployments and read ConfigMaps/Secrets on your behalf when you click
the GPU handoff toggle.

**Secret (Kubernetes)** — a Kubernetes object for storing sensitive values
(passwords, tokens, TLS certs), base64-encoded (not encrypted at rest by
default). Every Secret this project's manifests reference
(`litellm-auth`, `engram-*`, `memory-router-*`, `brave-api-key-secret`,
`codex-shim-auth`) must be created by hand before applying the
corresponding manifest — they are **never committed** to this repo.

**Service (Kubernetes)** — a stable network identity/DNS name in front of a
set of pods, so callers never need to track individual pod IPs as they
come and go. See [ClusterIP](#clusterip) and [LoadBalancer](#loadbalancer-service-type)
for the two types used here.

**shadow mode** — see [intent-orchestration modes](#intent-orchestration-modes).

**spec (numbered)** — a file under `specs/NNN_<name>.md` documenting one
capability of the system, in sequence — the durable record of *what was
decided and why*, distinct from the code that implements it. See the
[Specs index](architecture/README.md#specs-index) for all of them.

**systemd unit / timer** — the standard Linux service-management mechanism.
A **unit** (`.service`) defines a process to run; a **timer** (`.timer`)
triggers a unit on a schedule instead of running it continuously. Every
host-level (non-Kubernetes) piece of this project — Hermes gateway, all
five knowledge-vault services, the Engram bridges — is a systemd unit, and
several (knowledge-vault's pipeline stages) are driven by timers rather
than running as long-lived daemons.

**Tailnet / Tailscale** — a private, WireGuard-based mesh VPN. This
project's Tailnet hostname is `trantor.tail07dff9.ts.net`; several services
(Engram, memory-router) are reachable over it in addition to (or instead
of) the LAN, always terminating TLS at Traefik rather than at Tailscale
itself so mTLS client-cert verification still applies.

**Taint / Toleration** — a Kubernetes mechanism to *repel* pods from a node
(taint) unless a pod explicitly says it tolerates that taint (toleration).
This project's node carries a **dynamic** `nvidia.com/gpu:NoSchedule` taint
(present or absent depending on GPU/driver state) — any DaemonSet that must
run there regardless (like `nvidia-gpu-exporter`) needs an explicit
toleration, or it can silently fail to schedule the next time the taint
reappears.

**TLSOption** — a Traefik-specific Kubernetes CRD (not a built-in
Kubernetes object) that configures TLS behavior for an Ingress, including
`RequireAndVerifyClientCert` for mTLS. Every mTLS-protected service here
(`engram`, `model-panel`, `memory-router`) has its own `*-tlsoption.yaml`.

**Traefik** — the ingress controller (reverse proxy) this cluster uses for
all external HTTP(S) traffic. Routes by the `Host` header — a common gotcha
if you're tunneling/proxying locally and the header doesn't match what
Traefik expects.

**trantor** — the hostname of the single physical machine this entire
project runs on (both the Hermes host process and the k3s cluster).

**VRAM** — video/GPU memory. The scarcest resource in this project — one
physical GPU, one VRAM budget, shared between whichever model is currently
loaded. Nearly every design decision around `daily`/`large` profiles,
quantization choice, and GPU handoff exists to manage this one constraint.

---

**Didn't find it?** Check the [architecture overview](architecture/README.md)
first — most acronyms and service names are explained in context there too.
