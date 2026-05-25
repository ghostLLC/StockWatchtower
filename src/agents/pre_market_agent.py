"""Pre-market agent that uses tool-calling to autonomously research before deciding.

Replaces the old "pre-fetch everything → dump into prompt" pattern with an
agent loop: the LLM decides what to search for, executes tools, iterates,
and produces final trading decisions.

Fallback: if the model doesn't support tool calling or the agent loop fails,
falls back to the legacy analyze_with_llm() path.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from analyzer import _parse_llm_decisions, SignalDecision
from analyzer import analyze_with_llm as legacy_analyze_with_llm

from .tools import TOOL_SCHEMAS, execute_tool, set_tool_state

logger = logging.getLogger(__name__)

MAX_ROUNDS = 8

AGENT_SYSTEM_PROMPT = """\
你是一位经验丰富的A股交易策略分析师，专注于3个月左右的中线持仓管理。

你的任务是基于市场快照数据，**主动使用搜索工具**收集信息，然后给出具体的交易建议。

## 可用工具
- `search_financial_news(query)` — 搜索最新金融新闻，获取政策、行业、个股消息
- `get_stock_news(symbol)` — 查询特定个股的新闻公告
- `get_global_market_snapshot()` — 获取隔夜全球市场表现
- `get_north_flow()` — 获取北向资金流向
- `get_market_news()` — 获取市场综合新闻
- `get_dragon_tiger_matches()` — 查询龙虎榜匹配

## 分析流程
1. 先理解市场快照中的关键信号——哪些标的异常波动？哪些板块集中度高？
2. **主动搜索**：针对异常波动标的搜个股新闻，针对重仓板块搜索行业政策，针对市场整体搜索宏观消息
3. 用搜索到的消息面信息验证或推翻技术信号
4. 不确定时选择观望，不产生交易信号

## 分析原则
1. 技术面优先：关注价格与20日均线的关系、近期涨跌幅趋势
2. 消息面验证：**必须主动搜索**新闻来验证或推翻技术信号，不要凭空判断
3. 风控纪律：严格遵守仓位上限、板块集中度、止损止盈规则
4. 宁可错过，不要做错：不确定时选择观望

当信息收集充分后，以严格的JSON格式返回分析结论，decisions数组为空表示无需交易。"""


def _build_agent_prompt(
    snapshot: dict[str, Any],
    portfolio: dict[str, Any],
    watchlist: dict[str, Any],
    strategy: dict[str, Any],
) -> str:
    """Build the initial user message with market snapshot context."""

    def _v(val: Any) -> str:
        if val is None:
            return "无"
        return str(val)

    positions = snapshot.get("positions", [])
    position_lines = []
    for pos in positions:
        thesis = pos.get("thesis", "")
        thesis_str = f", 持仓逻辑={thesis}" if thesis else ""
        vol_info = f", 量比={pos.get('volume_ratio')}" if pos.get("volume_ratio") is not None else ""
        position_lines.append(
            f"- {pos.get('code')} {pos.get('name')}: "
            f"现价={_v(pos.get('latest'))}, 涨跌={_v(pos.get('change_pct'))}%{vol_info}, "
            f"距MA20={_v(pos.get('price_vs_ma20_pct'))}%, 近20日涨幅={_v(pos.get('monthly_change_pct'))}%, "
            f"行业={_v(pos.get('sector', ''))}, 板块={_v(pos.get('board'))}{thesis_str}"
        )

    watch_items = snapshot.get("watchlist", [])
    watch_lines = []
    for item in watch_items:
        vol_info = f", 量比={item.get('volume_ratio')}" if item.get("volume_ratio") is not None else ""
        watch_lines.append(
            f"- {item.get('code')} {item.get('name')}: "
            f"现价={_v(item.get('latest'))}, 涨跌={_v(item.get('change_pct'))}%{vol_info}, "
            f"距MA20={_v(item.get('price_vs_ma20_pct'))}%, 近20日涨幅={_v(item.get('monthly_change_pct'))}%, "
            f"行业={_v(item.get('sector', ''))}, 板块={_v(item.get('board'))}, 关注理由={_v(item.get('reason', ''))}"
        )

    indexes = snapshot.get("indexes", {})
    index_lines = [f"- {name}: 涨跌={data.get('change_pct')}%" for name, data in indexes.items()]

    risk_rules = strategy.get("risk_rules", {})
    portfolio_rules = strategy.get("portfolio_rules", {})
    opportunity_rules = strategy.get("opportunity_rules", {})

    prompt = f"""## 市场状态
{snapshot.get('market_state', 'unknown')}

### 指数表现
{chr(10).join(index_lines) if index_lines else '无数据'}

### 持仓标的技术数据
{chr(10).join(position_lines) if position_lines else '空仓'}

### 观察池标的技术数据
{chr(10).join(watch_lines) if watch_lines else '无观察标的'}

## 当前持仓概况
- 组合名称: {portfolio.get('portfolio_name', '')}
- 现金: {portfolio.get('cash', 0)}
- 持仓数: {len(positions)}

