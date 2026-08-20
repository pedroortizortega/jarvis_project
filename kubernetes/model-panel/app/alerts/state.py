"""Pure debounce/transition state machine for the Codex session degradation
alerter (D-12/D-13/D-14/D-20). No I/O, no clock of its own — `now` is always
injected by the caller (the ticker, or a test), matching the existing
`last_alias_heal_attempt` debounce precedent in `main.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

#: Question 2's resolved default (design D-13). Deliberately a separate
#: constant from `ALLOWED_SESSION_STATES` (D17 switchability) — alerting and
#: switchability are a superset-complement of each other only by coincidence
#: today.
ALERT_WORTHY_STATES = frozenset(
    {"expired_needs_relogin", "refresh_failed", "backend_unreachable", "unreachable"}
)

#: Question 5's resolved default (design D-12): wall-clock sustain, not a
#: poll-count, so the threshold survives any change to the ticker's own
#: polling interval.
SESSION_ALERT_SUSTAIN_SECONDS = 10.0

#: Design D-11.
SESSION_ALERT_POLL_INTERVAL_SECONDS = 5.0

#: Question 3's proposed default: a one-line next-action hint per
#: alert-worthy state (design D-20). Wording is intentionally a flat dict so
#: a future wording tweak is a one-line edit, never a code-shape change.
NEXT_ACTION_BY_STATE: Dict[str, str] = {
    "expired_needs_relogin": "Re-run bootstrap_login.md to restore the Codex session.",
    "refresh_failed": "Check codex-shim logs — the OAuth refresh call itself failed.",
    "backend_unreachable": "Check codex-shim's connectivity to the Kubernetes API server.",
    "unreachable": "Check that codex-shim is running and reachable from model-panel.",
}

_DEFAULT_NEXT_ACTION = "Check the model-panel dashboard for details."
_RECOVERY_NEXT_ACTION = "No action needed — the Codex session recovered."


@dataclass
class AlertDecision:
    kind: Literal["none", "degraded", "recovery"]
    payload: Optional[Dict[str, Any]] = None


def _session_get(session: Optional[Dict[str, Any]], key: str) -> Any:
    if not isinstance(session, dict):
        return None
    return session.get(key)


def _next_action_for(state: Optional[str]) -> str:
    return NEXT_ACTION_BY_STATE.get(state or "", _DEFAULT_NEXT_ACTION)


def _build_degradation_payload(
    session: Optional[Dict[str, Any]], *, previous_state: Optional[str], sustained_seconds: float
) -> Dict[str, Any]:
    state = _session_get(session, "state")
    return {
        "event": "session_degraded",
        "state": state,
        "previous_state": previous_state,
        "reason": _session_get(session, "reason"),
        "expires_at": _session_get(session, "expires_at"),
        "next_action": _next_action_for(state),
        "sustained_seconds": round(sustained_seconds, 1),
        "source": "model-panel",
    }


def _build_recovery_payload(session: Optional[Dict[str, Any]], *, previous_state: Optional[str]) -> Dict[str, Any]:
    return {
        "event": "session_recovered",
        "state": "valid",
        "previous_state": previous_state,
        "reason": None,
        "expires_at": _session_get(session, "expires_at"),
        "next_action": _RECOVERY_NEXT_ACTION,
        "sustained_seconds": None,
        "source": "model-panel",
    }


@dataclass
class SessionAlerter:
    """In-memory transition/debounce policy (design D-15: purely on
    `app.state.session_alerter`, no persistence). `observe()` is the whole
    policy and is 100% unit-testable with an injected `now` — no sleeping
    tests.
    """

    degraded_since: Optional[float] = None
    alerted_state: Optional[str] = None
    _tracking_state: Optional[str] = field(default=None, repr=False)
    _state_before_degraded: Optional[str] = field(default=None, repr=False)
    _last_state: Optional[str] = field(default=None, repr=False)

    def observe(self, session: Optional[Dict[str, Any]], now: float) -> AlertDecision:
        state = _session_get(session, "state")
        decision = self._observe(session, state, now)
        self._last_state = state
        return decision

    def _observe(self, session: Optional[Dict[str, Any]], state: Optional[str], now: float) -> AlertDecision:
        if state in ALERT_WORTHY_STATES:
            if self.degraded_since is None or self._tracking_state != state:
                # First sighting of this alert-worthy state, or a change
                # from a different alert-worthy state (re-arms, D-14).
                self._state_before_degraded = self._last_state
                self.degraded_since = now
                self.alerted_state = None
                self._tracking_state = state
                return AlertDecision(kind="none")

            if self.alerted_state == state:
                # One-shot per transition (D-14): already alerted, no repeat.
                return AlertDecision(kind="none")

            elapsed = now - self.degraded_since
            if elapsed >= SESSION_ALERT_SUSTAIN_SECONDS:
                self.alerted_state = state
                payload = _build_degradation_payload(
                    session, previous_state=self._state_before_degraded, sustained_seconds=elapsed
                )
                return AlertDecision(kind="degraded", payload=payload)
            return AlertDecision(kind="none")

        # Not alert-worthy (`valid`, `rate_limited`, `not_configured`, ...).
        if state == "valid" and self.alerted_state is not None:
            payload = _build_recovery_payload(session, previous_state=self.alerted_state)
            self.degraded_since = None
            self.alerted_state = None
            self._tracking_state = None
            self._state_before_degraded = None
            return AlertDecision(kind="recovery", payload=payload)

        # Transient blip that reverted before the sustain threshold, or a
        # non-alert-worthy state with nothing to recover from: re-arm quietly.
        self.degraded_since = None
        self.alerted_state = None
        self._tracking_state = None
        self._state_before_degraded = None
        return AlertDecision(kind="none")
