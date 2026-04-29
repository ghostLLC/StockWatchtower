from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SignalDecision:
    should_trade: bool
    action: str
    symbol: str
    summary: str
    reason: str
    urgency: str
    suggested_weight_change: float = 0.0
    price_zone: str = "待结合实时行情确认"
    current_weight: float = 0.0
    target_weight: float = 0.0
    sector: str = "未分类"
    execution_plan: str = "分批执行"
    invalidation_rule: str = "若后续行情与趋势条件失效，则放弃执行。"
    risk_note: str = "注意控制单票和组合总仓位。"


@dataclass
class PortfolioRiskState:
    invested_weight: float
    tech_weight: float
    max_single_position_weight: float
    position_count: int
    sector_weights: dict[str, float]


def _format_price_zone(latest: Any, action: str = "") -> str:
    if latest in (None, "", 0):
        return "待结合盘中价格确认"
    try:
        latest_val = float(latest)
        if action in ("buy", "add"):
            low = round(latest_val * 0.97, 2)
            high = round(latest_val * 1.01, 2)
        elif action in ("sell", "reduce", "take_profit"):
            low = round(latest_val * 0.985, 2)
            high = round(latest_val * 1.03, 2)
        else:
            low = round(latest_val * 0.985, 2)
            high = round(latest_val * 1.01, 2)
        return f"{low} - {high}"
    except Exception:
        return "待结合盘中价格确认"


def _is_tech_like(item: dict[str, Any]) -> bool:
    code = str(item.get("code", ""))
    reason = str(item.get("reason", ""))
    sector = str(item.get("sector", ""))
    keywords = ["芯片", "半导体", "算力", "ai", "软件", "服务器", "科技", "数据"]
    text = f"{reason} {sector}".lower()
    return code.startswith("688") or any(keyword.lower() in text for keyword in keywords)


def _position_weight(item: dict[str, Any]) -> float:
    # current_weight is the canonical field per validation; weight is a legacy fallback.
    for key in ["current_weight", "weight"]:
        value = item.get(key)
        if value is None or value == "":
            continue
        try:
            return round(float(value), 4)
        except Exception:
            continue
    return 0.0


def _sector_name(item: dict[str, Any]) -> str:
    return str(item.get("sector") or item.get("theme") or item.get("board") or "未分类")


def _build_portfolio_risk_state(positions: list[dict[str, Any]]) -> PortfolioRiskState:
    weights = [_position_weight(pos) for pos in positions]
    invested_weight = round(sum(weights), 4)
    tech_weight = round(sum(_position_weight(pos) for pos in positions if _is_tech_like(pos)), 4)
    max_single = round(max(weights), 4) if weights else 0.0
    sector_weights: dict[str, float] = {}
    for pos in positions:
        sector = _sector_name(pos)
        sector_weights[sector] = round(sector_weights.get(sector, 0.0) + _position_weight(pos), 4)
    return PortfolioRiskState(
        invested_weight=invested_weight,
        tech_weight=tech_weight,
        max_single_position_weight=max_single,
        position_count=len(positions),
        sector_weights=sector_weights,
    )


def _make_decision(
    *,
    action: str,
    symbol: str,
    summary: str,
    reason: str,
    urgency: str,
    suggested_weight_change: float,
    price_zone: str,
    current_weight: float = 0.0,
    target_weight: float | None = None,
    sector: str = "未分类",
    execution_plan: str = "分批执行",
    invalidation_rule: str = "若后续行情与趋势条件失效，则放弃执行。",
    risk_note: str = "注意控制单票和组合总仓位。",
) -> SignalDecision:
    return SignalDecision(
        should_trade=True,
        action=action,
        symbol=symbol,
        summary=summary,
        reason=reason,
        urgency=urgency,
        suggested_weight_change=round(suggested_weight_change, 4),
        price_zone=price_zone,
        current_weight=round(current_weight, 4),
        target_weight=round(current_weight + suggested_weight_change, 4) if target_weight is None else round(target_weight, 4),
        sector=sector,
        execution_plan=execution_plan,
        invalidation_rule=invalidation_rule,
        risk_note=risk_note,
    )


