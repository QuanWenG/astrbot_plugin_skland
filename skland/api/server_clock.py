"""Server-calibrated request time for the official garrison API."""

import math
from email.utils import parsedate_to_datetime
from time import monotonic


def response_time(payload, headers=None):
    value = payload.get("timestamp") if isinstance(payload, dict) else None
    try:
        seconds = float(value) if not isinstance(value, bool) else 0
        if math.isfinite(seconds) and seconds > 0:
            return int(seconds / 1000 if seconds >= 100_000_000_000 else seconds)
    except (ValueError, TypeError, OverflowError):
        pass
    try:
        value = parsedate_to_datetime((headers or {}).get("Date", "")).timestamp()
        return int(value) if math.isfinite(value) and value > 0 else None
    except (ValueError, TypeError, OverflowError, AttributeError):
        return None


class ServerClock:
    def __init__(self, timer=monotonic):
        self._timer = timer
        self._server = None
        self._observed = None

    def observe(self, payload, headers=None):
        seconds = response_time(payload, headers)
        if seconds is None:
            return False
        self._server = seconds
        self._observed = self._timer()
        return True

    def now(self):
        if self._server is None:
            return None
        return self._server + int(max(0, self._timer() - self._observed))


BATTLE_CLOCK = ServerClock()
