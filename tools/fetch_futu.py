#!/usr/bin/env python3
"""Download daily bars from a locally running FutuOpenD into <repo>/data/ (read-only quotes, no trading).

Writes the same per-symbol format as tools/fetch_yahoo.py (Date,Open,High,Low,Close,Volume),
so both strategy engines can use Futu data unchanged. Default output is data_futu/ so it never
silently overwrites the Yahoo cache; pass --out data to replace it.

Requirements (on the computer running FutuOpenD):
    python3 -m pip install futu-api
    FutuOpenD running and logged in, default 127.0.0.1:11111

Usage:
    python3 tools/fetch_futu.py                       # whole FriesTrader pool, since 2019-06-01
    python3 tools/fetch_futu.py --symbols NVDA,SNDK   # a few symbols
    python3 tools/fetch_futu.py --check               # only test the OpenD connection

Symbols use Yahoo style (NVDA, 0700.HK, 600519.SS); they are mapped to Futu codes (US.NVDA, HK.00700, SH.600519).
Futu limits history-kline quota by distinct symbols requested in the last 30 days; download once, then reuse.
Adjustment: forward-adjusted (QFQ), Futu's default. Yahoo's auto_adjust is also split+dividend adjusted,
so small differences in older prices between the two sources are expected.
"""
import argparse
import csv
import os
import socket
import sys
import time
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(REPO, 'strategies', 'friestrader', 'pool.txt')


def to_futu(sym):
    s = sym.upper()
    if s.endswith('.HK'):
        return 'HK.' + s[:-3].zfill(5)
    if s.endswith('.SS'):
        return 'SH.' + s[:-3]
    if s.endswith('.SZ'):
        return 'SZ.' + s[:-3]
    return 'US.' + s


def read_pool(path):
    out = []
    for line in open(path, encoding='utf-8'):
        s = line.split('#')[0].strip().upper()
        if s:
            out.append(s)
    return list(dict.fromkeys(out))


def check(host, port):
    try:
        with socket.create_connection((host, port), timeout=3):
            return True, ''
    except OSError as e:
        return False, str(e)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbols', help='comma-separated Yahoo-style symbols (default: FriesTrader pool + SPY)')
    ap.add_argument('--pool', default=POOL)
    ap.add_argument('--start', default='2019-06-01')
    ap.add_argument('--end', default=date.today().isoformat())
    ap.add_argument('--out', default=os.path.join(REPO, 'data_futu'))
    ap.add_argument('--host', default=os.environ.get('FUTU_HOST', '127.0.0.1'))
    ap.add_argument('--port', type=int, default=int(os.environ.get('FUTU_PORT', '11111')))
    ap.add_argument('--check', action='store_true', help='only test the OpenD connection')
    a = ap.parse_args()

    ok, err = check(a.host, a.port)
    print(f'FutuOpenD {a.host}:{a.port} ->', 'reachable' if ok else f'NOT reachable ({err})')
    if a.check or not ok:
        sys.exit(0 if ok else 1)

    try:
        from futu import AuType, KLType, OpenQuoteContext, RET_OK
    except ImportError:
        sys.exit('futu-api not installed: python3 -m pip install futu-api')

    syms = a.symbols.split(',') if a.symbols else read_pool(a.pool) + ['SPY']
    syms = list(dict.fromkeys(s.strip().upper() for s in syms if s.strip()))
    os.makedirs(a.out, exist_ok=True)
    ctx = OpenQuoteContext(host=a.host, port=a.port)
    failed = []
    try:
        for sym in syms:
            code = to_futu(sym)
            rows, key = [], None
            try:
                while True:
                    ret, frame, key = ctx.request_history_kline(code, start=a.start, end=a.end, ktype=KLType.K_DAY,
                                                                autype=AuType.QFQ, max_count=1000, page_req_key=key)
                    if ret != RET_OK:
                        raise RuntimeError(frame)
                    for _, r in frame.iterrows():
                        rows.append((str(r['time_key'])[:10], r['open'], r['high'], r['low'], r['close'], r['volume']))
                    if key is None:
                        break
                if not rows:
                    raise RuntimeError('no rows')
                with open(os.path.join(a.out, f'{sym}.csv'), 'w', newline='') as f:
                    w = csv.writer(f)
                    w.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
                    w.writerows(rows)
                print(f'{sym:<8} {code:<12} {len(rows):5d} rows  last {rows[-1][0]}')
            except Exception as e:  # keep going; report at the end
                failed.append((sym, str(e)[:120]))
                print(f'{sym:<8} {code:<12} FAILED {str(e)[:120]}')
            time.sleep(0.6)  # stay under Futu's request-rate limit
    finally:
        ctx.close()
    print(f'done: {len(syms) - len(failed)} ok, {len(failed)} failed -> {a.out}')


if __name__ == '__main__':
    main()