def _append_portfolio_risk_decisions(
    decisions: list[SignalDecision],
    positions: list[dict[str, Any]],
    risk_state: PortfolioRiskState,
    strategy: dict[str, Any],
) -> None:
    portfolio_rules = strategy.get("portfolio_rules", {})
    max_total_weight = float(portfolio_rules.get("max_total_weight", 0.8))
    max_single_weight = float(portfolio_rules.get("max_single_position_weight", 0.2))
    max_tech_weight = float(portfolio_rules.get("max_tech_weight", 0.55))
    max_sector_weight = float(portfolio_rules.get("max_sector_weight", 0.35))

    if risk_state.invested_weight > max_total_weight:
        excess = round(risk_state.invested_weight - max_total_weight, 4)
        decisions.append(
            _make_decision(
                action="rebalance",
                symbol="PORTFOLIO",
                summary="建议整体降低仓位",
                reason=f"当前估算总仓位约 {risk_state.invested_weight:.0%}，高于策略上限 {max_total_weight:.0%}，应优先回收 {excess:.0%} 左右仓位。",
                urgency="high",
                suggested_weight_change=-excess,
                price_zone="按持仓分批减仓",
                current_weight=risk_state.invested_weight,
                target_weight=max_total_weight,
                sector="组合",
                execution_plan="优先处理弹性大、走势弱的持仓，分2-3次回收总仓位。",
                invalidation_rule="若组合总仓位已自然回落到上限以内，可取消本次整体减仓。",
                risk_note="总仓位过高时，组合回撤容易被放大。",
            )
        )

    if risk_state.tech_weight > max_tech_weight:
        excess = round(risk_state.tech_weight - max_tech_weight, 4)
        decisions.append(
            _make_decision(
                action="rebalance",
                symbol="TECH_BUCKET",
                summary="建议降低科技方向集中度",
                reason=f"科技相关持仓估算约 {risk_state.tech_weight:.0%}，高于上限 {max_tech_weight:.0%}，组合风格过于集中。",
                urgency="medium",
                suggested_weight_change=-excess,
                price_zone="优先从高波动科技仓位中腾挪",
                current_weight=risk_state.tech_weight,
                target_weight=max_tech_weight,
                sector="科技",
                execution_plan="优先从高波动、涨幅透支或弱于板块的科技持仓中减仓。",
                invalidation_rule="若科技仓位占比已回到上限以内，取消该组合动作。",
                risk_note="风格过度集中时，一旦主线回撤，组合会同步受压。",
            )
        )

    for sector, sector_weight in risk_state.sector_weights.items():
        if sector_weight > max_sector_weight:
            excess = round(sector_weight - max_sector_weight, 4)
            decisions.append(
                _make_decision(
                    action="rebalance",
                    symbol=f"SECTOR::{sector}",
                    summary=f"建议降低 {sector} 板块集中度",
                    reason=f"当前 {sector} 方向仓位约 {sector_weight:.0%}，已高于板块上限 {max_sector_weight:.0%}。",
                    urgency="medium",
                    suggested_weight_change=-excess,
                    price_zone="从同板块内弱势个股分批减仓",
                    current_weight=sector_weight,
                    target_weight=max_sector_weight,
                    sector=sector,
                    execution_plan="优先减掉板块内弹性过高、走势转弱或盈利兑现充分的标的。",
                    invalidation_rule="若板块仓位已降至上限以下，则取消该动作。",
                    risk_note="单一板块过重会放大行业级回撤风险。",
                )
            )

    for pos in positions:
        weight = _position_weight(pos)
        if weight > max_single_weight:
            excess = round(weight - max_single_weight, 4)
            decisions.append(
                _make_decision(
                    action="trim",
                    symbol=pos.get("code", "UNKNOWN"),
                    summary=f"建议降低 {pos.get('name', pos.get('code', 'UNKNOWN'))} 单票仓位",
                    reason=f"当前单票估算仓位约 {weight:.0%}，高于单票上限 {max_single_weight:.0%}，建议分批回落到上限附近。",
                    urgency="medium",
                    suggested_weight_change=-excess,
                    price_zone=_format_price_zone(pos.get("latest"), "trim"),
                    current_weight=weight,
                    target_weight=max_single_weight,
                    sector=_sector_name(pos),
                    execution_plan="可分2次减仓，先减高于上限的部分，再观察趋势是否延续。",
                    invalidation_rule="若单票仓位已通过其他操作回落到上限附近，则不再额外减仓。",
                    risk_note="单票过重会让个股事件风险直接放大到账户层面。",
                )
            )


