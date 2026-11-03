#!/usr/bin/env python3
"""Download adjusted daily OHLCV from Yahoo for every symbol in the pool (+ benchmark) into <repo>/data/.
Runs on GitHub Actions, which has normal internet access.
Symbols use Yahoo format: AAPL, BRK-B, 0700.HK, 600519.SS, 000001.SZ"""
import argparse, os, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import yfinance as yf

ap = argparse.ArgumentParser()
ap.add_argument("--pool", default=os.path.join(REPO, "strategies", "friestrader", "pool.txt"))
ap.add_argument("--benchmark", default="SPY")
ap.add_argument("--start", default="2019-06-01")  # ~1.5y warm-up before a 2021 backtest start
ap.add_argument("--out", default=os.path.join(REPO, "data"))
a = ap.parse_args()

syms = [l.split("#")[0].strip().upper() for l in open(a.pool)]
syms = list(dict.fromkeys([s for s in syms if s] + [a.benchmark]))
os.makedirs(a.out, exist_ok=True)
failed = []
for s in syms:
    for attempt in range(3):
        try:
            df = yf.download(s, start=a.start, auto_adjust=True, progress=False, multi_level_index=False)
            if df is None or df.empty:
                raise ValueError("empty")
            df = df[["Open", "High", "Low", "Close", "Volume"]]
            # Yahoo often leaves the latest session's Volume empty for a while after the close;
            # dropping that row made the whole pool look a day old. Keep it, volume 0.
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            df["Volume"] = df["Volume"].fillna(0)
            df.index.name = "Date"
            df.to_csv(os.path.join(a.out, f"{s}.csv"), date_format="%Y-%m-%d")
            print(f"{s}: {len(df)} rows")
            break
        except Exception as e:
            if attempt == 2:
                print(f"{s}: FAILED ({e})"); failed.append(s)
            time.sleep(2)
if a.benchmark in failed:
    sys.exit("benchmark download failed")
