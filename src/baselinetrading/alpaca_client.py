"""The Alpaca side of market data: a BarFetcher built on alpaca-py.

Kept deliberately thin. It converts Alpaca's objects into our Bar and Session
types, and all judgement (validation, retries, caching, staleness) lives in
market_data.py. The trading client is created with paper=True; it's only used
here for the exchange calendar.
"""

from __future__ import annotations

import datetime as dt

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetCalendarRequest

from baselinetrading.bars import ET, Bar, Session
from baselinetrading.credentials import Credentials


class AlpacaFetcher:
    def __init__(self, credentials: Credentials, *, adjustment: str) -> None:
        key, secret = credentials.key_id.reveal(), credentials.secret_key.reveal()
        self._data = StockHistoricalDataClient(key, secret)
        self._trading = TradingClient(key, secret, paper=True)
        self._adjustment = Adjustment(adjustment)

    def minute_bars(self, symbol: str, start: dt.datetime, end: dt.datetime, feed: str) -> list[Bar]:
        # Alpaca treats `end` as inclusive; ask for one second less and filter to [start, end).
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end - dt.timedelta(seconds=1),
            feed=DataFeed(feed),
            adjustment=self._adjustment,
        )
        bars = [to_bar(b) for b in self._data.get_stock_bars(request).data.get(symbol, [])]
        return [b for b in bars if start <= b.start < end]

    def daily_bars(self, symbol: str, start: dt.date, end: dt.date, feed: str) -> list[Bar]:
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=dt.datetime.combine(start, dt.time(0), ET),
            end=dt.datetime.combine(end, dt.time(23, 59), ET),
            feed=DataFeed(feed),
            adjustment=self._adjustment,
        )
        return [to_bar(b) for b in self._data.get_stock_bars(request).data.get(symbol, [])]

    def daily_volumes(self, symbols: list[str], start: dt.datetime, end: dt.datetime,
                      feed: str) -> dict[str, list[tuple[dt.date, float, float, float]]]:
        """(session date, volume, VWAP, close) of each symbol's daily bars; alpaca-py follows the pages."""
        request = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Day, start=start, end=end,
                                   feed=DataFeed(feed), adjustment=self._adjustment)
        return {symbol: [(b.timestamp.astimezone(ET).date(), b.volume, b.vwap or b.close, b.close) for b in bars]
                for symbol, bars in self._data.get_stock_bars(request).data.items()}

    def sessions(self, start: dt.date, end: dt.date) -> list[Session]:
        return [to_session(c) for c in self._trading.get_calendar(GetCalendarRequest(start=start, end=end))]


def to_bar(alpaca_bar) -> Bar:
    """alpaca-py Bar -> our Bar. Alpaca timestamps are the bar's start, in UTC.

    A timestamp without a timezone is passed through unchanged, so validation
    rejects it instead of us guessing.
    """
    start = alpaca_bar.timestamp
    if start.tzinfo is not None:
        start = start.astimezone(dt.timezone.utc)
    return Bar(start, alpaca_bar.open, alpaca_bar.high, alpaca_bar.low, alpaca_bar.close, alpaca_bar.volume)


def to_session(calendar_day) -> Session:
    """alpaca-py Calendar -> Session. alpaca-py parses open/close as naive New York times."""
    return Session(date=calendar_day.date, open=_in_et(calendar_day.open), close=_in_et(calendar_day.close))


def _in_et(moment: dt.datetime) -> dt.datetime:
    return moment.replace(tzinfo=ET) if moment.tzinfo is None else moment.astimezone(ET)
