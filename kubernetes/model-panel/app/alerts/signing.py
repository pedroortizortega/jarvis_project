"""Pure HMAC-SHA256 V2 webhook signer (D-18/F-6).

Mirrors Hermes's `X-Webhook-Signature-V2` contract
(`gateway/platforms/webhook.py:1136-1161`): lowercase hex
``hmac.new(secret, b"<timestamp>.<raw_body>", sha256).hexdigest()``. Signing
never re-serializes the body — callers MUST sign and POST the exact same
bytes (D-18), or the signature will not match what Hermes independently
recomputes over the bytes it actually received.
"""

from __future__ import annotations

import hashlib
import hmac


def sign_v2(secret: str, body: bytes, timestamp: str) -> str:
    """Return the lowercase-hex HMAC-SHA256 signature Hermes's
    `X-Webhook-Signature-V2` expects, computed over
    ``timestamp.encode("utf-8") + b"." + body``.
    """
    mac = hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256)
    return mac.hexdigest()
