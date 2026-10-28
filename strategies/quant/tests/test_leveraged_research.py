import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import date,timedelta
from chang_research import Candidate
from leveraged_research import eligible_signals,MAP,stocks
from qbacktest import Bar


class LeveragedTests(unittest.TestCase):
    @unittest.skipUnless(list(Path('data/chang_20260915').glob('*.csv')), 'Requires downloaded 59-stock snapshot')
    def test_mapping_is_unique_and_in_universe(self):
        self.assertTrue(set(MAP)<=set(stocks()))
        self.assertEqual(len(MAP),len({r['etf'] for r in MAP.values()}))

    def test_prelisting_and_atr_warmup(self):
        bs=[Bar(date(2026,1,1)+timedelta(days=i),'X','US',100,102,98,100,1000) for i in range(25)]
        raw={bs[i].date:[Candidate('X','trend',bs[i-1].date,99)] for i in (1,19,20)}
        with patch('leveraged_research.make_signals',return_value=(raw,[])):
            es,ss,audit=eligible_signals({}, {'X':bs}, 'trend')
        self.assertEqual(list(es),[bs[20].date])
        self.assertEqual(es[bs[20].date][0].atr,4)
        self.assertEqual(ss[bs[20].date][0].atr,99)
        self.assertEqual(sum(r['reason']=='product_atr_warmup' for r in audit),2)


if __name__=='__main__':
    unittest.main()