def _append_position_management_decisions(
    decisions: list[SignalDecision],
    positions: list[dict[str, Any]],
    snapshot: dict[str, Any],
    strategy: dict[str, Any],
) -> None:
    risk_rules = strategy.get("risk_rules", {})
    opportunity_rules = strategy.get("opportunity_rules", {})
    alert_rules = strategy.get("alert_rules", {})
    market_state = snapshot.get("market_state", "neutral")

    position_breakdown_pct = float(risk_rules.get("position_breakdown_pct", -8.0))
    watchlist_stop_loss_pct = float(risk_rules.get("watchlist_stop_loss_pct", -3.5))
    trailing_take_profit_pct = float(risk_rules.get("trailing_take_profit_pct", 12.0))
    hard_take_profit_pct = float(risk_rules.get("hard_take_profit_pct", 18.0))
    max_add_weight = float(opportunity_rules.get("max_add_weight", 0.05))
    intraday_crash_pct = float(alert_rules.get("intraday_crash_pct", -5.0))
    volume_spike_ratio = float(alert_rules.get("volume_spike_ratio", 3.0))

    for pos in positions:
        weight = _position_weight(pos)
        if weight <= 0:
            continue
        change_pct = float(pos.get("change_pct", 0.0))
        monthly_change_pct = float(pos.get("monthly_change_pct", 0.0))
        price_vs_ma20 = float(pos.get("price_vs_ma20_pct", 0.0))
        name = pos.get("name", pos.get("code", "UNKNOWN"))
        sector = _sector_name(pos)
        vol_ratio = pos.get("volume_ratio")

        # ── Intraday crash: trigger immediately regardless of other checks ──
        if change_pct <= intraday_crash_pct and change_pct != 0.0:
            decisions.append(
                _make_decision(
                    action="alert",
                    symbol=pos.get("code", "UNKNOWN"),
                    summary=f"日内急跌告警 - {name}",
                    reason=f"{name} 当前跌幅 {change_pct:.2f}%，已触发日内急跌阈值 {intraday_crash_pct}%，请立即关注是否止损或减仓。",
                    urgency="high",
                    suggested_weight_change=-max_add_weight,
                    price_zone=_format_price_zone(pos.get("latest"), "alert"),
                    current_weight=weight,
                    sector=sector,
                    execution_plan="优先查看基本面/消息面是否有突发利空，若利空确认应果断减仓；若无明显利空，可观察20日均线支撑再决定。",
                    invalidation_rule="若跌幅快速收窄至阈值以内且收复分时均线，可取消本告警。",
                    risk_note="急跌往往伴随流动性收缩，减仓执行时注意滑点。",
                )
            )

        # ── Volume spike: unusual activity warning ──
        if vol_ratio is not None and vol_ratio >= volume_spike_ratio:
            direction = "放量上涨" if change_pct > 0 else "放量下跌"
            decisions.append(
                _make_decision(
                    action="alert",
                    symbol=pos.get("code", "UNKNOWN"),
                    summary=f"异常放量告警 - {name}",
                    reason=f"{name} 当前成交额为20日均量的 {vol_ratio:.1f} 倍（{direction}），出现异常放量，需判断是大资金进出还是事件驱动。",
                    urgency="high" if change_pct < -2.0 else "medium",
                    suggested_weight_change=0.0,
                    price_zone=_format_price_zone(pos.get("latest"), "alert"),
                    current_weight=weight,
                    sector=sector,
                    execution_plan="先观察放量方向能否持续：若放量上涨且站稳分时均线，可能是突破信号；若放量下跌，应警惕机构出货。",
                    invalidation_rule="若成交量快速回归正常水平，则取消本告警。",
                    risk_note="异常放量往往是行情的转折点，不要急于反向操作。",
                )
            )

        if market_state == "risk_off":
            decisions.append(
                _make_decision(
                    action="reduce",
                    symbol=pos.get("code", "UNKNOWN"),
                    summary=f"建议降低 {name} 仓位",
                    reason="指数进入明显风险区，优先控制组合回撤。",
                    urgency="high",
                    suggested_weight_change=-max_add_weight,
                    price_zone=_format_price_zone(pos.get("latest"), "reduce"),
                    current_weight=weight,
                    sector=sector,
                    execution_plan="先减仓 5% 左右观察，若指数继续走弱再继续回收仓位。",
                    invalidation_rule="若指数风险状态修复且个股重新站回20日均线，可取消本次减仓。",
                    risk_note="系统性风险优先级高于个股逻辑。",
                )
            )
            continue

        if change_pct <= position_breakdown_pct or (change_pct <= watchlist_stop_loss_pct and price_vs_ma20 < -2.0):
            decisions.append(
                _make_decision(
                    action="sell",
                    symbol=pos.get("code", "UNKNOWN"),
                    summary=f"建议卖出或显著减仓 {name}",
                    reason="个股跌破中期均线且盘中走弱，需要优先回避趋势破坏。",
                    urgency="high",
                    suggested_weight_change=-min(weight, max_add_weight if weight > 0 else max_add_weight),
                    price_zone=_format_price_zone(pos.get("latest"), "sell"),
                    current_weight=weight,
                    sector=sector,
                    execution_plan="若盘中弱势延续，优先卖出一半以上仓位；若次日仍无法修复，可继续退出。",
                    invalidation_rule="若股价重新收复20日均线并恢复强于板块的表现，可延后卖出。",
                    risk_note="趋势破坏时，先保住本金比等反弹更重要。",
                )
            )
            continue

        if monthly_change_pct >= hard_take_profit_pct and price_vs_ma20 < 1.5:
            decisions.append(
                _make_decision(
                    action="take_profit",
                    symbol=pos.get("code", "UNKNOWN"),
                    summary=f"建议分批止盈 {name}",
                    reason=f"近20日涨幅约 {monthly_change_pct:.2f}% ，已达到较高收益区，但价格相对20日均线优势收窄，适合锁定部分利润。",
                    urgency="medium",
                    suggested_weight_change=-max_add_weight,
                    price_zone=_format_price_zone(pos.get("latest"), "take_profit"),
                    current_weight=weight,
                    sector=sector,
                    execution_plan="先兑现 5% 仓位，剩余仓位继续观察是否还能维持趋势。",
                    invalidation_rule="若股价重新放量走强并扩大对20日均线的优势，可取消止盈。",
                    risk_note="浮盈票不及时落袋，回撤时心理成本会显著上升。",
                )
            )
            continue

        if monthly_change_pct >= trailing_take_profit_pct and change_pct < -2.0:
            decisions.append(
                _make_decision(
                    action="take_profit",
                    symbol=pos.get("code", "UNKNOWN"),
                    summary=f"建议回撤中分批止盈 {name}",
                    reason=f"标的已有较好浮盈（近20日约 {monthly_change_pct:.2f}%），但盘中回撤明显，适合先兑现一部分仓位。",
                    urgency="medium",
                    suggested_weight_change=-max_add_weight,
                    price_zone=_format_price_zone(pos.get("latest"), "take_profit"),
                    current_weight=weight,
                    sector=sector,
                    execution_plan="若回撤继续扩大，先兑现 5%，保留核心仓等待二次确认。",
                    invalidation_rule="若盘中回撤明显收敛并重新转强，可取消本次止盈。",
                    risk_note="高位回撤通常来得快，分批兑现能降低利润回吐。",
                )
            )
            continue

        if market_state == "risk_on" and price_vs_ma20 > 3.0 and monthly_change_pct > 6.0:
            decisions.append(
                _make_decision(
                    action="add",
                    symbol=pos.get("code", "UNKNOWN"),
                    summary=f"建议评估加仓 {name}",
                    reason="指数环境偏积极，个股站稳20日均线之上且中期趋势延续，可考虑小幅加仓强化优势仓位。",
                    urgency="low",
                    suggested_weight_change=max_add_weight,
                    price_zone=_format_price_zone(pos.get("latest"), "add"),
                    current_weight=weight,
                    sector=sector,
                    execution_plan="优先以小仓位试探性加仓，不追高，一次不超过 5%。",
                    invalidation_rule="若指数环境转弱或个股跌回20日均线下方，则取消加仓。",
                    risk_note="加仓只应发生在优势仓位，而不是试图摊平弱势票。",
                )
            )


