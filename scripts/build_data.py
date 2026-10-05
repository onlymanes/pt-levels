#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pt-levels 静态站数据构建：拉取 13 标的日线 -> 调用生产算法 -> 写 data/<TICKER>.json。

保密：本脚本只在私有环境 / GitHub Actions 构建时运行，算法代码永不进入前端仓库产物。
对外 JSON 只含中性字段：ticker/as_of/support/resistance/warning/candles。
"""
import json
import os
import sys
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
    "000001.SS": "上证指数",
    "510300.SS": "沪深300ETF",
    "GC=F": "黄金连续",
    "CL=F": "原油连续",
}


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

WARNING_TEXT = {
    "anomaly": "信号异常，建议回避。",
    "notice": "当前价位处于历史成交稀薄区，结果仅供参考。",
    "none": "",
}


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
    df = fetch_daily(ticker)
    if df is None or len(df) < MIN_ROWS:
        print(f"{ticker}: 数据不足，跳过", flush=True)
        return False
    as_of = df["date"].iloc[-1]
    max_window = vp.fixed_max_window(20)
    win, T, _anchor, _note = vp.select_window(df, max_window=max_window)
    prof = vp.build_profile(win, n_bins=2000, q1=0.20, q2=0.80, body_thresh=0.20)
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
        "warning": warning,
        "warning_text": WARNING_TEXT[warning],
        "candles": candles,
        "disclaimer": DISCLAIMER,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(os.path.join(DATA_DIR, f"{ticker}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"{ticker}: as_of={as_of} T={T} 支撑={payload['support']} "
          f"压力={payload['resistance']} 信号={signal}", flush=True)
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
