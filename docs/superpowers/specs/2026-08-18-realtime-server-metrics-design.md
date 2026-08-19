# Real-time server metrics gauges in model-panel

- Status: approved
- Date: 2026-08-18
- Related: `feat/gpu-handoff-web-panel`, `kubernetes/model-panel/`, `specs/001_k8s_llm_cluster.md` (§A8.0 VRAM caveat, §A9.7 observability note)

## Problem

The GPU handoff web panel (`kubernetes/model-panel`) currently shows model/session
status but no server resource utilization. We need three real-time gauges —
CPU %, RAM %, VRAM % — reflecting the actual host (`trantor`), not just the
panel's own container.

## Constraints discovered during exploration

- `model-panel`'s pod is deliberately hardened: `runAsNonRoot`, fixed
  UID/GID, `readOnlyRootFilesystem: true`, no `hostPID`, no GPU device
  access, no elevated capabilities (`kubernetes/model-panel/deployment.yaml`).
  Reading `/proc` or calling `pynvml` from inside this pod as-is would only
  ever see the container's own cgroup, not the host, and would see no GPU at
  all.
- `app/handoff/gpu.py` already documents the same tradeoff for GPU
  presence detection: it deliberately avoids `nvidia-smi`/host device
  inspection and uses the Kubernetes API instead ("Host device inspection
  would need privileges the panel must not have").
- The cluster (`trantor`) is a **single control-plane node** with exactly
  **one GPU** (`nvidia.com/gpu: "1"` node capacity). That GPU unit is the
  scarce resource the whole model-panel handoff feature exists to manage
  between vLLM/llama-service workloads.
- No Prometheus/Grafana stack, node-exporter, or GPU exporter exists yet in
  this cluster. `specs/001_k8s_llm_cluster.md` §A9.7 already flagged
  `kube-prometheus-stack` + `nvidia-dcgm-exporter` as a non-blocking future
  improvement — this change implements a narrower slice of that idea scoped
  to exactly what's needed today.

## Decisions

1. **No full Prometheus/Grafana stack.** Single-node cluster, three numbers
   needed — the panel backend scrapes exporter `/metrics` endpoints directly
   and parses the handful of series it needs. A full `kube-prometheus-stack`
   install would be disproportionate to the ask.
2. **`nvidia_gpu_exporter` (utkuozdemir), not `nvidia-dcgm-exporter`.**
   DCGM targets datacenter GPUs and requires the DCGM daemon; the cluster
   has a single consumer RTX 4070. `nvidia_gpu_exporter` wraps `nvidia-smi`
   directly and is the lighter, better-fitting choice for this hardware.
3. **GPU exporter must not request the `nvidia.com/gpu` Kubernetes
   resource.** Doing so would consume the node's only GPU unit and starve
   every other GPU workload the panel is meant to manage. This is a
   deliberate, narrowly-scoped exception to the hardening pattern used
   elsewhere in this project — approved by the user for this pod only.

   > **Amendment (implementation, 2026-08-18):** the mechanism below —
   > mounting host GPU character devices and driver libraries by hand via
   > `hostPath` — was the design-time sketch but was **not** what got
   > built. The implementation plan checked `nvidia_gpu_exporter`'s own
   > upstream deployment docs and found the exporter's officially
   > recommended way to get GPU visibility without reserving hardware:
   > `runtimeClassName: nvidia` + `NVIDIA_VISIBLE_DEVICES=all` +
   > `NVIDIA_DRIVER_CAPABILITIES=utility` (the cluster already has the
   > `nvidia` RuntimeClass installed). This is less privileged than the
   > hand-mounted `hostPath` approach below and needs no guessing at host
   > library paths. **Do not "restore" the hostPath mounts described next
   > — they were superseded, not missed.** See
   > `kubernetes/model-panel/gpu-exporter.yaml` for what's actually
   > deployed.

   The original sketch, kept here for context only: the exporter DaemonSet
   would mount the host's GPU character devices (`/dev/nvidia0`,
   `/dev/nvidiactl`, `/dev/nvidia-uvm`) and the host's NVIDIA driver
   binaries/libraries directly via `hostPath`, bypassing the device-plugin
   resource accounting entirely.
