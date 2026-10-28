"""Smoke tests for strategies/friestrader/backtest.py on synthetic prices (no network)."""
import csv
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(REPO, 'strategies', 'friestrader', 'backtest.py')
RULES = os.path.join(REPO, 'strategies', 'friestrader', 'rules.json')


def write_series(folder, sym, n, seed, drift, vol):
    rnd = random.Random(seed)
    d, p, days = date(2019, 6, 3), 50.0, 0
    with open(os.path.join(folder, f'{sym}.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
        while days < n:
            if d.weekday() < 5:
                o = p * (1 + rnd.gauss(0, vol / 3))
                c = o * (1 + rnd.gauss(drift, vol))
                h, l = max(o, c) * 1.005, min(o, c) * 0.995
                v = 2e6 * (3 if rnd.random() < 0.03 else 1)
                w.writerow([d.isoformat(), round(o, 4), round(h, 4), round(l, 4), round(c, 4), int(v)])
                p, days = c, days + 1
            d += timedelta(days=1)


class FriesTraderSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        os.makedirs(os.path.join(t, 'data'))
        syms = ['AAA', 'BBB', 'CCC', 'DDD', 'EEE']
        for k, s in enumerate(syms + ['SPY']):
            write_series(os.path.join(t, 'data'), s, 1100, k, 0.0004 * (k % 3), 0.015)
        with open(os.path.join(t, 'pool.txt'), 'w') as f:
            f.write('# test\n' + '\n'.join(syms) + '\nMISSING\n')
        out = os.path.join(t, 'out')
        subprocess.run([sys.executable, ENGINE, '--data', os.path.join(t, 'data'), '--pool', os.path.join(t, 'pool.txt'),
                        '--rules', RULES, '--out', out, '--start', '2021-01-01', '--end', '2023-06-30'],
                       check=True, capture_output=True, text=True)
        cls.summary = json.load(open(os.path.join(out, 'summary.json')))
        cls.trades = list(csv.DictReader(open(os.path.join(out, 'trades.csv'))))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_missing_symbol_reported(self):
        self.assertEqual(self.summary['missing_or_short'], ['MISSING'])

    def test_never_exceeds_max_positions(self):
        qty = {}
        for t in self.trades:  # trades are in time order; sells before buys within a day
            q = float(t['qty'])
            if t['side'] == 'buy':
                qty[t['symbol']] = qty.get(t['symbol'], 0) + q
                self.assertLessEqual(sum(1 for v in qty.values() if v > 1e-6), 4)
            else:
                qty[t['symbol']] = qty.get(t['symbol'], 0) - q
        self.assertLessEqual(len(self.summary['final_open_positions']), 4)

    def test_buys_only_after_signal_day(self):
        for t in self.trades:
            if t['side'] == 'buy':
                self.assertGreaterEqual(t['date'], '2021-01-01')

    def test_has_trades_and_metrics(self):
        self.assertGreater(len(self.trades), 0)
        for k in ('total_return', 'max_drawdown', 'sharpe'):
            self.assertIn(k, self.summary['strategy'])


if __name__ == '__main__':
    unittest.main()
