"""Free A-share quote fallbacks when East Money HTTP endpoints fail.

Chain (all free / no paid key):
    1. Tencent  (qt.gtimg.cn / ifzq.gtimg.cn / web.ifzq.gtimg.cn) - plain HTTP
    2. zzshare  (optional dependency, pip install zzshare)         - hosted API
    3. Sina     (hq.sinajs.cn)                                     - last resort (spot only)

Wired into eastmoney_client at four points; when East Money is unreachable
the free chain transparently takes over:
    fetch_stock_quote       -> fallback_spot_quote
    fetch_stock_order_book  -> fallback_order_book
    fetch_stock_daily       -> fallback_daily
    fetch_stock_minute      -> fallback_minute

Public helpers mirror the East Money client contracts:
    fallback_spot_quote(symbol)          -> dict like fetch_stock_quote()
    fallback_order_book(symbol)          -> StockOrderBook | None
    fallback_daily(...)                  -> list[dict] like fetch_stock_daily()
    fallback_minute(...)                 -> list[dict] like fetch_stock_minute()

All OHLCV rows use ascending time order with volume in lots (shou),
amount in CNY - identical to eastmoney_client._parse_klines.
"""
from __future__ import annotations

import contextlib
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://gu.qq.com/",
}

_TENCENT_RT = "https://qt.gtimg.cn/q="
_TENCENT_DAILY = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_TENCENT_MINUTE = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
_SINA_RT = "https://hq.sinajs.cn/list="


def _tencent_code(symbol: str) -> str | None:
    """6-digit code -> sh600519 / sz000001; indices keep sh/sz prefix."""
    raw = (symbol or "").strip().lower()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 6:
        return None
    digits = digits[-6:]
    if raw.startswith(("sh", "sz")):
        return raw[:2] + digits
    if digits.startswith(("5", "6", "9")):
        return "sh" + digits
    if digits in {"000300", "000016", "000905", "000852"}:
        return "sh" + digits
    return "sz" + digits


def _zzshare_code(symbol: str) -> str | None:
    """6-digit -> 600519.SH style ts_code."""
    raw = (symbol or "").strip().lower()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 6:
        return None
    digits = digits[-6:]
    if raw.startswith("sh") or (
        not raw.startswith("sz") and digits.startswith(("5", "6", "9"))
    ):
        return f"{digits}.SH"
    if digits in {"000300", "000016", "000905", "000852"}:
        return f"{digits}.SH"
    return f"{digits}.SZ"


def _http():
    """Shared requests getter (requests is pulled in by akshare)."""
    import requests

    return requests


def _f(text: Any) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# Tencent
# --------------------------------------------------------------------------- #
def _tencent_spot_raw(symbol: str) -> list[str] | None:
    code = _tencent_code(symbol)
    if not code:
        return None
    try:
        requests = _http()
        resp = requests.get(_TENCENT_RT + code, headers=_UA, timeout=6)
        resp.encoding = "gbk"
        if resp.status_code != 200 or "~" not in resp.text:
            return None
        body = resp.text.strip().split(";")[0]
        if '="' not in body:
            return None
        payload = body.split('="', 1)[1].rstrip('"')
        parts = payload.split("~")
        return parts if len(parts) > 40 else None
    except Exception as exc:
        logger.debug("Tencent spot failed for %s: %s", symbol, exc)
        return None


def tencent_spot_quote(symbol: str) -> dict[str, Any] | None:
    parts = _tencent_spot_raw(symbol)
    if not parts:
        return None
    price = _f(parts[3])
    if price <= 0:
        return None
    return {"code": parts[2], "name": parts[1] or parts[2], "price": price}


def tencent_order_book(symbol: str):
    from pa_agent.data.eastmoney_quote import OrderBookLevel, StockOrderBook

    parts = _tencent_spot_raw(symbol)
    if not parts:
        return None
    price = _f(parts[3])
    if price <= 0:
        return None
    bids = [
        OrderBookLevel(price=_f(parts[9 + i * 2]), volume=int(_f(parts[10 + i * 2])))
        for i in range(5)
    ]
    asks = [
        OrderBookLevel(price=_f(parts[19 + i * 2]), volume=int(_f(parts[20 + i * 2])))
        for i in range(5)
    ]
    pre_close = _f(parts[4])
    pct = ((price - pre_close) / pre_close * 100.0) if pre_close else 0.0
    return StockOrderBook(
        code=parts[2],
        name=parts[1] or parts[2],
        price=price,
        pct_chg=pct,
        open=_f(parts[5]),
        high=_f(parts[33]),
        low=_f(parts[34]),
        prev_close=pre_close,
        volume=int(_f(parts[36])),
        amount=_f(parts[37]) * 10000.0,
        bids=bids,
        asks=asks,
        depth_levels=5,
        depth_source="tencent_free",
    )


