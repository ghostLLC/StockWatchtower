from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so that imports like "from scheduler import ..." work.
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def sample_portfolio() -> dict:
    return {
        "portfolio_name": "测试组合",
        "cash": 100000,
        "positions": [
            {"code": "000001.SZ", "name": "平安银行", "weight": 0.15, "sector": "银行"},
            {"code": "600519.SH", "name": "贵州茅台", "weight": 0.10, "sector": "白酒"},
            {"code": "688981.SH", "name": "中芯国际", "weight": 0.12, "sector": "芯片"},
        ],
    }


@pytest.fixture
def sample_watchlist() -> dict:
    return {
        "watchlist": [
            {"code": "002415.SZ", "name": "海康威视", "reason": "AI 龙头", "sector": "科技"},
            {"code": "300750.SZ", "name": "宁德时代", "reason": "电池龙头", "sector": "新能源"},
        ],
    }


@pytest.fixture
def sample_strategy() -> dict:
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


@pytest.fixture
def sample_snapshot() -> dict:
    return {
        "market_state": "neutral",
        "positions": [
            {
                "code": "000001.SZ",
                "name": "平安银行",
                "latest": 12.50,
                "change_pct": -1.2,
                "price_vs_ma20_pct": 1.5,
                "monthly_change_pct": 5.0,
                "current_weight": 0.15,
                "sector": "银行",
            },
            {
                "code": "688981.SH",
                "name": "中芯国际",
                "latest": 55.00,
                "change_pct": 3.0,
                "price_vs_ma20_pct": 8.0,
                "monthly_change_pct": 15.0,
                "current_weight": 0.12,
                "sector": "芯片",
            },
        ],
        "watchlist": [
            {
                "code": "002415.SZ",
                "name": "海康威视",
                "latest": 35.00,
                "change_pct": 3.0,
                "price_vs_ma20_pct": 2.5,
                "monthly_change_pct": 8.0,
                "sector": "科技",
                "reason": "AI 龙头",
            },
        ],
        "indexes": {
            "上证指数": {"change_pct": 0.5},
            "创业板指": {"change_pct": -0.2},
        },
    }
