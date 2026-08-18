"""Single-flight proactive + reactive Codex token refresh (D14).

A background timer refreshes at ``expires_at - skew_seconds`` (proactive); a
request that receives a 401 from upstream triggers exactly one refresh and
one retry (reactive). Both paths share one ``asyncio.Lock`` so concurrent
callers cannot double-refresh.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Optional

from app.codex_auth import (
    CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
    CODEX_RATE_LIMITED_CODE,
    AuthError,
    refresh_codex_oauth_pure,
)
from app.store import SecretNotFound, TokenRecord, TokenStore

logger = logging.getLogger(__name__)

SessionState = Literal[
    "not_configured",
    "valid",
    "expiring_soon",
    "rate_limited",
    "expired_needs_relogin",
    "refresh_failed",
]


@dataclass
class SessionStatus:
    state: SessionState
    expires_at: Optional[float]
    last_refresh: Optional[str]
    last_error_code: Optional[str]
    reason: Optional[str]


#: Found during live cluster verification (Amendment 5): model-panel polls
#: `GET /api/status` every 2s, which calls `/internal/session` ->
#: `ensure_fresh()`. Once the cached token enters the skew window, every
#: single poll re-attempted a live OAuth refresh; if that refresh failed
#: (rate-limited, network blip) without advancing `expires_at`, the very
#: next 2s poll retried again, indefinitely — pure monitoring traffic
#: hammering the token endpoint. This bounds proactive retry attempts to
#: once per interval; it does not affect the reactive 401 single-flight
#: path (`handle_401_and_retry`), which stays driven by real request
#: traffic, not passive polling.
MIN_PROACTIVE_REFRESH_RETRY_INTERVAL_SECONDS = 30


class SessionManager:
    """Owns the Codex OAuth session lifecycle for one credential (D14)."""

    def __init__(
        self,
        *,
        store: TokenStore,
        refresh_fn: Callable[..., Any] = refresh_codex_oauth_pure,
        skew_seconds: int = CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
        clock: Callable[[], float] = time.time,
        min_retry_interval_seconds: int = MIN_PROACTIVE_REFRESH_RETRY_INTERVAL_SECONDS,
    ) -> None:
        self._store = store
        self._refresh_fn = refresh_fn
        self._skew = skew_seconds
        self._clock = clock
        self._min_retry_interval = min_retry_interval_seconds
        self._lock = asyncio.Lock()
        self._state: SessionState = "not_configured"
        self._last_error_code: Optional[str] = None
        self._reason: Optional[str] = None
        self._cached: Optional[TokenRecord] = None
        self._refresh_call_count = 0
        self._refresh_generation = 0
        self._last_failed_attempt_at: Optional[float] = None

    # -- read helpers ---------------------------------------------------

    def _load_cached(self) -> TokenRecord:
        if self._cached is None:
            try:
                self._cached = self._store.read()
            except SecretNotFound:
                self._state = "not_configured"
                self._reason = "codex-shim-auth Secret not found"
                raise
        return self._cached

    def status(self) -> dict:
        record = self._cached
        expires_at = record.expires_at if record else None
        last_refresh = record.last_refresh if record else None
        return {
            "state": self._state,
            "expires_at": expires_at,
            "last_refresh": last_refresh,
            "last_error_code": self._last_error_code,
            "reason": self._reason,
        }

    # -- refresh ----------------------------------------------------------

    def _classify_error(self, exc: AuthError) -> None:
        if exc.code == CODEX_RATE_LIMITED_CODE:
            self._state = "rate_limited"
        elif exc.relogin_required:
            self._state = "expired_needs_relogin"
        else:
            self._state = "refresh_failed"
        self._last_error_code = exc.code
        self._reason = str(exc)

    async def _do_refresh_locked(self) -> None:
        """Assumes ``self._lock`` is held by the caller."""
        try:
            record = self._load_cached()
        except SecretNotFound:
            raise
        self._refresh_call_count += 1
        try:
            tokens = await asyncio.to_thread(
                self._refresh_fn, record.access_token, record.refresh_token
            )
        except AuthError as exc:
            self._classify_error(exc)
            self._last_failed_attempt_at = self._clock()
            raise
        new_record = self._store.write(tokens)
        self._cached = new_record
        self._state = "valid"
        self._last_error_code = None
        self._reason = None
        self._last_failed_attempt_at = None
        self._refresh_generation += 1

    async def refresh(self) -> None:
        """Force a refresh, serialized via the shared lock."""
        async with self._lock:
            await self._do_refresh_locked()

    # -- proactive path -----------------------------------------------------

    async def ensure_fresh(self) -> str:
        """Return a valid access token, refreshing proactively if within skew."""
        try:
            record = self._load_cached()
        except SecretNotFound:
            raise

        now = self._clock()
        if record.expires_at is not None and now >= (record.expires_at - self._skew):
            if (
                self._last_failed_attempt_at is not None
                and (now - self._last_failed_attempt_at) < self._min_retry_interval
            ):
                # A recent proactive attempt already failed and the token is
                # still in the skew window — skip hitting the token endpoint
                # again; the last classified state (rate_limited/
                # expired_needs_relogin/refresh_failed) is still accurate
                # and callers (including the status endpoint) should keep
                # reporting it rather than retry every poll.
                return record.access_token if record else ""
            await self.refresh()
            record = self._cached
        else:
            self._state = "valid"
            self._reason = None
        return record.access_token if record else ""

    # -- reactive path (single-flight) ---------------------------------------

    async def handle_401_and_retry(
        self, call_fn: Callable[[str], Awaitable[Any]]
    ) -> Any:
        """On a 401 from upstream: refresh exactly once (single-flight across
        concurrent callers) and retry the caller's request once with the
        fresh token.
        """
        generation_before = self._refresh_generation
        async with self._lock:
            if self._refresh_generation == generation_before:
                await self._do_refresh_locked()
        record = self._cached
        access_token = record.access_token if record else ""
        return await call_fn(access_token)
