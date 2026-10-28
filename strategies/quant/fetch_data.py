#!/usr/bin/env python3
"""Download and normalize daily bars for qbacktest.py."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


FIELDS = ["date", "symbol", "market", "open", "high", "low", "close", "volume", "catalyst"]


def write_rows(rows: list[dict[str, object]], output: str) -> None:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda row: (str(row["date"]), str(row["symbol"])))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def fetch_yfinance(symbols: list[str], start: str, end: str) -> list[dict[str, object]]:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise SystemExit("缺少 yfinance；请运行: python3 -m pip install -r requirements-data.txt") from exc

    rows: list[dict[str, object]] = []
    for symbol in symbols:
        frame = yf.download(
            symbol,
            start=start,
            end=end,
            auto_adjust=True,
            actions=False,
            progress=False,
            threads=False,
        )
        if frame.empty:
            print(f"warning: {symbol} 没有返回数据")
            continue
        if getattr(frame.columns, "nlevels", 1) > 1:
            frame.columns = frame.columns.get_level_values(0)
        for date, row in frame.iterrows():
            rows.append(
                {
                    "date": date.date().isoformat(),
                    "symbol": symbol.upper(),
                    "market": "US",
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row["Volume"]),
                    "catalyst": 0,
                }
            )
    return rows


def fetch_akshare(symbols: list[str], start: str, end: str) -> list[dict[str, object]]:
    try:
        import akshare as ak
    except ImportError as exc:
        raise SystemExit("缺少 akshare；请运行: python3 -m pip install -r requirements-data.txt") from exc

    rows: list[dict[str, object]] = []
    for symbol in symbols:
        frame = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust="qfq",
        )
        if frame.empty:
            print(f"warning: {symbol} 没有返回数据")
            continue
        for _, row in frame.iterrows():
            rows.append(
                {
                    "date": str(row["日期"]),
                    "symbol": symbol,
                    "market": "CN",
                    "open": float(row["开盘"]),
                    "high": float(row["最高"]),
                    "low": float(row["最低"]),
                    "close": float(row["收盘"]),
                    "volume": float(row["成交量"]),
                    "catalyst": 0,
                }
            )
    return rows


def baostock_code(symbol: str) -> str:
    symbol = symbol.strip().lower()
    if symbol.startswith(("sh.", "sz.")):
        return symbol
    return f"sh.{symbol}" if symbol.startswith(("5", "6", "9")) else f"sz.{symbol}"


def fetch_baostock(symbols: list[str], start: str, end: str) -> list[dict[str, object]]:
    try:
        import baostock as bs
    except ImportError as exc:
        raise SystemExit("缺少 baostock；请运行: python3 -m pip install -r requirements-data.txt") from exc

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败: {login.error_msg}")
    rows: list[dict[str, object]] = []
    try:
        fields = "date,code,open,high,low,close,volume,tradestatus"
        for symbol in symbols:
            result = bs.query_history_k_data_plus(
                baostock_code(symbol),
                fields,
                start_date=start,
                end_date=end,
                frequency="d",
                adjustflag="2",
            )
            if result.error_code != "0":
                print(f"warning: {symbol}: {result.error_msg}")
                continue
            while result.next():
                day, _, open_, high, low, close, volume, trading = result.get_row_data()
                if trading != "1" or not all((open_, high, low, close, volume)):
                    continue
                rows.append(
                    {
                        "date": day,
                        "symbol": symbol.upper(),
                        "market": "CN",
                        "open": float(open_),
                        "high": float(high),
                        "low": float(low),
                        "close": float(close),
                        "volume": float(volume),
                        "catalyst": 0,
                    }
                )
    finally:
        bs.logout()
    return rows


def futu_market(code: str) -> str:
    prefix = code.split(".", 1)[0].upper()
    if prefix in {"SH", "SZ"}:
        return "CN"
    return prefix


def fetch_futu(
    symbols: list[str], start: str, end: str, host: str = "127.0.0.1", port: int = 11111
) -> list[dict[str, object]]:
    try:
        from futu import AuType, KLType, OpenQuoteContext, RET_OK
    except ImportError as exc:
        raise SystemExit("缺少 futu-api；请运行: python3 -m pip install -r requirements-futu.txt") from exc

    rows: list[dict[str, object]] = []
    quote_ctx = OpenQuoteContext(host=host, port=port)
    try:
        for symbol in symbols:
            page_req_key = None
            while True:
                ret, frame, page_req_key = quote_ctx.request_history_kline(
                    symbol,
                    start=start,
                    end=end,
                    ktype=KLType.K_DAY,
                    autype=AuType.QFQ,
                    max_count=1000,
                    page_req_key=page_req_key,
                )
                if ret != RET_OK:
                    raise RuntimeError(f"{symbol}: {frame}")
                for _, row in frame.iterrows():
                    rows.append(
                        {
                            "date": str(row["time_key"])[:10],
                            "symbol": symbol.upper(),
                            "market": futu_market(symbol),
                            "open": float(row["open"]),
                            "high": float(row["high"]),
                            "low": float(row["low"]),
                            "close": float(row["close"]),
                            "volume": float(row["volume"]),
                            "catalyst": 0,
                        }
                    )
                if page_req_key is None:
                    break
    finally:
        quote_ctx.close()
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="provider", required=True)
    for provider in ("yfinance", "akshare", "baostock", "futu"):
        child = subparsers.add_parser(provider)
        child.add_argument("--symbols", required=True, help="逗号分隔代码")
        child.add_argument("--start", required=True)
        child.add_argument("--end", required=True)
        child.add_argument("--output", required=True)
        if provider == "futu":
            child.add_argument("--host", default="127.0.0.1")
            child.add_argument("--port", type=int, default=11111)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
    if args.provider == "yfinance":
        rows = fetch_yfinance(symbols, args.start, args.end)
    elif args.provider == "akshare":
        rows = fetch_akshare(symbols, args.start, args.end)
    elif args.provider == "baostock":
        rows = fetch_baostock(symbols, args.start, args.end)
    else:
        rows = fetch_futu(symbols, args.start, args.end, args.host, args.port)
    write_rows(rows, args.output)
    print(f"wrote {len(rows):,} rows to {args.output}")


if __name__ == "__main__":
    main()
