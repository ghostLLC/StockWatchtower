from __future__ import annotations

import pytest

from analyzer import (
    PortfolioRiskState,
    SignalDecision,
    _build_portfolio_risk_state,
    _deduplicate_decisions,
    evaluate_market_snapshot,
)


# ── Helpers ──

def _make_position(
    code: str = "000001.SZ",
    name: str = "测试股",
    latest: float = 12.50,
    change_pct: float = -1.0,
    price_vs_ma20_pct: float = 1.0,
    monthly_change_pct: float = 3.0,
    current_weight: float = 0.15,
    sector: str = "银行",
    **kwargs,
) -> dict:
    pos: dict = {
        "code": code,
        "name": name,
        "latest": latest,
        "change_pct": change_pct,
        "price_vs_ma20_pct": price_vs_ma20_pct,
        "monthly_change_pct": monthly_change_pct,
        "current_weight": current_weight,
        "sector": sector,
    }
    pos.update(kwargs)
    return pos


def _make_watchlist_item(
    code: str = "002415.SZ",
    name: str = "海康威视",
    latest: float = 35.0,
    change_pct: float = 3.0,
    price_vs_ma20_pct: float = 2.5,
    monthly_change_pct: float = 8.0,
    sector: str = "科技",
    **kwargs,
) -> dict:
    item: dict = {
        "code": code,
        "name": name,
        "latest": latest,
        "change_pct": change_pct,
        "price_vs_ma20_pct": price_vs_ma20_pct,
        "monthly_change_pct": monthly_change_pct,
        "sector": sector,
    }
    item.update(kwargs)
    return item


def _default_strategy() -> dict:
    return {
        "style": "中线波段",
        "portfolio_rules": {
            "max_total_weight": 0.8,
            "max_single_position_weight": 0.2,
            "max_tech_weight": 0.55,
            "max_sector_weight": 0.35,
            "max_position_count": 6,
        },
        "risk_rules": {
            "position_breakdown_pct": -8.0,
            "watchlist_stop_loss_pct": -3.5,
            "trailing_take_profit_pct": 12.0,
            "hard_take_profit_pct": 18.0,
        },
        "opportunity_rules": {
            "max_add_weight": 0.05,
            "max_new_buy_weight": 0.1,
            "watchlist_breakout_pct": 2.5,
        },
        "alert_rules": {
            "intraday_crash_pct": -5.0,
            "volume_spike_ratio": 3.0,
        },
    }


def _empty_snapshot(market_state: str = "neutral") -> dict:
    return {
        "market_state": market_state,
        "positions": [],
        "watchlist": [],
        "indexes": {"上证指数": {"change_pct": 0.0}},
    }

# ── Core function tests ──

