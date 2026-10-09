#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pt-levels 静态站数据构建：拉取 13 标的日线 -> 调用生产算法 -> 写 data/<TICKER>.json。

保密：本脚本只在私有环境 / GitHub Actions 构建时运行，算法代码永不进入前端仓库产物。
对外 JSON 只含中性字段：ticker/as_of/support/resistance/warning/candles。
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(ROOT, "data")

# 生产算法：单一来源，直接复用现有实现（不复制、不改写）
# 本地运行时默认 ~/workspace/covered_call；Actions 构建时由 PROD_DIR 传入私有模块位置
_PROD_DIR = os.environ.get("PROD_DIR") or os.path.join(
    os.path.expanduser("~"), "workspace", "covered_call")
sys.path.insert(0, _PROD_DIR)
import volume_profile as vp  # noqa: E402

TICKERS = ["QQQ", "SPY", "CEG", "VICI", "CCJ", "VST", "BE",
           "TSLA", "GOOGL", "URA", "TLT", "PFE", "NVDA"]

# 非美股标的的中文显示名（前端用）
NAMES = {
    "000016": "上证50指数",
    "000300": "沪深300指数",
    "GC=F": "黄金连续",
    "CL=F": "原油连续",
}

# 东方财富数据源（Yahoo 缺历史数据的 A 股指数）：ticker -> eastmoney secid
EASTMONEY_SOURCE = {"000300": "1.000300", "000016": "1.000016"}

# Yahoo 代码别名（前端显示用短代码，实际取数用完整 Yahoo 代码）：ticker -> yahoo 代码
YAHOO_ALIAS = {"BTC": "BTC-USD"}


def load_tickers():
    """覆盖名单：优先读仓库根目录 tickers.txt（一行一个），没有则用内置 13 只。"""
    path = os.path.join(ROOT, "tickers.txt")
    if os.path.exists(path):
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                t = line.strip().upper()
                if t and not t.startswith("#") and t not in out:
                    out.append(t)
        if out:
            return out
    return TICKERS
DISCLAIMER = "仅供研究参考，不构成投资建议。"
MIN_ROWS = 60
CANDLE_DAYS = 180
MIN_DAYS_SHORT = 20   # 短期窗口下限（原有口径）
MIN_DAYS_MID = 55     # 中期窗口下限

WARNING_TEXT = {
    "anomaly": "信号异常，建议回避。",
    "notice": "当前价位处于历史成交稀薄区，结果仅供参考。",
    "none": "",
}


def fetch_eastmoney_daily(secid, years=2):
    """东方财富日 K，返回与 fetch_daily 相同列的 DataFrame（升序）。
    行格式：日期,开盘,收盘,最高,最低,成交量,成交额,..."""
    from datetime import date, timedelta
    today = date.today()
    beg = (today - timedelta(days=years * 366)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={secid}&klt=101&fqt=0&beg={beg}&end={end}"
           f"&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56,f57")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    klines = None
    for attempt in range(3):
        try:
            raw = urllib.request.urlopen(req, timeout=30).read().decode()
            klines = json.loads(raw)["data"]["klines"]
            break
        except Exception:
            if attempt == 2:
                return None
            time.sleep(5)
    rows = []
    for k in klines or []:
        p = k.split(",")
        if len(p) < 6:
            continue
        try:
            rows.append({"date": p[0], "open": float(p[1]), "close": float(p[2]),
                         "high": float(p[3]), "low": float(p[4]),
                         "volume": float(p[5])})
        except ValueError:
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "volume"]]


def fetch_daily(ticker):
    """yfinance 取 2 年日线，统一为 date/open/high/low/close/volume 列（升序）。"""
    df = yf.download(ticker, period="2y", interval="1d",
                     auto_adjust=False, progress=False)
    if df is None or len(df) == 0:
        return None
    df.columns = df.columns.get_level_values(0)
    out = df[["Open", "High", "Low", "Close", "Volume"]].rename(columns=str.lower)
    out = out.dropna()
    out = out.reset_index().rename(columns={"Date": "date", "index": "date"})
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out = out.sort_values("date").reset_index(drop=True)
    return out[["date", "open", "high", "low", "close", "volume"]]


