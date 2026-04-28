from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any


@dataclass
class SessionStatus:
    is_open_session: bool
    reason: str


def _parse_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute))


def get_session_status(strategy: dict[str, Any], now: datetime | None = None) -> SessionStatus:
    now = now or datetime.now()
    session_type = get_session_type(now)
    if session_type == "in_session":
        return SessionStatus(True, "当前处于A股开盘时段，开始巡检。")
    if session_type == "pre_market":
        return SessionStatus(True, "当前处于A股盘前时段，开始盘前分析。")
    return SessionStatus(False, "当前不在A股交易时段，暂不启动巡检。")


def get_session_type(now: datetime | None = None) -> str:
    """Return 'pre_market', 'in_session', or 'closed'.

    Pre-market: 09:15-09:25 on trading weekdays (Mon-Fri).
    In-session: 09:30-11:30 and 13:00-15:00 on trading weekdays.
    Closed: all other times.
    """
    now = now or datetime.now()
    if now.isoweekday() not in (1, 2, 3, 4, 5):
        return "closed"

    current = now.time()
    pre_start = time(9, 15)
    pre_end = time(9, 25)
    if pre_start <= current <= pre_end:
        return "pre_market"

    am_start = time(9, 30)
    am_end = time(11, 30)
    pm_start = time(13, 0)
    pm_end = time(15, 0)
    if (am_start <= current <= am_end) or (pm_start <= current <= pm_end):
        return "in_session"

    return "closed"
