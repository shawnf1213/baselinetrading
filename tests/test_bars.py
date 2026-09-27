import datetime as dt
import math

import pytest

from baselinetrading.bars import ET, Bar, DataUnavailable, check_fresh, validate_minute_bars
from tests.fakes import minute_bars, session

DAY = dt.date(2020, 3, 2)
START = dt.datetime(2020, 3, 2, 9, 30, tzinfo=ET)
END = dt.datetime(2020, 3, 2, 10, 0, tzinfo=ET)


def test_a_complete_window_passes_and_comes_back_sorted():
    bars = minute_bars(START, END)
    result = validate_minute_bars(list(reversed(bars)), start=START, end=END, max_missing=0)
    assert len(result) == 30
    assert [b.start for b in result] == sorted(b.start for b in bars)


def test_one_missing_minute_fails_when_none_are_allowed():
    bars = minute_bars(START, END, skip={"09:59"})
    with pytest.raises(DataUnavailable, match="1 of 30 minutes missing.*09:59"):
        validate_minute_bars(bars, start=START, end=END, max_missing=0)
    assert len(validate_minute_bars(bars, start=START, end=END, max_missing=1)) == 29


def test_an_empty_window_is_not_data():
    with pytest.raises(DataUnavailable, match="30 of 30 minutes missing"):
        validate_minute_bars([], start=START, end=END, max_missing=0)


def test_duplicates_are_refused():
    bars = minute_bars(START, END)
    with pytest.raises(DataUnavailable, match="duplicate"):
        validate_minute_bars(bars + bars[:1], start=START, end=END, max_missing=0)


def test_bars_outside_the_window_are_refused():
    bars = minute_bars(START, END + dt.timedelta(minutes=1))
    with pytest.raises(DataUnavailable, match="outside the requested window"):
        validate_minute_bars(bars, start=START, end=END, max_missing=0)


@pytest.mark.parametrize(
    "bar",
    [
        Bar(START, 10.0, 9.0, 11.0, 10.0, 100.0),  # high below low
        Bar(START, 10.0, 10.5, 9.5, 12.0, 100.0),  # close above high
        Bar(START, math.nan, 10.5, 9.5, 10.0, 100.0),
        Bar(START, 0.0, 10.5, 0.0, 10.0, 100.0),
        Bar(START, 10.0, 10.5, 9.5, 10.0, -1.0),
        Bar(START.replace(tzinfo=None), 10.0, 10.5, 9.5, 10.0, 100.0),
        Bar(START + dt.timedelta(seconds=30), 10.0, 10.5, 9.5, 10.0, 100.0),
    ],
)
def test_malformed_bars_are_refused(bar):
    with pytest.raises(DataUnavailable, match="bad bar"):
        validate_minute_bars([bar], start=START, end=START + dt.timedelta(minutes=1), max_missing=0)


def test_fresh_bars_pass():
    bars = tuple(minute_bars(START, END))
    check_fresh(bars, now=END + dt.timedelta(seconds=20), max_staleness=dt.timedelta(seconds=90))


def test_stale_bars_are_refused():
    bars = tuple(minute_bars(START, END))
    with pytest.raises(DataUnavailable, match="stale"):
        check_fresh(bars, now=END + dt.timedelta(seconds=91), max_staleness=dt.timedelta(seconds=90))


def test_bars_from_the_future_mean_the_clock_is_wrong():
    bars = tuple(minute_bars(START, END))
    with pytest.raises(DataUnavailable, match="check the clock"):
        check_fresh(bars, now=END - dt.timedelta(minutes=2), max_staleness=dt.timedelta(seconds=90))


def test_no_bars_are_never_fresh():
    with pytest.raises(DataUnavailable):
        check_fresh((), now=END, max_staleness=dt.timedelta(seconds=90))


def test_half_days_are_not_full_days():
    assert session(DAY).is_full_day
    assert not session(DAY, close=dt.time(13, 0)).is_full_day
