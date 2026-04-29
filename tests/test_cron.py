"""scheduler/cron.py 单元测试"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from scheduler.cron import cron_match, next_run_after, validate_cron


class TestValidateCron:
    def test_valid_every_minute(self):
        ok, msg = validate_cron("* * * * *")
        assert ok is True
        assert msg == ""

    def test_valid_specific_minute(self):
        ok, msg = validate_cron("5 * * * *")
        assert ok is True

    def test_valid_range(self):
        ok, msg = validate_cron("0 9-17 * * *")
        assert ok is True

    def test_valid_list(self):
        ok, msg = validate_cron("0 9,12,18 * * *")
        assert ok is True

    def test_valid_step(self):
        ok, msg = validate_cron("*/15 * * * *")
        assert ok is True

    def test_valid_weekday(self):
        ok, msg = validate_cron("0 9 * * 1-5")
        assert ok is True

    def test_invalid_wrong_field_count(self):
        ok, msg = validate_cron("* * *")
        assert ok is False
        assert "5 fields" in msg

    def test_invalid_out_of_range(self):
        ok, msg = validate_cron("60 * * * *")
        assert ok is False
        assert "range" in msg.lower()

    def test_invalid_step_zero(self):
        ok, msg = validate_cron("*/0 * * * *")
        assert ok is False
        assert "step" in msg.lower()


class TestCronMatch:
    def test_every_minute_matches(self):
        dt = datetime(2024, 7, 15, 14, 30)
        assert cron_match("* * * * *", dt) is True

    def test_specific_minute_match(self):
        dt = datetime(2024, 7, 15, 14, 5)
        assert cron_match("5 * * * *", dt) is True

    def test_specific_minute_no_match(self):
        dt = datetime(2024, 7, 15, 14, 6)
        assert cron_match("5 * * * *", dt) is False

    def test_hour_range_match(self):
        dt = datetime(2024, 7, 15, 10, 0)
        assert cron_match("0 9-11 * * *", dt) is True

    def test_hour_range_no_match(self):
        dt = datetime(2024, 7, 15, 12, 0)
        assert cron_match("0 9-11 * * *", dt) is False

    def test_weekday_match(self):
        # 2024-07-15 is a Monday (weekday=0 in Python, 1 in cron dow)
        dt = datetime(2024, 7, 15, 9, 0)
        assert cron_match("0 9 * * 1", dt) is True

    def test_weekday_no_match(self):
        dt = datetime(2024, 7, 15, 9, 0)
        assert cron_match("0 9 * * 0", dt) is False  # Sunday

    def test_month_match(self):
        dt = datetime(2024, 7, 15, 9, 0)
        assert cron_match("0 9 * 7 *", dt) is True

    def test_month_no_match(self):
        dt = datetime(2024, 8, 15, 9, 0)
        assert cron_match("0 9 * 7 *", dt) is False

    def test_step_every_15_min(self):
        dt1 = datetime(2024, 7, 15, 10, 0)
        dt2 = datetime(2024, 7, 15, 10, 15)
        dt3 = datetime(2024, 7, 15, 10, 7)
        assert cron_match("*/15 * * * *", dt1) is True
        assert cron_match("*/15 * * * *", dt2) is True
        assert cron_match("*/15 * * * *", dt3) is False

    def test_invalid_expr_returns_false(self):
        dt = datetime(2024, 7, 15, 14, 30)
        assert cron_match("not a cron", dt) is False


class TestNextRunAfter:
    def test_every_minute_next(self):
        dt = datetime(2024, 7, 15, 10, 0)
        nxt = next_run_after("* * * * *", dt)
        assert nxt is not None
        assert nxt.minute == 1
        assert nxt.hour == 10

    def test_specific_minute(self):
        dt = datetime(2024, 7, 15, 10, 5)
        nxt = next_run_after("10 * * * *", dt)
        assert nxt is not None
        assert nxt.hour == 10
        assert nxt.minute == 10

    def test_invalid_returns_none(self):
        dt = datetime(2024, 7, 15, 10, 0)
        assert next_run_after("bad expr", dt) is None

    def test_hour_boundary(self):
        dt = datetime(2024, 7, 15, 23, 58)
        nxt = next_run_after("0 * * * *", dt)
        assert nxt is not None
        assert nxt.day == 16
        assert nxt.hour == 0
        assert nxt.minute == 0

    def test_month_boundary(self):
        dt = datetime(2024, 7, 31, 23, 59)
        nxt = next_run_after("0 0 1 * *", dt)
        assert nxt is not None
        assert nxt.month == 8
        assert nxt.day == 1

    def test_weekday_sched(self):
        # Every Monday at 9am
        dt = datetime(2024, 7, 15, 9, 0)  # Monday
        nxt = next_run_after("0 9 * * 1", dt)
        assert nxt is not None
        # Next Monday
        assert nxt.weekday() == 0  # Monday
        assert nxt.day > 15
