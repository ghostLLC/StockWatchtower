from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from fetchers.capital_flow import (
    _safe_float,
    fetch_north_flow,
    fetch_north_flow_context,
)


# ── _safe_float ──

class TestSafeFloat:
    def test_normal_value(self) -> None:
        assert _safe_float(123.456) == 123.46

    def test_invalid_string_returns_zero(self) -> None:
        assert _safe_float("invalid") == 0.0

    def test_nan_returns_zero(self) -> None:
        assert _safe_float(float("nan")) == 0.0

    def test_inf_returns_zero(self) -> None:
        assert _safe_float(float("inf")) == 0.0

    def test_neg_inf_returns_zero(self) -> None:
        assert _safe_float(float("-inf")) == 0.0

    def test_none_returns_zero(self) -> None:
        assert _safe_float(None) == 0.0

    def test_int_value(self) -> None:
        assert _safe_float(42) == 42.0

    def test_negative_value(self) -> None:
        assert _safe_float(-50.678) == -50.68


# ── helpers ──

def _hsgt_hist_df(date_val: str, net: float, buy: float, sell: float) -> pd.DataFrame:
    return pd.DataFrame([
        {"日期": "2026-04-28", "净买入": 50000.0, "买入金额": 200000.0, "卖出金额": 150000.0},
        {"日期": date_val, "净买入": net, "买入金额": buy, "卖出金额": sell},
    ])


# ── fetch_north_flow ──

class TestFetchNorthFlow:
    @patch("fetchers.capital_flow.ak.stock_hsgt_hist_em")
    def test_parses_both_connects(self, mock_hsgt: object) -> None:
        def side_effect(symbol: str) -> pd.DataFrame:
            if symbol == "沪股通":
                return _hsgt_hist_df("2026-04-29", 100000.0, 500000.0, 400000.0)
            return _hsgt_hist_df("2026-04-29", 50000.0, 300000.0, 250000.0)

        mock_hsgt.side_effect = side_effect
        result = fetch_north_flow()

        assert result["沪股通"]["net_buy"] == 100000.0
        assert result["深股通"]["net_buy"] == 50000.0
        assert result["合计净买入"] == 150000.0

    @patch("fetchers.capital_flow.ak.stock_hsgt_hist_em")
    def test_negative_net_flow(self, mock_hsgt: object) -> None:
        def side_effect(symbol: str) -> pd.DataFrame:
            if symbol == "沪股通":
                return _hsgt_hist_df("2026-04-29", -80000.0, 200000.0, 280000.0)
            return _hsgt_hist_df("2026-04-29", -20000.0, 150000.0, 170000.0)

        mock_hsgt.side_effect = side_effect
        result = fetch_north_flow()

        assert result["合计净买入"] == -100000.0

    @patch("fetchers.capital_flow.ak.stock_hsgt_hist_em")
    def test_empty_dataframe_adds_note(self, mock_hsgt: object) -> None:
        mock_hsgt.return_value = pd.DataFrame()
        result = fetch_north_flow()
        assert len(result["notes"]) == 2
        assert all("无数据" in note for note in result["notes"])

    @patch("fetchers.capital_flow.ak.stock_hsgt_hist_em")
    def test_none_dataframe_adds_note(self, mock_hsgt: object) -> None:
        mock_hsgt.return_value = None
        result = fetch_north_flow()
        assert len(result["notes"]) == 2

    @patch("fetchers.capital_flow.ak.stock_hsgt_hist_em")
    def test_exception_adds_note(self, mock_hsgt: object) -> None:
        mock_hsgt.side_effect = RuntimeError("network error")
        result = fetch_north_flow()
        assert len(result["notes"]) == 2
        assert any("network error" in note for note in result["notes"])


# ── fetch_north_flow_context ──

class TestFetchNorthFlowContext:
    @patch("fetchers.capital_flow.ak.stock_hsgt_hist_em")
    def test_大幅净流入(self, mock_hsgt: object) -> None:
        def side_effect(symbol: str) -> pd.DataFrame:
            net = 600000.0 if symbol == "沪股通" else -50000.0
            return _hsgt_hist_df("2026-04-29", net, net + 200000.0, 200000.0)

        mock_hsgt.side_effect = side_effect
        result = fetch_north_flow_context()
        assert result["trend"] == "大幅净流入"
        assert "大幅净流入" in result["summary"]

    @patch("fetchers.capital_flow.ak.stock_hsgt_hist_em")
    def test_净流入(self, mock_hsgt: object) -> None:
        def side_effect(symbol: str) -> pd.DataFrame:
            net = 120000.0 if symbol == "沪股通" else 30000.0
            return _hsgt_hist_df("2026-04-29", net, net + 100000.0, 100000.0)

        mock_hsgt.side_effect = side_effect
        result = fetch_north_flow_context()
        assert result["trend"] == "净流入"

    @patch("fetchers.capital_flow.ak.stock_hsgt_hist_em")
    def test_大幅净流出(self, mock_hsgt: object) -> None:
        def side_effect(symbol: str) -> pd.DataFrame:
            net = -400000.0 if symbol == "沪股通" else -200000.0
            return _hsgt_hist_df("2026-04-29", net, 200000.0, 200000.0 - net)

        mock_hsgt.side_effect = side_effect
        result = fetch_north_flow_context()
        assert result["trend"] == "大幅净流出"

    @patch("fetchers.capital_flow.ak.stock_hsgt_hist_em")
    def test_净流出(self, mock_hsgt: object) -> None:
        def side_effect(symbol: str) -> pd.DataFrame:
            net = -80000.0 if symbol == "沪股通" else -30000.0
            return _hsgt_hist_df("2026-04-29", net, 200000.0, 200000.0 - net)

        mock_hsgt.side_effect = side_effect
        result = fetch_north_flow_context()
        assert result["trend"] == "净流出"

    @patch("fetchers.capital_flow.ak.stock_hsgt_hist_em")
    def test_均衡(self, mock_hsgt: object) -> None:
        def side_effect(symbol: str) -> pd.DataFrame:
            net = 50000.0 if symbol == "沪股通" else -30000.0
            return _hsgt_hist_df("2026-04-29", net, net + 150000.0, 150000.0)

        mock_hsgt.side_effect = side_effect
        result = fetch_north_flow_context()
        assert result["trend"] == "均衡"

    @patch("fetchers.capital_flow.ak.stock_hsgt_hist_em")
    def test_boundary_just_above_大幅流入(self, mock_hsgt: object) -> None:
        """500000.01 should trigger 大幅净流入."""
        def side_effect(symbol: str) -> pd.DataFrame:
            net = 500000.01 if symbol == "沪股通" else 0.0
            return _hsgt_hist_df("2026-04-29", net, net + 100000.0, 100000.0)

        mock_hsgt.side_effect = side_effect
        result = fetch_north_flow_context()
        assert result["trend"] == "大幅净流入"

    @patch("fetchers.capital_flow.ak.stock_hsgt_hist_em")
    def test_boundary_exactly_100000_goes_to_均衡(self, mock_hsgt: object) -> None:
        """100000.0 is NOT > 100000, so it falls through to 均衡."""
        def side_effect(symbol: str) -> pd.DataFrame:
            net = 100000.0 if symbol == "沪股通" else 0.0
            return _hsgt_hist_df("2026-04-29", net, net + 100000.0, 100000.0)

        mock_hsgt.side_effect = side_effect
        result = fetch_north_flow_context()
        assert result["trend"] == "均衡"
