from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any


@dataclass
class SessionStatus:
    is_open_session: bool
    reason: str


def get_session_status(strategy: dict[str, Any], now: datetime | None = None) -> SessionStatus:
    now = now or datetime.now()
    session_type = get_session_type(now, strategy)
    if session_type == "in_session":
        return SessionStatus(True, "当前处于A股开盘时段，开始巡检。")
    if session_type == "pre_market":
        return SessionStatus(True, "当前处于A股盘前时段，开始盘前分析。")
    return SessionStatus(False, "当前不在A股交易时段，暂不启动巡检。")


def get_session_type(now: datetime | None = None, strategy: dict[str, Any] | None = None) -> str:
    """Return 'pre_market', 'in_session', or 'closed'.

    Pre-market: 09:15-09:25 on trading weekdays (Mon-Fri).
    In-session: 09:30-11:30 and 13:00-15:00 on trading weekdays.
    Closed: all other times.

    When *strategy* is provided, reads trading_session config from it;
    otherwise falls back to the hardcoded A-share defaults above.
    """
    now = now or datetime.now()

    # Resolve trading session config
    trading_cfg = (strategy or {}).get("trading_session", {}) if strategy else {}
    weekdays = trading_cfg.get("weekdays", [1, 2, 3, 4, 5])
    sessions = trading_cfg.get("sessions", [["09:30", "11:30"], ["13:00", "15:00"]])
    pre_sessions = trading_cfg.get("pre_market_sessions", [["09:15", "09:25"]])

    if now.isoweekday() not in weekdays:
        return "closed"

    current = now.time()

    def _parse_time(ts: str) -> time:
        h, m = ts.split(":")
        return time(int(h), int(m))

    for start_str, end_str in pre_sessions:
        if _parse_time(start_str) <= current <= _parse_time(end_str):
            return "pre_market"

    for start_str, end_str in sessions:
        if _parse_time(start_str) <= current <= _parse_time(end_str):
            return "in_session"

    return "closed"