## 策略参数
- 风格: {strategy.get('style', '')}
- 止损线: {risk_rules.get('position_breakdown_pct', -8)}%
- 止盈线: 近20日涨幅{risk_rules.get('trailing_take_profit_pct', 12)}%
- 硬止盈: 近20日涨幅{risk_rules.get('hard_take_profit_pct', 18)}%
- 建仓触发: 当日涨幅≥{opportunity_rules.get('watchlist_breakout_pct', 2.5)}%且站上MA20
- 总仓位上限: {portfolio_rules.get('max_total_weight', 0.8):.0%}
- 单票上限: {portfolio_rules.get('max_single_position_weight', 0.2):.0%}
- 最多持仓数: {portfolio_rules.get('max_position_count', 6)}
- 科技方向上限: {portfolio_rules.get('max_tech_weight', 0.55):.0%}
- 单板块上限: {portfolio_rules.get('max_sector_weight', 0.35):.0%}

---
请使用工具主动搜索以下信息（按需，不必全部）：
1. 隔夜全球市场表现以判断外部环境
2. 北向资金流向以判断外资情绪
3. 针对涨跌异常的持仓/自选股，搜索个股新闻
4. 针对重仓板块，搜索行业政策与动态
5. 搜索市场综合新闻以把握宏观情绪

收集完信息后，给出最终分析。如果没有需要调整的，decisions数组留空。"""

    return prompt


def _run_agent_loop(
    messages: list[dict[str, Any]],
    strategy: dict[str, Any],
    portfolio: dict[str, Any],
) -> list[SignalDecision]:
    """Run the agent loop: send messages with tools, execute tool calls, iterate."""
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("[Agent] openai not installed.")
        return []

    client = OpenAI(
        base_url=os.getenv("MODEL_BASE_URL"),
        api_key=os.getenv("MODEL_API_KEY"),
    )
    model = os.getenv("MODEL_NAME", "gpt-4")
    llm_config = strategy.get("llm_analysis", {})
    temperature = float(llm_config.get("temperature", 0.3))
    max_tokens = int(llm_config.get("max_tokens", 4000))
    agent_max_rounds = int(strategy.get("agent", {}).get("max_rounds", MAX_ROUNDS))

    # First call: check if the model supports tool calling
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=90,
        )
    except Exception as exc:
        logger.warning("[Agent] Initial call failed (model may not support tools): %s", exc)
        return []

    msg = response.choices[0].message

    for round_num in range(1, agent_max_rounds + 1):
        if not msg.tool_calls:
            content = msg.content or ""
            return _parse_llm_decisions(content, portfolio, strategy)

        logger.info("[Agent] Round %d: %d tool call(s)", round_num, len(msg.tool_calls))

        # Add assistant message
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if msg.content:
            assistant_msg["content"] = msg.content
        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
        messages.append(assistant_msg)

        # Execute each tool call
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except (json.JSONDecodeError, TypeError):
                args = {}
            result = execute_tool(tc.function.name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

        # Continue the loop
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=90,
            )
            msg = response.choices[0].message
        except Exception as exc:
            logger.warning("[Agent] Round %d call failed: %s", round_num, exc)
            break

    # Ran out of rounds — try to parse whatever we got
    if msg.content:
        return _parse_llm_decisions(msg.content, portfolio, strategy)
    return []


def run_pre_market_agent(
    snapshot: dict[str, Any],
    portfolio: dict[str, Any],
    watchlist: dict[str, Any],
    strategy: dict[str, Any],
) -> list[SignalDecision]:
    """Run pre-market analysis with autonomous agent search.

    The agent has tools to search financial news, look up stock-specific
    announcements, check global markets, north flow, and dragon-tiger board.
    It decides what to research based on the snapshot context.

    Falls back to legacy analyze_with_llm() if the model doesn't support
    function calling or if the agent loop encounters a fatal error.
    """
    llm_config = strategy.get("llm_analysis", {})
    if not llm_config.get("enabled", True):
        return []

    set_tool_state(portfolio, watchlist)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": _build_agent_prompt(snapshot, portfolio, watchlist, strategy)},
    ]

    try:
        decisions = _run_agent_loop(messages, strategy, portfolio)
        if decisions:
            logger.info("[Agent] Produced %d decision(s) via agent loop.", len(decisions))
            return decisions
    except Exception as exc:
        logger.warning("[Agent] Loop failed, falling back to legacy: %s", exc)

    # --- Fallback: legacy pre-fetch + single LLM call ---
    use_fallback = strategy.get("agent", {}).get("fallback_to_prefetch", True)
    if not use_fallback:
        return []

    logger.info("[Agent] Falling back to legacy pre-fetch LLM analysis.")
    from fetchers.news_fetcher import fetch_pre_market_context
    from fetchers.capital_flow import fetch_north_flow_context

    news_context = fetch_pre_market_context(portfolio, watchlist)
    if strategy.get("monitoring", {}).get("north_flow_enabled", True):
        news_context["north_flow"] = fetch_north_flow_context()
    if strategy.get("monitoring", {}).get("dragon_tiger_enabled", True):
        from fetchers.news_fetcher import fetch_dragon_tiger_matches
        news_context["dragon_tiger"] = fetch_dragon_tiger_matches(portfolio, watchlist)

    return legacy_analyze_with_llm(snapshot, news_context, portfolio, watchlist, strategy)
