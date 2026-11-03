#!/usr/bin/env python3
"""Fill in one missing daily bar (the latest session) for symbols whose Yahoo daily history stops early.

Yahoo's daily endpoint sometimes lags for hours after the close (2026-11-02: 160 of 163 symbols
had no bar by 21:13 New York). For each data/<SYM>.csv without a row for --date, try in order:
  1. yahoo-intraday : Yahoo 30-minute bars for that session, aggregated to one daily bar
  2. stooq          : stooq.com daily CSV
  3. nasdaq         : api.nasdaq.com historical quotes
and append the first complete bar found. Only the one missing day is added; history stays Yahoo's.
Writes <out>/_fallback.json = {"date": ..., "filled": {sym: source}, "missing": [...]}.
Usage: fill_latest_bar.py --date 2026-11-02 --pool /tmp/pool.txt [--out data] [--sources yahoo-intraday,stooq,nasdaq]
"""
import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {'User-Agent': 'Mozilla/5.0 (fries-backtest daily monitor)', 'Accept': 'application/json, text/csv, */*'}


def has_bar(path, day):
    if not os.path.exists(path):
        return False
    with open(path, newline='') as f:
        rows = list(csv.reader(f))
    return any(r and r[0][:10] == str(day) for r in rows[-5:])


def append_bar(path, day, bar):
    """Append (or replace a later-dated stub of) the row for `day`, keeping the file sorted."""
    with open(path, newline='') as f:
        rows = list(csv.reader(f))
    header, body = rows[0], [r for r in rows[1:] if r and r[0][:10] != str(day)]
    body.append([str(day)] + [repr(float(bar[k])) for k in ('Open', 'High', 'Low', 'Close')] + [str(int(bar['Volume']))])
    body.sort(key=lambda r: r[0][:10])
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(body)


def valid(bar, prev_close=None):
    try:
        o, h, l, c = (float(bar[k]) for k in ('Open', 'High', 'Low', 'Close'))
    except (KeyError, TypeError, ValueError):
        return False
    if min(o, h, l, c) <= 0 or h < max(o, c) - 1e-6 or l > min(o, c) + 1e-6:
        return False
    # A bar more than 3x away from yesterday is a symbol mix-up or unadjusted split, not a session.
    return prev_close is None or 1 / 3 < c / prev_close < 3


def bar_from_intraday(df, day):
    """Aggregate regular-session intraday bars (tz-aware index, New York) into one daily bar.
    Requires bars up to the last half hour, so a half-finished session is not taken as the day."""
    if df is None or df.empty:
        return None
    df = df.dropna(subset=['Open', 'High', 'Low', 'Close'])
    idx = df.index.tz_convert('America/New_York') if df.index.tz is not None else df.index
    df = df[[t.date() == day and (9, 30) <= (t.hour, t.minute) < (16, 0) for t in idx]]
    if df.empty:
        return None
    last = (df.index.tz_convert('America/New_York') if df.index.tz is not None else df.index)[-1]
    if (last.hour, last.minute) < (15, 0):
        return None
    return {'Open': df['Open'].iloc[0], 'High': df['High'].max(), 'Low': df['Low'].min(),
            'Close': df['Close'].iloc[-1], 'Volume': float(df['Volume'].fillna(0).sum())}


def parse_stooq(text, day):
    for r in csv.DictReader(io.StringIO(text)):
        if r.get('Date') == str(day):
            return {'Open': r['Open'], 'High': r['High'], 'Low': r['Low'], 'Close': r['Close'],
                    'Volume': r.get('Volume') or 0}
    return None


def parse_nasdaq(payload, day):
    rows = (((payload or {}).get('data') or {}).get('tradesTable') or {}).get('rows') or []
    num = lambda s: float(str(s).replace('$', '').replace(',', '') or 0)
    for r in rows:
        if datetime.strptime(r['date'], '%m/%d/%Y').date() == day:
            return {'Open': num(r['open']), 'High': num(r['high']), 'Low': num(r['low']),
                    'Close': num(r['close']), 'Volume': num(r.get('volume', 0))}
    return None


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode('utf-8', 'replace')


def from_yahoo_intraday(sym, day):
    import yfinance as yf
    df = yf.download(sym, start=str(day), end=str(day + timedelta(days=1)), interval='30m',
                     auto_adjust=True, prepost=False, progress=False, multi_level_index=False)
    return bar_from_intraday(df, day)


def from_stooq(sym, day):
    d = day.strftime('%Y%m%d')
    return parse_stooq(get(f'https://stooq.com/q/d/l/?s={sym.lower()}.us&i=d&d1={d}&d2={d}'), day)


def from_nasdaq(sym, day):
    for cls in ('stocks', 'etf'):
        url = (f'https://api.nasdaq.com/api/quote/{sym}/historical?assetclass={cls}'
               f'&fromdate={day}&todate={day}&limit=5')
        bar = parse_nasdaq(json.loads(get(url)), day)
        if bar:
            return bar
    return None


SOURCES = {'yahoo-intraday': from_yahoo_intraday, 'stooq': from_stooq, 'nasdaq': from_nasdaq}


def prev_close(path, day):
    with open(path, newline='') as f:
        rows = [r for r in csv.reader(f)][1:]
    before = [r for r in rows if r and r[0][:10] < str(day)]
    return float(before[-1][4]) if before else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--date', required=True)
    ap.add_argument('--pool', default=os.path.join(REPO, 'strategies', 'friestrader', 'pool.txt'))
    ap.add_argument('--out', default=os.path.join(REPO, 'data'))
    ap.add_argument('--sources', default='yahoo-intraday,stooq,nasdaq')
    ap.add_argument('--dry-run', action='store_true', help='print what would be filled, write nothing')
    a = ap.parse_args()
    day = date.fromisoformat(a.date)
    syms = list(dict.fromkeys(s for s in (l.split('#')[0].strip().upper() for l in open(a.pool)) if s))
    order = [s.strip() for s in a.sources.split(',') if s.strip()]
    filled, missing, tried = {}, [], 0
    for sym in syms:
        path = os.path.join(a.out, f'{sym}.csv')
        if not os.path.exists(path) or has_bar(path, day):
            continue
        tried += 1
        pc = prev_close(path, day)
        for src in order:
            try:
                bar = SOURCES[src](sym, day)
            except Exception as e:  # one source failing must not stop the others
                print(f'{sym}: {src} error {type(e).__name__}: {e}'[:200])
                bar = None
            if bar and valid(bar, pc):
                print(f'{sym}: {day} from {src}  O {float(bar["Open"]):.2f} H {float(bar["High"]):.2f} '
                      f'L {float(bar["Low"]):.2f} C {float(bar["Close"]):.2f}')
                if not a.dry_run:
                    append_bar(path, day, bar)
                filled[sym] = src
                break
            time.sleep(0.3)
        else:
            missing.append(sym)
    print(f'fallback for {day}: {tried} symbols lacked the bar, filled {len(filled)}, still missing {len(missing)}')
    if not a.dry_run:
        with open(os.path.join(a.out, '_fallback.json'), 'w') as f:
            json.dump({'date': str(day), 'filled': filled, 'missing': missing}, f, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    sys.exit(main())
