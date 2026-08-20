"""Phase 9 (transition/debounce state machine) + Phase 10 (payload content)
tests for `app.alerts.state.SessionAlerter`. Every test injects `now`
directly — no sleeping, no wall clock (D-12)."""

from __future__ import annotations

from app.alerts.state import (
    ALERT_WORTHY_STATES,
    SESSION_ALERT_SUSTAIN_SECONDS,
    AlertDecision,
    SessionAlerter,
)


def session(state: str, **extra):
    return {"state": state, "reason": None, "expires_at": None, **extra}


# --- Phase 9: transition table -----------------------------------------------


def test_non_alert_worthy_state_stays_none_state():
    alerter = SessionAlerter()
    decision = alerter.observe(session("rate_limited"), now=0.0)
    assert decision.kind == "none"
    assert alerter.degraded_since is None


def test_alert_worthy_state_first_sighting_sets_degraded_since_state():
    alerter = SessionAlerter()
    decision = alerter.observe(session("refresh_failed"), now=100.0)
    assert decision.kind == "none"
    assert alerter.degraded_since == 100.0
    assert alerter.alerted_state is None


def test_same_alert_worthy_state_under_threshold_emits_nothing_state():
    alerter = SessionAlerter()
    alerter.observe(session("refresh_failed"), now=100.0)
    decision = alerter.observe(session("refresh_failed"), now=105.0)
    assert decision.kind == "none"
    assert alerter.alerted_state is None


def test_same_alert_worthy_state_at_threshold_emits_exactly_one_state():
    alerter = SessionAlerter()
    alerter.observe(session("refresh_failed"), now=0.0)

    # 9.9s: nothing yet.
    decision_before = alerter.observe(session("refresh_failed"), now=9.9)
    assert decision_before.kind == "none"

    # 10.0s: fires exactly once.
    decision_at = alerter.observe(session("refresh_failed"), now=10.0)
    assert decision_at.kind == "degraded"
    assert alerter.alerted_state == "refresh_failed"


def test_already_alerted_state_many_further_ticks_emit_none_state():
    alerter = SessionAlerter()
    alerter.observe(session("refresh_failed"), now=0.0)
    alerter.observe(session("refresh_failed"), now=10.0)  # fires

    for i in range(1, 101):
        decision = alerter.observe(session("refresh_failed"), now=10.0 + i)
        assert decision.kind == "none", i


def test_different_alert_worthy_state_resets_and_rearms_state():
    alerter = SessionAlerter()
    alerter.observe(session("refresh_failed"), now=0.0)
    alerter.observe(session("refresh_failed"), now=10.0)  # fires, alerted_state = refresh_failed

    decision_switch = alerter.observe(session("expired_needs_relogin"), now=11.0)
    assert decision_switch.kind == "none"
    assert alerter.degraded_since == 11.0
    assert alerter.alerted_state is None

    decision_before = alerter.observe(session("expired_needs_relogin"), now=20.9)
    assert decision_before.kind == "none"

    decision_fire_again = alerter.observe(session("expired_needs_relogin"), now=21.0)
    assert decision_fire_again.kind == "degraded"
    assert alerter.alerted_state == "expired_needs_relogin"


def test_valid_after_prior_alert_emits_recovery_and_rearms_state():
    alerter = SessionAlerter()
    alerter.observe(session("refresh_failed"), now=0.0)
    alerter.observe(session("refresh_failed"), now=10.0)  # fires

    decision = alerter.observe(session("valid"), now=15.0)
    assert decision.kind == "recovery"
    assert alerter.degraded_since is None
    assert alerter.alerted_state is None

    # Re-armed: a fresh degradation can alert again.
    alerter.observe(session("refresh_failed"), now=16.0)
    decision_again = alerter.observe(session("refresh_failed"), now=26.0)
    assert decision_again.kind == "degraded"


def test_valid_or_excluded_states_without_prior_alert_emit_none_state():
    for candidate_state in ("valid", "rate_limited", "not_configured"):
        alerter = SessionAlerter()
        decision = alerter.observe(session(candidate_state), now=0.0)
        assert decision.kind == "none", candidate_state


def test_rate_limited_and_not_configured_never_alert_no_matter_how_long_state():
    for excluded_state in ("rate_limited", "not_configured"):
        alerter = SessionAlerter()
        for tick in range(0, 1000, 10):
            decision = alerter.observe(session(excluded_state), now=float(tick))
            assert decision.kind == "none", (excluded_state, tick)


def test_alert_worthy_states_constant_matches_design_state():
    assert ALERT_WORTHY_STATES == frozenset(
        {"expired_needs_relogin", "refresh_failed", "backend_unreachable", "unreachable"}
    )


def test_sustain_threshold_constant_is_ten_seconds_state():
    assert SESSION_ALERT_SUSTAIN_SECONDS == 10.0


# --- Phase 10: payload content -----------------------------------------------


def test_debounced_alert_payload_includes_required_fields_state():
    alerter = SessionAlerter()
    sess = session("expired_needs_relogin", reason="token expired", expires_at=12345.0)
    alerter.observe(sess, now=0.0)
    decision = alerter.observe(sess, now=10.0)

    assert decision.kind == "degraded"
    payload = decision.payload
    assert payload["state"] == "expired_needs_relogin"
    assert payload["reason"] == "token expired"
    assert payload["expires_at"] == 12345.0
    assert isinstance(payload["next_action"], str) and payload["next_action"]
    assert payload["sustained_seconds"] >= 10.0
    assert "event" in payload
    assert "source" in payload


def test_recovery_payload_has_no_action_needed_hint_state():
    alerter = SessionAlerter()
    sess_bad = session("refresh_failed")
    alerter.observe(sess_bad, now=0.0)
    alerter.observe(sess_bad, now=10.0)

    decision = alerter.observe(session("valid"), now=15.0)
    assert decision.kind == "recovery"
    assert decision.payload["state"] == "valid"
    assert decision.payload["previous_state"] == "refresh_failed"
