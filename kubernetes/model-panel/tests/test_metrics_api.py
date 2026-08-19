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
