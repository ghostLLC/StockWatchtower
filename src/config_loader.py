from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def validate_portfolio(portfolio: dict[str, Any]) -> list[str]:
    """Return list of issues (empty means valid)."""
    issues = []
    positions = portfolio.get("positions", [])
    if not isinstance(positions, list):
        return ["portfolio.positions must be a list"]
    codes_seen: set[str] = set()
    for i, pos in enumerate(positions):
        code = pos.get("code", "")
        if not code:
            issues.append(f"positions[{i}]: missing 'code'")
            continue
        if "." not in code or len(code.split(".", 1)) != 2:
            issues.append(f"positions[{i}]: '{code}' does not match CODE.EXCHANGE format")
        if code in codes_seen:
            issues.append(f"positions[{i}]: duplicate code '{code}'")
        codes_seen.add(code)
        if "current_weight" in pos:
            w = pos["current_weight"]
            if not isinstance(w, (int, float)) or w < 0 or w > 1:
                issues.append(f"positions[{i}]: current_weight={w} out of [0,1]")
    return issues


def validate_watchlist(watchlist: dict[str, Any]) -> list[str]:
    """Return list of issues (empty means valid)."""
    issues = []
    items = watchlist.get("watchlist", [])
    if not isinstance(items, list):
        return ["watchlist.watchlist must be a list"]
    codes_seen: set[str] = set()
    for i, item in enumerate(items):
        code = item.get("code", "")
        if not code:
            issues.append(f"watchlist[{i}]: missing 'code'")
            continue
        if "." not in code or len(code.split(".", 1)) != 2:
            issues.append(f"watchlist[{i}]: '{code}' does not match CODE.EXCHANGE format")
        if code in codes_seen:
            issues.append(f"watchlist[{i}]: duplicate code '{code}'")
        codes_seen.add(code)
    return issues


def validate_strategy(strategy: dict[str, Any]) -> list[str]:
    """Return list of issues (empty means valid)."""
    issues = []
    risk_rules = strategy.get("risk_rules", {})
    for key in ("position_breakdown_pct", "watchlist_stop_loss_pct",
                 "trailing_take_profit_pct", "hard_take_profit_pct"):
        if key in risk_rules:
            try:
                float(risk_rules[key])
            except (TypeError, ValueError):
                issues.append(f"strategy.risk_rules.{key}: invalid number")
    portfolio_rules = strategy.get("portfolio_rules", {})
    for key in ("max_total_weight", "max_single_position_weight",
                 "max_tech_weight", "max_sector_weight"):
        val = portfolio_rules.get(key)
        if val is not None and (not isinstance(val, (int, float)) or val < 0 or val > 1):
            issues.append(f"strategy.portfolio_rules.{key}: {val} out of [0,1]")
    return issues


def bootstrap_environment() -> None:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()