def _append_open_position_decisions(
    decisions: list[SignalDecision],
    watch_items: list[dict[str, Any]],
    snapshot: dict[str, Any],
    strategy: dict[str, Any],
    risk_state: PortfolioRiskState,
) -> None:
    market_state = snapshot.get("market_state", "neutral")
    if market_state == "risk_off":
        return

    opportunity_rules = strategy.get("opportunity_rules", {})
    portfolio_rules = strategy.get("portfolio_rules", {})
    breakout_pct = float(opportunity_rules.get("watchlist_breakout_pct", 2.5))
    max_new_buy_weight = float(opportunity_rules.get("max_new_buy_weight", 0.1))
    max_total_weight = float(portfolio_rules.get("max_total_weight", 0.8))
    max_position_count = int(portfolio_rules.get("max_position_count", 6))
    max_sector_weight = float(portfolio_rules.get("max_sector_weight", 0.35))

    remaining_capacity = round(max_total_weight - risk_state.invested_weight, 4)
    if remaining_capacity <= 0 or risk_state.position_count >= max_position_count:
        return

    buy_candidates = [
        item for item in watch_items
        if float(item.get("change_pct", 0.0)) >= breakout_pct
        and float(item.get("price_vs_ma20_pct", 0.0)) > 0
        and float(item.get("monthly_change_pct", 0.0)) > 0
    ]
    buy_candidates = sorted(
        buy_candidates,
        key=lambda item: (
            float(item.get("monthly_change_pct", 0.0)),
            float(item.get("price_vs_ma20_pct", 0.0)),
            float(item.get("change_pct", 0.0)),
        ),
        reverse=True,
    )
    if not buy_candidates:
        return

    top = buy_candidates[0]
    sector = _sector_name(top)
    sector_weight = risk_state.sector_weights.get(sector, 0.0)
    if sector_weight >= max_sector_weight:
        return

    suggested_weight = round(min(max_new_buy_weight, remaining_capacity, max_sector_weight - sector_weight), 4)
    if suggested_weight <= 0:
        return

    indexes = snapshot.get("indexes", {})
    index_view = ", ".join([f"{name}{data.get('change_pct', 0.0)}%" for name, data in indexes.items()])
    decisions.append(
        _make_decision(
            action="buy",
            symbol=top.get("code", "UNKNOWN"),
            summary=f"建议评估建仓 {top.get('name', top.get('code', 'UNKNOWN'))}",
            reason=f"标的短中期趋势同步转强，且当前组合仍有约 {remaining_capacity:.0%} 仓位空间；指数环境：{index_view}",
            urgency="medium",
            suggested_weight_change=suggested_weight,
            price_zone=_format_price_zone(top.get("latest"), "buy"),
            current_weight=0.0,
            sector=sector,
            execution_plan="优先分2次建仓：先试仓一半，确认趋势延续后再补齐剩余计划仓位。",
            invalidation_rule="若次日跌回20日均线下方或板块同步转弱，则取消建仓。",
            risk_note="首次建仓先轻后重，避免把趋势判断做成一次性赌博。",
        )
    )


