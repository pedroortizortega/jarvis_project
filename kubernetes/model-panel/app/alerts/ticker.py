"""Daemon-thread session-degradation ticker (D-09/D-10/D-16/D-17/D-19).

Polls `codex-shim`'s session status through the *existing*
`CodexShimClient` on its own schedule, entirely off the request path —
`/api/status` takes zero diff. A `threading.Thread(daemon=True)` +
`threading.Event`, started/stopped from a FastAPI `lifespan` context
manager, never the `app.state.executor` used by multi-minute switches
(D-10: that pool is `max_workers=1` and can be held for minutes).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

import httpx

from app.alerts.signing import sign_v2
from app.alerts.state import SESSION_ALERT_POLL_INTERVAL_SECONDS, SessionAlerter

logger = logging.getLogger("model_panel.alerts")

#: D-16: bounded timeout so a slow/dead Hermes can at worst delay the *next*
#: tick, never a user request (the ticker is already off every request path).
_DEFAULT_TIMEOUT = httpx.Timeout(connect=2.0, read=3.0, write=3.0, pool=2.0)


def _default_http_post(url: str, *, content: bytes, headers: Dict[str, str], timeout: httpx.Timeout) -> Any:
    return httpx.post(url, content=content, headers=headers, timeout=timeout)


class SessionAlertTicker:
    """Owns the alert-worthy-session poll loop and webhook delivery.

    `get_session_status` mirrors `main.py`'s own `/api/status` handling
    (design Data Flow): if it raises, the tick treats that as the panel's
    synthetic ``"unreachable"`` state rather than propagating.
    """

    def __init__(
        self,
        *,
        get_session_status: Callable[[], Dict[str, Any]],
        webhook_url: Optional[str],
        webhook_secret: Optional[str],
        alerter: Optional[SessionAlerter] = None,
        interval_seconds: float = SESSION_ALERT_POLL_INTERVAL_SECONDS,
        http_post: Optional[Callable[..., Any]] = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._get_session_status = get_session_status
        self._webhook_url = webhook_url or None
        self._webhook_secret = webhook_secret or None
        self._alerter = alerter if alerter is not None else SessionAlerter()
        self._interval = interval_seconds
        self._http_post = http_post or _default_http_post
        self._clock = clock
        self._wall_clock = wall_clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def alerter(self) -> SessionAlerter:
        return self._alerter

    def _fail_closed(self) -> bool:
        return not self._webhook_url or not self._webhook_secret

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._fail_closed():
            # D-19: missing/empty secret or URL is a silent, fail-closed
            # no-op — never an unsigned POST, never a crash-loop.
            logger.warning(
                "session alert ticker: HERMES_WEBHOOK_URL/MODEL_PANEL_WEBHOOK_SECRET "
                "not both set — ticker will not start"
            )
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="session-alert-ticker", daemon=True)
        self._thread.start()

    def stop(self, timeout: Optional[float] = None) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning(
                    "session alert ticker: thread did not stop within %ss timeout", timeout
                )
            self._thread = None

    # -- tick loop -----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                # D-17: the whole tick body is caught — one bad tick must
                # never end the loop; only the stop Event does.
                logger.exception("session alert ticker: tick failed")
            self._stop.wait(self._interval)

    def _tick(self) -> None:
        try:
            session = self._get_session_status()
        except Exception as exc:
            # Mirrors main.py's own /api/status except-clause (design Data
            # Flow): a raising client becomes the panel's synthetic
            # "unreachable" state, sanitized locally, never str(exc) verbatim
            # token/response material.
            session = {"state": "unreachable", "reason": "codex-shim session status unavailable", "expires_at": None}
            del exc
        decision = self._alerter.observe(session, self._clock())
        if decision.kind in ("degraded", "recovery") and decision.payload is not None:
            self._deliver(decision.payload)

    def _deliver(self, payload: Dict[str, Any]) -> None:
        if self._fail_closed():
            return
        # D-18: serialized to bytes exactly ONCE — the same bytes are both
        # signed and POSTed as `content=`, never re-json.dumps'd.
        body = json.dumps(payload).encode("utf-8")
        ts = str(int(self._wall_clock()))
        signature = sign_v2(self._webhook_secret, body, ts)
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature-V2": signature,
            "X-Webhook-Timestamp": ts,
        }
        try:
            response = self._http_post(self._webhook_url, content=body, headers=headers, timeout=_DEFAULT_TIMEOUT)
            status = getattr(response, "status_code", None)
            if status is not None and not (200 <= status < 300):
                logger.warning("session alert ticker: webhook responded with status %s", status)
        except Exception:
            # D-16: never raises out of the ticker — a dead Hermes delays at
            # most the next tick.
            logger.warning("session alert ticker: webhook delivery failed", exc_info=True)
