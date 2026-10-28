import csv
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from fetch_data import baostock_code, futu_market
from qbacktest import Bar, Config, build_signals, load_bars, run_backtest


def make_bar(day, symbol="TEST", market="US", open_=100.0, high=101.0, low=99.0, close=100.0, volume=1000):
    return Bar(day, symbol, market, open_, high, low, close, volume)


class SignalTests(unittest.TestCase):
    def test_futu_market_mapping(self):
        self.assertEqual(futu_market("US.AAPL"), "US")
        self.assertEqual(futu_market("SH.600000"), "CN")
        self.assertEqual(futu_market("SZ.000001"), "CN")

    def test_baostock_code_mapping(self):
        self.assertEqual(baostock_code("600519"), "sh.600519")
        self.assertEqual(baostock_code("000001"), "sz.000001")

    def test_load_bars_accepts_normalized_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["date", "symbol", "market", "open", "high", "low", "close", "volume"])
                writer.writeheader()
                writer.writerow({"date": "2024-01-02", "symbol": "AAPL", "market": "US", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000})

            grouped = load_bars(str(path))

        self.assertEqual(grouped["AAPL"][0].close, 101)

    def test_load_bars_accepts_adjustment_rounding_noise(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["date", "symbol", "open", "high", "low", "close", "volume"])
                writer.writeheader()
                writer.writerow({"date": "2024-01-02", "symbol": "TEST", "open": 100, "high": 101, "low": 99, "close": 101.00000000000001, "volume": 1000})

            grouped = load_bars(str(path))

        self.assertEqual(len(grouped["TEST"]), 1)

    def test_ep_signal_enters_on_next_bar_not_signal_bar(self):
        start = date(2024, 1, 1)
        bars = []
        for i in range(63):
            bars.append(make_bar(start + timedelta(days=i), close=100 + i * 0.1))
        signal_day = start + timedelta(days=63)
        bars.append(make_bar(signal_day, open_=120, high=124, low=119, close=123, volume=5000))
        entry_day = start + timedelta(days=64)
        bars.append(make_bar(entry_day, open_=121, high=124, low=119, close=123, volume=1500))

        signals = build_signals({"TEST": bars}, "ep", Config())

        self.assertNotIn(signal_day, signals)
        self.assertEqual(signals[entry_day][0].signal_date, signal_day)

    def test_breakout_requires_confirmation_then_enters_next_bar(self):
        start = date(2024, 1, 1)
        bars = []
        for i in range(63):
            close = 50 + i * 0.5
            bars.append(make_bar(start + timedelta(days=i), open_=close - 0.2, high=close + 0.5, low=close - 0.5, close=close))
        for i in range(20):
            close = 80 + i * 0.2
            bars.append(make_bar(start + timedelta(days=63 + i), open_=close - 0.1, high=close + 0.4, low=close - 0.4, close=close))
        signal_day = start + timedelta(days=83)
        bars.append(make_bar(signal_day, open_=84, high=90, low=83.5, close=89, volume=2000))
        entry_day = start + timedelta(days=84)
        bars.append(make_bar(entry_day, open_=90, high=92, low=89, close=91))

        signals = build_signals({"TEST": bars}, "breakout", Config())

        self.assertNotIn(signal_day, signals)
        self.assertEqual(signals[entry_day][0].signal_date, signal_day)

    def test_ep_full_day_volume_is_only_used_for_next_day_entry(self):
        start = date(2024, 1, 1)
        bars = [make_bar(start + timedelta(days=i)) for i in range(63)]
        bars.append(make_bar(start + timedelta(days=63), open_=112, high=116, low=111, close=115, volume=2999))
        bars.append(make_bar(start + timedelta(days=64), open_=116, high=117, low=114, close=116))
        self.assertEqual(build_signals({"TEST": bars}, "ep", Config()), {})


class ExecutionTests(unittest.TestCase):
    def test_position_size_respects_risk_budget(self):
        start = date(2024, 1, 1)
        bars = [make_bar(start + timedelta(days=i), close=100) for i in range(63)]
        bars.append(make_bar(start + timedelta(days=63), open_=112, high=116, low=110, close=115, volume=5000))
        bars.append(make_bar(start + timedelta(days=64), open_=116, high=118, low=115, close=117))
        bars.append(make_bar(start + timedelta(days=65), open_=117, high=119, low=116, close=118))

        trades, _ = run_backtest({"TEST": bars}, "ep", Config(cost_bps=0, partial_after_days=99))

        self.assertEqual(len(trades), 1)
        self.assertLessEqual(trades[0].shares * trades[0].entry_price, 20_000)

    def test_cn_does_not_stop_on_entry_day(self):
        start = date(2024, 1, 1)
        bars = [make_bar(start + timedelta(days=i), market="CN") for i in range(63)]
        bars.append(make_bar(start + timedelta(days=63), market="CN", open_=112, high=116, low=110, close=115, volume=5000))
        bars.append(make_bar(start + timedelta(days=64), market="CN", open_=116, high=118, low=100, close=117))
        bars.append(make_bar(start + timedelta(days=65), market="CN", open_=117, high=118, low=116, close=117))

        trades, _ = run_backtest({"TEST": bars}, "ep", Config(cost_bps=0, partial_after_days=99))

        self.assertEqual(trades[0].reason, "end_of_data")


if __name__ == "__main__":
    unittest.main()
