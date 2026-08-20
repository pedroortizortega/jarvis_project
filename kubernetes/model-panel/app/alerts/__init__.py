"""Session-degradation alerting: pure signing + transition state machine
(Unit 2a), plus the daemon-thread ticker and lifespan wiring (Unit 2b).
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
