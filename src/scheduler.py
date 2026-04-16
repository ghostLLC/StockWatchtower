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
    trading_session = strategy.get("trading_session", {})
    weekdays = trading_session.get("weekdays", [1, 2, 3, 4, 5])
    sessions = trading_session.get("sessions", [["09:30", "11:30"], ["13:00", "15:00"]])

    weekday = now.isoweekday()
    if weekday not in weekdays:
        return SessionStatus(False, "非A股交易日，跳过巡检。")

    current = now.time()
    for start_raw, end_raw in sessions:
        start = _parse_time(start_raw)
        end = _parse_time(end_raw)
        if start <= current <= end:
            return SessionStatus(True, "当前处于A股开盘时段，开始巡检。")

    return SessionStatus(False, "当前不在A股开盘时段，暂不启动巡检。")
