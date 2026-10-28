import unittest
from datetime import date
from unittest.mock import patch
from tests.test_b1_research import bar
from b1_research import signals_for
from chang_research import Candidate
from chang_3221 import simulate


class TimeExitTests(unittest.TestCase):
    def run_case(self,bars,day):
        sig = {bars[0].date:[Candidate('X','b1_j12',date(2026,1,4),5)]}
        return simulate({'X':bars},sig,exit_rule='body5',profit_check_day=day,price_stop=False)

    def test_day2_counts_entry_and_executes_next_open(self):
        bars=[bar(0),bar(1,99,101,98,100),bar(2,90,91,89,90)]
        _,trades,fills,_,_=self.run_case(bars,2)
        self.assertEqual(fills[-1]['date'],str(bars[2].date))
        self.assertEqual(fills[-1]['price'],90)
        self.assertEqual(trades[0]['reason'],'not_profitable_day_2')

    def test_no_price_stop_and_day3(self):
        bars=[bar(0),bar(1,80,81,79,80),bar(2,80,81,79,80),bar(3,75,76,74,75)]
        _,trades,fills,_,_=self.run_case(bars,3)
        self.assertEqual(len(fills),2)
        self.assertEqual(fills[-1]['date'],str(bars[3].date))
        self.assertEqual(trades[0]['reason'],'not_profitable_day_3')

    def test_profitable_at_deadline_not_rechecked(self):
        bars=[bar(0),bar(1,101,102,100,101),bar(2,80,81,79,80),bar(3,75,76,74,75)]
        _,trades,_,_,_=self.run_case(bars,2)
        self.assertEqual(trades[0]['reason'],'end_of_test')

    def test_realized_profits_included(self):
        bars=[bar(0,100,110,99,110),bar(1,120,121,119,120),bar(2,95,96,94,95),bar(3,95,96,94,95)]
        _,trades,fills,_,_=self.run_case(bars,3)
        self.assertEqual(fills[1]['reason'],'body5_stage_1')
        self.assertEqual(trades[0]['reason'],'end_of_test')

    def test_ma60_strict_filter(self):
        bars=[bar(i) for i in range(65)]
        with patch('b1_research.kdj',return_value=[0]*len(bars)):
            signals,audit=signals_for({'X':bars},{'X':bars},above_ma60=True)
        self.assertFalse(signals)
        self.assertTrue(all(r['reason']=='not_above_ma60' for r in audit))
