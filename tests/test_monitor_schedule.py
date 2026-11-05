import sys
import unittest
import tempfile
from unittest.mock import patch
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from monitor_schedule import resolve, main


class ScheduleTests(unittest.TestCase):
    def at(self, value, asof=''):
        return resolve(datetime.fromisoformat(value), asof)

    def test_winter_and_summer(self):
        for timestamp in ('2026-11-04T22:07:00+00:00', '2026-07-06T22:07:00+00:00'):
            self.assertEqual(str(self.at(timestamp)[0]), timestamp[:10])

    def test_utc_midnight_and_new_york_midnight(self):
        for timestamp in ('2026-11-05T00:37:00+00:00', '2026-11-05T06:37:00+00:00'):
            self.assertEqual(self.at(timestamp), (datetime(2026, 11, 4).date(), ''))

    def test_friday_recovery_on_saturday(self):
        self.assertEqual(str(self.at('2026-11-07T06:37:00+00:00')[0]), '2026-11-06')

    def test_market_open_cutoff(self):
        self.assertFalse(self.at('2026-11-05T14:29:00+00:00')[1])
        self.assertTrue(self.at('2026-11-05T14:30:00+00:00')[1])
        self.assertFalse(self.at('2026-11-05T21:00:00+00:00')[1])

    def test_dst_boundaries(self):
        self.assertEqual(str(self.at('2026-03-09T04:30:00+00:00')[0]), '2026-03-08')
        self.assertEqual(str(self.at('2026-11-02T05:30:00+00:00')[0]), '2026-11-01')

    def test_manual_history_and_invalid_dates(self):
        self.assertEqual(str(self.at('2026-11-05T15:00:00+00:00', '2026-11-03')[0]), '2026-11-03')
        for value in ('2026-11-05', '2026-13-40', '$(echo hacked)', '20261103'):
            with self.assertRaises(ValueError):
                self.at('2026-11-05T15:00:00+00:00', value)

    def test_recheck_rejects_crossed_open_or_changed_session(self):
        for result in ((datetime(2026, 11, 4).date(), 'outside delivery window'),
                       (datetime(2026, 11, 5).date(), '')):
            with tempfile.NamedTemporaryFile() as out, patch('monitor_schedule.resolve', return_value=result):
                with patch.object(sys, 'argv', ['monitor_schedule', '--output', out.name,
                                               '--check-date', '2026-11-04']):
                    with self.assertRaises(SystemExit) as error:
                        main()
                    self.assertEqual(error.exception.code, 2)
                    self.assertEqual(Path(out.name).read_text(), '')
