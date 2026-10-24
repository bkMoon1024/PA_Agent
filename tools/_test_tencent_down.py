# -*- coding: utf-8 -*-
import sys, io, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pa_agent.data.ashare_fallback as fb
import pa_agent.data.eastmoney_client as c

# ---- 让东财全挂 ----
def boom(*a, **k):
    raise c.EastMoneyTransientError("SIMULATED EASTMONEY DOWN")
c._get_json_on_hosts = boom

# ---- 让腾讯全挂 ----
def t_down(*a, **k):
    return None
fb._tencent_spot_raw = t_down
fb.tencent_daily = lambda *a, **k: []
fb.tencent_minute = lambda *a, **k: []

print("############ 场景1: 东财挂 + 腾讯挂 -> 应降到 zzshare ############")

print("\n=== spot ===")
print(c.fetch_stock_quote("600519"))

print("\n=== order book ===")
ob = c.fetch_stock_order_book("600519")
if ob is None:
    print("[NULL] 没有兜底")
else:
    print("type=%s price=%s depth=%s src=%s" % (type(ob).__name__, ob.price, ob.depth_levels, ob.depth_source))
    print("bids:", ob.bids[:2])
    print("asks:", ob.asks[:2])

print("\n=== daily ===")
rows = c.fetch_stock_daily("600519", start_date="2026-09-01", end_date="2026-10-24", adjust="qfq")
print("rows=%d last=%s" % (len(rows), rows[-1] if rows else None))

print("\n=== minute 5 ===")
rows = c.fetch_stock_minute("600519", period="5", start_date="2026-10-23 09:30:00", end_date="2026-10-23 15:00:00")
print("rows=%d last=%s" % (len(rows), rows[-1] if rows else None))

print("\n############ 场景2: 东财挂 + 腾讯挂 + zzshare 挂 -> 应降到新浪(仅现价) ############")

# ---- 让 zzshare 也挂 ----
fb._zzshare_api = lambda: None
fb.zzshare_daily = lambda *a, **k: []
fb.zzshare_minute = lambda *a, **k: []

print("\n=== spot (应走新浪) ===")
print(c.fetch_stock_quote("600519"))

print("\n=== order book (腾讯+zzshare 都挂, 应为 None) ===")
print(c.fetch_stock_order_book("600519"))

print("\n=== daily (腾讯+zzshare 都挂, 应为空) ===")
print("rows=%d" % len(c.fetch_stock_daily("600519", start_date="2026-09-01", end_date="2026-10-24", adjust="qfq")))

print("\n=== minute (腾讯+zzshare 都挂, 应为空) ===")
print("rows=%d" % len(c.fetch_stock_minute("600519", period="5", start_date="2026-10-23 09:30:00", end_date="2026-10-23 15:00:00")))
