import dataclasses
import datetime as dt

import pytest

from baselinetrading.bars import ET, Bar
from baselinetrading.config import DEFAULT_CONFIG_PATH, load_config
from baselinetrading.market_data import HoldoutLocked, MarketData
from baselinetrading.strategy import BUY, NO_TRADE, SPEC, decide_entry, decide_from_data, stop_price
from tests.fakes import FakeFetcher, minute_bars, session

DAY = session(dt.date(2020, 3, 2))
PREVIOUS = session(dt.date(2020, 2, 28))
CONFIG = load_config(DEFAULT_CONFIG_PATH, today=dt.date(2026, 9, 28))


def at(hhmm, day=DAY):
    hour, minute = map(int, hhmm.split(":"))
    return dt.datetime.combine(day.date, dt.time(hour, minute), ET)


def signal_bars(price=500.0, skip=()):
    return tuple(minute_bars(at("09:30"), at("10:00"), price=price, skip=skip))


def entry_bars(price=502.0, start="15:00", end="15:30", skip=()):
    return tuple(minute_bars(at(start), at(end), price=price, skip=skip))


# The fake bars close at price + 0.01, so the 09:59 close is 500.01.


def test_an_up_morning_buys_with_the_stop_attached():
    decision = decide_entry(DAY, 499.0, signal_bars(), entry_bars())
    assert decision.action == BUY
    assert decision.stop_pct == 1.0
    assert decision.inputs.signal_price == 500.01
    assert decision.inputs.signal_return == pytest.approx(500.01 / 499.0 - 1)
    assert decision.inputs.reference_price == 502.01
    assert decision.inputs.entry_window_volume == 30 * 1000.0


@pytest.mark.parametrize("previous_close", [500.01, 501.0])
def test_a_flat_or_down_morning_does_nothing(previous_close):
    decision = decide_entry(DAY, previous_close, signal_bars(), entry_bars())
    assert decision.action == NO_TRADE
    assert "long-only" in decision.reason
    assert decision.inputs is not None  # the inputs are logged even when there's no trade


def test_half_days_are_skipped():
    half = session(DAY.date, close=dt.time(13, 0))
    assert decide_entry(half, 499.0, signal_bars(), entry_bars()).action == NO_TRADE


@pytest.mark.parametrize(
    ("signal", "entry", "expected"),
    [
        (signal_bars(skip={"09:59"}), entry_bars(), "signal window: 1 minute(s) missing, first at 09:59"),
        (signal_bars(), entry_bars(skip={"15:29"}), "entry window: 1 minute(s) missing, first at 15:29"),
        (signal_bars(), entry_bars(end="15:31"), "entry window: 1 bar(s) outside 15:00-15:30, first at 15:30"),
        ((), entry_bars(), "signal window: 30 minute(s) missing"),
    ],
)
def test_incomplete_or_lookahead_windows_mean_no_trade(signal, entry, expected):
    decision = decide_entry(DAY, 499.0, signal, entry)
    assert decision.action == NO_TRADE
    assert expected in decision.reason


def test_duplicated_bars_mean_no_trade():
    bars = signal_bars()
    decision = decide_entry(DAY, 499.0, bars[:-1] + bars[-2:-1], entry_bars())
    assert decision.action == NO_TRADE


@pytest.mark.parametrize("previous_close", [0.0, -1.0, float("nan"), float("inf"), None])
def test_an_unusable_previous_close_means_no_trade(previous_close):
    assert decide_entry(DAY, previous_close, signal_bars(), entry_bars()).action == NO_TRADE


def test_the_decision_ignores_nothing_it_is_given_and_sees_nothing_later():
    # Same inputs, same answer, and only the 09:59 close drives the signal.
    bars = list(signal_bars())
    bars[-1] = dataclasses.replace(bars[-1], close=498.0, low=497.0)
    assert decide_entry(DAY, 499.0, tuple(bars), entry_bars()).action == NO_TRADE
    assert decide_entry(DAY, 499.0, signal_bars(), entry_bars()).action == BUY


def test_stop_price_is_one_percent_below_rounded_down():
    assert stop_price(500.0) == 495.0
    assert stop_price(502.37) == 497.34  # 497.3463 rounded down, never tighter
    with pytest.raises(ValueError):
        stop_price(float("nan"))


def test_spec_fingerprint_changes_with_any_rule():
    assert SPEC.fingerprint() == dataclasses.replace(SPEC).fingerprint()
    assert SPEC.fingerprint() != dataclasses.replace(SPEC, stop_pct=1.5).fingerprint()


# --- wired to MarketData --------------------------------------------------------------


def research(fetcher, tmp_path):
    return MarketData(fetcher, CONFIG, cache_dir=tmp_path, now=lambda: at("12:00", session(dt.date(2026, 9, 28))), sleep=lambda s: None)


def with_previous_close(fetcher, close):
    fetcher.daily[PREVIOUS.date] = Bar(dt.datetime.combine(PREVIOUS.date, dt.time(0), ET), close, close, close, close, 1.0)
    return fetcher


def test_a_past_session_decides_from_research_data(tmp_path):
    fetcher = with_previous_close(FakeFetcher([PREVIOUS, DAY]), 499.0)
    decision = decide_from_data(research(fetcher, tmp_path), "SPY", DAY, live=False)
    assert decision.action == BUY


def test_a_data_gap_is_a_no_trade_with_the_reason(tmp_path):
    fetcher = with_previous_close(FakeFetcher([PREVIOUS, DAY]), 499.0)
    fetcher.skip = {"15:10"}
    decision = decide_from_data(research(fetcher, tmp_path), "SPY", DAY, live=False)
    assert decision.action == NO_TRADE
    assert decision.reason.startswith("data unavailable") and "15:10" in decision.reason


def test_a_missing_previous_close_is_a_no_trade(tmp_path):
    decision = decide_from_data(research(FakeFetcher([PREVIOUS, DAY]), tmp_path), "SPY", DAY, live=False)
    assert decision.action == NO_TRADE
    assert "daily bar" in decision.reason


def test_the_holdout_lock_is_not_swallowed(tmp_path):
    day, previous = session(dt.date(2024, 5, 2)), session(dt.date(2024, 5, 1))
    with pytest.raises(HoldoutLocked):
        decide_from_data(research(FakeFetcher([previous, day]), tmp_path), "SPY", day, live=False)


def test_live_decision_at_1530_uses_fresh_live_bars(tmp_path):
    today, yesterday = session(dt.date(2026, 9, 28)), session(dt.date(2026, 9, 25))
    fetcher = with_previous_close(FakeFetcher([yesterday, today]), 499.0)
    fetcher.daily[yesterday.date] = fetcher.daily.pop(PREVIOUS.date)
    fetcher.daily[yesterday.date] = dataclasses.replace(
        fetcher.daily[yesterday.date], start=dt.datetime.combine(yesterday.date, dt.time(0), ET)
    )
    data = MarketData(fetcher, CONFIG, cache_dir=tmp_path, now=lambda: at("15:30", today) + dt.timedelta(seconds=15), sleep=lambda s: None)
    assert decide_from_data(data, "SPY", today, live=True).action == BUY
    late = MarketData(fetcher, CONFIG, cache_dir=tmp_path, now=lambda: at("15:31", today) + dt.timedelta(seconds=15), sleep=lambda s: None)
    decision = decide_from_data(late, "SPY", today, live=True)
    assert decision.action == NO_TRADE and "outside 15:00-15:30" in decision.reason  # too late is refused, not rounded
