import unittest
from compare_stock_strategies import concentration


class ConcentrationTests(unittest.TestCase):
    def test_net_by_stock_before_positive_denominator(self):
        trades=[dict(symbol='A',pnl=100),dict(symbol='A',pnl=-70),
                dict(symbol='B',pnl=20),dict(symbol='C',pnl=-40)]
        result,rows=concentration(trades)
        self.assertEqual(result['net_pnl'],10)
        self.assertEqual(result['positive_stock_pnl'],50)
        self.assertEqual(result['top1_positive_share'],.6)
        self.assertEqual(result['top1_net_share'],3)
        self.assertEqual(result['profitable_symbols'],2)
        self.assertEqual(result['top_symbols'],['A','B'])
        self.assertAlmostEqual(result['net_without_top3_fixed_trades'],-.0004)

    def test_no_winners(self):
        result,_=concentration([dict(symbol='A',pnl=-1)])
        self.assertIsNone(result['top3_positive_share'])
        self.assertEqual(result['top_symbols'],[])
