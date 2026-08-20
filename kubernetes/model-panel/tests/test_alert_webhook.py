"""Phase 8 (signing, pure) + Phase 11 (delivery, ticker) tests.

Unit 2a's focused command (`-k "signing or state"`) only exercises the pure
signing tests below; the ticker/delivery tests (Phase 11) are added in
Unit 2b once `app/alerts/ticker.py` exists.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

from app.alerts.signing import sign_v2
from app.alerts.state import SessionAlerter
from app.alerts.ticker import SessionAlertTicker


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


# --- Phase 11: SessionAlertTicker delivery (D-09/D-10/D-16/D-17/D-19) -------


class _FailIfCalledPost:
    def __call__(self, *args, **kwargs):
        raise AssertionError("transport must not be invoked (fail-closed expected)")


class _RecordingPost:
    def __init__(self, responses=None, raise_exc=None):
        self.calls = []
        self._responses = list(responses) if responses is not None else None
        self._raise_exc = raise_exc

    def __call__(self, url, *, content, headers, timeout):
        self.calls.append({"url": url, "content": content, "headers": headers, "timeout": timeout})
        if self._raise_exc is not None:
            raise self._raise_exc
        if self._responses:
            return self._responses.pop(0)

        class _Resp:
            status_code = 200

        return _Resp()


def _always_degraded_session():
    return {"state": "refresh_failed", "reason": "boom", "expires_at": None}


def test_missing_secret_or_url_never_starts_ticker_zero_posts():
    post = _FailIfCalledPost()
    ticker = SessionAlertTicker(
        get_session_status=_always_degraded_session,
        webhook_url="",
        webhook_secret="",
        http_post=post,
    )
    ticker.start()
    time.sleep(0.05)
    ticker.stop(timeout=1.0)
    # No assertion needed beyond "post never called" — _FailIfCalledPost
    # raises AssertionError itself if invoked.


def test_body_serialized_once_same_bytes_signed_and_posted():
    post = _RecordingPost()
    alerter = SessionAlerter()
    ticker = SessionAlertTicker(
        get_session_status=_always_degraded_session,
        webhook_url="http://hermes.example/webhooks/route",
        webhook_secret="shh",
        alerter=alerter,
        http_post=post,
        clock=_Counter(start=0.0, step=11.0),  # first tick sets degraded_since, second fires
    )
    ticker._tick()
    ticker._tick()

    assert len(post.calls) == 1
    call = post.calls[0]
    body = call["content"]
    signature = call["headers"]["X-Webhook-Signature-V2"]
    ts = call["headers"]["X-Webhook-Timestamp"]
    assert sign_v2("shh", body, ts) == signature


def test_timestamp_header_present_and_within_range():
    post = _RecordingPost()
    ticker = SessionAlertTicker(
        get_session_status=_always_degraded_session,
        webhook_url="http://hermes.example/webhooks/route",
        webhook_secret="shh",
        http_post=post,
        clock=_Counter(start=0.0, step=11.0),
        wall_clock=lambda: 1_700_000_000.0,
    )
    ticker._tick()
    ticker._tick()

    assert len(post.calls) == 1
    ts = post.calls[0]["headers"]["X-Webhook-Timestamp"]
    assert ts == "1700000000"
    assert abs(int(ts) - 1_700_000_000) <= 300


def test_transport_error_is_logged_and_does_not_raise_ticker_survives():
    post = _RecordingPost(raise_exc=ConnectionError("boom"))
    ticker = SessionAlertTicker(
        get_session_status=_always_degraded_session,
        webhook_url="http://hermes.example/webhooks/route",
        webhook_secret="shh",
        http_post=post,
        clock=_Counter(start=0.0, step=11.0),
    )
    ticker._tick()
    ticker._tick()  # this one fires the alert and hits the raising transport
    ticker._tick()  # ticker must survive and still be usable afterwards
    assert len(post.calls) >= 1


def test_non_2xx_webhook_response_is_treated_as_delivery_failure_not_raised():
    class _BadResp:
        status_code = 503

    post = _RecordingPost(responses=[_BadResp()])
    ticker = SessionAlertTicker(
        get_session_status=_always_degraded_session,
        webhook_url="http://hermes.example/webhooks/route",
        webhook_secret="shh",
        http_post=post,
        clock=_Counter(start=0.0, step=11.0),
    )
    ticker._tick()
    ticker._tick()  # does not raise even though the stub returns 503
    assert len(post.calls) == 1


def test_one_bad_tick_does_not_end_ticker_loop_only_stop_event_does():
    calls = {"n": 0}

    def flaky_get_session_status():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("codex-shim exploded")
        return {"state": "valid", "reason": None, "expires_at": None}

    ticker = SessionAlertTicker(
        get_session_status=flaky_get_session_status,
        webhook_url="",
        webhook_secret="",
        http_post=_FailIfCalledPost(),
        interval_seconds=0.01,
    )
    ticker._thread = None
    import threading

    ticker._stop = threading.Event()
    thread = threading.Thread(target=ticker._run, daemon=True)
    thread.start()
    time.sleep(0.08)
    ticker._stop.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert calls["n"] >= 2  # survived the first raising tick and kept going


def test_alert_fires_with_no_browser_polling_api_status():
    """Task 11.8: the ticker is a real background thread driving its own
    poll loop — an alert fires entirely server-side. This test never
    touches an HTTP route or `/api/status` at all (what a browser tab's
    `setInterval` poll would do); the ticker talks to `get_session_status`
    directly, proving delivery does not depend on anyone watching the UI."""
    post = _RecordingPost()
    ticker = SessionAlertTicker(
        get_session_status=_always_degraded_session,
        webhook_url="http://hermes.example/webhooks/route",
        webhook_secret="shh",
        http_post=post,
        clock=_Counter(start=0.0, step=11.0),
        interval_seconds=0.01,
    )
    ticker.start()
    deadline = time.time() + 2.0
    while time.time() < deadline and not post.calls:
        time.sleep(0.01)
    ticker.stop(timeout=1.0)

    assert len(post.calls) >= 1


def test_stop_logs_warning_when_thread_does_not_join_within_timeout(caplog):
    """Code review follow-up (PR 2b): `stop()` used to drop the thread
    reference silently if `join(timeout=...)` returned before the thread
    actually finished — no diagnostic trail that shutdown didn't clean up
    in time. Now it must log a warning in that case."""

    class _NeverJoiningThread:
        def join(self, timeout=None):
            return  # simulates a join() that times out — thread keeps running

        def is_alive(self):
            return True

    ticker = SessionAlertTicker(
        get_session_status=lambda: {"state": "valid", "reason": None, "expires_at": None},
        webhook_url="",
        webhook_secret="",
    )
    ticker._thread = _NeverJoiningThread()

    with caplog.at_level(logging.WARNING, logger="model_panel.alerts"):
        ticker.stop(timeout=0.01)

    assert any("did not stop" in record.message for record in caplog.records)
    assert ticker._thread is None


def test_stop_logs_nothing_when_thread_joins_cleanly():
    """Regression guard: a clean shutdown (the common case) must not log a
    spurious warning."""
    ticker = SessionAlertTicker(
        get_session_status=lambda: {"state": "valid", "reason": None, "expires_at": None},
        webhook_url="http://hermes.example/webhooks/route",
        webhook_secret="shh",
        http_post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no delivery expected")),
        interval_seconds=60.0,
    )
    ticker.start()
    import logging as _logging

    logger = _logging.getLogger("model_panel.alerts")
    records_before = len(logger.handlers)  # no-op, just to keep imports tidy
    del records_before
    with_caplog = []

    class _Handler(_logging.Handler):
        def emit(self, record):
            with_caplog.append(record)

    handler = _Handler(level=_logging.WARNING)
    logger.addHandler(handler)
    try:
        ticker.stop(timeout=1.0)
    finally:
        logger.removeHandler(handler)

    assert not any("did not stop" in r.getMessage() for r in with_caplog)
    assert ticker._thread is None


class _Counter:
    """Deterministic monotonic-clock stand-in: returns start, start+step,
    start+2*step, ... on each call."""

    def __init__(self, start: float, step: float):
        self._value = start
        self._step = step

    def __call__(self) -> float:
        value = self._value
        self._value += self._step
        return value
