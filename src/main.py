from __future__ import annotations

from datetime import datetime
from pathlib import Path

from analyzer import evaluate_market_snapshot, analyze_with_llm
from config_loader import BASE_DIR, CONFIG_DIR, bootstrap_environment, load_json
from fetchers.market_data import fetch_market_snapshot
from fetchers.news_fetcher import fetch_pre_market_context
from scheduler import get_session_type

try:
    from notifier.emailer import send_signal_email
    from notifier.signal_registry import record_sent_signal, should_send_signal
except Exception:
    send_signal_email = None
    record_sent_signal = None
    should_send_signal = None


def ensure_runtime_dirs() -> None:
    for folder in ["logs", "reports", "data", "data/cache", "data/signals"]:
        (BASE_DIR / folder).mkdir(parents=True, exist_ok=True)


def load_runtime_configs() -> tuple[dict, dict, dict]:
    portfolio = load_json(CONFIG_DIR / "portfolio.json")
    watchlist = load_json(CONFIG_DIR / "watchlist.json")
    strategy = load_json(CONFIG_DIR / "strategy.json")
    return portfolio, watchlist, strategy


def _fmt_weight(value: float) -> str:
    return f"{value:.0%}"


def build_email_body(decision) -> str:
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


def _should_run_in_session_llm(strategy: dict) -> bool:
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


def _merge_decisions(primary: list, secondary: list) -> list:
    existing_keys = {(d.action, d.symbol) for d in primary}
    merged = list(primary)
    for d in secondary:
        if (d.action, d.symbol) not in existing_keys:
            merged.append(d)
            existing_keys.add((d.action, d.symbol))
    priority = {"high": 0, "medium": 1, "low": 2}
    return sorted(merged, key=lambda d: (priority.get(d.urgency, 9), d.action, d.symbol))


def _process_decisions(decisions: list, snapshot: dict, strategy: dict) -> None:
    cooldown_minutes = int(strategy.get("max_same_signal_cooldown_minutes", 180))

    if not decisions:
        report = (
            "# 本次巡检\n\n"
            "结论：暂无需要交易的信号。\n\n"
            f"数据源：{snapshot.get('data_sources', {})}\n"
        )
        save_report(report)
        print("No trade signal.")
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
        if send_signal_email and should_send_signal and record_sent_signal:
            allowed, reason = should_send_signal(decision, cooldown_minutes)
            if allowed:
                try:
                    send_signal_email(f"[交易信号] {decision.summary}", build_email_body(decision))
                    record_sent_signal(decision)
                    email_status = f"已发送（{reason}）"
                except Exception as exc:
                    email_status = f"发送失败：{exc}"
            else:
                email_status = f"已去重，未发送（{reason}）"
        elif send_signal_email:
            email_status = "邮件模块可用，但去重模块未加载"
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
    print("Trade signal generated.")


def main() -> None:
    bootstrap_environment()
    ensure_runtime_dirs()
    portfolio, watchlist, strategy = load_runtime_configs()

    session_type = get_session_type()
    if session_type == "closed":
        report = "# 本次巡检\n\n结论：当前不在A股交易时段，跳过巡检。\n"
        save_report(report)
        print("Not in trading session.")
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
        print(f"Market data fetch failed: {exc}")
        return

    # --- Pre-market: LLM-driven analysis with news context ---
    if session_type == "pre_market":
        news_context = fetch_pre_market_context(portfolio, watchlist)
        llm_decisions = analyze_with_llm(snapshot, news_context, portfolio, watchlist, strategy)

        if not llm_decisions:
            report_lines = [
                "# 盘前分析",
                "",
                f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"- 全球市场：{news_context.get('global_markets', {})}",
                f"- 新闻条数：{len(news_context.get('market_news', []))}",
                f"- 采集错误：{news_context.get('errors', [])}",
                "",
                "结论：LLM分析未产生交易信号，维持现有仓位不变。",
            ]
            save_report("\n".join(report_lines))
            print("Pre-market: no signals from LLM.")
            return

        decisions = llm_decisions
        _process_decisions(decisions, snapshot, strategy)
        return

    # --- In-session: rule engine + optional LLM enhancement ---
    decisions = evaluate_market_snapshot(snapshot, portfolio, watchlist, strategy)

    llm_config = strategy.get("llm_analysis", {})
    if llm_config.get("in_session_enabled") and _should_run_in_session_llm(strategy):
        try:
            news_context = fetch_pre_market_context(portfolio, watchlist)
            llm_decisions = analyze_with_llm(snapshot, news_context, portfolio, watchlist, strategy)
            decisions = _merge_decisions(decisions, llm_decisions)
        except Exception:
            pass

    _process_decisions(decisions, snapshot, strategy)


if __name__ == "__main__":
    main()
