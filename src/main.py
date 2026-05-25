from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import json as _json
from datetime import time as _time

from agents import run_pre_market_agent
from analyzer import evaluate_market_snapshot, analyze_with_llm, SignalDecision
from config_loader import BASE_DIR, CONFIG_DIR, bootstrap_environment, load_json, validate_portfolio, validate_watchlist, validate_strategy
from fetchers.capital_flow import fetch_north_flow_context
from fetchers.market_data import fetch_market_snapshot
from fetchers.news_fetcher import fetch_dragon_tiger_matches, fetch_pre_market_context
from logging_setup import configure_logging
from scheduler import get_session_type

logger = logging.getLogger(__name__)

try:
    from notifier.emailer import send_signal_email
    from notifier.signal_registry import record_sent_signal, should_send_signal
except Exception:
    send_signal_email = None  # type: ignore[assignment]
    record_sent_signal = None  # type: ignore[assignment]
    should_send_signal = None  # type: ignore[assignment]


def ensure_runtime_dirs() -> None:
    for folder in ["logs", "reports", "data", "data/cache", "data/signals"]:
        (BASE_DIR / folder).mkdir(parents=True, exist_ok=True)


def load_runtime_configs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    portfolio = load_json(CONFIG_DIR / "portfolio.json")
    watchlist = load_json(CONFIG_DIR / "watchlist.json")
    strategy = load_json(CONFIG_DIR / "strategy.json")
    for issues, label in [
        (validate_portfolio(portfolio), "portfolio"),
        (validate_watchlist(watchlist), "watchlist"),
        (validate_strategy(strategy), "strategy"),
    ]:
        for issue in issues:
            logger.warning("Config [%s]: %s", label, issue)
    return portfolio, watchlist, strategy


def _fmt_weight(value: float) -> str:
    return f"{value:.0%}"


def build_email_body(decision: SignalDecision) -> str:
    return (
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"动作：{decision.action}\n"
        f"标的：{decision.symbol}\n"
        f"板块/方向：{decision.sector}\n"
        f"摘要：{decision.summary}\n"
        f"当前仓位：{_fmt_weight(decision.current_weight)}\n"
        f"目标仓位：{_fmt_weight(decision.target_weight)}\n"
        f"建议仓位变化：{_fmt_weight(decision.suggested_weight_change)}\n"
        f"参考价格区间：{decision.price_zone}\n"
        f"执行建议：{decision.execution_plan}\n"
        f"失效条件：{decision.invalidation_rule}\n"
        f"风险提示：{decision.risk_note}\n"
        f"紧急程度：{decision.urgency}\n"
        f"原因：{decision.reason}\n"
    )


def save_report(content: str) -> Path:
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = BASE_DIR / "reports" / f"run_{ts}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _llm_cooldown_path() -> Path:
    return BASE_DIR / "data" / "signals" / "llm_in_session_last.txt"


def _should_run_in_session_llm(strategy: dict[str, Any]) -> bool:
    cooldown_minutes = int(strategy.get("llm_analysis", {}).get("in_session_cooldown_minutes", 60))
    path = _llm_cooldown_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(datetime.now().isoformat(), encoding="utf-8")
        return True
    try:
        last = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
        elapsed = (datetime.now() - last).total_seconds()
        if elapsed > cooldown_minutes * 60:
            path.write_text(datetime.now().isoformat(), encoding="utf-8")
            return True
    except Exception:
        path.write_text(datetime.now().isoformat(), encoding="utf-8")
        return True
    return False


def _daily_queue_path(ts: str | None = None) -> Path:
    if ts is None:
        ts = datetime.now().strftime("%Y%m%d")
    (BASE_DIR / "data" / "signals").mkdir(parents=True, exist_ok=True)
    return BASE_DIR / "data" / "signals" / f"daily_queue_{ts}.json"