def _tencent_kline_rows(raw: list[Any]) -> list[dict[str, Any]]:
    """Tencent rows: [time, open, close, high, low, volume, ...]."""
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 6:
            continue
        time_text = str(item[0]).strip()
        try:
            if len(time_text) == 8 and time_text.isdigit():
                bar_time = datetime.strptime(time_text, "%Y%m%d")
            elif len(time_text) == 10:
                bar_time = datetime.strptime(time_text, "%Y-%m-%d")
            elif len(time_text) == 12:
                bar_time = datetime.strptime(time_text, "%Y%m%d%H%M")
            else:
                bar_time = datetime.strptime(time_text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        amount = 0.0
        if len(item) > 7:
            amount = _f(item[7])
        rows.append(
            {
                "time": bar_time,
                "open": _f(item[1]),
                "close": _f(item[2]),
                "high": _f(item[3]),
                "low": _f(item[4]),
                "volume": _f(item[5]),
                "amount": amount,
                "pct_chg": None,
            }
        )
    rows.sort(key=lambda r: r["time"])
    return rows


def tencent_daily(
    symbol: str, *, start_date: str, end_date: str, adjust: str = "qfq"
) -> list[dict[str, Any]]:
    code = _tencent_code(symbol)
    if not code:
        return []
    fq = {"qfq": "qfq", "hfq": "hfq"}.get(adjust, "")
    try:
        requests = _http()
        resp = requests.get(
            _TENCENT_DAILY,
            params={"param": f"{code},day,,,640,{fq}"},
            headers=_UA,
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        node = (resp.json().get("data") or {}).get(code) or {}
        raw = node.get("qfqday") or node.get("hfqday") or node.get("day") or []
        rows = _tencent_kline_rows(raw)
    except Exception as exc:
        logger.debug("Tencent daily failed for %s: %s", symbol, exc)
        return []
    start = datetime.strptime(start_date[:10], "%Y-%m-%d") if len(start_date) >= 10 else None
    end = datetime.strptime(end_date[:10], "%Y-%m-%d") if len(end_date) >= 10 else None
    return [
        r
        for r in rows
        if (start is None or r["time"] >= start) and (end is None or r["time"] <= end)
    ]


def tencent_minute(
    symbol: str, *, period: str, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """period: '1' | '5' | '15' | '30' | '60' (East Money klt style)."""
    code = _tencent_code(symbol)
    if not code:
        return []
    p = f"m{period}"
    try:
        requests = _http()
        resp = requests.get(
            _TENCENT_MINUTE,
            params={"param": f"{code},{p},,320"},
            headers=_UA,
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        node = (resp.json().get("data") or {}).get(code) or {}
        raw = node.get(p) or node.get(f"qfq{p}") or []
        rows = _tencent_kline_rows(raw)
    except Exception as exc:
        logger.debug("Tencent minute failed for %s: %s", symbol, exc)
        return []
    start = datetime.strptime(start_date[:19], "%Y-%m-%d %H:%M:%S") if len(start_date) >= 10 else None
    end = datetime.strptime(end_date[:19], "%Y-%m-%d %H:%M:%S") if len(end_date) >= 10 else None
    return [
        r
        for r in rows
        if (start is None or r["time"] >= start) and (end is None or r["time"] <= end)
    ]


# --------------------------------------------------------------------------- #
# zzshare
# --------------------------------------------------------------------------- #
_zzshare_cooldown_until: float = 0.0
_ZZSHARE_COOLDOWN_S = 60.0


def _zzshare_api():
    """Build a zzshare DataApi with blocking retries disabled.

    zzshare retries 429 internally by sleeping on the Retry-After header
    (up to ~55s per attempt, 3 attempts). That would stall the 2.5s polling
    loop, so we replace the retry helper with a single non-blocking GET and
    trip a 60s circuit breaker on any 429.
    """
    global _zzshare_cooldown_until
    import time

    if time.monotonic() < _zzshare_cooldown_until:
        return None
    try:
        import zzshare

        api = zzshare.pro_api()
    except Exception as exc:
        logger.debug("zzshare unavailable: %s", exc)
        return None

    def _fast_request(url, params=None, max_retries=0):
        global _zzshare_cooldown_until
        import time as _t

        import requests

        res = requests.get(
            url, params=params, headers=api.headers, timeout=api.timeout
        )
        if res.status_code == 429:
            _zzshare_cooldown_until = _t.monotonic() + _ZZSHARE_COOLDOWN_S
            logger.info("zzshare rate limited, cooling down %ss", _ZZSHARE_COOLDOWN_S)
        return res

    with contextlib.suppress(Exception):
        api._request_with_retry = _fast_request
    return api


def zzshare_spot_quote(symbol: str) -> dict[str, Any] | None:
    api = _zzshare_api()
    code = _zzshare_code(symbol)
    if api is None or not code:
        return None
    try:
        df = api.rt_k(ts_code=code)
    except Exception as exc:
        logger.debug("zzshare rt_k failed for %s: %s", symbol, exc)
        return None
    if df is None or getattr(df, "empty", True):
        return None
    row = df.iloc[0]
    price = _f(row.get("close"))
    if price <= 0:
        return None
    return {
        "code": str(row.get("ts_code", code))[:6],
        "name": str(row.get("name") or code),
        "price": price,
    }


def zzshare_order_book(symbol: str):
    from pa_agent.data.eastmoney_quote import OrderBookLevel, StockOrderBook

    api = _zzshare_api()
    code = _zzshare_code(symbol)
    if api is None or not code:
        return None
    try:
        df = api.rt_k(ts_code=code)
    except Exception as exc:
        logger.debug("zzshare rt_k failed for %s: %s", symbol, exc)
        return None
    if df is None or getattr(df, "empty", True):
        return None
    row = df.iloc[0]
    price = _f(row.get("close"))
    if price <= 0:
        return None
    pre_close = _f(row.get("pre_close"))
    pct = ((price - pre_close) / pre_close * 100.0) if pre_close else 0.0
    bids = [
        OrderBookLevel(price=_f(row.get("bid_price1")), volume=int(_f(row.get("bid_volume1"))) // 100)
    ]
    asks = [
        OrderBookLevel(price=_f(row.get("ask_price1")), volume=int(_f(row.get("ask_volume1"))) // 100)
    ]
    return StockOrderBook(
        code=str(row.get("ts_code", code))[:6],
        name=str(row.get("name") or code),
        price=price,
        pct_chg=pct,
        open=_f(row.get("open")),
        high=_f(row.get("high")),
        low=_f(row.get("low")),
        prev_close=pre_close,
        volume=int(_f(row.get("vol"))) // 100,
        amount=_f(row.get("amount")),
        bids=bids,
        asks=asks,
        depth_levels=1,
        depth_source="zzshare_free",
    )


def zzshare_daily(
    symbol: str, *, start_date: str, end_date: str, adjust: str = "qfq"
) -> list[dict[str, Any]]:
    import zzshare

    code = _zzshare_code(symbol)
    if not code:
        return []
    beg = start_date.replace("-", "")[:8] or None
    end = end_date.replace("-", "")[:8] or None
    try:
        df = zzshare.daily(ts_code=code, start_date=beg, end_date=end)
    except Exception as exc:
        logger.debug("zzshare daily failed for %s: %s", symbol, exc)
        return []
    if df is None or getattr(df, "empty", True):
        return []
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            bar_time = datetime.strptime(str(row["trade_date"]), "%Y%m%d")
        except Exception:
            continue
        rows.append(
            {
                "time": bar_time,
                "open": _f(row.get("open")),
                "close": _f(row.get("close")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "volume": _f(row.get("vol")) / 100.0,
                "amount": _f(row.get("amount")),
                "pct_chg": _f(row.get("pct_chg")) if row.get("pct_chg") is not None else None,
            }
        )
    rows.sort(key=lambda r: r["time"])
    return rows


def zzshare_minute(
    symbol: str, *, period: str, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    api = _zzshare_api()
    code = _zzshare_code(symbol)
    if api is None or not code:
        return []
    freq_map = {"1": "1min", "5": "5min", "15": "15min", "30": "30min", "60": "60min"}
    freq = freq_map.get(str(period))
    if not freq:
        return []
    try:
        df = api.stk_mins(ts_code=code, freq=freq)
    except Exception as exc:
        logger.debug("zzshare stk_mins failed for %s: %s", symbol, exc)
        return []
    if df is None or getattr(df, "empty", True):
        return []
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            bar_time = datetime.strptime(str(row["trade_time"])[:12], "%Y%m%d%H%M")
        except Exception:
            continue
        rows.append(
            {
                "time": bar_time,
                "open": _f(row.get("open")),
                "close": _f(row.get("close")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "volume": _f(row.get("vol")) / 100.0,
                "amount": _f(row.get("amount")),
                "pct_chg": None,
            }
        )
    rows.sort(key=lambda r: r["time"])
    start = datetime.strptime(start_date[:19], "%Y-%m-%d %H:%M:%S") if len(start_date) >= 10 else None
    end = datetime.strptime(end_date[:19], "%Y-%m-%d %H:%M:%S") if len(end_date) >= 10 else None
    return [
        r
        for r in rows
        if (start is None or r["time"] >= start) and (end is None or r["time"] <= end)
    ]


# --------------------------------------------------------------------------- #
# Sina (last resort, real-time only)
# --------------------------------------------------------------------------- #
def sina_spot_quote(symbol: str) -> dict[str, Any] | None:
    code = _tencent_code(symbol)
    if not code:
        return None
    try:
        requests = _http()
        resp = requests.get(
            _SINA_RT + code,
            headers={**_UA, "Referer": "https://finance.sina.com.cn/"},
            timeout=6,
        )
        resp.encoding = "gbk"
        if resp.status_code != 200 or '="' not in resp.text:
            return None
        payload = resp.text.split('="', 1)[1].rstrip('";\n')
        parts = payload.split(",")
        if len(parts) < 4:
            return None
        price = _f(parts[3])
        if price <= 0:
            return None
        # Sina: parts[0] is the stock name, not the code
        name = parts[0] or code
        digits = "".join(ch for ch in code if ch.isdigit())[-6:]
        return {"code": digits, "name": name, "price": price}
    except Exception as exc:
        logger.debug("Sina spot failed for %s: %s", symbol, exc)
        return None


# --------------------------------------------------------------------------- #
# Public chained helpers
# --------------------------------------------------------------------------- #
def fallback_spot_quote(symbol: str) -> dict[str, Any] | None:
    for fn in (tencent_spot_quote, zzshare_spot_quote, sina_spot_quote):
        try:
            res = fn(symbol)
        except Exception as exc:
            logger.debug("fallback spot %s failed: %s", fn.__name__, exc)
            continue
        if res:
            logger.info("A-share spot fallback hit: %s", fn.__name__)
            return res
    return None


def fallback_order_book(symbol: str):
    for fn in (tencent_order_book, zzshare_order_book):
        try:
            res = fn(symbol)
        except Exception as exc:
            logger.debug("fallback order book %s failed: %s", fn.__name__, exc)
            continue
        if res is not None:
            logger.info("A-share order book fallback hit: %s", fn.__name__)
            return res
    return None


def fallback_daily(
    symbol: str, *, start_date: str, end_date: str, adjust: str = "qfq"
) -> list[dict[str, Any]]:
    for fn in (tencent_daily, zzshare_daily):
        try:
            rows = fn(symbol, start_date=start_date, end_date=end_date, adjust=adjust)
        except Exception as exc:
            logger.debug("fallback daily %s failed: %s", fn.__name__, exc)
            continue
        if rows:
            logger.info("A-share daily fallback hit: %s (%d rows)", fn.__name__, len(rows))
            return rows
    return []


def fallback_minute(
    symbol: str, *, period: str, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    for fn in (tencent_minute, zzshare_minute):
        try:
            rows = fn(symbol, period=period, start_date=start_date, end_date=end_date)
        except Exception as exc:
            logger.debug("fallback minute %s failed: %s", fn.__name__, exc)
            continue
        if rows:
            logger.info("A-share minute fallback hit: %s (%d rows)", fn.__name__, len(rows))
            return rows
    return []
