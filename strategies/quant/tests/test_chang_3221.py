import unittest
from datetime import date, timedelta
from chang_research import Candidate, indicators
from chang_3221 import breakeven, simulate
from qbacktest import Bar


class ChangTests(unittest.TestCase):
    def bar(self, n, o, h, l, c):
        return Bar(date(2026,1,5)+timedelta(days=n),'X','US',o,h,l,c,1000)

    def test_breakeven(self):
        self.assertAlmostEqual(breakeven(dict(invested=100000,net_sales=90000,shares=300),0),100/3)

    def test_partial_next_open_and_accounting(self):
        bars=[self.bar(0,100,101,99,100),self.bar(1,100,121,99,120),
              self.bar(2,121,132,120,131),self.bar(3,132,142,130,141),
              self.bar(4,142,144,140,143),self.bar(5,143,145,141,144)]
        sig={bars[0].date:[Candidate('X','trend',date(2026,1,4),5)]}
        stats,trades,fills,curve,_=simulate({'X':bars},sig)
        buys=[f for f in fills if f['side']=='buy']
        partial=[f for f in fills if f['reason'].startswith('take_profit')]
        self.assertEqual([f['date'] for f in partial],[str(bars[i].date) for i in (2,3,4)])
        self.assertEqual(sum(f['shares'] for f in partial),int(buys[0]['shares']*.7))
        self.assertAlmostEqual(sum(t['pnl'] for t in trades),curve[-1]['equity']-100000)

    def test_gap_stop(self):
        bars=[self.bar(0,100,101,99,100),self.bar(1,80,85,79,82)]
        sig={bars[0].date:[Candidate('X','trend',date(2026,1,4),5)]}
        _,_,fills,_,_=simulate({'X':bars},sig)
        self.assertEqual(fills[-1]['price'],80)
        self.assertEqual(fills[-1]['reason'],'gap_stop')

    def test_no_weekly_lookahead(self):
        bars=[self.bar(i,100,101,99,100) for i in range(330)]
        short=indicators(bars[:322])[-1]
        bars[-1]=self.bar(329,1000,1001,999,1000)
        full=indicators(bars)[321]
        self.assertEqual(short,full)
        self.assertEqual(full['weekly_ma'],100)


if __name__=='__main__':
    unittest.main()
