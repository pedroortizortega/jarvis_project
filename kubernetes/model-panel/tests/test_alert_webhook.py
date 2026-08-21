"""Phase 8 (signing, pure) + Phase 11 (delivery, ticker) tests.

Unit 2a's focused command (`-k "signing or state"`) only exercises the pure
signing tests below; the ticker/delivery tests (Phase 11) are added in
Unit 2b once `app/alerts/ticker.py` exists.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from app.alerts.signing import sign_v2


def test_sign_v2_matches_independently_computed_hmac_signing_vector():
    secret = "top-secret-key"
    body = b'{"event": "session_degraded", "state": "refresh_failed"}'
    ts = "1734000000"

    expected = hmac.new(secret.encode("utf-8"), ts.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()

    assert sign_v2(secret, body, ts) == expected


def test_sign_v2_signature_covers_exact_bytes_not_a_reserialized_dict_signing():
    """Regression against double-`json.dumps`: re-serializing the same
    logical payload with different key order/separators must NOT produce a
    matching signature."""
    secret = "top-secret-key"
    payload = {"b": 2, "a": 1}
    body = json.dumps(payload).encode()
    ts = "1734000000"

    signature = sign_v2(secret, body, ts)

    reserialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    assert reserialized != body
    mismatching_signature = sign_v2(secret, reserialized, ts)
    assert mismatching_signature != signature


def test_sign_v2_signature_changes_with_different_secret_or_body_signing():
    body = b'{"x": 1}'
    ts = "1734000000"
    sig_a = sign_v2("secret-a", body, ts)
    sig_b = sign_v2("secret-b", body, ts)
    assert sig_a != sig_b

    sig_body_a = sign_v2("secret-a", b'{"x": 1}', ts)
    sig_body_b = sign_v2("secret-a", b'{"x": 2}', ts)
    assert sig_body_a != sig_body_b
