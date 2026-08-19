# Real-time server metrics gauges Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three real-time gauges (CPU %, RAM %, VRAM %) to the `model-panel` web UI, reflecting the actual host (`trantor`), by deploying two lightweight Prometheus exporters and having the panel backend scrape them directly.

**Architecture:** Two new DaemonSets (`node-exporter` for CPU/RAM, `nvidia-gpu-exporter` for VRAM) expose `/metrics` text in-cluster. `model-panel`'s FastAPI backend gets a new `GET /api/metrics` route that scrapes both, parses the handful of series it needs, and returns `{cpu_pct, ram_pct, vram_pct}`. The existing 2-second frontend poll loop (`panel.js`) is extended to also call this endpoint and paint three bar gauges. No Prometheus/Grafana server is installed — this is a direct scrape, matching the single-node, three-numbers scope.

**Tech Stack:** FastAPI (existing), httpx (existing dependency, no new backend deps needed), `prom/node-exporter` and `utkuozdemir/nvidia_gpu_exporter` images, vanilla JS (existing, no build step), pytest (existing).

**Spec:** `docs/superpowers/specs/2026-08-18-realtime-server-metrics-design.md`

## Global Constraints

- No Prometheus/Grafana stack — panel backend scrapes exporter `/metrics` directly (design decision 1).
- GPU exporter must **never** request the `nvidia.com/gpu` Kubernetes resource — the node has exactly one GPU unit and model-panel's handoff feature depends on it staying available to vLLM/llama-service (design decision 3). Verified against the exporter's own upstream guidance: use `runtimeClassName: nvidia` + `NVIDIA_VISIBLE_DEVICES=all` + `NVIDIA_DRIVER_CAPABILITIES=utility` instead of a resource request — this is the officially documented way to get GPU visibility without reserving hardware, and the cluster already has the `nvidia` RuntimeClass installed.
- `node-exporter` uses read-only `hostPath` mounts (`/proc`, `/sys`, `/`) — no `hostNetwork`, no `hostPID` (unnecessary for global CPU/RAM stats; keeps the privilege footprint smaller than the design doc's original sketch).
- `model-panel`'s own `deployment.yaml` security context is unchanged — it only gains two new outbound-URL env vars.
- Any metric or value that can't be read (exporter down, malformed text) becomes `null` in the API response, never an error — same defensive shape as `/api/status`.
- Namespace for both new DaemonSets + Services: `llms` (same as `model-panel`, matches existing cluster-DNS conventions like `llama-router.llms.svc.cluster.local`).
- Pin exact image tags: `quay.io/prometheus/node-exporter:v1.12.1`, `utkuozdemir/nvidia_gpu_exporter:1.14.0` (current latest releases as of 2026-08-18 — do not use `:latest`).
- Confirmed metric names (verified against upstream docs): `node_cpu_seconds_total{cpu="N",mode="..."}`, `node_memory_MemTotal_bytes`, `node_memory_MemAvailable_bytes`, `nvidia_smi_memory_used_bytes{uuid="..."}`, `nvidia_smi_memory_total_bytes{uuid="..."}`.

---

### Task 1: Prometheus metrics parser + `MetricsClient`

**Files:**
- Create: `kubernetes/model-panel/app/clients/metrics_client.py`
- Test: `kubernetes/model-panel/tests/test_metrics_client.py`

**Interfaces:**
- Consumes: nothing from other tasks (pure parsing + an injected HTTP client, same injection pattern as `app/clients/llama_router.py`'s `LlamaRouterClient`).
- Produces: `MetricsClient` class with `fetch_metrics() -> Dict[str, Optional[float]]` returning `{"cpu_pct": ..., "ram_pct": ..., "vram_pct": ...}` — this exact method and return shape is what Task 2 wires into the HTTP route.

- [ ] **Step 1: Write the failing tests**

```python
# kubernetes/model-panel/tests/test_metrics_client.py
"""Real-time server metrics (CPU/RAM/VRAM %): parses node-exporter and
nvidia_gpu_exporter Prometheus text and computes the three gauge values
model-panel's /api/metrics endpoint returns.

CPU is computed from a delta between two scrapes of the cumulative
`node_cpu_seconds_total` counter (a single scrape can't yield a rate) — see
`compute_cpu_pct_from_deltas`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.clients.metrics_client import (
    MetricsClient,
    compute_cpu_pct_from_deltas,
    compute_ram_pct,
    compute_vram_pct,
    parse_node_cpu_totals,
    parse_single_gauge,
)

NODE_EXPORTER_TEXT = """\
# HELP node_cpu_seconds_total Seconds the CPUs spent in each mode.
# TYPE node_cpu_seconds_total counter
node_cpu_seconds_total{cpu="0",mode="idle"} 1000.5
node_cpu_seconds_total{cpu="0",mode="user"} 200.25
node_cpu_seconds_total{cpu="0",mode="system"} 50.1
node_cpu_seconds_total{cpu="1",mode="idle"} 980.0
node_cpu_seconds_total{cpu="1",mode="user"} 210.0
node_cpu_seconds_total{cpu="1",mode="system"} 60.0
# HELP node_memory_MemTotal_bytes Total usable RAM.
# TYPE node_memory_MemTotal_bytes gauge
node_memory_MemTotal_bytes 1.6e+10
# HELP node_memory_MemAvailable_bytes Available RAM.
# TYPE node_memory_MemAvailable_bytes gauge
node_memory_MemAvailable_bytes 4e+09
"""

GPU_EXPORTER_TEXT = """\
# HELP nvidia_smi_memory_used_bytes memory.used
# TYPE nvidia_smi_memory_used_bytes gauge
nvidia_smi_memory_used_bytes{uuid="GPU-abc"} 7.06740224e+08
# HELP nvidia_smi_memory_total_bytes memory.total
# TYPE nvidia_smi_memory_total_bytes gauge
nvidia_smi_memory_total_bytes{uuid="GPU-abc"} 1.2884901888e+10
"""


def test_parse_node_cpu_totals_sums_idle_and_non_idle_across_cores():
    idle, non_idle = parse_node_cpu_totals(NODE_EXPORTER_TEXT)
    assert idle == pytest.approx(1980.5)
    assert non_idle == pytest.approx(520.35)


def test_parse_node_cpu_totals_returns_none_when_absent():
    idle, non_idle = parse_node_cpu_totals("node_memory_MemTotal_bytes 100\n")
    assert idle is None
    assert non_idle is None


def test_parse_single_gauge_reads_labeled_and_unlabeled_metrics():
    assert parse_single_gauge(NODE_EXPORTER_TEXT, "node_memory_MemTotal_bytes") == pytest.approx(1.6e10)
    assert parse_single_gauge(GPU_EXPORTER_TEXT, "nvidia_smi_memory_used_bytes") == pytest.approx(706740224.0)


def test_parse_single_gauge_returns_none_when_missing():
    assert parse_single_gauge(NODE_EXPORTER_TEXT, "does_not_exist") is None


def test_compute_cpu_pct_from_deltas():
    pct = compute_cpu_pct_from_deltas(prev_idle=1000.0, prev_non_idle=200.0, cur_idle=1010.0, cur_non_idle=210.0)
    assert pct == pytest.approx(50.0)


def test_compute_cpu_pct_from_deltas_returns_none_when_no_time_has_passed():
    pct = compute_cpu_pct_from_deltas(prev_idle=1000.0, prev_non_idle=200.0, cur_idle=1000.0, cur_non_idle=200.0)
    assert pct is None


def test_compute_ram_pct():
    assert compute_ram_pct(mem_total=1.6e10, mem_available=4e9) == pytest.approx(75.0)


def test_compute_ram_pct_returns_none_on_missing_input():
    assert compute_ram_pct(mem_total=None, mem_available=4e9) is None
    assert compute_ram_pct(mem_total=0, mem_available=0) is None


def test_compute_vram_pct():
    assert compute_vram_pct(used=706740224.0, total=12884901888.0) == pytest.approx(5.5, abs=0.05)


class FakeResponse:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttpClient:
    """Injected in place of httpx.Client — same pattern as
    LlamaRouterClient's tests. Maps exact URLs to canned responses so
    MetricsClient's own URL-building is exercised too."""

    def __init__(self, responses: Dict[str, FakeResponse]):
        self._responses = responses
        self.requested_urls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requested_urls.append(url)
        if url not in self._responses:
            raise RuntimeError(f"unexpected URL: {url}")
        return self._responses[url]


def test_metrics_client_fetch_metrics_first_call_has_no_cpu_pct_yet():
    http = FakeHttpClient(
        {
            "http://node-exporter.llms.svc.cluster.local:9100/metrics": FakeResponse(NODE_EXPORTER_TEXT),
            "http://nvidia-gpu-exporter.llms.svc.cluster.local:9835/metrics": FakeResponse(GPU_EXPORTER_TEXT),
        }
    )
    client = MetricsClient(http_client=http)
    result = client.fetch_metrics()
    # No previous CPU sample yet -> cpu_pct is None on the very first call.
    assert result["cpu_pct"] is None
    assert result["ram_pct"] == pytest.approx(75.0)
    assert result["vram_pct"] == pytest.approx(5.5, abs=0.05)


def test_metrics_client_fetch_metrics_second_call_computes_cpu_pct():
    second_node_text = NODE_EXPORTER_TEXT.replace(
        'node_cpu_seconds_total{cpu="0",mode="idle"} 1000.5', 'node_cpu_seconds_total{cpu="0",mode="idle"} 1010.5'
    ).replace(
        'node_cpu_seconds_total{cpu="0",mode="user"} 200.25', 'node_cpu_seconds_total{cpu="0",mode="user"} 210.25'
    )
    http = FakeHttpClient(
        {
            "http://node-exporter.llms.svc.cluster.local:9100/metrics": FakeResponse(NODE_EXPORTER_TEXT),
            "http://nvidia-gpu-exporter.llms.svc.cluster.local:9835/metrics": FakeResponse(GPU_EXPORTER_TEXT),
        }
    )
    client = MetricsClient(http_client=http)
    client.fetch_metrics()  # primes the previous-sample cache

    http._responses["http://node-exporter.llms.svc.cluster.local:9100/metrics"] = FakeResponse(second_node_text)
    result = client.fetch_metrics()
    # +10s idle, +10s user -> 50% busy over the interval.
    assert result["cpu_pct"] == pytest.approx(50.0)


def test_metrics_client_returns_null_metrics_when_exporter_unreachable():
    http = FakeHttpClient({})  # every URL raises "unexpected URL"
    client = MetricsClient(http_client=http)
    result = client.fetch_metrics()
    assert result == {"cpu_pct": None, "ram_pct": None, "vram_pct": None}


def test_metrics_client_gpu_down_does_not_affect_cpu_and_ram():
    http = FakeHttpClient(
        {"http://node-exporter.llms.svc.cluster.local:9100/metrics": FakeResponse(NODE_EXPORTER_TEXT)}
    )
    client = MetricsClient(http_client=http)
    result = client.fetch_metrics()
    assert result["ram_pct"] == pytest.approx(75.0)
    assert result["vram_pct"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kubernetes/model-panel && python -m pytest tests/test_metrics_client.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'app.clients.metrics_client'`

- [ ] **Step 3: Write the implementation**

```python
# kubernetes/model-panel/app/clients/metrics_client.py
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
from typing import Any, Dict, Optional, Tuple

DEFAULT_NODE_EXPORTER_BASE_URL = "http://node-exporter.llms.svc.cluster.local:9100"
DEFAULT_GPU_EXPORTER_BASE_URL = "http://nvidia-gpu-exporter.llms.svc.cluster.local:9835"
DEFAULT_METRICS_TIMEOUT_SECONDS = 5.0

_CPU_METRIC_RE = re.compile(
    r'^node_cpu_seconds_total\{[^}]*mode="([^"]+)"[^}]*\}\s+([0-9.eE+\-]+)', re.MULTILINE
)


def parse_node_cpu_totals(text: str) -> Tuple[Optional[float], Optional[float]]:
    """Sums `node_cpu_seconds_total` across all cores into (idle, non_idle)
    buckets. Returns (None, None) if the metric isn't present at all
    (malformed/unexpected exporter output)."""
    idle = 0.0
    non_idle = 0.0
    found = False
    for match in _CPU_METRIC_RE.finditer(text):
        mode, value = match.group(1), float(match.group(2))
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
    metric line isn't present."""
    pattern = re.compile(
        rf'^{re.escape(metric_name)}(?:\{{[^}}]*\}})?\s+([0-9.eE+\-]+)', re.MULTILINE
    )
    match = pattern.search(text)
    if not match:
        return None
    return float(match.group(1))


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kubernetes/model-panel && python -m pytest tests/test_metrics_client.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add kubernetes/model-panel/app/clients/metrics_client.py kubernetes/model-panel/tests/test_metrics_client.py
git commit -m "feat(model-panel): add node/gpu exporter metrics parser and client"
```

---

### Task 2: `GET /api/metrics` endpoint

**Files:**
- Modify: `kubernetes/model-panel/app/main.py`
- Test: Create `kubernetes/model-panel/tests/test_metrics_api.py`

**Interfaces:**
- Consumes: `MetricsClient` from Task 1 (`app.clients.metrics_client.MetricsClient`, `.fetch_metrics() -> Dict[str, Optional[float]]`).
- Produces: `GET /api/metrics` route, bearer-guarded like `/api/status`, returning `MetricsClient.fetch_metrics()`'s dict verbatim as JSON. `create_app()` gains a new optional `metrics_client: Any = None` constructor param (same optional-injection style as `codex_shim_client`/`router_client`).

- [ ] **Step 1: Write the failing tests**

```python
# kubernetes/model-panel/tests/test_metrics_api.py
"""RED/GREEN tests for `GET /api/metrics` (real-time CPU/RAM/VRAM gauges)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conftest import FakeAppsV1Api, FakeCoreV1Api, FakeCustomObjectsApi

TOKEN = "test-bearer-token"


class FakeMetricsClient:
    def __init__(self, metrics: Dict[str, Optional[float]]):
        self._metrics = metrics
        self.calls = 0

    def fetch_metrics(self) -> Dict[str, Optional[float]]:
        self.calls += 1
        return self._metrics


def build_app(monkeypatch: pytest.MonkeyPatch, metrics_client: Any, token: Optional[str] = TOKEN):
    from app.main import create_app

    if token is not None:
        monkeypatch.setenv("MODEL_PANEL_AUTH_TOKEN", token)
    else:
        monkeypatch.delenv("MODEL_PANEL_AUTH_TOKEN", raising=False)

    return create_app(
        core_v1=FakeCoreV1Api(),
        apps_v1=FakeAppsV1Api(replicas={"llama-router": 1}, available={"llama-router": 1}),
        custom_objects_api=FakeCustomObjectsApi(),
        fetch_router_slots=lambda: [],
        preload_probe=lambda alias: None,
        restart_litellm=lambda: None,
        sleep=lambda _s: None,
        metrics_client=metrics_client,
    )


def auth_headers(token: str = TOKEN) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_metrics_endpoint_returns_fetch_metrics_result(monkeypatch):
    fake = FakeMetricsClient({"cpu_pct": 42.5, "ram_pct": 60.0, "vram_pct": None})
    app = build_app(monkeypatch, fake)
    with TestClient(app) as client:
        resp = client.get("/api/metrics", headers=auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"cpu_pct": 42.5, "ram_pct": 60.0, "vram_pct": None}
    assert fake.calls == 1


def test_metrics_endpoint_rejects_missing_bearer(monkeypatch):
    fake = FakeMetricsClient({"cpu_pct": None, "ram_pct": None, "vram_pct": None})
    app = build_app(monkeypatch, fake)
    with TestClient(app) as client:
        resp = client.get("/api/metrics")
    assert resp.status_code == 401
    assert fake.calls == 0


def test_metrics_endpoint_fails_closed_when_token_unset(monkeypatch):
    fake = FakeMetricsClient({"cpu_pct": None, "ram_pct": None, "vram_pct": None})
    app = build_app(monkeypatch, fake, token=None)
    with TestClient(app) as client:
        resp = client.get("/api/metrics", headers=auth_headers())
    assert resp.status_code == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kubernetes/model-panel && python -m pytest tests/test_metrics_api.py -v`
Expected: FAIL — `create_app() got an unexpected keyword argument 'metrics_client'`

- [ ] **Step 3: Wire the endpoint**

In `kubernetes/model-panel/app/main.py`, add the import near the other client imports (after the `llama_router` import, line 41):

```python
from app.clients.llama_router import LlamaRouterClient
from app.clients.metrics_client import MetricsClient
```

Add a `metrics_client: Any = None` parameter to `create_app(...)`'s signature, right after `router_client: Any = None,` (line 127):

```python
    router_client: Any = None,
    metrics_client: Any = None,
```

Inside `create_app`, right after the existing `shim_client`/`router` construction block (after line 186's closing of the `if shim_client is None or router is None:` block, before `app.state.state_store = store` at line 188), build the default `MetricsClient` the same way the other default clients are built:

```python
    if metrics_client is None:
        import httpx

        metrics_client = MetricsClient(
            http_client=httpx.Client(),
            node_exporter_url=os.environ.get(
                "NODE_EXPORTER_BASE_URL", "http://node-exporter.llms.svc.cluster.local:9100"
            ),
            gpu_exporter_url=os.environ.get(
                "GPU_EXPORTER_BASE_URL", "http://nvidia-gpu-exporter.llms.svc.cluster.local:9835"
            ),
        )
```

Add `app.state.metrics_client = metrics_client` next to the other `app.state.*` assignments (after line 190's `app.state.router_client = router`):

```python
    app.state.router_client = router
    app.state.metrics_client = metrics_client
```

Add the route right after `api_status` (after line 392's closing `)` of `api_status`, before `@app.post("/api/switch")`):

```python
    @app.get("/api/metrics")
    def api_metrics(request: Request) -> JSONResponse:
        _check_bearer(request)
        return JSONResponse(metrics_client.fetch_metrics())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kubernetes/model-panel && python -m pytest tests/test_metrics_api.py tests/test_api.py -v`
Expected: PASS — the new tests pass and no existing `test_api.py` test regresses (`create_app`'s new param is optional with a default, so every existing call site is unaffected).

- [ ] **Step 5: Commit**

```bash
git add kubernetes/model-panel/app/main.py kubernetes/model-panel/tests/test_metrics_api.py
git commit -m "feat(model-panel): add GET /api/metrics endpoint"
```

---

### Task 3: `node-exporter` DaemonSet + Service manifests

**Files:**
- Create: `kubernetes/model-panel/node-exporter.yaml`
- Create: `kubernetes/model-panel/node-exporter-service.yaml`
- Modify: `kubernetes/model-panel/kustomization.yaml`
- Test: Create `kubernetes/model-panel/tests/test_node_exporter_manifest.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: a `node-exporter` Service reachable at `node-exporter.llms.svc.cluster.local:9100` — the exact host `MetricsClient`'s default `node_exporter_url` (Task 1) expects.

- [ ] **Step 1: Write the failing test**

```python
# kubernetes/model-panel/tests/test_node_exporter_manifest.py
"""Manifest regression test: node-exporter must only ever get read-only
host filesystem access — this is the whole justification for trusting an
unprivileged in-cluster scrape of host CPU/RAM (see design doc decision 4)."""
from __future__ import annotations

from pathlib import Path

import yaml

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "node-exporter.yaml"


def _load_daemonset() -> dict:
    docs = list(yaml.safe_load_all(MANIFEST_PATH.read_text()))
    for doc in docs:
        if doc and doc.get("kind") == "DaemonSet":
            return doc
    raise AssertionError("node-exporter DaemonSet not found in node-exporter.yaml")


def test_host_volume_mounts_are_read_only():
    daemonset = _load_daemonset()
    container = daemonset["spec"]["template"]["spec"]["containers"][0]
    mounts_by_name = {m["name"]: m for m in container["volumeMounts"]}
    volumes_by_name = {v["name"]: v for v in daemonset["spec"]["template"]["spec"]["volumes"]}

    host_mounts = [name for name, v in volumes_by_name.items() if "hostPath" in v]
    assert host_mounts, "expected at least one hostPath volume"
    for name in host_mounts:
        assert mounts_by_name[name].get("readOnly") is True, (
            f"hostPath volume {name!r} must be mounted readOnly"
        )


def test_no_gpu_resource_requested():
    daemonset = _load_daemonset()
    container = daemonset["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})
    assert "nvidia.com/gpu" not in requests
    assert "nvidia.com/gpu" not in limits
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kubernetes/model-panel && python -m pytest tests/test_node_exporter_manifest.py -v`
Expected: FAIL — `FileNotFoundError`/`AssertionError: node-exporter DaemonSet not found`

- [ ] **Step 3: Write the manifests**

```yaml
# kubernetes/model-panel/node-exporter.yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: node-exporter
  namespace: llms
  labels:
    app.kubernetes.io/name: node-exporter
    app.kubernetes.io/part-of: gpu-handoff-web-panel
spec:
  selector:
    matchLabels:
      app: node-exporter
  template:
    metadata:
      labels:
        app: node-exporter
        app.kubernetes.io/name: node-exporter
        app.kubernetes.io/part-of: gpu-handoff-web-panel
    spec:
      automountServiceAccountToken: false
      containers:
        - name: node-exporter
          image: quay.io/prometheus/node-exporter:v1.12.1
          args:
            - --path.procfs=/host/proc
            - --path.sysfs=/host/sys
            - --path.rootfs=/host/root
            - --collector.filesystem.mount-points-exclude=^/(dev|proc|sys|var/lib/docker|var/lib/kubelet)($|/)
          ports:
            - name: http
              containerPort: 9100
          resources:
            requests:
              cpu: "50m"
              memory: 64Mi
            limits:
              cpu: "200m"
              memory: 128Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: proc
              mountPath: /host/proc
              readOnly: true
            - name: sys
              mountPath: /host/sys
              readOnly: true
            - name: root
              mountPath: /host/root
              readOnly: true
      volumes:
        - name: proc
          hostPath:
            path: /proc
        - name: sys
          hostPath:
            path: /sys
        - name: root
          hostPath:
            path: /
```

```yaml
# kubernetes/model-panel/node-exporter-service.yaml
apiVersion: v1
kind: Service
metadata:
  name: node-exporter
  namespace: llms
  labels:
    app.kubernetes.io/name: node-exporter
    app.kubernetes.io/part-of: gpu-handoff-web-panel
spec:
  type: ClusterIP
  selector:
    app: node-exporter
  ports:
    - name: http
      port: 9100
      targetPort: http
```

In `kubernetes/model-panel/kustomization.yaml`, add both files to `resources`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - rbac.yaml
  - state-configmap.yaml
  - deployment.yaml
  - service.yaml
  - node-exporter.yaml
  - node-exporter-service.yaml
  - tlsoption.yaml
  - ingress.yaml
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kubernetes/model-panel && python -m pytest tests/test_node_exporter_manifest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kubernetes/model-panel/node-exporter.yaml kubernetes/model-panel/node-exporter-service.yaml kubernetes/model-panel/kustomization.yaml kubernetes/model-panel/tests/test_node_exporter_manifest.py
git commit -m "feat(model-panel): deploy node-exporter DaemonSet for CPU/RAM metrics"
```

---

### Task 4: `nvidia-gpu-exporter` DaemonSet + Service manifests

**Files:**
- Create: `kubernetes/model-panel/gpu-exporter.yaml`
- Create: `kubernetes/model-panel/gpu-exporter-service.yaml`
- Modify: `kubernetes/model-panel/kustomization.yaml`
- Test: Create `kubernetes/model-panel/tests/test_gpu_exporter_manifest.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: an `nvidia-gpu-exporter` Service reachable at `nvidia-gpu-exporter.llms.svc.cluster.local:9835` — the exact host `MetricsClient`'s default `gpu_exporter_url` (Task 1) expects.

- [ ] **Step 1: Write the failing test**

```python
# kubernetes/model-panel/tests/test_gpu_exporter_manifest.py
"""Manifest regression test: the GPU exporter must NEVER request the
`nvidia.com/gpu` Kubernetes resource. The node has exactly one GPU unit;
requesting it here would exclusively allocate it away from vLLM/
llama-service, breaking the whole point of model-panel's handoff feature
(see design doc decision 3). GPU visibility instead comes from
`runtimeClassName: nvidia` + `NVIDIA_VISIBLE_DEVICES=all`, which does not
touch the device-plugin's resource accounting."""
from __future__ import annotations

from pathlib import Path

import yaml

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "gpu-exporter.yaml"


def _load_daemonset() -> dict:
    docs = list(yaml.safe_load_all(MANIFEST_PATH.read_text()))
    for doc in docs:
        if doc and doc.get("kind") == "DaemonSet":
            return doc
    raise AssertionError("nvidia-gpu-exporter DaemonSet not found in gpu-exporter.yaml")


def test_no_gpu_resource_requested():
    daemonset = _load_daemonset()
    container = daemonset["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    assert "nvidia.com/gpu" not in resources.get("requests", {})
    assert "nvidia.com/gpu" not in resources.get("limits", {})


def test_uses_nvidia_runtime_class_for_device_visibility():
    daemonset = _load_daemonset()
    pod_spec = daemonset["spec"]["template"]["spec"]
    assert pod_spec.get("runtimeClassName") == "nvidia"
    container = pod_spec["containers"][0]
    env_by_name = {e["name"]: e["value"] for e in container.get("env", [])}
    assert env_by_name.get("NVIDIA_VISIBLE_DEVICES") == "all"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kubernetes/model-panel && python -m pytest tests/test_gpu_exporter_manifest.py -v`
Expected: FAIL — `FileNotFoundError`/`AssertionError: nvidia-gpu-exporter DaemonSet not found`

- [ ] **Step 3: Write the manifests**

```yaml
# kubernetes/model-panel/gpu-exporter.yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: nvidia-gpu-exporter
  namespace: llms
  labels:
    app.kubernetes.io/name: nvidia-gpu-exporter
    app.kubernetes.io/part-of: gpu-handoff-web-panel
spec:
  selector:
    matchLabels:
      app: nvidia-gpu-exporter
  template:
    metadata:
      labels:
        app: nvidia-gpu-exporter
        app.kubernetes.io/name: nvidia-gpu-exporter
        app.kubernetes.io/part-of: gpu-handoff-web-panel
    spec:
      automountServiceAccountToken: false
      # Deliberately NOT requesting the `nvidia.com/gpu` resource (see
      # design doc decision 3) — GPU visibility comes from the runtime
      # class + env vars below, which is the exporter's own documented
      # way to avoid exclusively allocating the node's single GPU.
      runtimeClassName: nvidia
      containers:
        - name: exporter
          image: utkuozdemir/nvidia_gpu_exporter:1.14.0
          env:
            - name: HOME
              value: /tmp
            - name: NVIDIA_VISIBLE_DEVICES
              value: all
            - name: NVIDIA_DRIVER_CAPABILITIES
              value: utility
          ports:
            - name: http
              containerPort: 9835
          resources:
            requests:
              cpu: "50m"
              memory: 32Mi
            limits:
              cpu: "200m"
              memory: 64Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
```

```yaml
# kubernetes/model-panel/gpu-exporter-service.yaml
apiVersion: v1
kind: Service
metadata:
  name: nvidia-gpu-exporter
  namespace: llms
  labels:
    app.kubernetes.io/name: nvidia-gpu-exporter
    app.kubernetes.io/part-of: gpu-handoff-web-panel
spec:
  type: ClusterIP
  selector:
    app: nvidia-gpu-exporter
  ports:
    - name: http
      port: 9835
      targetPort: http
```

In `kubernetes/model-panel/kustomization.yaml`, add both files to `resources`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - rbac.yaml
  - state-configmap.yaml
  - deployment.yaml
  - service.yaml
  - node-exporter.yaml
  - node-exporter-service.yaml
  - gpu-exporter.yaml
  - gpu-exporter-service.yaml
  - tlsoption.yaml
  - ingress.yaml
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kubernetes/model-panel && python -m pytest tests/test_gpu_exporter_manifest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kubernetes/model-panel/gpu-exporter.yaml kubernetes/model-panel/gpu-exporter-service.yaml kubernetes/model-panel/kustomization.yaml kubernetes/model-panel/tests/test_gpu_exporter_manifest.py
git commit -m "feat(model-panel): deploy nvidia-gpu-exporter DaemonSet for VRAM metrics"
```

---

### Task 5: Wire exporter URLs into `model-panel`'s own deployment

**Files:**
- Modify: `kubernetes/model-panel/deployment.yaml`

**Interfaces:**
- Consumes: the Service DNS names from Task 3 (`node-exporter.llms.svc.cluster.local:9100`) and Task 4 (`nvidia-gpu-exporter.llms.svc.cluster.local:9835`) — these already match `MetricsClient`'s hardcoded defaults (Task 1), so this task is redundant with those defaults at runtime, but explicit env vars keep `deployment.yaml` self-documenting and override-able, matching how every other backing service (`CODEX_SHIM_BASE_URL`, `LLAMA_ROUTER_BASE_URL`) is already wired.
- Produces: nothing new consumed by later tasks.

- [ ] **Step 1: Add the env vars**

In `kubernetes/model-panel/deployment.yaml`, add two entries to the container's `env` list, after `LLAMA_ROUTER_BASE_URL` (after line 51):

```yaml
            - name: LLAMA_ROUTER_BASE_URL
              value: http://llama-router.llms.svc.cluster.local:8080/v1
            - name: NODE_EXPORTER_BASE_URL
              value: http://node-exporter.llms.svc.cluster.local:9100
            - name: GPU_EXPORTER_BASE_URL
              value: http://nvidia-gpu-exporter.llms.svc.cluster.local:9835
```

- [ ] **Step 2: Verify the manifest still parses**

Run: `cd kubernetes/model-panel && python -c "import yaml; yaml.safe_load(open('deployment.yaml'))"`
Expected: no output, exit code 0 (valid YAML)

- [ ] **Step 3: Commit**

```bash
git add kubernetes/model-panel/deployment.yaml
git commit -m "feat(model-panel): wire exporter base URLs into deployment env"
```

---

### Task 6: Frontend gauges (HTML + CSS + JS)

**Files:**
- Modify: `kubernetes/model-panel/app/templates/index.html`
- Modify: `kubernetes/model-panel/app/static/panel.css`
- Modify: `kubernetes/model-panel/app/static/panel.js`
- Test: Create `kubernetes/model-panel/tests/test_static_metrics_ui.py`

**Interfaces:**
- Consumes: `GET /api/metrics`'s response shape from Task 2 (`{cpu_pct, ram_pct, vram_pct}`, each `float` or `null`).
- Produces: nothing consumed by later tasks — this is the last task in the chain.

There is no JS test runner in this repo (vanilla JS, no build step, no existing JS tests) — the automated check for this task is a lightweight Python string-membership test against the static files, catching accidental deletion/typos in element ids the JS and HTML must agree on. Real visual verification happens in Task 7 against the live cluster.

- [ ] **Step 1: Write the failing test**

```python
# kubernetes/model-panel/tests/test_static_metrics_ui.py
"""Regression test: the three gauge element ids referenced by panel.js
must exist in index.html, and panel.js must actually call /api/metrics.
A pure string-membership check — this repo has no JS test runner — but it
catches the class of bug where an id gets renamed in one file and not the
other, which `render()`/`renderGauge()` would otherwise fail on silently
(getElementById returns null, .className throws)."""
from __future__ import annotations

from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent / "app"
INDEX_HTML = (_APP_DIR / "templates" / "index.html").read_text()
PANEL_JS = (_APP_DIR / "static" / "panel.js").read_text()
PANEL_CSS = (_APP_DIR / "static" / "panel.css").read_text()

GAUGE_NAMES = ["cpu", "ram", "vram"]


def test_index_html_has_a_gauge_fill_and_value_element_per_metric():
    for name in GAUGE_NAMES:
        assert f'id="gauge-{name}-fill"' in INDEX_HTML
        assert f'id="gauge-{name}-value"' in INDEX_HTML


def test_panel_js_polls_metrics_endpoint():
    assert '"/api/metrics"' in PANEL_JS or "'/api/metrics'" in PANEL_JS


def test_panel_js_renders_each_gauge():
    for name in GAUGE_NAMES:
        assert f'"gauge-{name}-fill"' in PANEL_JS or f"'gauge-{name}-fill'" in PANEL_JS


def test_panel_css_defines_gauge_state_classes():
    for state in ["ok", "warn", "bad", "unknown"]:
        assert f".gauge-fill.{state}" in PANEL_CSS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kubernetes/model-panel && python -m pytest tests/test_static_metrics_ui.py -v`
Expected: FAIL — none of the `gauge-*` markers exist yet

- [ ] **Step 3: Add the HTML**

In `kubernetes/model-panel/app/templates/index.html`, insert a new `<section id="metrics">` right after the two badges (after line 21's `<div id="session-badge" class="badge">-</div>`, before the `<div id="profile-picker">` block):

```html
      <div id="mode-badge" class="badge">-</div>
      <div id="session-badge" class="badge">-</div>

      <section id="metrics">
        <div class="gauge" id="gauge-cpu">
          <div class="gauge-label">CPU</div>
          <div class="gauge-bar"><div class="gauge-fill unknown" id="gauge-cpu-fill"></div></div>
          <div class="gauge-value" id="gauge-cpu-value">unknown</div>
        </div>
        <div class="gauge" id="gauge-ram">
          <div class="gauge-label">RAM</div>
          <div class="gauge-bar"><div class="gauge-fill unknown" id="gauge-ram-fill"></div></div>
          <div class="gauge-value" id="gauge-ram-value">unknown</div>
        </div>
        <div class="gauge" id="gauge-vram">
          <div class="gauge-label">VRAM</div>
          <div class="gauge-bar"><div class="gauge-fill unknown" id="gauge-vram-fill"></div></div>
          <div class="gauge-value" id="gauge-vram-value">unknown</div>
        </div>
      </section>

      <div id="profile-picker">
```

- [ ] **Step 4: Add the CSS**

Append to `kubernetes/model-panel/app/static/panel.css`:

```css
#metrics {
  display: flex;
  gap: 1rem;
  margin: 1rem 0;
}

.gauge { flex: 1; min-width: 0; }

.gauge-label {
  font-size: 0.85rem;
  color: #666;
  margin-bottom: 0.25rem;
}

.gauge-bar {
  background: #eee;
  border-radius: 0.5rem;
  height: 0.6rem;
  overflow: hidden;
}

.gauge-fill {
  height: 100%;
  width: 0%;
  transition: width 0.4s ease, background-color 0.4s ease;
}

.gauge-fill.ok { background: #4caf7d; }
.gauge-fill.warn { background: #f0c419; }
.gauge-fill.bad { background: #d9534f; }
.gauge-fill.unknown { background: #bbb; }

.gauge-value {
  font-size: 0.8rem;
  color: #444;
  margin-top: 0.15rem;
}
```

- [ ] **Step 5: Add the JS**

In `kubernetes/model-panel/app/static/panel.js`, add two new functions after `render()` (after line 89's closing `}`):

```js
function gaugeClass(pct) {
  if (pct === null || pct === undefined) return "unknown";
  if (pct < 70) return "ok";
  if (pct < 90) return "warn";
  return "bad";
}

function renderGauge(name, pct) {
  const fill = el(`gauge-${name}-fill`);
  const value = el(`gauge-${name}-value`);
  const cls = gaugeClass(pct);
  fill.className = `gauge-fill ${cls}`;
  fill.style.width = (pct === null || pct === undefined ? 0 : pct) + "%";
  value.textContent = pct === null || pct === undefined ? "unknown" : `${pct.toFixed(1)}%`;
}

function renderMetrics(metrics) {
  renderGauge("cpu", metrics.cpu_pct);
  renderGauge("ram", metrics.ram_pct);
  renderGauge("vram", metrics.vram_pct);
}
```

Extend `poll()` (lines 97-105) to also fetch and render metrics, reusing the same 2-second loop instead of adding a second timer:

```js
async function poll() {
  const { status, body } = await apiGet("/api/status");
  if (status === 401 || status === 503) {
    showTokenGate(true);
    return;
  }
  showTokenGate(false);
  if (body) render(body);

  const metrics = await apiGet("/api/metrics");
  if (metrics.body) renderMetrics(metrics.body);
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd kubernetes/model-panel && python -m pytest tests/test_static_metrics_ui.py -v`
Expected: PASS

- [ ] **Step 7: Run the full backend test suite**

Run: `cd kubernetes/model-panel && python -m pytest -v`
Expected: PASS — every test from Tasks 1-6 plus all pre-existing tests green.

- [ ] **Step 8: Commit**

```bash
git add kubernetes/model-panel/app/templates/index.html kubernetes/model-panel/app/static/panel.css kubernetes/model-panel/app/static/panel.js kubernetes/model-panel/tests/test_static_metrics_ui.py
git commit -m "feat(model-panel): render CPU/RAM/VRAM gauges in the panel UI"
```

---

### Task 7: Live cluster verification

**Files:** none (verification only — may produce a follow-up commit if reality
diverges from the assumptions below, per this codebase's established
"Amendment" convention seen in `app/main.py`'s `_default_restart_litellm`
and `app/handoff/gpu.py`'s docstring).

**Interfaces:** none — this task consumes the deployed state of Tasks 3-6 and produces no interface for further tasks.

- [ ] **Step 1: Apply the manifests**

```bash
kubectl apply -k kubernetes/model-panel
kubectl -n llms rollout status daemonset/node-exporter
kubectl -n llms rollout status daemonset/nvidia-gpu-exporter
kubectl -n llms rollout status deployment/model-panel
```

Expected: all three roll out successfully.

- [ ] **Step 2: Confirm both exporters' raw output matches the assumed metric names**

```bash
kubectl -n llms run curl-check --rm -i --restart=Never --image=curlimages/curl -- \
  curl -s http://node-exporter.llms.svc.cluster.local:9100/metrics | grep -E '^node_cpu_seconds_total|^node_memory_Mem(Total|Available)_bytes'
kubectl -n llms run curl-check --rm -i --restart=Never --image=curlimages/curl -- \
  curl -s http://nvidia-gpu-exporter.llms.svc.cluster.local:9835/metrics | grep -E '^nvidia_smi_memory_(used|total)_bytes'
```

Expected: both greps return matching lines. If either metric name differs from what `app/clients/metrics_client.py` expects (Task 1's `_CPU_METRIC_RE` or the `parse_single_gauge` calls), update the parser to match reality and add a short docstring note explaining what was found live — same pattern as the existing "Found missing during live cluster verification (Amendment N)" comments elsewhere in this codebase.

- [ ] **Step 3: Confirm the panel's own endpoint reflects real values**

```bash
curl -s -H "Authorization: Bearer $MODEL_PANEL_AUTH_TOKEN" https://<panel-host>/api/metrics
```

Expected: `{"cpu_pct": <0-100 or null>, "ram_pct": <0-100>, "vram_pct": <0-100 or null>}`, with `ram_pct`/`vram_pct` non-null immediately and `cpu_pct` non-null from the second call onward (first call always returns `null` per Task 1's design).

- [ ] **Step 4: Confirm the GPU exporter didn't consume the GPU allocation**

```bash
kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}'
```

Expected: `1` — unchanged from before this change. If it dropped to `0`, the `nvidia-gpu-exporter` DaemonSet is requesting the GPU resource somewhere and must be fixed before proceeding (this would silently block every vLLM/llama-service GPU switch).

- [ ] **Step 5: Visually confirm the gauges in the browser**

Open the panel, confirm three gauges render, update roughly every 2 seconds, and turn yellow/red under real CPU/GPU load (e.g. trigger a local model switch and watch VRAM climb).

- [ ] **Step 6: If any assumption above didn't hold, commit the fix**

```bash
git add -A
git commit -m "fix(model-panel): correct metrics parsing found during live cluster verification"
```

If everything matched, no commit is needed for this task.
