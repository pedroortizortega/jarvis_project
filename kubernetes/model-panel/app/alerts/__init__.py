"""Session-degradation alerting: pure HMAC signing and the transition/debounce
state machine (Unit 2a). The daemon-thread ticker and FastAPI lifespan wiring
(Unit 2b) land in a later change and are not part of this module yet.
"""

from __future__ import annotations

from app.alerts.signing import sign_v2
from app.alerts.state import (
    ALERT_WORTHY_STATES,
    SESSION_ALERT_POLL_INTERVAL_SECONDS,
    SESSION_ALERT_SUSTAIN_SECONDS,
    AlertDecision,
    SessionAlerter,
)

__all__ = [
    "sign_v2",
    "ALERT_WORTHY_STATES",
    "SESSION_ALERT_POLL_INTERVAL_SECONDS",
    "SESSION_ALERT_SUSTAIN_SECONDS",
    "AlertDecision",
    "SessionAlerter",
]
