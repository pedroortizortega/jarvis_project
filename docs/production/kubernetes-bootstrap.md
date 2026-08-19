# Production runbook: bootstrapping the Kubernetes cluster from scratch

Stand up the whole k3s cluster and every workload on a fresh machine, in the
order that actually works — later steps depend on earlier ones (GPU before
model-serving, namespaces before secrets, LiteLLM before anything that
routes through it). This runbook sequences and cross-links the detailed
steps that already live in specs 001, 011, 012, 014 and each service's own
deep-dive under `docs/services/` — it does not repeat their content, it
orders it.

**Read [Architecture overview](../architecture/README.md) first** if you
haven't — this runbook assumes you know what each piece is.

## Quick path

1. [Phase 0 — OS prep](#phase-0--os-prep)
2. [Phase 1 — k3s](#phase-1--k3s)
3. [Phase 2 — GPU](#phase-2--gpu-nvidia-driver--device-plugin)
4. [Phase 3 — MetalLB](#phase-3--metallb)
5. [Phase 4 — namespaces](#phase-4--namespaces)
6. [Phase 5 — model serving (llms)](#phase-5--model-serving-llms)
7. [Phase 6 — model-panel](#phase-6--model-panel)
8. [Phase 7 — mcps (Engram, memory-router, brave-search)](#phase-7--mcps-engram-memory-router-brave-search)
9. [Final checklist](#final-validation-checklist)

## Assumptions

- **Single node.** This runbook targets what's actually running today: one
  machine (`trantor`) as both control-plane and the only worker. Spec
  **001** originally planned Raspberry Pi worker nodes in a separate
  `hermes-agents` namespace — that never materialized; Hermes moved to a
  native host install instead (spec **004**). If you're adding real worker
  nodes later, spec 001's Part B (RPi join) still applies, but nothing in
  this repo currently depends on it.
- Linux host, NVIDIA GPU, on a LAN you control the IP range for.
- You're comfortable running commands as your own user with `sudo` for
  system-level steps — nothing here should run entirely as root.

## Phase 0 — OS prep

Full commands (netplan/NetworkManager static IP, swap off, kernel modules,
sysctl, firewall — separate blocks for Debian/Ubuntu vs. Arch/CachyOS) are
in **specs/001_k8s_llm_cluster.md, section A0**. Do all of it before
installing k3s — swap must be off and the sysctl/module settings must be in
place first, or kubelet won't start cleanly.

If you're on CachyOS/Arch like `trantor`, use the `#### A0-alt` and
`#### Firewall en CachyOS/Arch` subsections specifically — the nftables
policy-drop ruleset there is the one actually in production, not a
theoretical alternative.

## Phase 1 — k3s

```bash
curl -sfL https://get.k3s.io | sh -s - \
  --write-kubeconfig-mode 644 \
  --disable servicelb \
  --node-name pc-master
```

`--disable servicelb` is required — MetalLB (Phase 3) replaces it and the
two conflict if both run. Full A1/A2 steps (including the `fish`-shell
`KUBECONFIG` variant, since `trantor`'s shell is fish, not bash) are in
**specs/001, sections A1–A2**.

**Known failure mode, already hit in production on this exact host** (dual
dynamic IPv6 via Wi-Fi SLAAC confusing the node-registration controller,
then a CNI-path mismatch after installing the NVIDIA container toolkit) —
see **specs/001, the "Troubleshooting: el nodo queda NotReady..." block
under A5** for both symptoms and their exact fixes before you assume
something else is wrong. If your node cycles `Ready`↔`NotReady` every ~15
minutes, that's the first thing to check, not a networking mystery to
re-diagnose from scratch.

## Phase 2 — GPU (NVIDIA driver + device plugin)

Full commands (driver install, `nvidia-ctk runtime configure`, device
plugin DaemonSet, verification) are in **specs/001, section A5** — including
a CachyOS-specific driver variant (`nvidia-open`) and three more
already-diagnosed failure modes for this exact host (CNI config template
overwritten by the toolkit's pacman hook; device plugin needing an explicit
`RuntimeClass` because `runc` is still the default runtime even after
`nvidia-ctk` configures containerd). Read that whole A5 troubleshooting
block before improvising — all three symptoms there were real production
incidents on `trantor`, not hypotheticals.

Verify before continuing:

```bash
kubectl describe node trantor | grep -A5 Allocatable
# must show: nvidia.com/gpu: 1
```

Everything downstream (vLLM, llama.cpp variants, `nvidia-gpu-exporter`)
depends on this being correct — don't proceed until it is.

## Phase 3 — MetalLB

```bash
kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.14.8/config/manifests/metallb-native.yaml
kubectl -n metallb-system rollout status deployment/controller
```

Then apply an `IPAddressPool` + `L2Advertisement` for a LAN range you own
(not your router's DHCP range) — exact YAML in **specs/001, section A6**.
Production uses `192.168.1.240-192.168.1.250`; LiteLLM gets `.241`, Traefik
gets `.240` (Traefik's LoadBalancer IP comes bundled with k3s — you don't
apply it separately, but it draws from this same MetalLB pool once it
exists).

## Phase 4 — namespaces

```bash
kubectl create namespace llms
kubectl create namespace mcps
```

`mcps` is what Engram, memory-router, and brave-search-mcp all actually
live in — see [Known drift and gotchas](../architecture/README.md#known-drift-and-gotchas)
for why `kubernetes/engram/namespace.yaml` says `mcps` and not `engram`
(fixed in PR #16; if you're on an older checkout, the namespace name in
that file is what to trust, not the directory name).

## Phase 5 — model serving (`llms`)

This is the biggest phase. Full detail (secrets, ordering, exact
`kubectl apply` sequence, model-download jobs, LiteLLM alias table,
`codex-shim` bootstrap) is in
**[docs/services/llama-service.md](../services/llama-service.md)** —
follow its "Deploying from scratch" section directly rather than
duplicating it here. In short, the order matters:

1. Secrets first (`llms/litellm-auth`, and `llms/codex-shim-auth` +
   `llms/codex-shim-key` if you want Cloud mode at all).
2. `kubernetes/llama-service/` manifests (PVCs, Deployments at `replicas: 0`
   except `llama-router`, the router ConfigMap).
3. The model-download Jobs you actually need — at minimum the **daily**
   model, since `llama-router` can't come up without it.
4. `kubernetes/proxy/litellm-config.yaml` — the single routing layer
   everything else in the cluster and Hermes itself will call through.
5. `kubernetes/codex-shim/` — only needed if you want Cloud mode
   (model-panel's toggle, or LiteLLM's `cloud` alias) to work at all.

Verify with the exact `curl .../v1/models` command in that doc before
moving on — nothing past this point works if LiteLLM isn't answering.

## Phase 6 — model-panel

Once Phase 5 is live, model-panel's GPU-handoff toggle and metrics gauges
have something real to control and measure. Full sequence — secret, image
build/import, both exporter DaemonSets, the panel Deployment itself — is in
**[docs/services/model-panel.md § Deploying](../services/model-panel.md#deploying)**.

## Phase 7 — `mcps` (Engram, memory-router, brave-search)

**Engram Cloud** is the one piece here that's actually live in production —
full install (CA/certs, secrets, manifests, verification) is in
**`docs/engram-cloud/installation.md`**.

**memory-router** is code-complete and tested but **not yet deployed**
anywhere, including in this runbook's own reference production. Its
"Deploying" section
([docs/services/memory-router.md § Deploying](../services/memory-router.md#deploying))
documents the intended sequence and flags exactly what's still missing
(an unbuilt image, a placeholder ingress hostname) — read it before
attempting this in a new environment; don't assume it's a copy-paste-ready
step the way the others are.

**brave-search-mcp** — see `kubernetes/mcps/brave-mcp-activation-guide.md`
for its own short activation steps (mainly: a `BRAVE_API_KEY` secret).

## Final validation checklist

```bash
kubectl get nodes -o wide                        # trantor, Ready
kubectl describe node trantor | grep -A5 Allocatable   # nvidia.com/gpu: 1
kubectl get pods -n llms -o wide                  # litellm, llama-router Running; others per what you started
kubectl get pods -n mcps -o wide                  # engram-cloud, engram-postgres Running
kubectl get svc -n llms litellm                   # EXTERNAL-IP assigned, not <pending>
curl http://192.168.1.241:4000/v1/models \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY"  # lists your configured aliases
kubectl get pods -n llms -l app=model-panel       # Running, 1/1
```

If every one of those is true, the cluster is production-ready for
[Hermes to be pointed at it](hermes-gateway-install.md) — that's a
separate, host-level installation, not part of this cluster bootstrap.

## See also

- [Architecture overview](../architecture/README.md)
- [Glossary](../glossary.md)
- `specs/001_k8s_llm_cluster.md` — the founding spec this phase-by-phase
  order is extracted from, including every troubleshooting block referenced
  above
- [docs/services/](../services/) — the five per-service deep dives this
  runbook cross-links into for Phases 5–7
- [Installing Hermes gateway on the host](hermes-gateway-install.md) — the
  companion runbook for the non-Kubernetes half of this project