def latest_signal(df):
    """最新一根 K 线的三级信号（rebalance/attention/none）。"""
    n = len(df)
    k = 10
    positions = list(range(n - k - 1, n))
    cpt1, cpt2 = [], []
    for pos in positions:
        p1, p2, _ = vp.rolling_pt(df, pos, n_bins=200, q1=0.20, q2=0.80,
                                  body_thresh=0.20)
        cpt1.append(p1)
        cpt2.append(p2)
    regimes = vp.rolling_regime(df, positions, cpt1, cpt2, slope_k=k)
    return regimes[-1]["signal"]


def build_ticker(ticker):
    if ticker in EASTMONEY_SOURCE:
        df = fetch_eastmoney_daily(EASTMONEY_SOURCE[ticker])
    else:
        df = fetch_daily(YAHOO_ALIAS.get(ticker, ticker))
    if df is None or len(df) < MIN_ROWS:
        print(f"{ticker}: 数据不足，跳过", flush=True)
        return False
    as_of = df["date"].iloc[-1]
    # 短期（原有口径）：minT=20
    max_window = vp.fixed_max_window(MIN_DAYS_SHORT)
    win, T, _anchor, _note = vp.select_window(df, min_days=MIN_DAYS_SHORT,
                                              max_window=max_window)
    prof = vp.build_profile(win, n_bins=2000, q1=0.20, q2=0.80, body_thresh=0.20)
    # 中期：minT=55
    max_window_mid = vp.fixed_max_window(MIN_DAYS_MID)
    win_mid, T_mid, _a2, _n2 = vp.select_window(df, min_days=MIN_DAYS_MID,
                                                max_window=max_window_mid)
    prof_mid = vp.build_profile(win_mid, n_bins=2000, q1=0.20, q2=0.80,
                                body_thresh=0.20)
    try:
        signal = latest_signal(df)
    except Exception as e:  # noqa: BLE001 - 信号算不出时按异常处理，不输出价位
        print(f"{ticker}: 信号计算失败 ({e})，跳过", flush=True)
        return False
    warning = {"rebalance": "anomaly", "attention": "notice"}.get(signal, "none")
    candles = [
        {"time": r["date"], "open": round(float(r["open"]), 2),
         "high": round(float(r["high"]), 2), "low": round(float(r["low"]), 2),
         "close": round(float(r["close"]), 2)}
        for _, r in df.tail(CANDLE_DAYS).iterrows()
    ]
    payload = {
        "ticker": ticker,
        "as_of": as_of,
        "support": round(float(prof["PT1"]), 2),
        "resistance": round(float(prof["PT2"]), 2),
        "mid_support": round(float(prof_mid["PT1"]), 2),
        "mid_resistance": round(float(prof_mid["PT2"]), 2),
        "warning": warning,
        "warning_text": WARNING_TEXT[warning],
        "candles": candles,
        "disclaimer": DISCLAIMER,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(os.path.join(DATA_DIR, f"{ticker}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"{ticker}: as_of={as_of} T={T} 支撑={payload['support']} "
          f"压力={payload['resistance']} T_mid={T_mid} "
          f"中期支撑={payload['mid_support']} 中期压力={payload['mid_resistance']} "
          f"信号={signal}", flush=True)
    return True


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    tickers = load_tickers()
    print(f"覆盖标的 {len(tickers)} 只", flush=True)
    ok = sum(build_ticker(t) for t in tickers)
    # 名单本身也发布，供前端做输入校验与自动补全（保持 tickers.txt 的顺序）
    done = [t for t in tickers
            if os.path.exists(os.path.join(DATA_DIR, f"{t}.json"))]
    with open(os.path.join(DATA_DIR, "tickers.json"), "w", encoding="utf-8") as f:
        json.dump({"tickers": done, "count": len(done),
                   "names": {t: NAMES[t] for t in done if t in NAMES}},
                  f, ensure_ascii=False)
    print(f"完成 {ok}/{len(tickers)}", flush=True)
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
