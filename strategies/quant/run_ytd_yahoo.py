"""Re-run Codex's B1 strategies on the repo-root Yahoo data (data/*.csv) for 2026 YTD.

Reuses the existing signal and simulation code unchanged:
  b1_research.signals_for, chang_research.make_signals, sector_b1.simulate.
Only the data source differs from watchlist_backtest.py:
  - Prices: ../../data/<SYM>.csv from the GitHub Actions yfinance download (auto-adjusted).
  - Cutoff: last complete session (default 2026-10-27), dropping any partial intraday bar.
  - Sectors: import_watchlist.prepare() on data/watchlist.csv (same mapping as Codex).
  - AZN is NOT rescaled: Yahoo history is already continuous across the 2026-02-02 listing change.
Run from strategies/quant:  python3 run_ytd_yahoo.py [--cutoff 2026-10-27]
"""
import argparse
import csv
import json
from datetime import date
from pathlib import Path

from b1_research import signals_for
from chang_research import make_signals
from import_watchlist import prepare, ROOT as UNIVERSE
from qbacktest import Bar
from sector_b1 import simulate

PRICES = Path(__file__).resolve().parents[2] / 'data'
OUT = Path('results/ytd_yahoo')

SPECS = {
    'b1_ma60_d2': ('B1+MA60+第2天检查', 'body5', 2, 'b1_ma60'),
    'b1_ma60_d3': ('B1+MA60+第3天检查', 'body5', 3, 'b1_ma60'),
    'b1_ma60':    ('B1+MA60，无时间退出', 'body5', None, 'b1_ma60'),
    'b1':         ('B1，无MA60', 'body5', None, 'b1'),
    'both_322':   ('突破/超跌+322', '322', None, 'both'),
}


def load_yahoo(symbol, cutoff):
    path = PRICES / f'{symbol}.csv'
    bars = []
    with path.open(newline='') as f:
        for r in csv.DictReader(f):
            d = date.fromisoformat(r['Date'][:10])
            if d > cutoff:
                continue
            o, h, l, c, v = (float(r[k]) for k in ('Open', 'High', 'Low', 'Close', 'Volume'))
            if min(o, h, l, c) <= 0:
                continue
            bars.append(Bar(d, symbol, 'US', o, max(h, o, c), min(l, o, c), c, v))
    return bars


def universe(cutoff):
    prepare('data/watchlist.csv')
    manifest = json.loads((UNIVERSE / 'universe.json').read_text())
    sectors = manifest['sectors']
    data, missing = {}, []
    for s in manifest['list_symbols']:
        src = 'GOOG' if s == 'GOOGL' else s
        if not (PRICES / f'{src}.csv').exists():
            missing.append(s)
            continue
        bars = [Bar(b.date, s, b.market, b.open, b.high, b.low, b.close, b.volume) for b in load_yahoo(src, cutoff)]
        if s == 'WOLF':
            bars = [b for b in bars if b.date >= date(2025, 9, 29)]
        if not bars or bars[-1].date != cutoff:
            missing.append(s)
            continue
        data[s] = bars
    return data, sectors, missing


def signal_sets(data):
    b1, _ = signals_for(data, data)
    b1_ma60, _ = signals_for(data, data, above_ma60=True)
    both, _ = make_signals(data, 'both')
    raw = {'b1': b1, 'b1_ma60': b1_ma60, 'both': both}
    return {k: {d: [c for c in cs if c.signal_date.year == 2026] for d, cs in v.items() if d.year == 2026}
            for k, v in raw.items()}


def monthly(curve):
    ends = {}
    for row in curve:
        ends[str(row['date'])[:7]] = row['equity']
    out, prev = {}, 100000.
    for m, eq in ends.items():
        out[m] = eq / prev - 1
        prev = eq
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cutoff', default='2026-10-27')
    a = ap.parse_args()
    cutoff = date.fromisoformat(a.cutoff)
    data, sectors, missing = universe(cutoff)
    sig = signal_sets(data)
    results = {}
    for key, (label, rule, day, sk) in SPECS.items():
        r, trades, fills, curve, weights, contrib, skips = simulate(data, sig[sk], day, 'close_only', sectors, rule)
        r['monthly_returns'] = monthly(curve)
        results[key] = r
        folder = OUT / key
        folder.mkdir(parents=True, exist_ok=True)
        for name, rows in [('trades', trades), ('fills', fills), ('equity', curve), ('skipped', skips)]:
            if rows:
                with (folder / f'{name}.csv').open('w', newline='') as f:
                    w = csv.DictWriter(f, fieldnames=list(rows[0]))
                    w.writeheader()
                    w.writerows(rows)
        print(f"{label:<22} {r['total_return']:8.2%}  DD {r['max_drawdown']:7.2%}  trades {r['trades']:3d}  "
              f"win {r['win_rate']:.0%}  exp {r['mean_exposure']:.0%}  |  " +
              ' '.join(f"{m[5:]}:{v:+.1%}" for m, v in r['monthly_returns'].items()))
    (OUT / 'summary.json').write_text(json.dumps(dict(cutoff=str(cutoff), symbols=len(data), missing=missing,
                                                      results=results), ensure_ascii=False, indent=1, default=str))
    print('symbols', len(data), 'missing', missing)


if __name__ == '__main__':
    main()
