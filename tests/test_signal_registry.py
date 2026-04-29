from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from analyzer import SignalDecision
from notifier import signal_registry


def _make_decision(action: str = "sell", symbol: str = "000001.SZ") -> SignalDecision:
    return SignalDecision(
        should_trade=True,
        action=action,
        symbol=symbol,
        summary="测试信号",
        reason="测试理由",
        urgency="high",
    )


class TestShouldSendSignal:
    def test_first_signal_returns_true(self, tmp_path: Path) -> None:
        signal_registry.REGISTRY_PATH = tmp_path / "registry.json"
        decision = _make_decision()
        result, msg = signal_registry.should_send_signal(decision, cooldown_minutes=180)
        assert result is True
        assert "首次" in msg

    def test_within_cooldown_returns_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        signal_registry.REGISTRY_PATH = tmp_path / "registry.json"

        now = datetime(2026, 4, 29, 10, 0, 0)
        monkeypatch.setattr(signal_registry, "datetime", _FakeDatetime(now))

        decision = _make_decision()
        # First call records at now
        result, _ = signal_registry.should_send_signal(decision, cooldown_minutes=180)
        assert result is True
        signal_registry.record_sent_signal(decision)

        # Second call at now+5 min: within cooldown
        monkeypatch.setattr(signal_registry, "datetime", _FakeDatetime(datetime(2026, 4, 29, 10, 5, 0)))
        result, msg = signal_registry.should_send_signal(decision, cooldown_minutes=180)
        assert result is False
        assert "冷却" in msg

    def test_after_cooldown_returns_true(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        signal_registry.REGISTRY_PATH = tmp_path / "registry.json"

        now = datetime(2026, 4, 29, 10, 0, 0)
        monkeypatch.setattr(signal_registry, "datetime", _FakeDatetime(now))

        decision = _make_decision()
        result, _ = signal_registry.should_send_signal(decision, cooldown_minutes=180)
        assert result is True
        signal_registry.record_sent_signal(decision)

        # After cooldown (200 min later)
        monkeypatch.setattr(signal_registry, "datetime", _FakeDatetime(datetime(2026, 4, 29, 13, 20, 0)))
        result, msg = signal_registry.should_send_signal(decision, cooldown_minutes=180)
        assert result is True
        assert "冷却" in msg

    def test_different_actions_dont_conflict(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        signal_registry.REGISTRY_PATH = tmp_path / "registry.json"

        now = datetime(2026, 4, 29, 10, 0, 0)
        monkeypatch.setattr(signal_registry, "datetime", _FakeDatetime(now))

        buy = _make_decision(action="buy", symbol="000001.SZ")
        sell = _make_decision(action="sell", symbol="000001.SZ")

        result_b, _ = signal_registry.should_send_signal(buy, cooldown_minutes=180)
        assert result_b is True
        signal_registry.record_sent_signal(buy)

        # Different action for same symbol should not be blocked
        result_s, _ = signal_registry.should_send_signal(sell, cooldown_minutes=180)
        assert result_s is True


class TestRecordSentSignal:
    def test_records_to_registry_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        signal_registry.REGISTRY_PATH = tmp_path / "registry.json"
        now = datetime(2026, 4, 29, 10, 0, 0)
        monkeypatch.setattr(signal_registry, "datetime", _FakeDatetime(now))

        decision = _make_decision()
        signal_registry.record_sent_signal(decision)

        assert signal_registry.REGISTRY_PATH.exists()
        import json

        data = json.loads(signal_registry.REGISTRY_PATH.read_text(encoding="utf-8"))
        key = f"{decision.action}|{decision.symbol}"
        assert key in data["signals"]
        assert data["signals"][key]["summary"] == "测试信号"

    def test_new_registry_created_when_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        registry_path = tmp_path / "nonexistent" / "registry.json"
        signal_registry.REGISTRY_PATH = registry_path
        now = datetime(2026, 4, 29, 10, 0, 0)
        monkeypatch.setattr(signal_registry, "datetime", _FakeDatetime(now))

        decision = _make_decision()
        signal_registry.record_sent_signal(decision)
        assert registry_path.exists()


class TestPruneOldRecords:
    def test_old_records_are_pruned(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Records older than 30 days should be pruned, allowing a new send."""
        signal_registry.REGISTRY_PATH = tmp_path / "registry.json"

        # Fake: current time is now
        now = datetime(2026, 4, 29, 10, 0, 0)
        monkeypatch.setattr(signal_registry, "datetime", _FakeDatetime(now))

        decision = _make_decision()
        signal_registry.record_sent_signal(decision)

        # Fake: 31 days later (past 30-day prune window)
        future = datetime(2026, 5, 30, 10, 0, 0)
        monkeypatch.setattr(signal_registry, "datetime", _FakeDatetime(future))

        result, msg = signal_registry.should_send_signal(decision, cooldown_minutes=180)
        assert result is True
        # After pruning the old record, this should be treated as "first send"
        assert "首次" in msg


class _FakeDatetime:
    """A callable that returns a fixed datetime, with now() classmethod."""

    def __init__(self, dt: datetime) -> None:
        self._dt = dt

    def now(self, tz: object = None) -> datetime:  # noqa: ARG002
        return self._dt

    @classmethod
    def fromisoformat(cls, value: str) -> datetime:
        return datetime.fromisoformat(value)
