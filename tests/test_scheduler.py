from __future__ import annotations

from datetime import datetime

import pytest

from scheduler import SessionStatus, get_session_status, get_session_type


def make_dt(weekday: int, hour: int, minute: int) -> datetime:
    """Build a datetime for a given ISO weekday (1=Mon .. 7=Sun) in April 2026."""
    # April 27 2026 is a Monday
    base = datetime(2026, 4, 27, hour, minute)
    offset = weekday - 1
    from datetime import timedelta

    return base + timedelta(days=offset)


class TestGetSessionType:
    def test_weekday_morning_in_session(self) -> None:
        tue_10 = make_dt(2, 10, 0)  # Tuesday 10:00
        assert get_session_type(tue_10) == "in_session"

    def test_weekday_afternoon_in_session(self) -> None:
        wed_14 = make_dt(3, 14, 0)  # Wednesday 14:00
        assert get_session_type(wed_14) == "in_session"

    def test_weekday_pre_market(self) -> None:
        fri_0920 = make_dt(5, 9, 20)  # Friday 09:20
        assert get_session_type(fri_0920) == "pre_market"

    def test_weekday_before_open(self) -> None:
        mon_08 = make_dt(1, 8, 0)  # Monday 08:00
        assert get_session_type(mon_08) == "closed"

    def test_weekday_lunch_break(self) -> None:
        thu_12 = make_dt(4, 12, 0)  # Thursday 12:00
        assert get_session_type(thu_12) == "closed"

    def test_weekday_after_close(self) -> None:
        fri_16 = make_dt(5, 16, 0)  # Friday 16:00
        assert get_session_type(fri_16) == "closed"

    def test_saturday_closed(self) -> None:
        sat = make_dt(6, 10, 0)  # Saturday 10:00
        assert get_session_type(sat) == "closed"

    def test_sunday_closed(self) -> None:
        sun = make_dt(7, 14, 0)  # Sunday 14:00
        assert get_session_type(sun) == "closed"

    # ── Boundary tests ──
    def test_boundary_0915_pre_market(self) -> None:
        assert get_session_type(make_dt(1, 9, 15)) == "pre_market"

    def test_boundary_0925_pre_market(self) -> None:
        assert get_session_type(make_dt(1, 9, 25)) == "pre_market"

    def test_boundary_0930_in_session(self) -> None:
        assert get_session_type(make_dt(1, 9, 30)) == "in_session"

    def test_boundary_1130_in_session(self) -> None:
        assert get_session_type(make_dt(1, 11, 30)) == "in_session"

    def test_boundary_1300_in_session(self) -> None:
        assert get_session_type(make_dt(1, 13, 0)) == "in_session"

    def test_boundary_1500_in_session(self) -> None:
        assert get_session_type(make_dt(1, 15, 0)) == "in_session"

    # ── Gap between pre_market and in_session ──
    def test_gap_0926_to_0929_closed(self) -> None:
        assert get_session_type(make_dt(1, 9, 26)) == "closed"
        assert get_session_type(make_dt(1, 9, 29)) == "closed"


class TestGetSessionTypeWithStrategy:
    def test_uses_strategy_weekdays(self) -> None:
        """When strategy specifies only Mon-Wed, Thursday should be closed."""
        strategy = {"trading_session": {"weekdays": [1, 2, 3]}}
        thu_10 = make_dt(4, 10, 0)  # Thursday
        assert get_session_type(thu_10, strategy) == "closed"

    def test_uses_strategy_sessions(self) -> None:
        """Custom session hours are respected."""
        strategy = {"trading_session": {
            "weekdays": [1, 2, 3, 4, 5],
            "sessions": [["10:00", "11:00"], ["14:00", "15:00"]],
            "pre_market_sessions": [["09:00", "09:30"]],
        }}
        assert get_session_type(make_dt(1, 9, 15), strategy) == "pre_market"
        assert get_session_type(make_dt(1, 9, 35), strategy) == "closed"
        assert get_session_type(make_dt(1, 10, 30), strategy) == "in_session"
        assert get_session_type(make_dt(1, 13, 0), strategy) == "closed"

    def test_missing_trading_session_falls_back(self) -> None:
        """Strategy without trading_session uses defaults (Monday 10:00 is in_session)."""
        assert get_session_type(make_dt(1, 10, 0), {}) == "in_session"

    def test_empty_sessions_treated_as_closed(self) -> None:
        """Empty session list means always closed."""
        strategy = {"trading_session": {"weekdays": [1, 2, 3, 4, 5], "sessions": [], "pre_market_sessions": []}}
        assert get_session_type(make_dt(1, 10, 0), strategy) == "closed"


class TestGetSessionStatus:
    @pytest.fixture
    def strategy(self) -> dict:
        return {}

    def test_in_session_status(self, strategy: dict) -> None:
        status = get_session_status(strategy, make_dt(2, 10, 0))
        assert status.is_open_session is True
        assert "开盘" in status.reason

    def test_pre_market_status(self, strategy: dict) -> None:
        status = get_session_status(strategy, make_dt(3, 9, 20))
        assert status.is_open_session is True
        assert "盘前" in status.reason

    def test_closed_status(self, strategy: dict) -> None:
        status = get_session_status(strategy, make_dt(6, 10, 0))
        assert status.is_open_session is False
        assert "不在" in status.reason
