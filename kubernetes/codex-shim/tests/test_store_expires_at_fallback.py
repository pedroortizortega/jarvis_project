"""Regression test for a bug found by /code-review (Amendment 5): when a
refreshed access token's `exp` claim fails to parse, `TokenStore.write()`
used to silently OMIT `expires_at` from the Secret patch (leaving the old
value persisted) while returning a `TokenRecord` with `expires_at=None`.
`SessionManager.ensure_fresh()` treats `expires_at is None` as
never-expiring, so the shim silently stopped proactively refreshing that
credential forever — relying solely on the reactive 401 path from then on.
"""

from __future__ import annotations

import base64

from app.store import TokenStore
from tests.conftest import FakeCoreV1Api


def test_unparseable_exp_falls_back_to_previously_persisted_expires_at(fake_core_v1):
    fake_core_v1.seed(
        "codex-shim-auth",
        "llms",
        {
            "access_token": "old-token",
            "refresh_token": "old-refresh",
            "expires_at": "1000.0",
        },
    )
    store = TokenStore(k8s_core_v1=fake_core_v1)

    # A refreshed access token that is NOT a well-formed JWT — exp cannot parse.
    record = store.write({"access_token": "not-a-jwt", "refresh_token": "new-refresh"})

    assert record.expires_at == 1000.0, (
        "expected the previously persisted expires_at to be reused when the "
        f"new token's exp can't be parsed, got {record.expires_at!r}"
    )

    # The patched Secret itself must also carry the fallback value, not omit
    # the key and silently drift from what write() just returned.
    patched_secret = fake_core_v1.read_namespaced_secret("codex-shim-auth", "llms")
    persisted_expires_at = base64.b64decode(patched_secret.data["expires_at"]).decode()
    assert persisted_expires_at == "1000.0", persisted_expires_at


def test_unparseable_exp_with_no_previous_value_logs_and_stores_none(fake_core_v1):
    # No prior Secret at all — nothing to fall back to.
    store = TokenStore(k8s_core_v1=fake_core_v1)
    # Seed a bare-minimum secret without expires_at, only after first write
    # would exist; simulate the very first write ever happening with a
    # non-JWT token by seeding nothing and patching directly (write() reads
    # back via self.read(), which raises SecretNotFound if truly absent —
    # but write() always patches first-write scenarios only after some
    # record exists in practice; here we assert the degrade-gracefully path
    # doesn't raise).
    record = store.write({"access_token": "not-a-jwt", "refresh_token": "rt"})
    assert record.expires_at is None
