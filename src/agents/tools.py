"""Tool implementations for the pre-market analysis agent.

Each tool has a schema (OpenAI function-calling format) and an execute function.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fetchers.capital_flow import fetch_north_flow_context
from fetchers.news_fetcher import (
    fetch_dragon_tiger_matches,
    fetch_global_market,
    fetch_market_news,
    fetch_stock_news,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------

try:
    from duckduckgo_search import DDGS

    def _web_search(query: str, max_results: int = 10) -> list[dict[str, str]]:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(f"{query} 股票 新闻", max_results=max_results))
                return [{"title": r["title"], "body": r["body"][:300], "href": r["href"]} for r in results]
        except Exception:
            return []

except ImportError:
    DDGS = None  # type: ignore[assignment]

    def _web_search(query: str, max_results: int = 10) -> list[dict[str, str]]:  # noqa: F811
        return []

# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_financial_news",
            "description": "搜索最新金融新闻和市场资讯。用于获取政策消息、行业动态、市场热点、个股相关新闻。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如'科技板块 政策 2026'、'央行 降准'、'新能源汽车 补贴'、'沪深300 走势'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回最多条数，默认10",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_news",
            "description": "获取特定个股的相关新闻和公告。用于深入了解持仓或自选股的最新消息面。每次调用只查询一只股票。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，格式如'688981.SH'或纯数字代码如'688981'",
                    },
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_global_market_snapshot",
            "description": "获取隔夜全球市场主要指数表现：纳斯达克、标普500、道琼斯、恒生指数。用于判断外部市场情绪对A股的传导。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_north_flow",
            "description": "获取北向资金（沪股通+深股通）当日净流向、趋势判断和汇总。北向资金是外资情绪的领先指标。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_news",
            "description": "获取当前市场层面的综合新闻列表（非特定个股），涵盖宏观政策、行业热点、市场情绪等。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dragon_tiger_matches",
            "description": "查询今日龙虎榜中是否有持仓股或自选股的身影，以及资金买卖情况。龙虎榜反映主力游资动向。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

# ---------------------------------------------------------------------------
# Tool execution dispatcher
# ---------------------------------------------------------------------------

# Portfolio/watchlist cache for tools that need them.
_tool_state: dict[str, Any] = {}


def set_tool_state(portfolio: dict[str, Any], watchlist: dict[str, Any]) -> None:
    _tool_state["portfolio"] = portfolio
    _tool_state["watchlist"] = watchlist


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool by name and return the result as a JSON string."""
    try:
        if name == "search_financial_news":
            return _handle_search_financial_news(arguments)
        elif name == "get_stock_news":
            return _handle_get_stock_news(arguments)
        elif name == "get_global_market_snapshot":
            return _handle_get_global_market()
        elif name == "get_north_flow":
            return _handle_get_north_flow()
        elif name == "get_market_news":
            return _handle_get_market_news()
        elif name == "get_dragon_tiger_matches":
            return _handle_get_dragon_tiger()
        else:
            return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)
    except Exception as exc:
        logger.warning("Tool %s failed: %s", name, exc)
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _handle_search_financial_news(args: dict[str, Any]) -> str:
    query = str(args.get("query", ""))
    limit = int(args.get("limit", 10))
    if not query:
        return json.dumps({"error": "query is required"}, ensure_ascii=False)

    results = _web_search(query, max_results=limit)
    if not results:
        # Fallback: use akshare market news filter
        try:
            all_news = fetch_market_news(limit=30)
            query_lower = query.lower()
            results = [
                {"title": n.get("title", ""), "body": n.get("summary", ""), "href": ""}
                for n in all_news
                if query_lower in n.get("title", "").lower()
                or query_lower in n.get("summary", "").lower()
            ][:limit] or [
                {"title": n.get("title", ""), "body": n.get("summary", ""), "href": ""}
                for n in all_news[:limit]
            ]
        except Exception:
            pass

    if not results:
        return json.dumps(
            {"query": query, "results": [], "note": "暂无相关新闻，请尝试其他关键词或使用 get_market_news 获取综合新闻"},
            ensure_ascii=False,
        )

    return json.dumps({"query": query, "results": results}, ensure_ascii=False)


def _handle_get_stock_news(args: dict[str, Any]) -> str:
    symbol = str(args.get("symbol", ""))
    if not symbol:
        return json.dumps({"error": "symbol is required"}, ensure_ascii=False)

    news = fetch_stock_news([symbol], limit=8)
    items = news.get(symbol, [])
    if not items:
        return json.dumps(
            {"symbol": symbol, "news": [], "note": "该标的最新新闻较少，可尝试 search_financial_news 搜索相关关键词"},
            ensure_ascii=False,
        )
    return json.dumps({"symbol": symbol, "news": items}, ensure_ascii=False)


def _handle_get_global_market() -> str:
    data = fetch_global_market()
    if not data:
        return json.dumps({"global_markets": {}, "note": "全球市场数据暂不可用"}, ensure_ascii=False)
    return json.dumps({"global_markets": data}, ensure_ascii=False)


def _handle_get_north_flow() -> str:
    data = fetch_north_flow_context()
    return json.dumps(data, ensure_ascii=False)


def _handle_get_market_news() -> str:
    items = fetch_market_news(limit=20)
    return json.dumps({"market_news": items}, ensure_ascii=False)


def _handle_get_dragon_tiger() -> str:
    portfolio = _tool_state.get("portfolio", {})
    watchlist = _tool_state.get("watchlist", {})
    matches = fetch_dragon_tiger_matches(portfolio, watchlist)
    if not matches:
        return json.dumps({"dragon_tiger": [], "note": "今日龙虎榜无持仓/自选股匹配"}, ensure_ascii=False)
    return json.dumps({"dragon_tiger": matches}, ensure_ascii=False)
