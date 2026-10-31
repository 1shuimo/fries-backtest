import random
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'monitor'))
from daily_monitor import Position, replay, size_candidates, swing_low  # noqa: E402
from chang_research import Candidate, indicators  # noqa: E402
from qbacktest import Bar  # noqa: E402
from sector_b1 import simulate  # noqa: E402


def walk(seed, n=160):
    rnd = random.Random(seed)
    d, px, bars = date(2025, 10, 1), 100., []
    while len(bars) < n:
        d += timedelta(days=1)
        if d.weekday() >= 5:
            continue
        o = px * (1 + rnd.gauss(0, .01))
        c = o * (1 + rnd.gauss(.002, .035))
        bars.append(Bar(d, 'X', 'US', o, max(o, c) * 1.01, min(o, c) * .99, c, 1e6))
        px = c
    return bars


class ReplayMatchesBacktest(unittest.TestCase):
    def test_sell_fills_match_sector_b1(self):
        checked = 0
        for seed in range(60):
            bars = walk(seed)
            i = next(k for k, b in enumerate(bars) if b.date >= date(2026, 1, 5))
            atr = indicators(bars)[i - 1]['atr']
            sig = {bars[i].date: [Candidate('X', 'b1_j12', bars[i - 1].date, atr)]}
            info = {('X', bars[i].date): (swing_low(bars, i - 1), bars[i - 1].low)}
            _, _, fills, *_ = simulate({'X': bars}, sig, 3, 'close_only', {'X': 'S'}, 'body5', stop_info=info)
            buy = [f for f in fills if f['side'] == 'buy']
            if not buy:
                continue
            sells = [(f['date'], f['qty']) for f in fills if f['side'] == 'sell' and f['reason'] != 'end_of_test']
            qty = buy[0]['qty']
            p = replay(Position('X', bars[i].date, bars[i].open, qty, qty), bars)
            self.assertEqual([(str(d), q) for d, q in p.sim_fills], sells, f'seed {seed}')
            checked += 1
        self.assertGreater(checked, 40)

    def flat(self, closes, base_low=99.5):
        """60 quiet bars around 100 with one swing low of 96 at bar 50, then (open, close[, low]) bars."""
        d, bars = date(2026, 1, 1), []
        for k in range(60):
            lo = 96 if k == 50 else base_low
            bars.append(Bar(d + timedelta(days=k), 'X', 'US', 100, 100.5, lo, 100, 1e6))
        for k, oc in enumerate(closes):
            o, c = oc[:2]
            lo = oc[2] if len(oc) > 2 else min(o, c)
            bars.append(Bar(d + timedelta(days=60 + k), 'X', 'US', o, max(o, c), lo, c, 1e6))
        return bars

    def test_swing_stop_within_10pct(self):
        bars = self.flat([(100, 100.2)])
        self.assertEqual(swing_low(bars, 59), 96)
        p = replay(Position('X', bars[60].date, 100, 30, 30), bars)
        self.assertEqual((p.stop, p.stop_kind, p.action), (96, '前低', 'hold'))

    def test_day_low_when_swing_too_far(self):
        bars = self.flat([(100, 100.2, 98.7)])
        p = replay(Position('X', bars[60].date, 110, 30, 30), bars)  # 96 is 12.7% below 110
        self.assertEqual((p.stop, p.stop_kind), (98.7, '买入日最低价'))

    def test_intraday_stop_then_overdue(self):
        bars = self.flat([(100, 99, 98), (98.5, 97, 95.5)])
        p = replay(Position('X', bars[60].date, 100, 30, 30), bars)
        self.assertEqual((p.action, p.sim_fills), ('overdue', [(bars[61].date, 30)]))

    def test_day3_exit(self):
        bars = self.flat([(100, 99.8), (99.8, 99.9), (99.9, 99.7)])
        p = replay(Position('X', bars[60].date, 100, 30, 30), bars)
        self.assertEqual((p.held, p.action, p.sell_qty), (3, 'day3', 30))

    def test_body5_partial_and_breakeven(self):
        bars = self.flat([(100, 106)])
        p = replay(Position('X', bars[60].date, 100, 30, 30), bars)
        self.assertEqual((p.action, p.sell_qty), ('body5', 10))
        bars = self.flat([(100, 106), (106, 104), (104, 99.0)])
        p = replay(Position('X', bars[60].date, 100, 30, 20, 1060), bars)
        self.assertTrue(p.armed)
        self.assertEqual(p.action, 'hold')  # 1060 realised + 20*99 > 3003 cost
        bars = self.flat([(100, 106), (106, 104), (104, 98.5)])  # above the 96 stop, below breakeven
        p = replay(Position('X', bars[60].date, 100, 30, 20, 1000), bars)
        self.assertEqual((p.action, p.sell_qty), ('breakeven', 20))

    def test_sizing_caps(self):
        c = [dict(symbol='A', sector='S', close=100., dist=4., new=True),
             dict(symbol='B', sector='S', close=100., dist=1., new=True)]
        out, cash = size_candidates(c, [], 100000., {'A': 'S', 'B': 'S'}, {})
        self.assertEqual(out[0]['qty'], 119)              # 500 / (4 + 2*100*0.1%)
        self.assertEqual(out[1]['why'], '行业30%')         # 20% cap would allow 199; sector room 30% - 12.4%
        self.assertLessEqual((out[0]['qty'] + out[1]['qty']) * 100, 30000)


if __name__ == '__main__':
    unittest.main()