4. **`node-exporter` (`prom/node-exporter`) for CPU/RAM**, standard
   DaemonSet with read-only `hostPath` mounts to `/proc`, `/sys`, `/`.
   No special privileges beyond read-only host filesystem access, which is
   the exporter's normal, well-understood deployment shape.

   > **Amendment (implementation, 2026-08-18):** `hostNetwork: true`
   > (in the original sketch below) was dropped — the panel reaches this
   > exporter through its ClusterIP Service, so binding to the host's
   > network namespace was never actually needed and only would have
   > widened the privilege footprint for no benefit. See
   > `kubernetes/model-panel/node-exporter.yaml`.
5. **Panel backend adds one new endpoint, `GET /api/metrics`**, following
   the existing bearer-auth pattern of `GET /api/status`. It fetches both
   exporters' `/metrics` text over the cluster network (via each
   DaemonSet's ClusterIP Service), computes:
   - `cpu_pct` from `node_cpu_seconds_total` (non-idle modes over total,
     aggregated across cores)
   - `ram_pct` from `1 - node_memory_MemAvailable_bytes /
     node_memory_MemTotal_bytes`
   - `vram_pct` from `nvidia_smi_memory_used_bytes /
     nvidia_smi_memory_total_bytes`

   and returns `{"cpu_pct": float|null, "ram_pct": float|null, "vram_pct":
   float|null}`.
6. **Frontend reuses the existing poll loop shape** (`panel.js`,
   `POLL_INTERVAL_MS = 2000`): a second `setInterval` (or folded into the
   same tick) calls `/api/metrics` and updates a new `#metrics` section in
   `index.html` with three simple bar/gauge elements. CSS follows the
   existing ok/warn/bad badge palette: green `<70%`, yellow `70–90%`, red
   `>90%`; `null` renders as a distinct gray "unknown" state instead of
   breaking the section.

## Architecture

```
trantor node
├── node-exporter (DaemonSet, ClusterIP Service, :9100)      — reads /proc, /sys
├── nvidia_gpu_exporter (DaemonSet, runtimeClassName: nvidia)  — wraps nvidia-smi
│
└── model-panel pod (unchanged hardening)
    ├── GET /api/metrics  → scrapes both exporters' Services, parses, returns JSON
    └── panel.js           → polls /api/metrics every 2s, renders 3 gauges
```

No new cluster-wide services beyond the two DaemonSets + their ClusterIP
Services. No change to `model-panel`'s own `deployment.yaml` security
context — it stays exactly as hardened as it is today; it only gains an
outbound HTTP call to two in-cluster Services.

## Error handling

If either exporter is unreachable or returns unparseable text,
`/api/metrics` sets that metric to `null` rather than failing the whole
response (same defensive shape as `/api/status`). The frontend renders a
`null` gauge as gray "unknown" and leaves the other two gauges functioning
normally.

## Testing

- Backend: unit tests feeding fixture Prometheus-text bodies (captured
  real `node_exporter`/`nvidia_gpu_exporter` output shape) into the parser,
  asserting correct `%` math for CPU/RAM/VRAM, and asserting `null` on
  malformed/missing text or a simulated connection failure per exporter
  independently.
- No integration test against real NVIDIA hardware; `nvidia_gpu_exporter`
  output is mocked via fixture text, matching how `gpu.py`'s existing tests
  mock the Kubernetes API rather than touching real GPU state.
- Manifest tests (`test_rbac_manifest.py`-style) for the two new DaemonSets
  verifying: no `nvidia.com/gpu` resource request on the GPU exporter, and
  correct read-only mounts on `node-exporter`.

## Out of scope

- Historical/graphed metrics, alerting, or a Grafana dashboard — this is
  live current-value gauges only, per the original ask.
- Multi-node support — the cluster has one node today; DaemonSets already
  scale correctly if that changes later, no extra work implied.
