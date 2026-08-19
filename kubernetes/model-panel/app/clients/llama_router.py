"""Client for `llama-router`'s request-triggered autoload preload path
(D18). This is the in-cluster equivalent of `switch-model.sh`'s router
activation call (lines 52-60): a blocking `max_tokens: 1` completion loads
the target preset (the router's `--models-max 1 --models-autoload` args
evict the previously loaded one), followed by a readiness confirmation via
the same `GET /health?model=<preset>&autoload=false` probe form the
router's own `startupProbe` uses (`deployment-router.yaml:86`).
"""
from __future__ import annotations

from typing import Any

DEFAULT_BASE_URL = "http://llama-router.llms.svc.cluster.local:8080/v1"
DEFAULT_TIMEOUT_SECONDS = 300  # matches design's `router_ready_timeout=300`

CHAT_COMPLETIONS_PATH = "/chat/completions"
HEALTH_PATH = "/health"


class RouterPreloadError(Exception):
    """Raised when a preload or readiness confirmation call fails. The
    caller MUST treat this as a step failure and unwind, never proceed to
    the alias patch."""


class LlamaRouterClient:
    """Thin HTTP client for `llama-router`'s preload/confirm endpoints.

    `http_client` is injected (any object exposing `.post(url, json=,
    headers=, timeout=)` / `.get(url, params=, headers=, timeout=)` that
    returns an httpx-like Response) so tests never need a real network
    call — same injection pattern as `codex_shim.CodexShimClient`.
    """

    def __init__(
        self,
        http_client: Any,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def preload(self, preset: str) -> None:
        """Blocking call that loads `preset` on the router. Mirrors
        `switch-model.sh`'s exact request body — the request only returns
        once the model is fully loaded (`--models-autoload`)."""
        try:
            resp = self._http.post(
                f"{self._base_url}{CHAT_COMPLETIONS_PATH}",
                json={
                    "model": preset,
                    "messages": [{"role": "user", "content": "OK"}],
                    "max_tokens": 1,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
                headers=self._headers(),
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except Exception as exc:
            raise RouterPreloadError(f"preload({preset!r}) failed: {exc}") from exc

    def confirm_loaded(self, preset: str) -> bool:
        """Confirms `preset` is loaded via the same probe form as the
        router's own `startupProbe`."""
        try:
            resp = self._http.get(
                f"{self._base_url}{HEALTH_PATH}",
                params={"model": preset, "autoload": "false"},
                headers=self._headers(),
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except Exception as exc:
            raise RouterPreloadError(f"confirm_loaded({preset!r}) failed: {exc}") from exc
        return True
