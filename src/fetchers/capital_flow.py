from __future__ import annotations

import math
from typing import Any

import akshare as ak


def _safe_float(val: Any) -> float:
    try:
        result = float(val)
        if math.isnan(result) or math.isinf(result):
            return 0.0
        return round(result, 2)
    except (TypeError, ValueError):
        return 0.0


def fetch_north_flow() -> dict[str, Any]:
    result: dict[str, Any] = {"沪股通": None, "深股通": None, "合计净买入": 0.0, "notes": []}

    for connect in ("沪股通", "深股通"):
        try:
            df = ak.stock_hsgt_hist_em(symbol=connect)
            if df is None or df.empty:
                result["notes"].append(f"{connect}: 无数据")
                continue
            cols = list(df.columns)
            date_col = cols[0]
            net_col = cols[1] if len(cols) > 1 else None
            buy_col = cols[2] if len(cols) > 2 else None
            sell_col = cols[3] if len(cols) > 3 else None
            last = df.iloc[-1]
            result[connect] = {
                "date": str(last[date_col]) if date_col and date_col in df.columns else "",
                "net_buy": _safe_float(last[net_col]) if net_col and net_col in df.columns else 0.0,
                "buy_amount": _safe_float(last[buy_col]) if buy_col and buy_col in df.columns else 0.0,
                "sell_amount": _safe_float(last[sell_col]) if sell_col and sell_col in df.columns else 0.0,
            }
            if result[connect]:
                result["合计净买入"] += result[connect]["net_buy"]
        except Exception as exc:
            result["notes"].append(f"{connect}: {exc}")

    return result


def fetch_north_flow_context() -> dict[str, Any]:
    flow = fetch_north_flow()

    trend = "均衡"
    total = flow.get("合计净买入", 0.0)
    if total > 500000:
        trend = "大幅净流入"
    elif total > 100000:
        trend = "净流入"
    elif total < -500000:
        trend = "大幅净流出"
    elif total < -100000:
        trend = "净流出"

    return {
        **flow,
        "trend": trend,
        "summary": f"北向资金今日{trend}（合计{total:.0f}万），沪股通{flow.get('沪股通', {}).get('net_buy', 0):.0f}万，深股通{flow.get('深股通', {}).get('net_buy', 0):.0f}万",
    }
