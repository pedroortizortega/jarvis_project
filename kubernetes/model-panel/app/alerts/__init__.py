"""Session-degradation alerting: pure HMAC signing, the transition/debounce
state machine, and the daemon-thread ticker that polls session state and
delivers signed webhook alerts.
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
from app.alerts.ticker import SessionAlertTicker

__all__ = [
    "sign_v2",
    "ALERT_WORTHY_STATES",
    "SESSION_ALERT_POLL_INTERVAL_SECONDS",
    "SESSION_ALERT_SUSTAIN_SECONDS",
    "AlertDecision",
    "SessionAlerter",
    "SessionAlertTicker",
]
