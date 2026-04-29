from __future__ import annotations

import sys
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, "src")

from fetchers.news_fetcher import (
    _safe_float,
    fetch_dragon_tiger_matches,
    fetch_global_market,
    fetch_market_news,
    fetch_stock_news,
)


class TestSafeFloat:
    def test_normal(self):
        assert _safe_float(42.5) == 42.5

    def test_with_scale(self):
        assert _safe_float(4200, scale=100) == 42.0

    def test_invalid(self):
        assert _safe_float("bad") == 0.0


class TestFetchMarketNews:
    def test_parses_news(self):
        df = pd.DataFrame({
            "标题": ["新闻标题A", "新闻标题B"],
            "摘要": ["摘要A", "摘要B"],
            "发布时间": ["09:00", "10:00"],
        })
        with patch("fetchers.news_fetcher.ak.stock_info_global_em", return_value=df):
            result = fetch_market_news(limit=10)

        assert len(result) == 2
        assert result[0]["title"] == "新闻标题A"

    def test_empty_df(self):
        with patch("fetchers.news_fetcher.ak.stock_info_global_em", return_value=None):
            result = fetch_market_news()
        assert result == []

    def test_returns_empty_on_exception(self):
        with patch("fetchers.news_fetcher.ak.stock_info_global_em", side_effect=RuntimeError("fail")):
            result = fetch_market_news()
        assert result == []


class TestFetchStockNews:
    def test_parses_stock_news(self):
        df = pd.DataFrame({
            "col0": ["a"],
            "新闻标题": ["测试个股新闻"],
            "col2": [""],
            "发布时间": ["11:30"],
        })
        with patch("fetchers.news_fetcher.ak.stock_news_em", return_value=df):
            result = fetch_stock_news(["688981.SH"], limit=5)

        assert "688981.SH" in result
        assert len(result["688981.SH"]) == 1


class TestFetchDragonTigerMatches:
    def test_matches_tracked_codes(self):
        df = pd.DataFrame({
            "代码": ["688981"],
            "名称": ["中芯国际"],
            "买入额": [5000.0],
            "卖出额": [3000.0],
        })
        portfolio = {"positions": [{"code": "688981.SH", "name": "中芯国际"}]}
        watchlist = {"watchlist": []}

        with patch("fetchers.news_fetcher.ak.stock_lhb_detail_em", return_value=df):
            result = fetch_dragon_tiger_matches(portfolio, watchlist)

        assert len(result) == 1
        assert result[0]["code"] == "688981"

    def test_no_tracked_codes(self):
        df = pd.DataFrame({
            "代码": ["000001"],
            "名称": ["平安银行"],
            "买入额": [1000.0],
            "卖出额": [500.0],
        })
        portfolio = {"positions": [{"code": "688981.SH"}]}
        watchlist = {"watchlist": []}

        with patch("fetchers.news_fetcher.ak.stock_lhb_detail_em", return_value=df):
            result = fetch_dragon_tiger_matches(portfolio, watchlist)

        assert len(result) == 0

    def test_handles_api_error(self):
        portfolio = {"positions": []}
        watchlist = {"watchlist": []}

        with patch("fetchers.news_fetcher.ak.stock_lhb_detail_em", side_effect=RuntimeError("fail")):
            result = fetch_dragon_tiger_matches(portfolio, watchlist)

        assert result == []


class TestFetchGlobalMarket:
    def test_parses_global_index(self):
        mock_response = type("Response", (), {
            "json": lambda self: {"data": {"f58": "纳斯达克", "f43": 18500, "f170": 125}},
            "raise_for_status": lambda self: None,
        })()
        with patch("requests.get", return_value=mock_response):
            result = fetch_global_market()

        assert "纳斯达克" in result
        assert result["纳斯达克"]["change_pct"] == 1.25
