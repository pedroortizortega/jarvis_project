from __future__ import annotations

import base64
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import pytest

# Allow `import app.xxx` when tests run from the repo root or this directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def jwt_with_exp(exp: float) -> str:
    """Build a syntactically-valid (unsigned) JWT with only an `exp` claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        ('{"exp": %d}' % int(exp)).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}."


class FakeSecret:
    def __init__(self, data: Dict[str, str]):
        self.data = data


class FakeCoreV1Api:
    """In-memory stand-in for kubernetes.client.CoreV1Api, scoped to Secrets."""

    def __init__(self, initial: Optional[Dict[str, str]] = None):
        # initial: plain (undecoded) key->value map, will be base64-encoded
        self._secrets: Dict[str, Dict[str, Dict[str, str]]] = {}
        if initial is not None:
            self.seed("codex-shim-auth", "llms", initial)
        self.patch_calls: list = []

    def seed(self, name: str, namespace: str, plain_data: Dict[str, str]) -> None:
        encoded = {
            k: base64.b64encode(v.encode("utf-8")).decode("ascii")
            for k, v in plain_data.items()
        }
        self._secrets.setdefault(namespace, {})[name] = encoded

    def read_namespaced_secret(self, name: str, namespace: str):
        ns = self._secrets.get(namespace, {})
        if name not in ns:

            class _NotFound(Exception):
                status = 404

            raise _NotFound(f"secret {namespace}/{name} not found")
        return FakeSecret(dict(ns[name]))

    def patch_namespaced_secret(self, name: str, namespace: str, body: Dict[str, Any]):
        self.patch_calls.append({"name": name, "namespace": namespace, "body": body})
        ns = self._secrets.setdefault(namespace, {})
        existing = dict(ns.get(name, {}))
        existing.update(body.get("data", {}))
        ns[name] = existing
        return FakeSecret(existing)


@pytest.fixture
def fake_core_v1():
    return FakeCoreV1Api()


def mock_token_transport(responder):
    """Build an httpx.MockTransport that calls `responder(request)` -> httpx.Response."""

    def handler(request: httpx.Request) -> httpx.Response:
        return responder(request)

    return httpx.MockTransport(handler)
