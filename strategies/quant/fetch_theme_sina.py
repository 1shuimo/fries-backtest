"""Download the selected US universe from AKShare's free Sina adapter."""
import akshare as ak
from fetch_data import write_rows
from run_theme_backtest import GROUPS

rows = []
for symbol in sorted({s for symbols in GROUPS.values() for s in symbols}):
    frame = ak.stock_us_daily(symbol=symbol, adjust="qfq")
    frame = frame[(frame["date"].astype(str) >= "2025-06-01") & (frame["date"].astype(str) <= "2026-09-04")]
    for _, bar in frame.iterrows():
        rows.append(dict(date=str(bar["date"])[:10], symbol=symbol, market="US",
                         open=float(bar["open"]), high=float(bar["high"]),
                         low=float(bar["low"]), close=float(bar["close"]),
                         volume=float(bar["volume"]), catalyst=0))
    print(symbol, len(frame), flush=True)
write_rows(rows, "data/us_themes_23_sina.csv")
