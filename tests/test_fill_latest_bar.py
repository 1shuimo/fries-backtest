import csv
import os
import sys
import tempfile
import unittest
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import fill_latest_bar as F  # noqa: E402

DAY = date(2026, 11, 2)


def intraday(last_start='15:30'):
    idx = pd.date_range(f'{DAY} 09:30', f'{DAY} {last_start}', freq='30min', tz='America/New_York')
    n = len(idx)
    return pd.DataFrame({'Open': [100 + i for i in range(n)], 'High': [101 + i for i in range(n)],
                         'Low': [99 + i for i in range(n)], 'Close': [100.5 + i for i in range(n)],
                         'Volume': [1000] * n}, index=idx.tz_convert('UTC'))


class FillLatestBar(unittest.TestCase):
    def test_intraday_aggregates_full_session(self):
        b = F.bar_from_intraday(intraday(), DAY)
        self.assertEqual((b['Open'], b['High'], b['Low'], b['Close'], b['Volume']), (100, 113, 99, 112.5, 13000))

    def test_intraday_rejects_unfinished_session(self):
        self.assertIsNone(F.bar_from_intraday(intraday('13:00'), DAY))

    def test_parsers(self):
        self.assertEqual(F.parse_stooq('Date,Open,High,Low,Close,Volume\n2026-11-02,1,2,0.5,1.5,10\n', DAY)['Close'], '1.5')
        self.assertIsNone(F.parse_stooq('No data', DAY))
        payload = {'data': {'tradesTable': {'rows': [
            {'date': '11/02/2026', 'open': '$204.10', 'high': '$206.00', 'low': '$201.50', 'close': '$205.25', 'volume': '1,234,567'}]}}}
        b = F.parse_nasdaq(payload, DAY)
        self.assertEqual((b['Close'], b['Volume']), (205.25, 1234567))
        self.assertIsNone(F.parse_nasdaq({'data': None}, DAY))

    def test_valid(self):
        self.assertTrue(F.valid({'Open': 10, 'High': 11, 'Low': 9, 'Close': 10.5}, 10))
        self.assertFalse(F.valid({'Open': 10, 'High': 9, 'Low': 8, 'Close': 10.5}, 10))   # high below close
        self.assertFalse(F.valid({'Open': 40, 'High': 41, 'Low': 39, 'Close': 40}, 10))    # 4x jump

    def test_append_keeps_order_and_header(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'X.csv')
            with open(p, 'w', newline='') as f:
                csv.writer(f).writerows([['Date', 'Open', 'High', 'Low', 'Close', 'Volume'],
                                         ['2026-10-29', 1, 1, 1, 1, 1], ['2026-10-30', 2, 2, 2, 2, 2]])
            self.assertFalse(F.has_bar(p, DAY))
            F.append_bar(p, DAY, {'Open': 3, 'High': 3.5, 'Low': 2.5, 'Close': 3, 'Volume': 7})
            rows = list(csv.reader(open(p)))
            self.assertEqual([r[0] for r in rows], ['Date', '2026-10-29', '2026-10-30', '2026-11-02'])
            self.assertTrue(F.has_bar(p, DAY))
            self.assertEqual(F.prev_close(p, DAY), 2.0)


if __name__ == '__main__':
    unittest.main()
