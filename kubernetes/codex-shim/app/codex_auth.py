"""Vendored Codex OAuth refresh logic (D11).

Provenance
----------
Source file:   kubernetes/docker/hermes-agent/hermes_cli/auth.py
Source symbols: ``refresh_codex_oauth_pure``, ``AuthError``,
                 ``is_rate_limited_auth_error``, ``_parse_retry_after_seconds``,
                 constants ``CODEX_OAUTH_TOKEN_URL``, ``CODEX_OAUTH_CLIENT_ID``,
                 ``CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS``,
                 ``CODEX_RATE_LIMITED_CODE``, ``CODEX_OAUTH_USER_AGENT``.
Source repo state: ``kubernetes/docker/hermes-agent/`` is excluded from this
                 repository's git history (see .gitignore:222) — there is no
                 commit hash to cite. Vendored as of 2026-08-17 from the file
                 with sha256 ``59eaa96769155cf69f5e4f9237568c2a9fd37d7aecd2c575fb19d82ae30618d1``.
Rationale:      design.md D11 — ``refresh_codex_oauth_pure`` is explicitly
                 state-free ("without mutating Hermes auth state"), ~130 lines,
                 and takes/returns plain dicts, so vendoring is bounded and
                 mechanical. Drift from the source is accepted and made
                 visible by this header — re-diff against the source file
                 above if Hermes's refresh logic changes.

Modifications from the source:
- Dropped the Hermes-specific self-heal-from-``~/.codex/auth.json`` path
  (``_refresh_codex_auth_tokens``) — the shim never reads Hermes's or the
  Codex CLI's local credential files (D16: dedicated, separate session).
- Dropped Hermes's credential-pool / multi-profile plumbing — the shim owns
  exactly one credential.
- ``httpx`` import restricted to what this module needs.
- Added an optional ``transport`` parameter to ``refresh_codex_oauth_pure``
  (testability hook only — an ``httpx.BaseTransport`` for stubbing the token
  endpoint in tests; defaults to real network I/O, matching the source
  behaviour exactly when omitted).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

# --- Vendored constants (verbatim from hermes_cli/auth.py) -----------------
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120
CODEX_RATE_LIMITED_CODE = "codex_rate_limited"
CODEX_OAUTH_USER_AGENT = "codex-shim/0.1.0 (+jarvis_project gpu-handoff-web-panel)"


class AuthError(RuntimeError):
    """Structured auth error with UX mapping hints (vendored, D11)."""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        code: Optional[str] = None,
        relogin_required: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.relogin_required = relogin_required


def is_rate_limited_auth_error(error: Exception) -> bool:
    """True when an :class:`AuthError` represents upstream rate-limiting /
    quota exhaustion rather than missing or invalid credentials (vendored)."""
    return (
        isinstance(error, AuthError)
        and not error.relogin_required
        and error.code == CODEX_RATE_LIMITED_CODE
    )


def _parse_retry_after_seconds(headers: Any) -> Optional[int]:
    """Best-effort parse of a ``Retry-After`` header into whole seconds
    (vendored, verbatim logic)."""
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        seconds = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def refresh_codex_oauth_pure(
    access_token: str,
    refresh_token: str,
    *,
    timeout_seconds: float = 20.0,
    transport: Optional[httpx.BaseTransport] = None,
) -> Dict[str, Any]:
    """Refresh Codex OAuth tokens without mutating any external auth state.

    Vendored verbatim (behaviourally) from ``hermes_cli.auth.refresh_codex_oauth_pure``.
    Returns a dict with ``access_token``, ``refresh_token``, ``last_refresh``.
    Raises :class:`AuthError` on any non-2xx response, classified per D14/D11:
    - 429 -> ``code=CODEX_RATE_LIMITED_CODE``, ``relogin_required=False``
    - ``invalid_grant`` / ``invalid_token`` / ``invalid_request`` /
      ``refresh_token_reused`` / bare 401/403 -> ``relogin_required=True``
    - other non-200 -> ``code="codex_refresh_failed"``, ``relogin_required=False``
      (maps to ``refresh_failed`` session state upstream, not relogin)
    """
    del access_token  # Access token is only used by callers to decide whether to refresh.
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise AuthError(
            "codex-shim is missing refresh_token. A dedicated `codex login` "
            "bootstrap is required (D16).",
            provider="openai-codex",
            code="codex_auth_missing_refresh_token",
            relogin_required=True,
        )

    timeout = httpx.Timeout(max(5.0, float(timeout_seconds)))
    with httpx.Client(
        timeout=timeout,
        transport=transport,
        headers={
            "Accept": "application/json",
            "User-Agent": CODEX_OAUTH_USER_AGENT,
        },
    ) as client:
        response = client.post(
            CODEX_OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CODEX_OAUTH_CLIENT_ID,
            },
        )

    if response.status_code == 429:
        retry_after = _parse_retry_after_seconds(getattr(response, "headers", None))
        if retry_after is not None:
            message = (
                f"Codex provider quota exhausted (429); retry after {retry_after}s. "
                "Credentials are still valid."
            )
        else:
            message = (
                "Codex provider quota exhausted (429). Credentials are still valid; "
                "retry after the usage limit resets."
            )
        raise AuthError(
            message,
            provider="openai-codex",
            code=CODEX_RATE_LIMITED_CODE,
            relogin_required=False,
        )

    if response.status_code != 200:
        code = "codex_refresh_failed"
        message = f"Codex token refresh failed with status {response.status_code}."
        relogin_required = False
        try:
            err = response.json()
            if isinstance(err, dict):
                err_obj = err.get("error")
                if isinstance(err_obj, dict):
                    nested_code = err_obj.get("code") or err_obj.get("type")
                    if isinstance(nested_code, str) and nested_code.strip():
                        code = nested_code.strip()
                    nested_msg = err_obj.get("message")
                    if isinstance(nested_msg, str) and nested_msg.strip():
                        message = f"Codex token refresh failed: {nested_msg.strip()}"
                elif isinstance(err_obj, str) and err_obj.strip():
                    code = err_obj.strip()
                    err_desc = err.get("error_description") or err.get("message")
                    if isinstance(err_desc, str) and err_desc.strip():
                        message = f"Codex token refresh failed: {err_desc.strip()}"
        except Exception:
            pass
        if code in {"invalid_grant", "invalid_token", "invalid_request"}:
            relogin_required = True
        if code == "refresh_token_reused":
            message = (
                "Codex refresh token was already consumed by another client. "
                "codex-shim needs its own dedicated `codex login` re-run (D16)."
            )
            relogin_required = True
        if response.status_code in {401, 403} and not relogin_required:
            relogin_required = True
        raise AuthError(
            message,
            provider="openai-codex",
            code=code,
            relogin_required=relogin_required,
        )

    try:
        refresh_payload = response.json()
    except Exception as exc:
        raise AuthError(
            "Codex token refresh returned invalid JSON.",
            provider="openai-codex",
            code="codex_refresh_invalid_json",
            relogin_required=True,
        ) from exc

    refreshed_access = refresh_payload.get("access_token")
    if not isinstance(refreshed_access, str) or not refreshed_access.strip():
        raise AuthError(
            "Codex token refresh response was missing access_token.",
            provider="openai-codex",
            code="codex_refresh_missing_access_token",
            relogin_required=True,
        )

    updated = {
        "access_token": refreshed_access.strip(),
        "refresh_token": refresh_token.strip(),
        "last_refresh": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    next_refresh = refresh_payload.get("refresh_token")
    if isinstance(next_refresh, str) and next_refresh.strip():
        updated["refresh_token"] = next_refresh.strip()
    return updated
