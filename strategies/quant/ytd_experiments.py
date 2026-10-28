"""Robustness experiments for B1+MA60+day-3 on 2026 YTD Yahoo data.

Each idea is tested on several parameter values and judged on BOTH halves
(Jan-Jun and Jul-Oct), so a change that only fixes July/October is visible as such.
Run from strategies/quant:  python3 ytd_experiments.py
"""
import csv
import json
import math
from datetime import date

from b1_research import kdj, signals_for
from qbacktest import sma
from run_ytd_yahoo import PRICES, universe, OUT
from sector_b1 import simulate

CUTOFF = date(2026, 10, 27)


def index_close(symbol):
    rows = [r for r in csv.DictReader((PRICES / f'{symbol}.csv').open()) if r['Date'][:10] <= str(CUTOFF)]
    return [(date.fromisoformat(r['Date'][:10]), float(r['Close'])) for r in rows]


def regime(symbol, n):
    """date -> True if index close > its n-day SMA (known at that close)."""
    s = index_close(symbol)
    out = {}
    for i, (d, c) in enumerate(s):
        if i >= n - 1:
            out[d] = c > sum(x for _, x in s[i - n + 1:i + 1]) / n
    return out


def run(data, sectors, signals, arm=0.0):
    r, trades, fills, curve, *_ = simulate(data, signals, 3, 'close_only', sectors, 'body5', arm_pct=arm)
    h1_end = [row['equity'] for row in curve if str(row['date']) <= '2026-06-30'][-1]
    jul_start = [row['equity'] for row in curve if str(row['date']) < '2026-07-01'][-1]
    jul_end = [row['equity'] for row in curve if str(row['date']) <= '2026-07-31'][-1]
    oct_start = [row['equity'] for row in curve if str(row['date']) < '2026-10-01'][-1]
    return dict(total=r['total_return'], dd=r['max_drawdown'], trades=r['trades'], win=r['win_rate'],
                exp=r['mean_exposure'], h1=h1_end / 1e5 - 1, h2=curve[-1]['equity'] / h1_end - 1,
                jul=jul_end / jul_start - 1, oct=curve[-1]['equity'] / oct_start - 1)


def main():
    data, sectors, _ = universe(CUTOFF)
    base, _ = signals_for(data, data, above_ma60=True)
    base = {d: [c for c in cs if c.signal_date.year == 2026] for d, cs in base.items() if d.year == 2026}

    # per-symbol J by date, for breadth and turn-up filters
    J = {s: dict(zip([b.date for b in bs], kdj(bs))) for s, bs in data.items()}
    days = sorted({d for s in J for d in J[s] if d.year == 2026})
    breadth = {d: sum(1 for s in J if J[s].get(d, math.nan) < 12) / sum(1 for s in J if d in J[s]) for d in days}
    prevJ = {}
    for s, bs in data.items():
        ds = [b.date for b in bs]
        prevJ[s] = {d: J[s][p] for p, d in zip(ds, ds[1:])}

    def filt(pred):
        return {d: [c for c in cs if pred(c)] for d, cs in base.items()}

    tests = [('原版 B1+MA60+第3天', base, 0.0)]
    for sym, n in (('QQQ', 20), ('QQQ', 50), ('SPY', 20), ('SPY', 50)):
        reg = regime(sym, n)
        tests.append((f'大盘过滤 {sym}>MA{n}', filt(lambda c, reg=reg: reg.get(c.signal_date, True)), 0.0))
    for cap in (0.15, 0.20, 0.25, 0.30):
        tests.append((f'扎堆过滤 J<12占比≤{cap:.0%}', filt(lambda c, cap=cap: breadth.get(c.signal_date, 0) <= cap), 0.0))
    tests.append(('J拐头 (J>前一日J)', filt(lambda c: J[c.symbol][c.signal_date] > prevJ[c.symbol].get(c.signal_date, math.inf)), 0.0))
    for arm in (0.01, 0.02, 0.03, 0.05):
        tests.append((f'保本缓冲 浮盈>{arm:.0%}才启用', base, arm))

    rows = []
    print(f"{'规则':<26}{'全年':>8}{'上半年':>8}{'7-10月':>8}{'7月':>8}{'10月':>8}{'回撤':>8}{'交易':>6}{'胜率':>6}{'仓位':>6}")
    for name, sig, arm in tests:
        m = run(data, sectors, sig, arm)
        rows.append(dict(rule=name, **m))
        print(f"{name:<26}{m['total']:8.1%}{m['h1']:8.1%}{m['h2']:8.1%}{m['jul']:8.1%}{m['oct']:8.1%}{m['dd']:8.1%}{m['trades']:6d}{m['win']:6.0%}{m['exp']:6.0%}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'experiments.json').write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    globals().update(base=base, filt=filt, breadth=breadth, J=J, prevJ=prevJ, data=data, sectors=sectors)


if __name__ == '__main__':
    main()
