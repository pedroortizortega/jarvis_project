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


def test_parse_node_cpu_totals_skips_malformed_float_values():
    """Test that malformed float values (e.g., '1e', '1.2.3') are skipped
    rather than raising ValueError. One bad line should not kill aggregation."""
    text = """\
# TYPE node_cpu_seconds_total counter
node_cpu_seconds_total{cpu="0",mode="idle"} 1000.5
node_cpu_seconds_total{cpu="0",mode="user"} 1e
node_cpu_seconds_total{cpu="1",mode="idle"} 980.0
node_cpu_seconds_total{cpu="1",mode="user"} 210.0
"""
    # Should skip the malformed '1e' line and sum the rest.
    idle, non_idle = parse_node_cpu_totals(text)
    # 1000.5 + 980.0 = 1980.5 (idle)
    # 210.0 (non_idle, the '1e' line is skipped)
    assert idle == pytest.approx(1980.5)
    assert non_idle == pytest.approx(210.0)


def test_parse_single_gauge_reads_labeled_and_unlabeled_metrics():
    assert parse_single_gauge(NODE_EXPORTER_TEXT, "node_memory_MemTotal_bytes") == pytest.approx(1.6e10)
    assert parse_single_gauge(GPU_EXPORTER_TEXT, "nvidia_smi_memory_used_bytes") == pytest.approx(706740224.0)


def test_parse_single_gauge_returns_none_when_missing():
    assert parse_single_gauge(NODE_EXPORTER_TEXT, "does_not_exist") is None


def test_parse_single_gauge_returns_none_on_malformed_float_value():
    """Test that malformed float values are treated as absent (None)
    rather than raising ValueError."""
    text = """\
# TYPE node_memory_MemTotal_bytes gauge
node_memory_MemTotal_bytes 1.2.3
"""
    result = parse_single_gauge(text, "node_memory_MemTotal_bytes")
    assert result is None


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