def _deduplicate_decisions(decisions: list[SignalDecision]) -> list[SignalDecision]:
    seen: set[tuple[str, str]] = set()
    deduped: list[SignalDecision] = []
    for decision in decisions:
        key = (decision.action, decision.symbol)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(decision)
    return deduped


def _filter_by_strategy(items: list[dict[str, Any]], strategy: dict[str, Any]) -> list[dict[str, Any]]:
    """Filter items by boards_allowed and markets_allowed from strategy config.

    If the config fields are absent or empty, all items pass through unchanged.
    """
    boards_allowed = strategy.get("boards_allowed")
    markets_allowed = strategy.get("markets_allowed")

    if not boards_allowed and not markets_allowed:
        return list(items)

    filtered: list[dict[str, Any]] = []
    for item in items:
        if boards_allowed:
            board = item.get("board", "")
            if board not in boards_allowed:
                continue
        if markets_allowed:
            market = item.get("market", "")
            if market not in markets_allowed:
                continue
        filtered.append(item)
    return filtered


def evaluate_market_snapshot(snapshot: dict[str, Any], portfolio: dict[str, Any], watchlist: dict[str, Any], strategy: dict[str, Any]) -> list[SignalDecision]:
    decisions: list[SignalDecision] = []
    positions = _filter_by_strategy(snapshot.get("positions", []), strategy)
    watch_items = _filter_by_strategy(snapshot.get("watchlist", []), strategy)

    risk_state = _build_portfolio_risk_state(positions)
    _append_portfolio_risk_decisions(decisions, positions, risk_state, strategy)
    _append_position_management_decisions(decisions, positions, snapshot, strategy)
    _append_open_position_decisions(decisions, watch_items, snapshot, strategy, risk_state)

    decisions = _deduplicate_decisions(decisions)
    priority = {"high": 0, "medium": 1, "low": 2}
    return sorted(decisions, key=lambda item: (priority.get(item.urgency, 9), item.action, item.symbol))


