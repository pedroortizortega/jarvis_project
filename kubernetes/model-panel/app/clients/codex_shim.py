"""Client for `codex-shim`'s `/internal/session` status endpoint, plus the
fail-closed precondition (D17): `model-panel` MUST check session status
before any switch-to-Cloud mutation and MUST perform zero cluster
mutations when the session is not switchable.

Replaces the removed `clients/openai.py` (Amendment 1 — Codex OAuth shim
replaces the pay-per-use OpenAI API key / spend indicator).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULT_BASE_URL = "http://codex-shim.llms.svc.cluster.local:8080"
SESSION_PATH = "/internal/session"

# D17 / D8': only these two session states permit a switch to Cloud.
ALLOWED_SESSION_STATES = {"valid", "expiring_soon"}


class SwitchBlocked(Exception):
    """Raised by `assert_switch_to_cloud_allowed` when the Codex session is
    not switchable (or the shim is unreachable). The caller MUST NOT have
    performed any cluster mutation before this is raised."""

    def __init__(self, reason: str, session_state: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.session_state = session_state


class CodexShimClient:
    """Thin HTTP client for `codex-shim`'s `/internal/session` endpoint.

    The `http_client` is injected (any object exposing `.get(url, timeout=)`
    that returns an httpx-like Response) so tests never need a real network
    call.
    """

    def __init__(self, http_client: Any, base_url: str = DEFAULT_BASE_URL, timeout: float = 5.0):
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def get_session_status(self) -> Dict[str, Any]:
        resp = self._http.get(f"{self._base_url}{SESSION_PATH}", timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()


def assert_switch_to_cloud_allowed(client: Any) -> None:
    """Fail-closed precondition (D17). Calls `client.get_session_status()`
    and raises `SwitchBlocked` unless the reported state is `valid` or
    `expiring_soon`. Any transport/protocol error (including a timeout or
    an unreachable shim) is treated as not-switchable, never as "proceed
    anyway". MUST be called before any K8s API call."""
    try:
        status = client.get_session_status()
    except Exception as exc:
        raise SwitchBlocked(f"codex-shim unreachable: {exc}") from exc

    state = status.get("state") if isinstance(status, dict) else None
    if state not in ALLOWED_SESSION_STATES:
        reason = status.get("reason") if isinstance(status, dict) else None
        raise SwitchBlocked(
            reason or f"codex session not switchable: {state}", session_state=state
        )
