"""Secret-backed token store for codex-shim (D12).

Reads and patches the `codex-shim-auth` Secret via the Kubernetes API
(never a mounted volume — see D12 rationale: a token store must be
read-back-and-written, and projected volumes are read-only / lag the sync
period). Shape mirrors Hermes's `auth.json`: `tokens.access_token`,
`tokens.refresh_token`, `last_refresh`, plus a shim-added `expires_at`
(parsed from the access-token JWT `exp` claim, cached — never re-derived on
the hot path per D12).
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SECRET_NAME = os.environ.get("CODEX_SHIM_AUTH_SECRET_NAME", "codex-shim-auth")
SECRET_NAMESPACE = os.environ.get("CODEX_SHIM_NAMESPACE", "llms")

# Keys inside the Secret's `data` map (base64-encoded by the K8s API).
_KEY_ACCESS_TOKEN = "access_token"
_KEY_REFRESH_TOKEN = "refresh_token"
_KEY_LAST_REFRESH = "last_refresh"
_KEY_EXPIRES_AT = "expires_at"


@dataclass
class TokenRecord:
    access_token: str
    refresh_token: str
    last_refresh: Optional[str]
    expires_at: Optional[float]  # unix seconds, parsed from JWT `exp`, cached


def parse_jwt_exp(access_token: str) -> Optional[float]:
    """Best-effort extraction of the `exp` claim from a JWT access token.

    Returns None (never raises) on any malformed token so a bad token
    surfaces as an auth error downstream instead of crashing the store.
    """
    if not isinstance(access_token, str) or not access_token.strip():
        return None
    try:
        parts = access_token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = claims.get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
        return None
    except Exception:
        return None


class SecretNotFound(RuntimeError):
    """Raised when the codex-shim-auth Secret does not exist (not_configured)."""


class TokenStore:
    """Reads/patches the `codex-shim-auth` Secret via the K8s API client.

    The K8s client is constructed lazily (and can be injected for tests) so
    importing this module never requires an in-cluster config or kubeconfig.
    """

    def __init__(self, k8s_core_v1: Any = None):
        self._core_v1 = k8s_core_v1

    def _client(self) -> Any:
        if self._core_v1 is not None:
            return self._core_v1
        from kubernetes import client, config as k8s_config  # local import: optional dep at import time

        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
        self._core_v1 = client.CoreV1Api()
        return self._core_v1

    def read(self) -> TokenRecord:
        core_v1 = self._client()
        try:
            secret = core_v1.read_namespaced_secret(SECRET_NAME, SECRET_NAMESPACE)
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status == 404:
                raise SecretNotFound(
                    f"Secret {SECRET_NAMESPACE}/{SECRET_NAME} not found"
                ) from exc
            raise

        data = self._decode_data(getattr(secret, "data", None) or {})
        access_token = data.get(_KEY_ACCESS_TOKEN, "")
        refresh_token = data.get(_KEY_REFRESH_TOKEN, "")
        last_refresh = data.get(_KEY_LAST_REFRESH) or None
        expires_at_raw = data.get(_KEY_EXPIRES_AT)
        expires_at = float(expires_at_raw) if expires_at_raw else parse_jwt_exp(access_token)

        return TokenRecord(
            access_token=access_token,
            refresh_token=refresh_token,
            last_refresh=last_refresh,
            expires_at=expires_at,
        )

    def write(self, tokens: Dict[str, Any]) -> TokenRecord:
        """Patch the Secret with a refreshed token pair.

        `tokens` is the dict returned by `refresh_codex_oauth_pure`:
        `access_token`, `refresh_token`, `last_refresh`. `expires_at` is
        derived here from the new access token's JWT `exp` and cached in
        the Secret so the hot path never re-parses the JWT.
        """
        access_token = str(tokens.get("access_token", ""))
        refresh_token = str(tokens.get("refresh_token", ""))
        last_refresh = tokens.get("last_refresh")
        expires_at = parse_jwt_exp(access_token)

        core_v1 = self._client()

        if expires_at is None:
            # Found by /code-review (Amendment 5): if the new access token's
            # `exp` fails to parse, `_KEY_EXPIRES_AT` used to be silently
            # OMITTED from the patch (leaving the Secret's old value in
            # place) while the returned in-memory TokenRecord.expires_at was
            # None. ensure_fresh() treats `expires_at is None` as
            # never-expiring, so the shim silently stopped proactively
            # refreshing this credential forever, relying solely on the
            # reactive 401 path. Keep the Secret and the in-memory record
            # consistent instead: fall back to whatever expires_at is
            # already persisted rather than losing it.
            try:
                previous = self.read()
                expires_at = previous.expires_at
            except SecretNotFound:
                expires_at = None
            if expires_at is None:
                logger.warning(
                    "codex-shim store: new access token's exp claim did not parse "
                    "and no previous expires_at was available; proactive refresh "
                    "scheduling is degraded until the next reactive 401"
                )

        body_data = {
            _KEY_ACCESS_TOKEN: access_token,
            _KEY_REFRESH_TOKEN: refresh_token,
        }
        if last_refresh:
            body_data[_KEY_LAST_REFRESH] = str(last_refresh)
        if expires_at is not None:
            body_data[_KEY_EXPIRES_AT] = str(expires_at)

        patch_body = {"data": self._encode_data(body_data)}
        core_v1.patch_namespaced_secret(SECRET_NAME, SECRET_NAMESPACE, patch_body)

        return TokenRecord(
            access_token=access_token,
            refresh_token=refresh_token,
            last_refresh=str(last_refresh) if last_refresh else None,
            expires_at=expires_at,
        )

    @staticmethod
    def _decode_data(data: Dict[str, str]) -> Dict[str, str]:
        decoded: Dict[str, str] = {}
        for key, value in (data or {}).items():
            if value is None:
                continue
            try:
                decoded[key] = base64.b64decode(value).decode("utf-8")
            except Exception:
                logger.warning("codex-shim store: failed to decode Secret key %s", key)
        return decoded

    @staticmethod
    def _encode_data(data: Dict[str, str]) -> Dict[str, str]:
        return {
            key: base64.b64encode(value.encode("utf-8")).decode("ascii")
            for key, value in data.items()
        }