SYSTEM_PROMPT = """\
你是一位经验丰富的A股交易策略分析师，专注于3个月左右的中线持仓管理。

你的任务是基于提供的市场数据、新闻资讯和策略参数，分析当前持仓和观察池，给出具体的交易建议。

分析原则：
1. 技术面优先：关注价格与20日均线的关系、近期涨跌幅趋势
2. 消息面验证：用新闻和全球市场表现来验证或推翻技术信号
3. 风控纪律：严格遵守仓位上限、板块集中度、止损止盈规则
4. 宁可错过，不要做错：不确定时选择观望，不产生交易信号

请以严格的JSON格式返回分析结论，不要包含任何其他文字。"""


def _build_llm_prompt(
    market_snapshot: dict[str, Any],
    news_context: dict[str, Any],
    portfolio: dict[str, Any],
    watchlist: dict[str, Any],
    strategy: dict[str, Any],
) -> str:
    def _v(val: Any) -> str:
        if val is None:
            return "无"
        return str(val)

    positions = market_snapshot.get("positions", [])
    position_lines = []
    for pos in positions:
        thesis = pos.get("thesis", "")
        thesis_str = f", 持仓逻辑={thesis}" if thesis else ""
        vol_info = f", 量比(20日均)={pos.get('volume_ratio')}" if pos.get("volume_ratio") is not None else ""
        position_lines.append(
            f"- {pos.get('code')} {pos.get('name')}: "
            f"最新价={_v(pos.get('latest'))}, 当日涨跌={_v(pos.get('change_pct'))}%, "
            f"距MA20={_v(pos.get('price_vs_ma20_pct'))}%, 近20日涨幅={_v(pos.get('monthly_change_pct'))}%{vol_info}, "
            f"行业={_v(pos.get('sector', ''))}, 板块={_v(pos.get('board'))}{thesis_str}"
        )

    watch_items = market_snapshot.get("watchlist", [])
    watch_lines = []
    for item in watch_items:
        vol_info = f", 量比(20日均)={item.get('volume_ratio')}" if item.get("volume_ratio") is not None else ""
        watch_lines.append(
            f"- {item.get('code')} {item.get('name')}: "
            f"最新价={_v(item.get('latest'))}, 当日涨跌={_v(item.get('change_pct'))}%{vol_info}, "
            f"距MA20={_v(item.get('price_vs_ma20_pct'))}%, 近20日涨幅={_v(item.get('monthly_change_pct'))}%, "
            f"行业={_v(item.get('sector', ''))}, 板块={_v(item.get('board'))}, 关注理由={_v(item.get('reason', ''))}"
        )

    indexes = market_snapshot.get("indexes", {})
    index_lines = [f"- {name}: 涨跌={data.get('change_pct')}%" for name, data in indexes.items()]

    global_markets = news_context.get("global_markets", {})
    global_lines = []
    for name, data in global_markets.items():
        if isinstance(data, dict):
            global_lines.append(f"- {name}: {data.get('price')}（{data.get('change_pct')}%）")

    market_news = news_context.get("market_news", [])
    news_lines = [f"- {item.get('title', '')}" for item in market_news[:15]]

    stock_news = news_context.get("stock_news", {})
    stock_news_lines = []
    for symbol, items in stock_news.items():
        if titles := "；".join(item.get("title", "") for item in items[:3]):
            stock_news_lines.append(f"- {symbol}: {titles}")

    north_flow = news_context.get("north_flow", {})
    north_flow_str = north_flow.get("summary", "") if north_flow else ""

    dragon_tiger = news_context.get("dragon_tiger", [])
    dragon_tiger_lines = []
    for item in dragon_tiger:
        buy = item.get("buy_amount", 0)
        sell = item.get("sell_amount", 0)
        net = buy - sell
        tag = "净买入" if net > 0 else "净卖出" if net < 0 else "均衡"
        dragon_tiger_lines.append(
            f"- {item.get('code', '')} {item.get('name', '')}: {tag} {abs(net):.0f}万（买{buy:.0f}/卖{sell:.0f}）"
        )

    risk_rules = strategy.get("risk_rules", {})
    portfolio_rules = strategy.get("portfolio_rules", {})
    opportunity_rules = strategy.get("opportunity_rules", {})

    prompt = f"""## 当前市场状态
{market_snapshot.get('market_state', 'unknown')}

### 指数表现
{chr(10).join(index_lines) if index_lines else '无数据'}

### 持仓标的技术数据
{chr(10).join(position_lines) if position_lines else '空仓'}

### 观察池标的技术数据
{chr(10).join(watch_lines) if watch_lines else '无观察标的'}

## 隔夜全球市场
{chr(10).join(global_lines) if global_lines else '无数据'}

## 最新市场新闻
{chr(10).join(news_lines) if news_lines else '无数据'}

## 个股相关新闻
{chr(10).join(stock_news_lines) if stock_news_lines else '无数据'}

## 北向资金流向
{north_flow_str if north_flow_str else '无数据'}

## 龙虎榜匹配
{chr(10).join(dragon_tiger_lines) if dragon_tiger_lines else '无匹配'}

## 策略参数与风控规则
- 持仓风格: {strategy.get('style', '')}
- 止损线: 持仓跌破{risk_rules.get('position_breakdown_pct', -8)}%
- 止盈线: 近20日涨幅{risk_rules.get('trailing_take_profit_pct', 12)}%（分批），{risk_rules.get('hard_take_profit_pct', 18)}%（硬止盈）
- 建仓触发: 当日涨幅≥{opportunity_rules.get('watchlist_breakout_pct', 2.5)}%且站上MA20
- 总仓位上限: {portfolio_rules.get('max_total_weight', 0.8):.0%}
- 单票上限: {portfolio_rules.get('max_single_position_weight', 0.2):.0%}
- 最多持仓数: {portfolio_rules.get('max_position_count', 6)}
- 科技方向上限: {portfolio_rules.get('max_tech_weight', 0.55):.0%}
- 单板块上限: {portfolio_rules.get('max_sector_weight', 0.35):.0%}

## 当前持仓概况
- 组合名称: {portfolio.get('portfolio_name', '')}
- 现金: {portfolio.get('cash', 0)}
- 持仓数: {len(positions)}

请以JSON格式返回你的分析结论，格式如下：
{{
  "market_assessment": "整体市场环境评估（1-2句话）",
  "decisions": [
    {{
      "action": "buy|sell|add|reduce|trim|take_profit|rebalance",
      "symbol": "股票代码如688981.SH",
      "summary": "一句话执行摘要",
      "reason": "详细分析理由，包含技术面和消息面依据",
      "urgency": "high|medium|low",
      "suggested_weight_change": 0.05,
      "price_zone": "建议价格区间",
      "execution_plan": "分批执行步骤",
      "invalidation_rule": "放弃执行的条件",
      "risk_note": "需特别注意的风险"
    }}
  ]
}}

注意：如果没有需要调整的，decisions数组为空。每个标的最多一个决策。组合层面调整用symbol="PORTFOLIO"表示。"""
    return prompt


