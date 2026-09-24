"""Unit tests for parse_schedule format handling (pure parser, no DB/LLM)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import parse_schedule  # noqa: E402


class ParseScheduleRangeTests(unittest.TestCase):
    def test_hourly_range_expands_inclusive(self):
        self.assertEqual(
            parse_schedule("06:00-18:00/1h"),
            (
                "daily",
                "06:00,07:00,08:00,09:00,10:00,11:00,12:00,13:00,14:00,15:00,16:00,17:00,18:00",
            ),
        )

    def test_two_hour_step(self):
        self.assertEqual(
            parse_schedule("06:00-18:00/2h"),
            ("daily", "06:00,08:00,10:00,12:00,14:00,16:00,18:00"),
        )

    def test_uneven_step_stops_within_range(self):
        self.assertEqual(
            parse_schedule("06:10-18:00/5h"),
            ("daily", "06:10,11:10,16:10"),
        )

    def test_minute_step(self):
        self.assertEqual(
            parse_schedule("09:00-10:00/30m"),
            ("daily", "09:00,09:30,10:00"),
        )

    def test_start_equals_end(self):
        self.assertEqual(parse_schedule("08:00-08:00/1h"), ("daily", "08:00"))

    def test_end_before_start_rejected(self):
        with self.assertRaises(ValueError):
            parse_schedule("18:00-06:00/1h")

    def test_out_of_range_time_rejected(self):
        with self.assertRaises(ValueError):
            parse_schedule("25:00-18:00/1h")
        with self.assertRaises(ValueError):
            parse_schedule("06:00-18:99/1h")

    def test_bad_unit_rejected(self):
        with self.assertRaises(ValueError):
            parse_schedule("06:00-18:00/1d")

    def test_case_insensitive_unit(self):
        self.assertEqual(
            parse_schedule("06:00-07:00/1H"),
            ("daily", "06:00,07:00"),
        )


class ParseScheduleRegressionTests(unittest.TestCase):
    def test_interval(self):
        self.assertEqual(parse_schedule("6h"), ("interval", "21600"))
        self.assertEqual(parse_schedule("30m"), ("interval", "1800"))

    def test_daily_single(self):
        self.assertEqual(parse_schedule("08:00"), ("daily", "08:00"))

    def test_daily_list(self):
        self.assertEqual(parse_schedule("08:00,12:00"), ("daily", "08:00,12:00"))

    def test_multi_daily(self):
        self.assertEqual(
            parse_schedule("mon-08:00,wed-12:00"),
            ("multi_daily", "mon-08:00,wed-12:00"),
        )


if __name__ == "__main__":
    unittest.main()
