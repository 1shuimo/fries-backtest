"""Offline regression tests: stale inputs must never produce report artifacts."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'monitor'))
import daily_monitor as dm


class DailyMonitorFreshness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pool = {f'S{i}': 'sector' for i in range(10)}
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'account_equity_usd': 100000}))
        self.outputs = [self.root / name for name in ('report.md', 'summary.txt', 'email.html', 'email.txt')]

    def bars(self, symbol, dates=('2026-11-04',)):
        path = self.root / f'{symbol}.csv'
        path.write_text('Date,Open,High,Low,Close,Volume\n' + ''.join(
            f'{d},100,101,99,100,1000\n' for d in dates))
        return path

    def run_monitor(self, *args, positions=(), outputs=True):
        argv = ['daily_monitor', '--data', str(self.root), '--config', str(self.config)]
        if outputs:
            for flag, path in zip(('--out', '--summary-out', '--html-out', '--text-out'), self.outputs):
                argv += [flag, str(path)]
        argv += list(args)
        stdout, stderr = io.StringIO(), io.StringIO()
        email_view = Mock()
        email_view.subject.return_value = 'subject'
        email_view.build_html.return_value = '<p>report</p>'
        email_view.build_text.return_value = 'report'
        with patch.object(sys, 'argv', argv), patch.object(dm, 'load_pool', return_value=dict(self.pool)), \
                patch.object(dm, 'read_positions', return_value=list(positions)), \
                patch.dict(sys.modules, {'email_view': email_view}), \
                patch.dict('os.environ', {'ANTHROPIC_API_KEY': ''}), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                dm.main()
            except SystemExit as exc:
                code = exc.code
            else:
                code = 0
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_blocked(self, result, code=4):
        self.assertEqual(result[0], code, result[2])
        self.assertEqual(result[1], '')
        self.assertFalse(any(path.exists() for path in self.outputs))

    def holding(self, symbol, qty=1):
        return dm.Position(symbol, date(2026, 11, 3), 100, 1, qty)

    def test_missing_all_files_is_not_fresh(self):
        self.assert_blocked(self.run_monitor('--require-date', '2026-11-04'))

    def test_empty_pool_is_not_fresh(self):
        self.pool = {}
        self.assert_blocked(self.run_monitor('--require-date', '2026-11-04'))

    def test_single_existing_file_does_not_change_denominator(self):
        self.bars('S0')
        result = self.run_monitor('--require-date', '2026-11-04')
        self.assert_blocked(result)
        self.assertIn('1/10', result[2])

    def test_header_only_files_are_not_fresh(self):
        for symbol in self.pool:
            self.bars(symbol, ())
        self.assert_blocked(self.run_monitor('--require-date', '2026-11-04'))

    def test_malformed_files_fail_closed(self):
        for symbol in self.pool:
            self.bars(symbol).write_text('Date,Open\n2026-11-04,100\n')
        self.assert_blocked(self.run_monitor('--require-date', '2026-11-04'))

    def test_stale_pool_is_not_fresh(self):
        for symbol in self.pool:
            self.bars(symbol, ('2026-11-03',))
        self.assert_blocked(self.run_monitor('--require-date', '2026-11-04'))

    def test_ninety_percent_is_sufficient_for_unheld_pool(self):
        for symbol in list(self.pool)[:9]:
            self.bars(symbol)
        result = self.run_monitor('--require-date', '2026-11-04', '--check-only')
        self.assertEqual(result[0], 0, result[2])
        self.assertIn('9/10', result[2])
        self.assertEqual(result[1], '')
        self.assertFalse(any(path.exists() for path in self.outputs))

    def test_below_ninety_percent_is_blocked(self):
        for symbol in list(self.pool)[:8]:
            self.bars(symbol)
        self.assert_blocked(self.run_monitor('--require-date', '2026-11-04'))

    def test_stale_holding_blocks_otherwise_sufficient_pool(self):
        for symbol in list(self.pool)[:9]:
            self.bars(symbol)
        for dates in ((), ('2026-11-03',)):
            with self.subTest(dates=dates):
                self.bars('S9', dates)
                result = self.run_monitor('--require-date', '2026-11-04', '--check-only',
                                          positions=[self.holding('S9')])
                self.assert_blocked(result)
                self.assertIn('Holdings missing bars', result[2])

    def test_missing_holding_outside_pool_is_blocked(self):
        for symbol in self.pool:
            self.bars(symbol)
        self.assert_blocked(self.run_monitor('--require-date', '2026-11-04',
                                            positions=[self.holding('HELD')]))

    def test_closed_position_does_not_require_fresh_bar(self):
        for symbol in list(self.pool)[:9]:
            self.bars(symbol)
        result = self.run_monitor('--require-date', '2026-11-04', '--check-only',
                                  positions=[self.holding('S9', qty=0)])
        self.assertEqual(result[0], 0, result[2])

    def test_mismatching_explicit_date_is_rejected(self):
        self.assert_blocked(self.run_monitor('--require-date', '2026-11-04', '--asof', '2026-11-03'), code=2)

    def test_require_date_controls_report_without_benchmark_files(self):
        for symbol in self.pool:
            self.bars(symbol, ('2026-11-04', '2026-11-05'))
        result = self.run_monitor('--require-date', '2026-11-04')
        self.assertEqual(result[0], 0, result[2])
        self.assertIn('# 盯盘提醒 2026-11-04', result[1])
        self.assertTrue(all(path.exists() for path in self.outputs))

    def test_matching_explicit_date_is_allowed(self):
        for symbol in self.pool:
            self.bars(symbol)
        result = self.run_monitor('--require-date', '2026-11-04', '--asof', '2026-11-04', '--check-only')
        self.assertEqual(result[0], 0, result[2])

    def test_holidays_and_weekends_skip_without_benchmark_files(self):
        for day in ('2026-11-07', '2026-11-26'):
            with self.subTest(day=day):
                self.assert_blocked(self.run_monitor('--require-date', day), code=3)

    def test_no_date_and_empty_benchmarks_fail_cleanly(self):
        self.bars('SPY', ())
        self.bars('NVDA', ())
        self.assert_blocked(self.run_monitor())

    def test_stale_holding_blocks_even_without_require_date(self):
        self.assert_blocked(self.run_monitor('--asof', '2026-11-04', positions=[self.holding('S0')]))


if __name__ == '__main__':
    unittest.main()
