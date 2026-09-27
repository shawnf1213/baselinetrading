import datetime as dt

from alpaca.data.models import Bar as AlpacaBar
from alpaca.trading.models import Calendar

from baselinetrading.alpaca_client import to_bar, to_session
from baselinetrading.bars import ET


def test_alpaca_bars_convert_to_utc_bars():
    raw = {"t": "2020-03-02T14:30:00Z", "o": 300.0, "h": 301.0, "l": 299.5, "c": 300.5, "v": 12345, "n": 10, "vw": 300.2}
    bar = to_bar(AlpacaBar("SPY", raw))
    assert bar.start == dt.datetime(2020, 3, 2, 14, 30, tzinfo=dt.timezone.utc)
    assert bar.start.astimezone(ET).strftime("%H:%M") == "09:30"
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (300.0, 301.0, 299.5, 300.5, 12345)


def test_alpaca_calendar_times_are_new_york_times():
    day = to_session(Calendar(date="2020-11-27", open="09:30", close="13:00"))  # day after Thanksgiving
    assert day.open == dt.datetime(2020, 11, 27, 9, 30, tzinfo=ET)
    assert day.close.utcoffset() == dt.timedelta(hours=-5)
    assert not day.is_full_day
