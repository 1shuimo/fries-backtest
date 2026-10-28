"""Snapshot free daily data for the September 15 notes experiment."""
from pathlib import Path
import json
import math
import argparse
import akshare as ak
from fetch_data import write_rows
from expand_theme_backtest import SOFTWARE, LISTED
from run_theme_backtest import GROUPS

BIGTECH_ADDITIONS = 'QCOM AMD ARM MRVL INTC TXN ADI NXPI TSM ASML AMAT LRCX KLAC ANET CSCO HPE PANW FTNT ZS TEAM INTU'.split()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--add-bigtech', action='store_true')
    args = parser.parse_args()
    symbols = sorted({s for group in GROUPS.values() for s in group} | set(SOFTWARE) | set(LISTED) | {"AVGO", "SMCI", "DELL"})
    folder = Path("data/chang_20260915")
    if args.add_bigtech:
        symbols = BIGTECH_ADDITIONS
        folder = Path('data/chang_bigtech_20260915')
    folder.mkdir(parents=True, exist_ok=True)
    status = {}
    for symbol in symbols:
        path = folder / f"{symbol}.csv"
        if path.exists():
            print("cached", symbol, flush=True)
            continue
        try:
            frame = ak.stock_us_daily(symbol=symbol, adjust="qfq")
            days = frame["date"].astype(str)
            start = LISTED.get(symbol, "2025-02-24" if symbol == "SNDK" else "2024-01-01")
            frame = frame[(days >= start) & (days <= "2026-09-15")]
            rows = [dict(date=str(b["date"])[:10], symbol=symbol, market="US", catalyst=0,
                         **{k:float(b[k]) for k in ("open", "high", "low", "close", "volume")}) for _,b in frame.iterrows()]
            if not rows or not all(math.isfinite(r[k]) for r in rows for k in ("open", "high", "low", "close", "volume")):
                raise ValueError("Empty or non-finite data")
            write_rows(rows, str(path))
            status[symbol] = {"rows":len(rows), "last":rows[-1]["date"]}
            print(symbol, len(rows), rows[-1]["date"], flush=True)
        except Exception as exc:
            status[symbol] = {"error":str(exc)}
            print("FAILED", symbol, str(exc), flush=True)
    (folder / "download_log.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    missing = [s for s in symbols if not (folder / f"{s}.csv").exists()]
    if missing:
        raise RuntimeError(f"Missing symbols: {missing}")


if __name__ == "__main__":
    main()
