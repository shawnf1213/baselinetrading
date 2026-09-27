import datetime as dt
import json

import pytest

from baselinetrading.bars import ET, Bar, DataUnavailable, Session
from baselinetrading.config import DEFAULT_CONFIG_PATH, load_config
from baselinetrading.market_data import HoldoutLocked, MarketData
from tests.fakes import FakeFetcher, minute_bars, session

CONFIG = load_config(DEFAULT_CONFIG_PATH, today=dt.date(2026, 9, 28))
PAST = session(dt.date(2020, 3, 2))  # in-sample, a Monday
PAST_PREVIOUS = session(dt.date(2020, 2, 28))  # the Friday before
TODAY = session(dt.date(2026, 9, 28))  # after the holdout
HOLDOUT_DAY = session(dt.date(2024, 5, 1))


def at(day_session: Session, hhmm: str, seconds: int = 0) -> dt.datetime:
    hour, minute = map(int, hhmm.split(":"))
    return dt.datetime.combine(day_session.date, dt.time(hour, minute, seconds), ET)


def market(fetcher, tmp_path, *, now=None, holdout_unlocked=False, sleeps=None):
    return MarketData(
        fetcher,
        CONFIG,
        cache_dir=tmp_path,
        now=lambda: now or at(TODAY, "15:30", 20),
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
        holdout_unlocked=holdout_unlocked,
    )


def cache_files(tmp_path):
    return list(tmp_path.rglob("*.json"))


# --- past sessions (research) -------------------------------------------------------


def test_past_session_is_fetched_validated_and_cached(tmp_path):
    fetcher = FakeFetcher([PAST])
    data = market(fetcher, tmp_path)
    bars = data.minute_bars("SPY", PAST, at(PAST, "09:30"), at(PAST, "10:00"), feed="sip")
    assert len(bars) == 30
    assert len(cache_files(tmp_path)) == 1
    again = data.minute_bars("SPY", PAST, at(PAST, "15:30"), at(PAST, "15:55"), feed="sip")
    assert len(again) == 25
    assert fetcher.minute_calls == 1  # second read came from the cache


def test_a_failed_fetch_is_retried_then_refused_and_never_cached(tmp_path):
    fetcher = FakeFetcher([PAST])
    fetcher.failures_before_success = 99
    sleeps = []
    with pytest.raises(DataUnavailable, match="failed after 3 attempts.*ConnectionError"):
        market(fetcher, tmp_path, sleeps=sleeps).minute_bars(
            "SPY", PAST, at(PAST, "09:30"), at(PAST, "10:00"), feed="sip"
        )
    assert fetcher.minute_calls == 3
    assert sleeps == [1, 2]
    assert cache_files(tmp_path) == []


def test_a_transient_failure_recovers(tmp_path):
    fetcher = FakeFetcher([PAST])
    fetcher.failures_before_success = 1
    bars = market(fetcher, tmp_path).minute_bars("SPY", PAST, at(PAST, "09:30"), at(PAST, "10:00"), feed="sip")
    assert len(bars) == 30


def test_an_empty_response_for_a_trading_day_is_a_failure(tmp_path):
    fetcher = FakeFetcher([PAST])
    fetcher.minute_override = []
    with pytest.raises(DataUnavailable, match="no SPY sip bars returned"):
        market(fetcher, tmp_path).minute_bars("SPY", PAST, at(PAST, "09:30"), at(PAST, "10:00"), feed="sip")
    assert cache_files(tmp_path) == []


def test_malformed_bars_are_refused_and_not_cached(tmp_path):
    fetcher = FakeFetcher([PAST])
    bad = Bar(at(PAST, "09:30").astimezone(dt.timezone.utc), 10.0, 9.0, 11.0, 10.0, 1.0)
    fetcher.minute_override = [bad]
    with pytest.raises(DataUnavailable, match="bad bar"):
        market(fetcher, tmp_path).minute_bars("SPY", PAST, at(PAST, "09:30"), at(PAST, "10:00"), feed="sip")
    assert cache_files(tmp_path) == []


def test_gaps_only_matter_inside_the_requested_window(tmp_path):
    fetcher = FakeFetcher([PAST])
    fetcher.skip = {"12:00"}
    data = market(fetcher, tmp_path)
    assert len(data.minute_bars("SPY", PAST, at(PAST, "09:30"), at(PAST, "10:00"), feed="sip")) == 30
    with pytest.raises(DataUnavailable, match="12:00"):
        data.minute_bars("SPY", PAST, at(PAST, "11:30"), at(PAST, "12:30"), feed="sip")


def test_gaps_are_checked_on_cache_reads_too(tmp_path):
    fetcher = FakeFetcher([PAST])
    fetcher.skip = {"09:45"}
    market(fetcher, tmp_path).minute_bars("SPY", PAST, at(PAST, "15:00"), at(PAST, "15:30"), feed="sip")
    with pytest.raises(DataUnavailable, match="09:45"):
        market(fetcher, tmp_path).minute_bars("SPY", PAST, at(PAST, "09:30"), at(PAST, "10:00"), feed="sip")
    assert fetcher.minute_calls == 1


