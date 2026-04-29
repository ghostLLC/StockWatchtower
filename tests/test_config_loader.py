from __future__ import annotations

import json
from pathlib import Path

import pytest

from config_loader import BASE_DIR, CONFIG_DIR, load_json, save_json, validate_portfolio, validate_watchlist, validate_strategy


class TestLoadJson:
    def test_reads_temp_json(self, tmp_path: Path) -> None:
        data = {"key": "value", "num": 42}
        fpath = tmp_path / "test.json"
        fpath.write_text(json.dumps(data), encoding="utf-8")
        assert load_json(fpath) == data

    def test_reads_empty_dict(self, tmp_path: Path) -> None:
        fpath = tmp_path / "empty.json"
        fpath.write_text("{}", encoding="utf-8")
        assert load_json(fpath) == {}


class TestSaveJson:
    def test_writes_and_round_trips(self, tmp_path: Path) -> None:
        data = {"a": 1, "b": [2, 3], "c": {"nested": True}}
        fpath = tmp_path / "saved.json"
        save_json(fpath, data)
        assert fpath.exists()
        loaded = json.loads(fpath.read_text(encoding="utf-8"))
        assert loaded == data

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        data = {"hello": "world"}
        fpath = tmp_path / "sub" / "dir" / "cfg.json"
        save_json(fpath, data)
        assert fpath.exists()
        assert load_json(fpath) == data


class TestBaseDir:
    def test_base_dir_is_path(self) -> None:
        assert isinstance(BASE_DIR, Path)

    def test_base_dir_contains_src(self) -> None:
        assert (BASE_DIR / "src").is_dir()

    def test_config_dir_is_path(self) -> None:
        assert isinstance(CONFIG_DIR, Path)

    def test_config_dir_under_base_dir(self) -> None:
        assert CONFIG_DIR.parent == BASE_DIR
        assert CONFIG_DIR.name == "config"


class TestValidatePortfolio:
    def test_valid_portfolio_returns_no_issues(self) -> None:
        portfolio = {"positions": [{"code": "000001.SZ", "current_weight": 0.15}]}
        assert validate_portfolio(portfolio) == []

    def test_duplicate_code_detected(self) -> None:
        portfolio = {"positions": [
            {"code": "000001.SZ", "current_weight": 0.1},
            {"code": "000001.SZ", "current_weight": 0.1},
        ]}
        issues = validate_portfolio(portfolio)
        assert any("duplicate" in i.lower() for i in issues)

    def test_weight_out_of_range(self) -> None:
        portfolio = {"positions": [{"code": "000001.SZ", "current_weight": 1.5}]}
        issues = validate_portfolio(portfolio)
        assert any("out of" in i.lower() for i in issues)

    def test_weight_field_validated_as_fallback(self) -> None:
        """weight field (without current_weight) should also be validated."""
        portfolio = {"positions": [{"code": "000001.SZ", "weight": 0.15}]}
        issues = validate_portfolio(portfolio)
        assert len(issues) == 0

    def test_weight_field_out_of_range(self) -> None:
        portfolio = {"positions": [{"code": "000001.SZ", "weight": -0.1}]}
        issues = validate_portfolio(portfolio)
        assert any("out of" in i.lower() for i in issues)

    def test_both_weights_different_warns(self) -> None:
        """When current_weight and weight differ, emit a warning."""
        portfolio = {"positions": [{"code": "000001.SZ", "current_weight": 0.15, "weight": 0.10}]}
        issues = validate_portfolio(portfolio)
        assert any("differs" in i.lower() for i in issues)

    def test_missing_code_reported(self) -> None:
        portfolio = {"positions": [{"name": "test"}]}
        issues = validate_portfolio(portfolio)
        assert any("missing" in i.lower() for i in issues)

    def test_bad_code_format_reported(self) -> None:
        portfolio = {"positions": [{"code": "000001"}]}  # no exchange suffix
        issues = validate_portfolio(portfolio)
        assert any("CODE.EXCHANGE" in i for i in issues)


class TestValidateWatchlist:
    def test_valid_watchlist(self) -> None:
        watchlist = {"watchlist": [{"code": "688981.SH"}]}
        assert validate_watchlist(watchlist) == []

    def test_duplicate_code(self) -> None:
        watchlist = {"watchlist": [{"code": "688981.SH"}, {"code": "688981.SH"}]}
        issues = validate_watchlist(watchlist)
        assert any("duplicate" in i.lower() for i in issues)

    def test_missing_code(self) -> None:
        watchlist = {"watchlist": [{"name": "test"}]}
        issues = validate_watchlist(watchlist)
        assert any("missing" in i.lower() for i in issues)


class TestValidateStrategy:
    def test_valid_strategy(self) -> None:
        strategy = {
            "risk_rules": {"position_breakdown_pct": -8.0},
            "portfolio_rules": {"max_total_weight": 0.8},
        }
        assert validate_strategy(strategy) == []

    def test_invalid_number_in_risk_rules(self) -> None:
        strategy = {"risk_rules": {"position_breakdown_pct": "not_a_number"}}
        issues = validate_strategy(strategy)
        assert any("invalid" in i.lower() for i in issues)

    def test_weight_out_of_range(self) -> None:
        strategy = {"portfolio_rules": {"max_total_weight": 1.5}}
        issues = validate_strategy(strategy)
        assert any("out of" in i.lower() for i in issues)
