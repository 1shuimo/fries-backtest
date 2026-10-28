import math
import unittest
from datetime import date, timedelta

from b1_research import kdj, signals_for
from chang_3221 import simulate
from chang_research import Candidate
from qbacktest import Bar


def bar(i,o=100,h=101,l=99,c=100):
    return Bar(date(2026,1,5)+timedelta(days=i),'X','US',o,h,l,c,1000)


class B1Tests(unittest.TestCase):
    def test_kdj_formula_warmup_and_no_lookahead(self):
        bars = [bar(i,50,100,0,0) for i in range(12)]
        js = kdj(bars)
        self.assertTrue(all(math.isnan(x) for x in js[:8]))
        self.assertAlmostEqual(js[8],100/9)
        self.assertEqual(kdj(bars[:10])[-1],js[9])
        self.assertLess(js[-1],0)

    def test_flat_price_is_neutral(self):
        self.assertEqual(kdj([bar(i,100,100,100,100) for i in range(10)])[-1],50)

    def test_body_threshold_next_open_thirds_and_above7(self):
        bars = [bar(0),bar(1,100,105,99,105),bar(2,110,119,109,119),
                bar(3,120,126,119,126),bar(4,130,131,129,130)]
        signals = {bars[0].date:[Candidate('X','b1_j12',date(2026,1,4),5)]}
        summary,trades,fills,curve,_ = simulate({'X':bars},signals,exit_rule='body5')
        sells = [f for f in fills if f['side']=='sell']
        self.assertEqual([f['date'] for f in sells],[str(bars[i].date) for i in (2,3,4)])
        self.assertEqual([f['price'] for f in sells],[110,120,130])
        self.assertEqual(sum(f['shares'] for f in sells),fills[0]['shares'])
        self.assertEqual([f['shares'] for f in sells],[16,16,17])
        self.assertEqual(summary['end_of_test_positions'],0)
        self.assertAlmostEqual(sum(t['pnl'] for t in trades),curve[-1]['equity']-100000)

    def test_gap_up_is_not_body_and_stop_priority(self):
        bars = [bar(0),bar(1,110,111,109,110),bar(2,110,117,109,117),bar(3,80,85,79,82)]
        signals = {bars[0].date:[Candidate('X','b1_j12',date(2026,1,4),5)]}
        _,_,fills,_,_ = simulate({'X':bars},signals,exit_rule='body5')
        self.assertEqual(len(fills),2)
        self.assertEqual(fills[-1]['reason'],'gap_stop')
        self.assertEqual(fills[-1]['price'],80)

    def test_signal_next_session_and_atr_known(self):
        bars = [bar(i,100,101,89,90) for i in range(25)]
        signals,_ = signals_for({'X':bars},{'X':bars})
        self.assertTrue(signals)
        for day,cs in signals.items():
            self.assertLess(cs[0].signal_date,day)
            self.assertEqual(cs[0].atr,12)