def _queue_daily_signal(decision: SignalDecision) -> None:
    path = _daily_queue_path()
    if path.exists():
        queue = _json.loads(path.read_text(encoding="utf-8"))
    else:
        queue = {"date": datetime.now().strftime("%Y-%m-%d"), "items": []}
    key = f"{decision.action}|{decision.symbol}"
    if any(i.get("key") == key for i in queue["items"]):
        return
    queue["items"].append({
        "key": key,
        "urgency": decision.urgency,
        "action": decision.action,
        "symbol": decision.symbol,
        "summary": decision.summary,
        "reason": decision.reason,
        "current_weight": decision.current_weight,
        "target_weight": decision.target_weight,
        "suggested_weight_change": decision.suggested_weight_change,
        "sector": decision.sector,
        "risk_note": decision.risk_note,
    })
    path.write_text(_json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_session_end() -> bool:
    current = datetime.now().time()
    return _time(11, 25) <= current <= _time(11, 30) or _time(14, 55) <= current <= _time(15, 0)


def _send_daily_summary() -> None:
    if send_signal_email is None:
        return
    path = _daily_queue_path()
    if not path.exists():
        return
    queue = _json.loads(path.read_text(encoding="utf-8"))
    items = queue.get("items", [])
    if not items:
        return

    lines = [
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"日期：{queue.get('date', '')}",
        "",
        f"本交易日累计产生 {len(items)} 条非紧急信号（汇总）：",
        "",
    ]
    for i, item in enumerate(items, 1):
        lines.extend([
            f"--- {i}. [{item['urgency']}] {item['action']} {item['symbol']} ---",
            f"摘要：{item['summary']}",
            f"仓位变化：{_fmt_weight(item['suggested_weight_change'])}",
            f"板块：{item['sector']}",
            f"风险提示：{item.get('risk_note', '无')}",
            "",
        ])
    lines.append("这些信号均可在控制面板的巡检报告中查看详情。")

    try:
        send_signal_email("[每日信号汇总] 非紧急信号合集", "\n".join(lines))
        path.unlink()
        logger.info("Daily summary sent.")
    except Exception as exc:
        logger.error("Daily summary failed: %s", exc)


def _merge_decisions(primary: list[SignalDecision], secondary: list[SignalDecision]) -> list[SignalDecision]:
    existing_keys = {(d.action, d.symbol) for d in primary}
    merged = list(primary)
    for d in secondary:
        if (d.action, d.symbol) not in existing_keys:
            merged.append(d)
            existing_keys.add((d.action, d.symbol))
    priority = {"high": 0, "medium": 1, "low": 2}
    return sorted(merged, key=lambda d: (priority.get(d.urgency, 9), d.action, d.symbol))


def _process_decisions(decisions: list[SignalDecision], snapshot: dict[str, Any], strategy: dict[str, Any]) -> None:
    cooldown_minutes = int(strategy.get("max_same_signal_cooldown_minutes", 180))
    send_only_on_action = strategy.get("send_only_on_action", True)

    if not decisions:
        report = (
            "# 本次巡检\n\n"
            "结论：暂无需要交易的信号。\n\n"
            f"数据源：{snapshot.get('data_sources', {})}\n"
        )
        save_report(report)
        logger.info("No trade signal.")
        return

    report_lines = [
        "# 本次巡检",
        "",
        f"- 数据源：{snapshot.get('data_sources', {})}",
        f"- 市场状态：{snapshot.get('market_state', 'unknown')}",
        "",
    ]
    for decision in decisions:
        email_status = "未发送"
        is_high = decision.urgency == "high"

        if send_signal_email is not None and should_send_signal is not None and record_sent_signal is not None:
            allowed, reason = should_send_signal(decision, cooldown_minutes)
            if allowed and (is_high or not send_only_on_action):
                try:
                    send_signal_email(f"[交易信号] {decision.summary}", build_email_body(decision))
                    record_sent_signal(decision)
                    email_status = f"已发送（{reason}）"
                except Exception as exc:
                    email_status = f"发送失败：{exc}"
            elif allowed:
                _queue_daily_signal(decision)
                email_status = f"已加入每日汇总（{reason}）"
            else:
                email_status = f"已去重，未发送（{reason}）"
        elif send_signal_email is not None:
            if is_high or not send_only_on_action:
                try:
                    send_signal_email(f"[交易信号] {decision.summary}", build_email_body(decision))
                    email_status = "已发送（去重模块未加载）"
                except Exception as exc:
                    email_status = f"发送失败：{exc}"
            else:
                _queue_daily_signal(decision)
                email_status = "已加入每日汇总（去重模块未加载）"
        else:
            email_status = "邮件模块未启用"

        report_lines.extend([
            f"## {decision.summary}",
            f"- 动作：{decision.action}",
            f"- 标的：{decision.symbol}",
            f"- 板块/方向：{decision.sector}",
            f"- 当前仓位：{_fmt_weight(decision.current_weight)}",
            f"- 目标仓位：{_fmt_weight(decision.target_weight)}",
            f"- 仓位变化：{_fmt_weight(decision.suggested_weight_change)}",
            f"- 价格区间：{decision.price_zone}",
            f"- 执行建议：{decision.execution_plan}",
            f"- 失效条件：{decision.invalidation_rule}",
            f"- 风险提示：{decision.risk_note}",
            f"- 紧急程度：{decision.urgency}",
            f"- 原因：{decision.reason}",
            f"- 邮件状态：{email_status}",
            "",
        ])

    save_report("\n".join(report_lines))
    logger.info("Trade signal generated; %d signals processed.", len(decisions))


def main() -> None:
    bootstrap_environment()
    configure_logging()
    ensure_runtime_dirs()
    portfolio, watchlist, strategy = load_runtime_configs()

    session_type = get_session_type(strategy=strategy)
    if session_type == "closed":
        report = "# 本次巡检\n\n结论：当前不在A股交易时段，跳过巡检。\n"
        save_report(report)
        logger.info("Not in trading session.")
        return

    try:
        snapshot = fetch_market_snapshot(portfolio, watchlist)
    except Exception as exc:
        report = (
            "# 本次巡检\n\n"
            "结论：行情采集失败，已跳过本次巡检。\n\n"
            f"原因：{exc}\n"
        )
        save_report(report)
        logger.error("Market data fetch failed: %s", exc)
        return

    # --- Pre-market: agent-driven analysis with autonomous search ---
    if session_type == "pre_market":
        decisions = run_pre_market_agent(snapshot, portfolio, watchlist, strategy)

        if not decisions:
            report_lines = [
                "# 盘前分析",
                "",
                f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"- 市场状态：{snapshot.get('market_state', 'unknown')}",
                "",
                "结论：Agent分析未产生交易信号，维持现有仓位不变。",
            ]
            save_report("\n".join(report_lines))
            logger.info("Pre-market: no signals from agent.")
            return

        _process_decisions(decisions, snapshot, strategy)
        return

    # --- In-session: rule engine + optional LLM enhancement ---
    decisions = evaluate_market_snapshot(snapshot, portfolio, watchlist, strategy)

    llm_config = strategy.get("llm_analysis", {})
    if llm_config.get("in_session_enabled") and _should_run_in_session_llm(strategy):
        try:
            news_context = fetch_pre_market_context(portfolio, watchlist)
            if strategy.get("monitoring", {}).get("north_flow_enabled", True):
                news_context["north_flow"] = fetch_north_flow_context()
            if strategy.get("monitoring", {}).get("dragon_tiger_enabled", True):
                news_context["dragon_tiger"] = fetch_dragon_tiger_matches(portfolio, watchlist)
            llm_decisions = analyze_with_llm(snapshot, news_context, portfolio, watchlist, strategy)
            decisions = _merge_decisions(decisions, llm_decisions)
        except Exception as exc:
            logger.warning("[LLM] in-session analysis skipped: %s", exc)

    _process_decisions(decisions, snapshot, strategy)

    if _is_session_end():
        _send_daily_summary()


if __name__ == "__main__":
    main()
