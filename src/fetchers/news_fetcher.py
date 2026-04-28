from __future__ import annotations

from datetime import datetime
from typing import Any

import akshare as ak
import requests

GLOBAL_INDICES = {
    "纳斯达克": "100.IXIC",
    "标普500": "100.SPX",
    "道琼斯": "100.DJIA",
    "恒生指数": "100.HSI",
}

EASTMONEY_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"


def _safe_float(value: Any, scale: float = 1.0) -> float:
    try:
        return round(float(value) / scale, 2)
    except Exception:
        return 0.0


def _fetch_global_index(secid: str) -> dict[str, Any] | None:
    try:
        resp = requests.get(
            EASTMONEY_QUOTE_URL,
            params={
                "secid": secid,
                "fields": "f58,f43,f170",
                "fltt": 2,
                "invt": 2,
            },
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json().get("data")
        if not data or data.get("f43") is None:
            return None
        return {
            "name": data.get("f58", ""),
            "price": _safe_float(data.get("f43"), 100),
            "change_pct": _safe_float(data.get("f170"), 100),
            "source": "eastmoney",
        }
    except Exception:
        return None


def fetch_global_market() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, secid in GLOBAL_INDICES.items():
        quote = _fetch_global_index(secid)
        if quote:
            result[name] = quote
    return result


def fetch_market_news(limit: int = 20) -> list[dict[str, Any]]:
    try:
        df = ak.stock_info_global_em()
        if df is None or df.empty:
            return []
        cols = list(df.columns)
        title_col = cols[0] if len(cols) > 0 else None
        summary_col = cols[1] if len(cols) > 1 else None
        time_col = cols[2] if len(cols) > 2 else None
        items: list[dict[str, Any]] = []
        for _, row in df.head(limit).iterrows():
            title = str(row[title_col]) if title_col and title_col in df.columns else ""
            summary = str(row[summary_col]) if summary_col and summary_col in df.columns else ""
            pub_time = str(row[time_col]) if time_col and time_col in df.columns else ""
            items.append({
                "title": title or summary,
                "time": pub_time,
                "summary": summary[:200],
            })
        return items
    except Exception:
        return []


def fetch_stock_news(symbols: list[str], limit: int = 5) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        code = symbol.split(".", 1)[0]
        try:
            df = ak.stock_news_em(symbol=code)
            if df is None or df.empty:
                result[symbol] = []
                continue
            cols = list(df.columns)
            title_col = cols[1] if len(cols) > 1 else cols[0]
            time_col = cols[3] if len(cols) > 3 else cols[0]
            items: list[dict[str, Any]] = []
            for _, row in df.head(limit).iterrows():
                items.append({
                    "title": str(row[title_col]) if title_col in df.columns else "",
                    "time": str(row[time_col]) if time_col in df.columns else "",
                })
            result[symbol] = items
        except Exception:
            result[symbol] = []
    return result


def fetch_pre_market_context(portfolio: dict[str, Any], watchlist: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "global_markets": {},
        "market_news": [],
        "stock_news": {},
        "errors": [],
    }

    try:
        context["global_markets"] = fetch_global_market()
    except Exception as exc:
        context["errors"].append(f"全球市场: {exc}")

    try:
        context["market_news"] = fetch_market_news()
    except Exception as exc:
        context["errors"].append(f"市场新闻: {exc}")

    positions = portfolio.get("positions", [])
    watch_items = watchlist.get("watchlist", [])
    all_symbols = [p.get("code") for p in positions if p.get("code")]
    all_symbols += [w.get("code") for w in watch_items if w.get("code")]
    unique = list(dict.fromkeys(all_symbols))

    try:
        context["stock_news"] = fetch_stock_news(unique)
    except Exception as exc:
        context["errors"].append(f"个股新闻: {exc}")

    return context
