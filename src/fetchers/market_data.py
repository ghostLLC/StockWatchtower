from __future__ import annotations

from pathlib import Path
from time import sleep
from typing import Any, Callable

import akshare as ak
import pandas as pd
import requests

from config_loader import BASE_DIR


INDEX_SYMBOLS = {
    "sh000001": {"name": "上证指数", "secid": "1.000001"},
    "sz399001": {"name": "深证成指", "secid": "0.399001"},
    "sz399006": {"name": "创业板指", "secid": "0.399006"},
}

CACHE_DIR = BASE_DIR / "data" / "cache"
EASTMONEY_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"


def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _normalize_stock_code(ts_code: str) -> str:
    code, exchange = ts_code.split(".", 1)
    exchange = exchange.upper()
    if exchange == "SH":
        return f"sh{code}"
    if exchange == "SZ":
        return f"sz{code}"
    raise ValueError(f"不支持的交易所代码: {ts_code}")


def _to_secid(ts_code: str) -> str:
    code, exchange = ts_code.split(".", 1)
    exchange = exchange.upper()
    if exchange == "SH":
        return f"1.{code}"
    if exchange == "SZ":
        return f"0.{code}"
    raise ValueError(f"不支持的交易所代码: {ts_code}")


def _safe_pct(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return round(float(value), 2)
    except Exception:
        return 0.0


def _safe_price(value: Any, *, scale: float = 1.0) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return round(float(value) / scale, 2)
    except Exception:
        return None


def _retry_call(fn: Callable[..., Any], *args: Any, retries: int = 3, delay: float = 1.2, **kwargs: Any) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                sleep(delay * attempt)
    if last_error:
        raise last_error
    raise RuntimeError("未知调用错误")


def _fetch_stock_realtime_quotes_akshare(symbols: list[str]) -> list[dict[str, Any]]:
    all_quotes = _retry_call(ak.stock_zh_a_spot_em)
    needed_codes = {symbol.split(".", 1)[0] for symbol in symbols}
    rows = all_quotes[all_quotes["代码"].astype(str).isin(needed_codes)].copy()
    quotes: list[dict[str, Any]] = []
    for _, row in rows.iterrows():
        code = str(row.get("代码", "")).strip()
        exchange = "SH" if code.startswith("6") or code.startswith("688") else "SZ"
        quotes.append(
            {
                "code": f"{code}.{exchange}",
                "name": row.get("名称", ""),
                "latest": row.get("最新价"),
                "change_pct": _safe_pct(row.get("涨跌幅")),
                "turnover": row.get("成交额"),
                "amplitude": _safe_pct(row.get("振幅")),
                "high": row.get("最高"),
                "low": row.get("最低"),
                "source": "akshare_primary",
            }
        )
    return quotes


def _fetch_em_quote(secid: str) -> dict[str, Any]:
    response = requests.get(
        EASTMONEY_QUOTE_URL,
        params={
            "secid": secid,
            "fields": "f58,f43,f170,f48,f171,f44,f45",
            "fltt": 2,
            "invt": 2,
        },
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or {}
    if not data:
        raise RuntimeError(f"东方财富返回空数据: {secid}")
    return data


def _fetch_stock_realtime_quotes_fallback(symbols: list[str]) -> list[dict[str, Any]]:
    quotes: list[dict[str, Any]] = []
    for symbol in symbols:
        secid = _to_secid(symbol)
        data = _retry_call(_fetch_em_quote, secid, retries=2, delay=0.8)
        quotes.append(
            {
                "code": symbol,
                "name": data.get("f58", ""),
                "latest": _safe_price(data.get("f43"), scale=100),
                "change_pct": _safe_price(data.get("f170"), scale=100) or 0.0,
                "turnover": data.get("f48"),
                "amplitude": _safe_price(data.get("f171"), scale=100) or 0.0,
                "high": _safe_price(data.get("f44"), scale=100),
                "low": _safe_price(data.get("f45"), scale=100),
                "source": "eastmoney_fallback",
            }
        )
    return quotes


def _fetch_stock_realtime_quotes(symbols: list[str]) -> tuple[list[dict[str, Any]], str]:
    if not symbols:
        return [], "none"
    try:
        return _fetch_stock_realtime_quotes_akshare(symbols), "akshare_primary"
    except Exception:
        return _fetch_stock_realtime_quotes_fallback(symbols), "eastmoney_fallback"


def _fetch_index_snapshot_akshare() -> dict[str, Any]:
    index_quotes = _retry_call(ak.stock_zh_index_spot)
    snapshot: dict[str, Any] = {}
    for symbol, meta in INDEX_SYMBOLS.items():
        matched = index_quotes[index_quotes["代码"] == symbol]
        if matched.empty:
            continue
        row = matched.iloc[0]
        snapshot[meta["name"]] = {
            "symbol": symbol,
            "latest": row.get("最新价"),
            "change_pct": _safe_pct(row.get("涨跌幅")),
            "source": "akshare_primary",
        }
    return snapshot



def _fetch_index_snapshot_fallback() -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for symbol, meta in INDEX_SYMBOLS.items():
        data = _retry_call(_fetch_em_quote, meta["secid"], retries=2, delay=0.8)
        snapshot[meta["name"]] = {
            "symbol": symbol,
            "latest": _safe_price(data.get("f43"), scale=100),
            "change_pct": _safe_price(data.get("f170"), scale=100) or 0.0,
            "source": "eastmoney_fallback",
        }
    return snapshot



def _fetch_index_snapshot() -> tuple[dict[str, Any], str]:
    try:
        return _fetch_index_snapshot_akshare(), "akshare_primary"
    except Exception:
        return _fetch_index_snapshot_fallback(), "eastmoney_fallback"


def _daily_cache_path(symbol: str) -> Path:
    safe_symbol = symbol.replace(".", "_")
    return CACHE_DIR / f"daily_{safe_symbol}.csv"


def _load_cached_daily(symbol: str) -> pd.DataFrame:
    path = _daily_cache_path(symbol)
    if not path.exists():
        raise RuntimeError(f"无可用日线缓存: {symbol}")
    return pd.read_csv(path)


def _fetch_recent_daily(symbol: str, limit: int = 30) -> tuple[pd.DataFrame, str]:
    _ensure_cache_dir()
    normalized = _normalize_stock_code(symbol)
    try:
        daily = _retry_call(
            ak.stock_zh_a_hist,
            symbol=normalized,
            period="daily",
            adjust="qfq",
            retries=3,
            delay=1.0,
        )
        if daily.empty:
            raise RuntimeError(f"日线数据为空: {symbol}")
        daily = daily.tail(limit).copy()
        daily.to_csv(_daily_cache_path(symbol), index=False, encoding="utf-8-sig")
        return daily, "akshare_primary"
    except Exception:
        try:
            cached = _load_cached_daily(symbol)
            return cached.tail(limit).copy(), "daily_cache_fallback"
        except Exception:
            return pd.DataFrame(), "daily_unavailable"



def _build_symbol_snapshot(item: dict[str, Any], realtime_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    symbol = item.get("code", "")
    realtime = realtime_map.get(symbol, {})
    daily, daily_source = _fetch_recent_daily(symbol)

    ma20 = None
    price_vs_ma20_pct = 0.0
    monthly_change_pct = 0.0
    avg_turnover = None
    volume_ratio = None
    if not daily.empty and "收盘" in daily.columns:
        closes = daily["收盘"].astype(float)
        ma20 = round(closes.tail(20).mean(), 2) if len(closes) >= 20 else round(closes.mean(), 2)
        latest_close = float(closes.iloc[-1])
        if ma20:
            price_vs_ma20_pct = round((latest_close - ma20) / ma20 * 100, 2)
        if len(closes) >= 20:
            monthly_change_pct = round((latest_close - float(closes.iloc[-20])) / float(closes.iloc[-20]) * 100, 2)
        if "成交额" in daily.columns:
            amounts = daily["成交额"].astype(float).tail(20)
            if len(amounts) >= 5:
                avg_turnover = round(amounts.mean(), 2)
                today_amount = float(realtime.get("turnover")) if realtime.get("turnover") is not None else None
                if avg_turnover and avg_turnover > 0 and today_amount and today_amount > 0:
                    volume_ratio = round(today_amount / avg_turnover, 2)

    return {
        "code": symbol,
        "name": item.get("name", symbol),
        "board": item.get("board", "unknown"),
        "sector": item.get("sector", ""),
        "reason": item.get("reason", ""),
        "current_weight": item.get("current_weight", 0.0),
        "target_weight": item.get("target_weight", 0.0),
        "max_weight": item.get("max_weight", 0.0),
        "entry_date": item.get("entry_date", ""),
        "thesis": item.get("thesis", ""),
        "latest": realtime.get("latest"),
        "change_pct": realtime.get("change_pct", 0.0),
        "turnover": realtime.get("turnover"),
        "avg_turnover": avg_turnover,
        "volume_ratio": volume_ratio,
        "amplitude": realtime.get("amplitude", 0.0),
        "high": realtime.get("high"),
        "low": realtime.get("low"),
        "ma20": ma20,
        "price_vs_ma20_pct": price_vs_ma20_pct,
        "monthly_change_pct": monthly_change_pct,
        "realtime_source": realtime.get("source", "unknown"),
        "daily_source": daily_source,
    }


def fetch_market_snapshot(portfolio: dict[str, Any], watchlist: dict[str, Any]) -> dict[str, Any]:
    positions = portfolio.get("positions", [])
    watch_items = watchlist.get("watchlist", [])
    tracked = positions + watch_items
    symbols = [item.get("code") for item in tracked if item.get("code")]

    realtime_quotes, realtime_source = _fetch_stock_realtime_quotes(symbols) if symbols else ([], "none")
    realtime_map = {quote["code"]: quote for quote in realtime_quotes}

    watch_snapshots = [_build_symbol_snapshot(item, realtime_map) for item in watch_items]
    position_snapshots = [_build_symbol_snapshot(item, realtime_map) for item in positions]
    index_snapshot, index_source = _fetch_index_snapshot()

    market_state = "neutral"
    sh_change = index_snapshot.get("上证指数", {}).get("change_pct", 0.0)
    cyb_change = index_snapshot.get("创业板指", {}).get("change_pct", 0.0)

    if sh_change <= -1.5 or cyb_change <= -2.0:
        market_state = "risk_off"
    elif sh_change >= 0.8 or cyb_change >= 1.2:
        market_state = "risk_on"

    return {
        "market_state": market_state,
        "tracked_count": len(tracked),
        "positions": position_snapshots,
        "watchlist": watch_snapshots,
        "indexes": index_snapshot,
        "data_sources": {
            "realtime": realtime_source,
            "indexes": index_source,
        },
    }
