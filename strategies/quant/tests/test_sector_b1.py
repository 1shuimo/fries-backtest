import unittest
from pathlib import Path
from datetime import date, timedelta
from unittest.mock import patch
from dataclasses import replace
from sector_b1 import walk_bar,simulate,SECTOR,GROUPS
from leveraged_research import stocks
from chang_research import Candidate
from qbacktest import Bar


def bar(i,o=100,h=102,l=99,c=101):
    return Bar(date(2026,1,5)+timedelta(days=i),'X','US',o,h,l,c,1000)


class SectorTests(unittest.TestCase):
    def position(self):
        return dict(cost=100.1,realized=0,qty=1,stop=90,armed=False)

    @unittest.skipUnless(list(Path('data/chang_20260915').glob('*.csv')), 'Requires downloaded 59-stock snapshot')
    def test_mapping_unique_complete(self):
        self.assertEqual(set(SECTOR),set(stocks()))
        self.assertEqual(len(SECTOR),sum(len(ss.split()) for ss in GROUPS.values()))

    def test_intraday_order_sensitivity(self):
        first=self.position()
        hit=walk_bar(first,bar(0),'high_first')
        self.assertAlmostEqual(hit[0],100.1/.999)
        self.assertEqual(hit[1],'breakeven_stop')
        second=self.position()
        self.assertIsNone(walk_bar(second,bar(0),'low_first'))
        self.assertTrue(second['armed'])

    def test_gap_uses_open_not_stop(self):
        p=self.position()
        p.update(stop=100.1/.999,armed=True)
        hit=walk_bar(p,bar(0,80,85,79,82),'high_first')
        self.assertEqual(hit,(80,'gap_stop'))

    def test_initial_stop_can_happen_before_profit(self):
        hit=walk_bar(self.position(),bar(0,100,102,80,101),'low_first')
        self.assertEqual(hit,(90,'initial_atr_stop'))

    def test_sector_buy_cap_and_cash_conservation(self):
        names=['AAPL','DELL','HPE']
        bs={s:[replace(bar(i,100,100,100,100),symbol=s) for i in range(3)] for s in names}
        sig={date(2026,1,5):[Candidate(s,'b1',date(2026,1,4),.1) for s in names]}
        summary,trades,fills,curve,exposure,*_=simulate(bs,sig)
        self.assertLessEqual(max(r['weight'] for r in exposure),.30+1e-9)
        self.assertAlmostEqual(sum(t['pnl'] for t in trades),curve[-1]['equity']-100000)
        self.assertLessEqual(sum(f['qty']*f['price'] for f in fills if f['side']=='buy'),30000)

    def test_passive_breach_reduced_next_open(self):
        names=['AAPL','DELL']
        bs={s:[replace(bar(0,100,100,100,100),symbol=s),
               replace(bar(1,200,200,200,200),symbol=s),
               replace(bar(2,200,200,200,200),symbol=s)] for s in names}
        sig={date(2026,1,5):[Candidate(s,'b1',date(2026,1,4),.1) for s in names]}
        _,_,fills,_,exposure,*_=simulate(bs,sig)
        trims=[f for f in fills if f['reason']=='sector30_trim']
        self.assertTrue(trims)
        self.assertTrue(all(f['date']==str(date(2026,1,7)) for f in trims))
        day3=[r['weight'] for r in exposure if r['date']=='2026-01-07']
        self.assertLessEqual(max(day3),.30+1e-9)

    def test_close_only_ignores_intraday_profit_and_stop(self):
        bs=[replace(bar(0,100,120,70,100),symbol='AAPL'),
            replace(bar(1,100,100,95,99),symbol='AAPL'),
            replace(bar(2,80,85,79,82),symbol='AAPL')]
        sig={bs[0].date:[Candidate('AAPL','b1',date(2026,1,4),5)]}
        _,trades,fills,*_=simulate({'AAPL':bs},sig,2,'close_only')
        self.assertEqual(len(fills),2)
        self.assertEqual(trades[0]['reason'],'day2_not_profitable')
        self.assertEqual(fills[-1]['price'],80)

    def test_close_profit_then_loss_executes_next_open(self):
        bs=[replace(bar(0,100,102,99,101),symbol='AAPL'),
            replace(bar(1,101,102,98,99),symbol='AAPL'),
            replace(bar(2,80,85,79,82),symbol='AAPL')]
        sig={bs[0].date:[Candidate('AAPL','b1',date(2026,1,4),5)]}
        _,trades,fills,*_=simulate({'AAPL':bs},sig,3,'close_only')
        self.assertEqual(trades[0]['reason'],'close_breakeven_exit')
        self.assertEqual(fills[-1]['date'],str(bs[2].date))
        self.assertEqual(fills[-1]['price'],80)

    def test_close_atr_stop_executes_at_open(self):
        bs=[replace(bar(0,100,101,80,85),symbol='AAPL'),
            replace(bar(1,75,80,70,79),symbol='AAPL')]
        sig={bs[0].date:[Candidate('AAPL','b1',date(2026,1,4),5)]}
        _,trades,fills,*_=simulate({'AAPL':bs},sig,2,'close_only')
        self.assertEqual(trades[0]['reason'],'close_atr_stop')
        self.assertEqual(fills[-1]['price'],75)

    def test_azn_unit_conversion(self):
        from watchlist_backtest import normalize_azn
        bs=[replace(bar(0,90,91,89,90),date=date(2026,1,30)),
            replace(bar(1,180,182,178,180),date=date(2026,2,2))]
        adjusted=normalize_azn(bs)
        self.assertEqual(adjusted[0].close,adjusted[1].close)
        self.assertEqual(adjusted[0].volume,bs[0].volume/2)
        self.assertEqual(bs[0].close,90)
