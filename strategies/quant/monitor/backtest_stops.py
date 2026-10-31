#!/usr/bin/env python3
"""Compare stops on the pool.txt universe, 2026 YTD, B1+MA60+day-3, close_only exits otherwise:
  atr   : original close-confirmed entry-2*ATR20 stop
  swing : intraday stop at the previous swing low if within 10% of entry, else the buy day's low
Run from strategies/quant:  python3 monitor/backtest_stops.py [--cutoff 2026-10-30]"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from daily_monitor import REPO, load_bars, load_pool, swing_low  # noqa: E402
from b1_research import signals_for  # noqa: E402
from sector_b1 import simulate  # noqa: E402


def stop_info(data, signals):
    info = {}
    for day, cs in signals.items():
        for c in cs:
            bars = data[c.symbol]
            i = next(k for k in range(len(bars) - 1, -1, -1) if bars[k].date == c.signal_date)
            info[(c.symbol, day)] = (swing_low(bars, i), bars[i].low)
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cutoff', default='2026-10-30')
    a = ap.parse_args()
    cutoff = date.fromisoformat(a.cutoff)
    sectors = load_pool()
    data = {}
    for s in sectors:
        f = REPO / 'data' / f'{s}.csv'
        if f.exists():
            bars = load_bars(f, s, cutoff)
            if bars and bars[-1].date == cutoff and len(bars) > 80:
                data[s] = bars
    raw, _ = signals_for(data, data, above_ma60=True)
    sig = {d: [c for c in cs if c.signal_date.year == 2026] for d, cs in raw.items() if d.year == 2026}
    info = stop_info(data, sig)
    months = {}
    for label, si in [('2ATR 收盘止损（原版）', None), ('前低≤10% 否则买入日低点（盘中止损）', info)]:
        r, trades, fills, curve, *_ = simulate(data, sig, 3, 'close_only', sectors, 'body5', stop_info=si)
        ends = {}
        for row in curve:
            ends[row['date'][:7]] = row['equity']
        prev, m = 100000., []
        for k, v in ends.items():
            m.append(f'{k[5:]}:{v / prev - 1:+.1%}')
            prev = v
        stops = [t for t in trades if t['reason'] in ('price_stop', 'gap_stop', 'close_atr_stop')]
        print(f"{label:<28} 收益 {r['total_return']:+7.2%}  回撤 {r['max_drawdown']:7.2%}  交易 {r['trades']:3d}  "
              f"胜率 {r['win_rate']:.0%}  仓位 {r['mean_exposure']:.0%}  止损出局 {len(stops)}")
        print('   ', ' '.join(m))
    print('symbols', len(data))


if __name__ == '__main__':
    main()
