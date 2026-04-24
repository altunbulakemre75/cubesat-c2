"""
Bug #1: _parse_tle_epoch silently returns datetime.now() on parse failure.
Effect: malformed TLE → pass predictions for today instead of refusing
the input. User sees wildly wrong passes and thinks the whole system is broken.
Fix: raise ValueError; caller maps to HTTP 422.
"""

from datetime import datetime, timezone, timedelta

import pytest

from src.api.routes.satellites import _parse_tle_epoch


def test_valid_tle_line_parses_correct_epoch():
    # ISS TLE line 1, epoch 2026-113.61927547 (day 113 = Apr 23, 2026)
    line1 = "1 25544U 98067A   26113.61927547  .00009382  00000+0  17870-3 0  9991"
    epoch = _parse_tle_epoch(line1)
    assert epoch.year == 2026
    assert epoch.month == 4
    assert epoch.day == 23


def test_corrupt_tle_line_raises_instead_of_returning_now():
    """Previously this silently returned datetime.now(UTC), which meant a
    typo'd TLE would still be inserted with today's date and generate
    plausible-looking but wrong passes."""
    with pytest.raises(ValueError):
        _parse_tle_epoch("this is not a valid TLE line")


def test_short_tle_line_raises():
    with pytest.raises(ValueError):
        _parse_tle_epoch("1 25544")


def test_empty_tle_raises():
    with pytest.raises(ValueError):
        _parse_tle_epoch("")