class TestEvaluateMarketSnapshot:
    def test_empty_portfolio_and_watchlist_no_decisions(self) -> None:
        decisions = evaluate_market_snapshot(
            _empty_snapshot(), {}, {}, _default_strategy()
        )
        assert decisions == []

    # ── risk_off ──

    def test_risk_off_triggers_reduce_for_each_position(self) -> None:
        snapshot = {
            "market_state": "risk_off",
            "positions": [
                _make_position("000001.SZ", "平安银行", current_weight=0.15),
                _make_position("600519.SH", "贵州茅台", current_weight=0.10, sector="白酒"),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": -2.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        reduces = [d for d in decisions if d.action == "reduce"]
        assert len(reduces) == 2
        assert all(d.urgency == "high" for d in reduces)

    # ── Stop-loss ──

    def test_hard_stop_loss_change_pct_below_minus_8(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [
                _make_position("000001.SZ", "平安银行", change_pct=-8.5, current_weight=0.15),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": 0.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        sells = [d for d in decisions if d.action == "sell"]
        assert len(sells) == 1
        assert sells[0].urgency == "high"

    def test_soft_stop_loss_below_ma20_and_drop(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [
                _make_position(
                    "000001.SZ",
                    "平安银行",
                    change_pct=-4.0,
                    price_vs_ma20_pct=-3.0,
                    current_weight=0.15,
                ),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": 0.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        sells = [d for d in decisions if d.action == "sell"]
        assert len(sells) == 1

    def test_soft_stop_loss_not_triggered_when_above_ma20(self) -> None:
        """Soft stop-loss requires BOTH change_pct <= -3.5 AND price_vs_ma20 < -2."""
        snapshot = {
            "market_state": "neutral",
            "positions": [
                _make_position(
                    "000001.SZ",
                    "平安银行",
                    change_pct=-4.0,
                    price_vs_ma20_pct=3.0,  # above MA20 — should NOT trigger
                    current_weight=0.15,
                ),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": 0.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        sells = [d for d in decisions if d.action == "sell"]
        assert len(sells) == 0

    # ── Take-profit ──

    def test_hard_take_profit_monthly_gain_above_18_and_ma20_narrowing(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [
                _make_position(
                    "000001.SZ",
                    "平安银行",
                    change_pct=-0.5,  # no stop-loss, no trailing profit
                    monthly_change_pct=20.0,
                    price_vs_ma20_pct=1.0,  # < 1.5
                    current_weight=0.10,
                ),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": 0.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        profits = [d for d in decisions if d.action == "take_profit"]
        assert len(profits) == 1
        assert profits[0].urgency == "medium"

    def test_trailing_take_profit_monthly_gain_above_12_and_intraday_pullback(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [
                _make_position(
                    "000001.SZ",
                    "平安银行",
                    change_pct=-3.0,  # intraday pullback, below -2%
                    monthly_change_pct=15.0,  # >= 12%
                    price_vs_ma20_pct=4.0,  # > 1.5 so hard TP doesn't trigger
                    current_weight=0.10,
                ),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": 0.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        profits = [d for d in decisions if d.action == "take_profit"]
        assert len(profits) == 1

    # ── Intraday crash alert ──

    def test_intraday_crash_alert(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [
                _make_position(
                    "000001.SZ", "平安银行", change_pct=-6.0, current_weight=0.10
                ),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": 0.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        alerts = [d for d in decisions if d.action == "alert"]
        crash_alerts = [a for a in alerts if "急跌" in a.summary]
        assert len(crash_alerts) == 1
        assert crash_alerts[0].urgency == "high"

    def test_intraday_crash_not_triggered_for_zero_change(self) -> None:
        """change_pct == 0.0 should not trigger crash alert due to `change_pct != 0.0` guard."""
        snapshot = {
            "market_state": "neutral",
            "positions": [
                _make_position(
                    "000001.SZ", "平安银行", change_pct=0.0, current_weight=0.10
                ),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": 0.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        alerts = [d for d in decisions if d.action == "alert"]
        crash_alerts = [a for a in alerts if "急跌" in a.summary]
        assert len(crash_alerts) == 0

    # ── Volume spike alert ──

    def test_volume_spike_alert(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [
                _make_position(
                    "000001.SZ",
                    "平安银行",
                    change_pct=2.0,
                    current_weight=0.10,
                    volume_ratio=3.5,
                ),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": 0.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        alerts = [d for d in decisions if d.action == "alert"]
        vol_alerts = [a for a in alerts if "放量" in a.summary]
        assert len(vol_alerts) == 1

    def test_volume_spike_below_threshold_no_alert(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [
                _make_position(
                    "000001.SZ",
                    "平安银行",
                    change_pct=2.0,
                    current_weight=0.10,
                    volume_ratio=2.0,
                ),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": 0.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        alerts = [d for d in decisions if d.action == "alert"]
        vol_alerts = [a for a in alerts if "放量" in a.summary]
        assert len(vol_alerts) == 0

    # ── Add signal (risk_on) ──

    def test_add_signal_in_risk_on_with_strong_ma20_and_momentum(self) -> None:
        snapshot = {
            "market_state": "risk_on",
            "positions": [
                _make_position(
                    "000001.SZ",
                    "平安银行",
                    change_pct=1.0,
                    price_vs_ma20_pct=5.0,  # > 3.0
                    monthly_change_pct=8.0,  # > 6.0
                    current_weight=0.10,
                ),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": 1.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        adds = [d for d in decisions if d.action == "add"]
        assert len(adds) == 1
        assert adds[0].urgency == "low"

    # ── Buy candidate from watchlist ──

    def test_buy_candidate_from_watchlist(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [
                _make_position("000001.SZ", "平安银行", current_weight=0.10),
            ],
            "watchlist": [
                _make_watchlist_item(
                    "002415.SZ", "海康威视", change_pct=4.0,
                    price_vs_ma20_pct=2.5, monthly_change_pct=10.0, sector="科技",
                ),
            ],
            "indexes": {"上证指数": {"change_pct": 0.5}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        buys = [d for d in decisions if d.action == "buy"]
        assert len(buys) == 1
        assert buys[0].symbol == "002415.SZ"
        assert buys[0].urgency == "medium"

    def test_buy_candidate_blocked_by_risk_off(self) -> None:
        snapshot = {
            "market_state": "risk_off",
            "positions": [
                _make_position("000001.SZ", "平安银行", current_weight=0.10),
            ],
            "watchlist": [
                _make_watchlist_item(
                    "002415.SZ", "海康威视", change_pct=4.0,
                    price_vs_ma20_pct=2.5, monthly_change_pct=10.0,
                ),
            ],
            "indexes": {"上证指数": {"change_pct": -2.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        buys = [d for d in decisions if d.action == "buy"]
        assert len(buys) == 0

    def test_buy_candidate_below_breakout_threshold_ignored(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [],
            "watchlist": [
                _make_watchlist_item(
                    "002415.SZ", "海康威视", change_pct=1.5,  # < 2.5%
                    price_vs_ma20_pct=2.5, monthly_change_pct=5.0,
                ),
            ],
            "indexes": {"上证指数": {"change_pct": 0.5}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        buys = [d for d in decisions if d.action == "buy"]
        assert len(buys) == 0

    def test_buy_candidate_below_ma20_ignored(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [],
            "watchlist": [
                _make_watchlist_item(
                    "002415.SZ", "海康威视", change_pct=3.0,
                    price_vs_ma20_pct=-1.0,  # below MA20
                    monthly_change_pct=5.0,
                ),
            ],
            "indexes": {"上证指数": {"change_pct": 0.5}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        buys = [d for d in decisions if d.action == "buy"]
        assert len(buys) == 0

    def test_buy_candidate_negative_monthly_ignored(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [],
            "watchlist": [
                _make_watchlist_item(
                    "002415.SZ", "海康威视", change_pct=3.0,
                    price_vs_ma20_pct=2.5, monthly_change_pct=-2.0,  # negative monthly
                ),
            ],
            "indexes": {"上证指数": {"change_pct": 0.5}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        buys = [d for d in decisions if d.action == "buy"]
        assert len(buys) == 0

    # ── Portfolio risk: overweight total ──

    def test_overweight_total_triggers_rebalance(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [
                _make_position("000001.SZ", "平安银行", current_weight=0.50),
                _make_position("600519.SH", "贵州茅台", current_weight=0.40, sector="白酒"),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": 0.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        portfolio_decisions = [d for d in decisions if d.symbol == "PORTFOLIO"]
        assert len(portfolio_decisions) == 1
        assert portfolio_decisions[0].action == "rebalance"
        assert portfolio_decisions[0].urgency == "high"

    # ── Portfolio risk: overweight tech ──

    def test_overweight_tech_triggers_rebalance(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [
                _make_position(
                    "688001.SH", "科创板股", current_weight=0.40, sector="芯片"
                ),
                _make_position(
                    "688002.SH", "另一只科创", current_weight=0.20, sector="芯片"
                ),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": 0.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        tech_decisions = [d for d in decisions if d.symbol == "TECH_BUCKET"]
        assert len(tech_decisions) == 1
        assert tech_decisions[0].action == "rebalance"

    # ── Portfolio risk: overweight sector ──

    def test_overweight_sector_triggers_rebalance(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [
                _make_position(
                    "000001.SZ", "银行A", current_weight=0.25, sector="银行"
                ),
                _make_position(
                    "000002.SZ", "银行B", current_weight=0.15, sector="银行"
                ),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": 0.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        sector_decisions = [d for d in decisions if d.symbol == "SECTOR::银行"]
        assert len(sector_decisions) == 1
        assert sector_decisions[0].action == "rebalance"

    # ── Portfolio risk: overweight single position ──

    def test_overweight_single_position_triggers_trim(self) -> None:
        snapshot = {
            "market_state": "neutral",
            "positions": [
                _make_position(
                    "000001.SZ", "平安银行", current_weight=0.30, sector="银行"
                ),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": 0.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        trims = [d for d in decisions if d.action == "trim"]
        assert len(trims) == 1
        assert trims[0].symbol == "000001.SZ"


# ── Helpers ──

class TestBuildPortfolioRiskState:
    def test_calculates_weights_correctly(self) -> None:
        positions = [
            _make_position("000001.SZ", current_weight=0.15),
            _make_position("600519.SH", current_weight=0.10, sector="白酒"),
        ]
        state = _build_portfolio_risk_state(positions)
        assert state.invested_weight == pytest.approx(0.25)
        assert state.max_single_position_weight == pytest.approx(0.15)
        assert state.position_count == 2

    def test_empty_positions(self) -> None:
        state = _build_portfolio_risk_state([])
        assert state.invested_weight == 0.0
        assert state.max_single_position_weight == 0.0
        assert state.position_count == 0

    def test_tech_detection(self) -> None:
        positions = [
            _make_position("688981.SH", "中芯国际", current_weight=0.10, sector="芯片"),
            _make_position("000001.SZ", "平安银行", current_weight=0.15, sector="银行"),
        ]
        state = _build_portfolio_risk_state(positions)
        # 688 code is tech, 000001 is not
        assert state.tech_weight == pytest.approx(0.10)

    def test_sector_aggregation(self) -> None:
        positions = [
            _make_position("000001.SZ", "银行A", current_weight=0.15, sector="银行"),
            _make_position("000002.SZ", "银行B", current_weight=0.10, sector="银行"),
        ]
        state = _build_portfolio_risk_state(positions)
        assert state.sector_weights["银行"] == pytest.approx(0.25)


class TestDeduplicateDecisions:
    def test_keeps_first_occurrence(self) -> None:
        d1 = SignalDecision(True, "sell", "000001.SZ", "s1", "r1", "high")
        d2 = SignalDecision(True, "sell", "000001.SZ", "s2", "r2", "medium")
        d3 = SignalDecision(True, "buy", "002415.SZ", "s3", "r3", "medium")
        result = _deduplicate_decisions([d1, d2, d3])
        assert len(result) == 2
        assert result[0].summary == "s1"  # d1 kept
        assert result[1].summary == "s3"  # d3 kept (different key)

    def test_different_actions_same_symbol_kept(self) -> None:
        d1 = SignalDecision(True, "sell", "000001.SZ", "s1", "r1", "high")
        d2 = SignalDecision(True, "buy", "000001.SZ", "s2", "r2", "medium")
        result = _deduplicate_decisions([d1, d2])
        assert len(result) == 2

    def test_empty_list(self) -> None:
        assert _deduplicate_decisions([]) == []


class TestResultSorting:
    def test_decisions_sorted_by_urgency(self) -> None:
        """Decision list should be sorted: high → medium → low."""
        snapshot = {
            "market_state": "risk_off",
            "positions": [
                _make_position("000001.SZ", "平安银行", current_weight=0.90, sector="银行"),
            ],
            "watchlist": [],
            "indexes": {"上证指数": {"change_pct": -2.0}},
        }
        decisions = evaluate_market_snapshot(snapshot, {}, {}, _default_strategy())
        urgency_order = [d.urgency for d in decisions]
        # high should come before medium, etc.
        priority = {"high": 0, "medium": 1, "low": 2}
        assert urgency_order == sorted(urgency_order, key=lambda u: priority.get(u, 9))
