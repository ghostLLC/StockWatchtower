from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, "src")

from fetchers.market_data import (
    _fetch_stock_realtime_quotes,
    _fetch_stock_realtime_quotes_akshare,
    _normalize_stock_code,
    _safe_pct,
    _safe_price,
    _to_secid,
    fetch_market_snapshot,
)


class TestNormalizeStockCode:
    def test_sh_code(self):
        assert _normalize_stock_code("688981.SH") == "sh688981"

    def test_sz_code(self):
        assert _normalize_stock_code("000001.SZ") == "sz000001"

    def test_invalid_exchange(self):
        with pytest.raises(ValueError, match="不支持的交易所代码"):
            _normalize_stock_code("123456.BAD")


class TestToSecid:
    def test_sh_to_secid(self):
        assert _to_secid("688981.SH") == "1.688981"

    def test_sz_to_secid(self):
        assert _to_secid("000001.SZ") == "0.000001"


class TestSafePct:
    def test_normal_value(self):
        assert _safe_pct(3.5678) == 3.57

    def test_none_value(self):
        assert _safe_pct(None) == 0.0

    def test_invalid_string(self):
        assert _safe_pct("invalid") == 0.0

    def test_zero(self):
        assert _safe_pct(0) == 0.0

    def test_negative(self):
        assert _safe_pct(-5.123) == -5.12


class TestSafePrice:
    def test_normal_price(self):
        assert _safe_price(1250, scale=100) == 12.5

    def test_none_price(self):
        assert _safe_price(None) is None

    def test_empty_string(self):
        assert _safe_price("") is None

    def test_dash(self):
        assert _safe_price("-") is None

    def test_no_scale(self):
        assert _safe_price(42.5) == 42.5


class TestFetchStockRealtimeQuotes:
    def test_parses_spot_data(self):
        df = pd.DataFrame({
            "代码": ["688981", "000001"],
            "名称": ["中芯国际", "平安银行"],
            "最新价": [52.30, 12.50],
            "涨跌幅": [2.5, -1.2],
            "成交额": [5000000, 3000000],
            "振幅": [3.1, 1.5],
            "最高": [53.00, 12.80],
            "最低": [51.00, 12.30],
        })
        with patch("fetchers.market_data._retry_call", return_value=df):
            result = _fetch_stock_realtime_quotes_akshare(["688981.SH", "000001.SZ"])

        assert len(result) == 2
        sh = [q for q in result if q["code"] == "688981.SH"][0]
        assert sh["name"] == "中芯国际"
        assert sh["latest"] == 52.30
        assert sh["change_pct"] == 2.5
        assert sh["source"] == "akshare_primary"

    def test_empty_symbols(self):
        result, source = _fetch_stock_realtime_quotes([])
        assert result == []
        assert source == "none"


class TestFetchIndexSnapshotAkshare:
    def test_parses_index_data(self):
        df = pd.DataFrame({
            "代码": ["sh000001", "sz399001", "sz399006"],
            "名称": ["上证指数", "深证成指", "创业板指"],
            "最新价": [3300.50, 11000.20, 2200.80],
            "涨跌幅": [0.5, -0.3, -2.5],
        })
        # _retry_call wraps ak.stock_zh_index_spot which may not exist in
        # akshare >=1.14; mock it out entirely.
        mock_retry = MagicMock(return_value=df)
        mock_ak = MagicMock()
        with patch("fetchers.market_data.ak", mock_ak), \
             patch("fetchers.market_data._retry_call", mock_retry):
            from fetchers.market_data import _fetch_index_snapshot_akshare as fn
            result = fn()

        sh = result["上证指数"]
        assert sh["symbol"] == "sh000001"
        assert sh["latest"] == 3300.50
        assert sh["change_pct"] == 0.5
        assert sh["source"] == "akshare_primary"


class TestMarketState:
    def _make_indexes(self, sh_pct, cyb_pct):
        return {
            "上证指数": {"symbol": "sh000001", "latest": 3300, "change_pct": sh_pct, "source": "mock"},
            "深证成指": {"symbol": "sz399001", "latest": 11000, "change_pct": 0.0, "source": "mock"},
            "创业板指": {"symbol": "sz399006", "latest": 2200, "change_pct": cyb_pct, "source": "mock"},
        }

    def _empty_configs(self):
        return ({"positions": []}, {"watchlist": []})

    def test_risk_off_by_sh(self):
        portfolio, watchlist = self._empty_configs()
        with patch("fetchers.market_data._fetch_stock_realtime_quotes", return_value=([], "mock")), \
             patch("fetchers.market_data._fetch_index_snapshot", return_value=(self._make_indexes(-1.5, 0.0), "mock")), \
             patch("fetchers.market_data._fetch_recent_daily", return_value=(pd.DataFrame(), "mock")):
            snapshot = fetch_market_snapshot(portfolio, watchlist)
        assert snapshot["market_state"] == "risk_off"

    def test_risk_off_by_cyb(self):
        portfolio, watchlist = self._empty_configs()
        with patch("fetchers.market_data._fetch_stock_realtime_quotes", return_value=([], "mock")), \
             patch("fetchers.market_data._fetch_index_snapshot", return_value=(self._make_indexes(0.0, -2.0), "mock")), \
             patch("fetchers.market_data._fetch_recent_daily", return_value=(pd.DataFrame(), "mock")):
            snapshot = fetch_market_snapshot(portfolio, watchlist)
        assert snapshot["market_state"] == "risk_off"

    def test_risk_on_by_sh(self):
        portfolio, watchlist = self._empty_configs()
        with patch("fetchers.market_data._fetch_stock_realtime_quotes", return_value=([], "mock")), \
             patch("fetchers.market_data._fetch_index_snapshot", return_value=(self._make_indexes(0.8, 0.0), "mock")), \
             patch("fetchers.market_data._fetch_recent_daily", return_value=(pd.DataFrame(), "mock")):
            snapshot = fetch_market_snapshot(portfolio, watchlist)
        assert snapshot["market_state"] == "risk_on"

    def test_risk_on_by_cyb(self):
        portfolio, watchlist = self._empty_configs()
        with patch("fetchers.market_data._fetch_stock_realtime_quotes", return_value=([], "mock")), \
             patch("fetchers.market_data._fetch_index_snapshot", return_value=(self._make_indexes(0.0, 1.2), "mock")), \
             patch("fetchers.market_data._fetch_recent_daily", return_value=(pd.DataFrame(), "mock")):
            snapshot = fetch_market_snapshot(portfolio, watchlist)
        assert snapshot["market_state"] == "risk_on"

    def test_neutral(self):
        portfolio, watchlist = self._empty_configs()
        with patch("fetchers.market_data._fetch_stock_realtime_quotes", return_value=([], "mock")), \
             patch("fetchers.market_data._fetch_index_snapshot", return_value=(self._make_indexes(0.3, -0.5), "mock")), \
             patch("fetchers.market_data._fetch_recent_daily", return_value=(pd.DataFrame(), "mock")):
            snapshot = fetch_market_snapshot(portfolio, watchlist)
        assert snapshot["market_state"] == "neutral"
