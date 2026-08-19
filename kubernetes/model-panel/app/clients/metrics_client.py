"""Real-time server metrics (CPU %, RAM %, VRAM %) for the panel's
`GET /api/metrics`. Scrapes `node-exporter` (CPU/RAM) and
`nvidia-gpu-exporter` (VRAM) `/metrics` endpoints directly — no Prometheus
server, matching the design's "single node, three numbers" scope (see
`docs/superpowers/specs/2026-08-18-realtime-server-metrics-design.md`).

CPU is a cumulative counter (`node_cpu_seconds_total`); a single scrape
can't produce a percentage, so `MetricsClient` keeps the previous sample in
memory and computes a delta-based percentage across two calls. The first
call after startup therefore always returns `cpu_pct: None` — the frontend
already renders `None` as a distinct "unknown" gauge state, so this needs
no special-casing there.

Same HTTP-client-injection pattern as `LlamaRouterClient`
(`app/clients/llama_router.py`): `http_client` is any object exposing
`.get(url, timeout=)` returning a response with `.text`/`.raise_for_status()`,
so tests never need a real network call.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Dict, Optional, Tuple

DEFAULT_NODE_EXPORTER_BASE_URL = "http://node-exporter.llms.svc.cluster.local:9100"
DEFAULT_GPU_EXPORTER_BASE_URL = "http://nvidia-gpu-exporter.llms.svc.cluster.local:9835"
# Both exporters run on the same node as the panel, so anything slower than
# this is already too slow to be useful for a 2-second-refresh gauge — and
# a single `/api/metrics` request does two sequential scrapes, so a high
# timeout here can block a threadpool worker for up to 2x its value.
DEFAULT_METRICS_TIMEOUT_SECONDS = 1.5

_CPU_METRIC_RE = re.compile(
    r'^node_cpu_seconds_total\{[^}]*mode="([^"]+)"[^}]*\}\s+([0-9.eE+\-]+)', re.MULTILINE
)


def parse_node_cpu_totals(text: str) -> Tuple[Optional[float], Optional[float]]:
    """Sums `node_cpu_seconds_total` across all cores into (idle, non_idle)
    buckets. Returns (None, None) if the metric isn't present at all
    (malformed/unexpected exporter output). Skips any lines with malformed
    float values rather than raising.

    Every mode other than "idle" — including "iowait" and "steal" — is
    deliberately counted as non_idle/busy here. This differs from the
    common convention of treating idle + iowait as not-busy; it's a
    conscious choice for this simple gauge, not an oversight."""
    idle = 0.0
    non_idle = 0.0
    found = False
    for match in _CPU_METRIC_RE.finditer(text):
        mode = match.group(1)
        try:
            value = float(match.group(2))
        except ValueError:
            # Malformed float value (e.g., '1e', '1.2.3') — skip this line.
            continue
        found = True
        if mode == "idle":
            idle += value
        else:
            non_idle += value
    if not found:
        return None, None
    return idle, non_idle


def parse_single_gauge(text: str, metric_name: str) -> Optional[float]:
    """Reads the first sample of a single-value gauge metric, with or
    without labels (`node_memory_MemTotal_bytes 100` or
    `nvidia_smi_memory_used_bytes{uuid="..."} 100`). Returns None if the
    metric line isn't present or the value is malformed."""
    pattern = re.compile(
        rf'^{re.escape(metric_name)}(?:\{{[^}}]*\}})?\s+([0-9.eE+\-]+)', re.MULTILINE
    )
    match = pattern.search(text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        # Malformed float value (e.g., '1e', '1.2.3') — treat as absent.
        return None


def compute_cpu_pct_from_deltas(
    prev_idle: float, prev_non_idle: float, cur_idle: float, cur_non_idle: float
) -> Optional[float]:
    """% CPU busy over the interval between two `node_cpu_seconds_total`
    samples. Returns None if no time has elapsed (delta is zero/negative —
    e.g. exporter restarted and its counter reset)."""
    delta_idle = cur_idle - prev_idle
    delta_non_idle = cur_non_idle - prev_non_idle
    total = delta_idle + delta_non_idle
    if total <= 0:
        return None
    return round((delta_non_idle / total) * 100, 1)


def compute_ram_pct(mem_total: Optional[float], mem_available: Optional[float]) -> Optional[float]:
    if not mem_total:
        return None
    if mem_available is None:
        return None
    return round((1 - mem_available / mem_total) * 100, 1)


def compute_vram_pct(used: Optional[float], total: Optional[float]) -> Optional[float]:
    if not total:
        return None
    if used is None:
        return None
    return round((used / total) * 100, 1)


class MetricsClient:
    """Scrapes node-exporter + nvidia-gpu-exporter and returns
    `{"cpu_pct", "ram_pct", "vram_pct"}` — each `Optional[float]`, `None`
    when the source exporter is unreachable or its text is unparseable.
    Never raises; `GET /api/metrics` can call `fetch_metrics()` unguarded.
    """

    def __init__(
        self,
        http_client: Any,
        node_exporter_url: str = DEFAULT_NODE_EXPORTER_BASE_URL,
        gpu_exporter_url: str = DEFAULT_GPU_EXPORTER_BASE_URL,
        timeout: float = DEFAULT_METRICS_TIMEOUT_SECONDS,
    ):
        self._http = http_client
        self._node_url = node_exporter_url.rstrip("/")
        self._gpu_url = gpu_exporter_url.rstrip("/")
        self._timeout = timeout
        self._prev_cpu: Optional[Tuple[float, float]] = None
        # `GET /api/metrics` is a sync route, so FastAPI runs it in a
        # threadpool — concurrent requests (multiple tabs, overlapping
        # polls) can race on the read-modify-write of `_prev_cpu` below.
        self._lock = threading.Lock()

    def _fetch_text(self, base_url: str) -> Optional[str]:
        try:
            resp = self._http.get(f"{base_url}/metrics", timeout=self._timeout)
            resp.raise_for_status()
            return resp.text
        except Exception:
            return None

    def fetch_metrics(self) -> Dict[str, Optional[float]]:
        node_text = self._fetch_text(self._node_url)
        gpu_text = self._fetch_text(self._gpu_url)

        cpu_pct: Optional[float] = None
        ram_pct: Optional[float] = None
        if node_text is not None:
            idle, non_idle = parse_node_cpu_totals(node_text)
            if idle is not None and non_idle is not None:
                # Only the shared-state read/compute/write needs the lock;
                # the HTTP fetches above already happened outside it so
                # concurrent requests don't serialize on network I/O.
                with self._lock:
                    if self._prev_cpu is not None:
                        cpu_pct = compute_cpu_pct_from_deltas(
                            self._prev_cpu[0], self._prev_cpu[1], idle, non_idle
                        )
                    self._prev_cpu = (idle, non_idle)
            mem_total = parse_single_gauge(node_text, "node_memory_MemTotal_bytes")
            mem_available = parse_single_gauge(node_text, "node_memory_MemAvailable_bytes")
            ram_pct = compute_ram_pct(mem_total, mem_available)

        vram_pct: Optional[float] = None
        if gpu_text is not None:
            vram_used = parse_single_gauge(gpu_text, "nvidia_smi_memory_used_bytes")
            vram_total = parse_single_gauge(gpu_text, "nvidia_smi_memory_total_bytes")
            vram_pct = compute_vram_pct(vram_used, vram_total)

        return {"cpu_pct": cpu_pct, "ram_pct": ram_pct, "vram_pct": vram_pct}