@pytest.mark.parametrize("content", ["not json", '{"format": 1}', json.dumps({"format": 99})])
def test_a_corrupt_cache_file_is_ignored_and_refetched(tmp_path, content):
    fetcher = FakeFetcher([PAST])
    data = market(fetcher, tmp_path)
    data.minute_bars("SPY", PAST, at(PAST, "09:30"), at(PAST, "10:00"), feed="sip")
    [path] = cache_files(tmp_path)
    path.write_text(content)
    assert len(data.minute_bars("SPY", PAST, at(PAST, "09:30"), at(PAST, "10:00"), feed="sip")) == 30
    assert fetcher.minute_calls == 2


def test_windows_outside_the_session_are_a_bug(tmp_path):
    with pytest.raises(ValueError):
        market(FakeFetcher([PAST]), tmp_path).minute_bars(
            "SPY", PAST, at(PAST, "09:00"), at(PAST, "10:00"), feed="sip"
        )


# --- holdout lock ------------------------------------------------------------------


def test_holdout_dates_are_locked(tmp_path):
    fetcher = FakeFetcher([HOLDOUT_DAY])
    with pytest.raises(HoldoutLocked):
        market(fetcher, tmp_path).minute_bars(
            "SPY", HOLDOUT_DAY, at(HOLDOUT_DAY, "09:30"), at(HOLDOUT_DAY, "10:00"), feed="sip"
        )
    with pytest.raises(HoldoutLocked):
        market(fetcher, tmp_path).sessions(dt.date(2022, 12, 1), dt.date(2023, 1, 31))
    assert fetcher.minute_calls == 0


def test_an_unlocked_holdout_can_be_read(tmp_path):
    data = market(FakeFetcher([HOLDOUT_DAY]), tmp_path, holdout_unlocked=True)
    bars = data.minute_bars("SPY", HOLDOUT_DAY, at(HOLDOUT_DAY, "09:30"), at(HOLDOUT_DAY, "10:00"), feed="sip")
    assert len(bars) == 30


# --- live -------------------------------------------------------------------------


def test_live_bars_are_complete_and_fresh(tmp_path):
    fetcher = FakeFetcher([TODAY])
    bars = market(fetcher, tmp_path, now=at(TODAY, "15:30", 20)).live_minute_bars("SPY", TODAY, at(TODAY, "15:00"))
    assert len(bars) == 30
    assert bars[-1].end == at(TODAY, "15:30")
    assert cache_files(tmp_path) == []  # today's data is never cached


def test_live_bars_with_the_latest_minute_missing_are_refused(tmp_path):
    fetcher = FakeFetcher([TODAY])
    fetcher.skip = {"15:29"}
    with pytest.raises(DataUnavailable, match="15:29"):
        market(fetcher, tmp_path, now=at(TODAY, "15:30", 20)).live_minute_bars("SPY", TODAY, at(TODAY, "15:00"))


def test_live_bars_that_stopped_arriving_are_refused(tmp_path):
    fetcher = FakeFetcher([TODAY])
    fetcher.minute_override = minute_bars(at(TODAY, "15:00"), at(TODAY, "15:30"))  # feed froze at 15:30
    with pytest.raises(DataUnavailable):
        market(fetcher, tmp_path, now=at(TODAY, "15:33", 5)).live_minute_bars("SPY", TODAY, at(TODAY, "15:00"))


def test_todays_sip_data_is_refused_on_the_free_plan(tmp_path):
    with pytest.raises(DataUnavailable, match="SIP"):
        market(FakeFetcher([TODAY]), tmp_path).minute_bars(
            "SPY", TODAY, at(TODAY, "09:30"), at(TODAY, "10:00"), feed="sip"
        )


# --- calendar and previous close -----------------------------------------------------


def test_previous_close_comes_from_the_prior_session_on_the_research_feed(tmp_path):
    fetcher = FakeFetcher([PAST_PREVIOUS, PAST])
    fetcher.daily[PAST_PREVIOUS.date] = Bar(
        dt.datetime.combine(PAST_PREVIOUS.date, dt.time(0), ET), 300.0, 301.0, 295.0, 296.5, 1e8
    )
    assert market(fetcher, tmp_path).previous_close("SPY", PAST) == 296.5
    assert fetcher.daily_calls == ["sip"]


def test_a_missing_previous_close_is_unavailable(tmp_path):
    with pytest.raises(DataUnavailable, match="expected one SPY daily bar"):
        market(FakeFetcher([PAST_PREVIOUS, PAST]), tmp_path).previous_close("SPY", PAST)


def test_closed_days_have_no_session(tmp_path):
    assert market(FakeFetcher([PAST]), tmp_path).session_on(dt.date(2020, 3, 1)) is None


def test_a_calendar_without_timezones_is_refused(tmp_path):
    naive = Session(PAST.date, PAST.open.replace(tzinfo=None), PAST.close.replace(tzinfo=None))
    with pytest.raises(DataUnavailable, match="no timezone"):
        market(FakeFetcher([naive]), tmp_path).session_on(PAST.date)