def _safe_num(value: Any, default: float = 0.0) -> float:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return default


def _parse_llm_decisions(content: str, portfolio: dict[str, Any], strategy: dict[str, Any]) -> list[SignalDecision]:
    json_text = content.strip()

    code_block = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
    if code_block:
        json_text = code_block.group(1).strip()

    if not json_text.startswith('{'):
        match = re.search(r'\{.*?\}', content, re.DOTALL)
        if match:
            json_text = match.group(0)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []

    llm_decisions = data.get("decisions", [])
    if not isinstance(llm_decisions, list):
        return []

    positions = portfolio.get("positions", [])
    pos_map: dict[str, dict[str, Any]] = {p.get("code", ""): p for p in positions}

    def _find_pos_data(sym: str) -> dict[str, Any]:
        if sym in pos_map:
            return pos_map[sym]
        code_only = sym.split(".", 1)[0] if "." in sym else sym
        for k, v in pos_map.items():
            if k.startswith(code_only + "."):
                return v
        return {}

    decisions: list[SignalDecision] = []
    for item in llm_decisions:
        if not isinstance(item, dict):
            continue
        action = item.get("action", "")
        if action in ("hold", ""):
            continue

        symbol = str(item.get("symbol", "UNKNOWN"))
        pos_data = _find_pos_data(symbol)
        current_weight = _safe_num(
            pos_data.get("current_weight") or pos_data.get("weight") or 0
        )
        suggested_change = _safe_num(item.get("suggested_weight_change", 0.0))
        target_weight = _safe_num(
            item.get("target_weight", current_weight + suggested_change)
        )
        if target_weight == 0.0 and suggested_change != 0.0:
            target_weight = current_weight + suggested_change
        sector = str(item.get("sector") or pos_data.get("sector") or "未分类")

        decisions.append(_make_decision(
            action=action,
            symbol=symbol,
            summary=str(item.get("summary", f"{action} {symbol}")),
            reason=str(item.get("reason", "")),
            urgency=str(item.get("urgency", "medium")),
            suggested_weight_change=suggested_change,
            price_zone=str(item.get("price_zone", "待确认")),
            current_weight=current_weight,
            target_weight=target_weight,
            sector=sector,
            execution_plan=str(item.get("execution_plan", "分批执行")),
            invalidation_rule=str(item.get("invalidation_rule", "若后续行情与趋势条件失效，则放弃执行。")),
            risk_note=str(item.get("risk_note", "注意控制单票和组合总仓位。")),
        ))

    return decisions


def analyze_with_llm(
    market_snapshot: dict[str, Any],
    news_context: dict[str, Any],
    portfolio: dict[str, Any],
    watchlist: dict[str, Any],
    strategy: dict[str, Any],
) -> list[SignalDecision]:
    """Run LLM analysis on pre-market or intraday context. Returns trading decisions."""
    llm_config = strategy.get("llm_analysis", {})
    if not llm_config.get("enabled", True):
        return []

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("[LLM] openai not installed, skipping LLM analysis.")
        return []

    client = OpenAI(
        base_url=os.getenv("MODEL_BASE_URL"),
        api_key=os.getenv("MODEL_API_KEY"),
    )
    model = os.getenv("MODEL_NAME", "gpt-4")
    prompt = _build_llm_prompt(market_snapshot, news_context, portfolio, watchlist, strategy)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=float(llm_config.get("temperature", 0.3)),
            max_tokens=int(llm_config.get("max_tokens", 4000)),
            timeout=90,
        )
        content = response.choices[0].message.content
        if not content:
            return []
        return _parse_llm_decisions(content, portfolio, strategy)
    except Exception as exc:
        logger.error("[LLM] API call failed: %s", exc)
        return []
