import unittest
from datetime import date, timedelta
from unittest.mock import patch

from korea_leveraged import map_signals
from qbacktest import Bar


class KoreaCalendarTests(unittest.TestCase):
    def test_next_hk_session_and_known_atr(self):
        bars = [Bar(date(2026,1,1)+timedelta(days=i),'07747','HK',100,102,98,100,1000)
                for i in range(25)]
        signal_date = bars[20].date
        # The next calendar day has no HK trading; wait for the following bar.
        bars = [b for i,b in enumerate(bars) if i != 21]
        audit = [dict(symbol='005930',strategy='trend',signal_date=str(signal_date))]
        with patch('korea_leveraged.make_signals',return_value=({},audit)):
            signals, mapped = map_signals({}, {'07747':bars}, 'trend')
        self.assertEqual(list(signals), [signal_date+timedelta(days=2)])
        self.assertEqual(next(iter(signals.values()))[0].atr, 4)
        self.assertEqual(mapped[0]['reason'], 'eligible')

    def test_no_future_bar_or_warmup_means_no_trade(self):
        bars = [Bar(date(2026,1,1)+timedelta(days=i),'07709','HK',100,102,98,100,1000)
                for i in range(10)]
        audit = [dict(symbol='000660',strategy='dip45',signal_date=str(bars[i].date)) for i in (0,9)]
        with patch('korea_leveraged.make_signals',return_value=({},audit)):
            signals, mapped = map_signals({}, {'07709':bars}, 'dip45')
        self.assertFalse(signals)
        self.assertEqual([r['reason'] for r in mapped], ['atr_warmup','no_next_hk_session'])
