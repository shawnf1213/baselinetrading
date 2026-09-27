"""Shared test helpers: bars, sessions and a scriptable fake data vendor."""

import datetime as dt

from baselinetrading.bars import ET, MINUTE, Bar, Session


def session(day: dt.date, close: dt.time = dt.time(16, 0)) -> Session:
    return Session(day, dt.datetime.combine(day, dt.time(9, 30), ET), dt.datetime.combine(day, close, ET))


def minute_bars(start: dt.datetime, end: dt.datetime, *, skip=(), price: float = 500.0) -> list[Bar]:
    bars, moment = [], start
    while moment < end:
        if moment.astimezone(ET).strftime("%H:%M") not in skip:
            bars.append(Bar(moment.astimezone(dt.timezone.utc), price, price + 0.05, price - 0.05, price + 0.01, 1000.0))
        moment += MINUTE
    return bars


class FakeFetcher:
    """Serves a full, clean day of bars for every session unless told otherwise."""

    def __init__(self, sessions):
        self.calendar = list(sessions)
        self.minute_calls = 0
        self.daily_calls = []
        self.failures_before_success = 0
        self.skip = ()
        self.minute_override = None
        self.daily = {}

    def minute_bars(self, symbol, start, end, feed):
        self.minute_calls += 1
        if self.failures_before_success:
            self.failures_before_success -= 1
            raise ConnectionError("simulated network failure")
        if self.minute_override is not None:
            return list(self.minute_override)
        return minute_bars(start, end, skip=self.skip)

    def daily_bars(self, symbol, start, end, feed):
        self.daily_calls.append(feed)
        return [bar for day, bar in self.daily.items() if start <= day <= end]

    def sessions(self, start, end):
        return [s for s in self.calendar if start <= s.date <= end]
