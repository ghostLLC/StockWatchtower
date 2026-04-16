from __future__ import annotations

from datetime import datetime
from pathlib import Path

from analyzer import evaluate_market_snapshot
from config_loader import BASE_DIR, CONFIG_DIR, bootstrap_environment, load_json
from fetchers.market_data import fetch_market_snapshot
from scheduler import get_session_status

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


def main() -> None:
    bootstrap_environment()
    ensure_runtime_dirs()
    portfolio, watchlist, strategy = load_runtime_configs()

    session_status = get_session_status(strategy)
    if not session_status.is_open_session:
        report = f"# 本次巡检\n\n结论：{session_status.reason}\n"
        save_report(report)
        print(session_status.reason)
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

    decisions = evaluate_market_snapshot(snapshot, portfolio, watchlist, strategy)

    if not decisions:
        report = (
            "# 本次巡检\n\n"
            "结论：暂无需要交易的信号。\n\n"
            f"数据源：{snapshot.get('data_sources', {})}\n"
        )
        save_report(report)
        print("No trade signal.")
        return

    cooldown_minutes = int(strategy.get("max_same_signal_cooldown_minutes", 180))
    report_lines = [
        "# 本次巡检",
        "",
        f"- 数据源：{snapshot.get('data_sources', {})}",
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


if __name__ == "__main__":
    main()
