"""Market data access: fetch, validate, cache. Fails closed.

MarketData sits between the strategy/backtester and a BarFetcher (the Alpaca
adapter in alpaca_client.py, or a fake in tests). Its rules:

* Every request either returns validated data or raises DataUnavailable.
  Transient failures are retried with backoff a configured number of times;
  after that the data is unavailable. It never falls back to an older value.
* Only successful, sane reads of sessions that are already over get cached.
  Failures, empty responses and today's data are never cached. A cache file
  that fails to parse or validate is ignored and fetched again.
* Minute windows are checked for gaps on every read, including cache reads.
* Live reads also get a staleness check against the clock.
* Real-time SIP data isn't on the free plan, so asking for today's data on the
  SIP feed raises instead of silently returning 15-minute-delayed bars.
* Dates in the holdout split raise HoldoutLocked unless the holdout has been
  unlocked. The backtester (module 4) will unlock it only for a frozen spec.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar

from baselinetrading.bars import (
    ET,
    MINUTE,
    Bar,
    DataUnavailable,
    Session,
    bar_problems,
    check_fresh,
    validate_minute_bars,
)
from baselinetrading.config import Config

T = TypeVar("T")
_CACHE_FORMAT = 1


class HoldoutLocked(Exception):
    """A request touched the holdout split before the strategy was frozen."""


class BarFetcher(Protocol):
    """Raw access to a data vendor. Implementations may raise anything; MarketData handles it."""

    def minute_bars(self, symbol: str, start: dt.datetime, end: dt.datetime, feed: str) -> list[Bar]:
        """1-minute bars with start in [start, end)."""

    def daily_bars(self, symbol: str, start: dt.date, end: dt.date, feed: str) -> list[Bar]:
        """Daily bars for sessions from start to end, inclusive."""

    def sessions(self, start: dt.date, end: dt.date) -> list[Session]:
        """Exchange calendar: regular sessions from start to end, inclusive."""


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class MarketData:
    def __init__(
        self,
        fetcher: BarFetcher,
        config: Config,
        *,
        cache_dir: Path | None,
        now: Callable[[], dt.datetime] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
        holdout_unlocked: bool = False,
    ) -> None:
        self._fetcher = fetcher
        self._data = config.data
        self._holdout = config.splits.holdout
        self._cache_dir = cache_dir
        self._now = now
        self._sleep = sleep
        self._holdout_unlocked = holdout_unlocked

    @property
    def live_feed(self) -> str:
        return self._data.live_feed

    @property
    def research_feed(self) -> str:
        return self._data.research_feed

    # --- calendar -----------------------------------------------------------------

    def sessions(self, start: dt.date, end: dt.date) -> list[Session]:
        """Regular sessions from start to end, inclusive, checked for sanity."""
        self._guard_holdout(start, end)
        sessions = self._fetch(f"calendar {start} to {end}", lambda: self._fetcher.sessions(start, end))
        for session in sessions:
            if session.open.tzinfo is None or session.close.tzinfo is None:
                raise DataUnavailable(f"calendar entry for {session.date} has no timezone")
            if session.open.astimezone(ET).date() != session.date or not session.open < session.close:
                raise DataUnavailable(f"calendar entry for {session.date} is inconsistent: {session}")
            if not start <= session.date <= end:
                raise DataUnavailable(f"calendar returned {session.date}, outside {start} to {end}")
        dates = [s.date for s in sessions]
        if dates != sorted(set(dates)):
            raise DataUnavailable("calendar entries are duplicated or out of order")
        return sessions

    def session_on(self, day: dt.date) -> Session | None:
        """The session on `day`, or None if the market is closed that day."""
        sessions = self.sessions(day, day)
        return sessions[0] if sessions else None

    def previous_session(self, session: Session) -> Session:
        # 10 calendar days always contain a trading day; US markets never close that long.
        earlier = self.sessions(session.date - dt.timedelta(days=10), session.date - dt.timedelta(days=1))
        if not earlier:
            raise DataUnavailable(f"no session found in the 10 days before {session.date}")
        return earlier[-1]

    # --- prices -------------------------------------------------------------------

    def previous_close(self, symbol: str, session: Session) -> float:
        """Official close of the session before `session`.

        Always from the research feed (SIP): yesterday is more than 15 minutes old,
        so the free plan allows it, and the IEX "close" is only IEX's last trade.
        """
        previous = self.previous_session(session)
        self._guard_holdout(previous.date, previous.date)
        feed = self._data.research_feed
        bars = self._fetch(
            f"{symbol} daily bar for {previous.date}",
            lambda: self._fetcher.daily_bars(symbol, previous.date, previous.date, feed),
        )
        matching = [b for b in bars if b.start.astimezone(ET).date() == previous.date]
        if len(matching) != 1:
            raise DataUnavailable(f"expected one {symbol} daily bar for {previous.date}, got {len(matching)}")
        problems = bar_problems(matching[0])
        if problems:
            raise DataUnavailable(f"bad {symbol} daily bar for {previous.date}: {'; '.join(problems)}")
        return matching[0].close

    def minute_bars(
        self, symbol: str, session: Session, start: dt.datetime, end: dt.datetime, *, feed: str,
        allow_gaps: bool = False,
    ) -> tuple[Bar, ...]:
        """Validated 1-minute bars for [start, end) inside `session`, gaps checked unless allow_gaps."""
        if not session.open <= start < end <= session.close:
            raise ValueError(f"window {start} to {end} is not inside the session {session.open} to {session.close}")
        self._guard_holdout(session.date, session.date)
        if session.date < self._today():
            bars = [b for b in self._past_session_bars(symbol, session, feed) if start <= b.start < end]
        elif feed == "sip":
            raise DataUnavailable("today's SIP data isn't real-time on the free plan; use the live feed")
        else:
            bars = self._fetch(
                f"{symbol} {feed} bars {start} to {end}",
                lambda: self._fetcher.minute_bars(symbol, start, end, feed),
            )
        max_missing = (end - start) // MINUTE if allow_gaps else self._data.max_missing_minutes
        return validate_minute_bars(bars, start=start, end=end, max_missing=max_missing)

    def live_minute_bars(
        self, symbol: str, session: Session, start: dt.datetime, *, allow_gaps: bool = False, fresh: bool = True
    ) -> tuple[Bar, ...]:
        """Today's bars from `start` up to the last completed minute, on the live feed.

        Checks gaps (unless allow_gaps) like any read, and that the newest bar is fresh (unless fresh=False).
        """
        now = self._now()
        if session.date != self._today():
            raise ValueError(f"live data requested for {session.date}, but today is {self._today()}")
        end = min(now.replace(second=0, microsecond=0), session.close)
        if end <= start:
            raise DataUnavailable(f"no completed minute yet after {start}")
        bars = self.minute_bars(symbol, session, start, end, feed=self._data.live_feed, allow_gaps=allow_gaps)
        if fresh:
            check_fresh(bars, now=now, max_staleness=dt.timedelta(seconds=self._data.max_staleness_seconds))
        return bars

    def daily_closes(self, symbol: str, start: dt.date, end: dt.date) -> dict[dt.date, float]:
        """Official daily closes on the research feed, keyed by session date. Malformed bars raise."""
        self._guard_holdout(start, end)
        feed = self._data.research_feed
        bars = self._fetch(
            f"{symbol} daily bars {start} to {end}", lambda: self._fetcher.daily_bars(symbol, start, end, feed)
        )
        closes: dict[dt.date, float] = {}
        for bar in bars:
            problems = bar_problems(bar)
            if problems:
                raise DataUnavailable(f"bad {symbol} daily bar at {bar.start}: {'; '.join(problems)}")
            closes[bar.start.astimezone(ET).date()] = bar.close
        return closes

    def display_bars(self, symbol: str, session: Session, end: dt.datetime) -> list[Bar]:
        """Bars for the chart only, never for decisions: gaps allowed, malformed bars dropped, not cached."""
        self._guard_holdout(session.date, session.date)
        feed = self._data.live_feed if session.date >= self._today() else self._data.research_feed
        end = min(end.replace(second=0, microsecond=0), session.close)
        if end <= session.open:
            return []
        bars = self._fetch(
            f"{symbol} chart bars for {session.date}",
            lambda: self._fetcher.minute_bars(symbol, session.open, end, feed),
        )
        return sorted((b for b in bars if not bar_problems(b)), key=lambda b: b.start)

    # --- internals ----------------------------------------------------------------

    def _past_session_bars(self, symbol: str, session: Session, feed: str) -> list[Bar]:
        cached = self._read_cache(symbol, session, feed)
        if cached is not None:
            return cached
        bars = self._fetch(
            f"{symbol} {feed} bars for {session.date}",
            lambda: self._fetcher.minute_bars(symbol, session.open, session.close, feed),
        )
        # Sanity only; gaps are judged per window by the caller. An empty response for a
        # trading day is a failure, not "no trades", and is never cached.
        if not bars:
            raise DataUnavailable(f"no {symbol} {feed} bars returned for {session.date}")
        validate_minute_bars(bars, start=session.open, end=session.close, max_missing=len(_minutes(session)))
        self._write_cache(symbol, session, feed, bars)
        return bars

    def _fetch(self, what: str, call: Callable[[], T]) -> T:
        attempts = self._data.fetch_attempts
        for attempt in range(1, attempts + 1):
            try:
                return call()
            except (DataUnavailable, HoldoutLocked):
                raise
            except Exception as exc:  # network errors, API errors, anything: all mean "no data"
                failure = exc
                if attempt < attempts:
                    self._sleep(2 ** (attempt - 1))
        raise DataUnavailable(
            f"{what}: failed after {attempts} attempts ({type(failure).__name__}: {str(failure)[:200]})"
        )

    def _guard_holdout(self, first: dt.date, last: dt.date) -> None:
        if self._holdout_unlocked:
            return
        if first <= self._holdout.end and last >= self._holdout.start:
            raise HoldoutLocked(
                f"{first} to {last} touches the holdout ({self._holdout.start} to {self._holdout.end}), "
                "which stays locked until the strategy is frozen"
            )

    def _today(self) -> dt.date:
        return self._now().astimezone(ET).date()

    def _cache_path(self, symbol: str, session: Session, feed: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{feed}-{self._data.adjustment}" / symbol / f"{session.date.isoformat()}.json"

    def _read_cache(self, symbol: str, session: Session, feed: str) -> list[Bar] | None:
        path = self._cache_path(symbol, session, feed)
        if path is None or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            expected = {
                "format": _CACHE_FORMAT,
                "symbol": symbol,
                "feed": feed,
                "adjustment": self._data.adjustment,
                "date": session.date.isoformat(),
            }
            if {k: payload.get(k) for k in expected} != expected:
                return None
            bars = [
                Bar(dt.datetime.fromisoformat(row[0]), *(float(x) for x in row[1:6])) for row in payload["bars"]
            ]
            if not bars:
                return None
            validate_minute_bars(bars, start=session.open, end=session.close, max_missing=len(_minutes(session)))
            return bars
        except (OSError, ValueError, KeyError, TypeError, IndexError, DataUnavailable):
            return None  # corrupt or foreign cache file: fetch again, never trust it

    def _write_cache(self, symbol: str, session: Session, feed: str, bars: list[Bar]) -> None:
        path = self._cache_path(symbol, session, feed)
        if path is None:
            return
        payload = {
            "format": _CACHE_FORMAT,
            "symbol": symbol,
            "feed": feed,
            "adjustment": self._data.adjustment,
            "date": session.date.isoformat(),
            "bars": [[b.start.isoformat(), b.open, b.high, b.low, b.close, b.volume] for b in bars],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary file and rename, so a crash can't leave half a file behind.
        handle, temp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise


def _minutes(session: Session) -> list[dt.datetime]:
    return [session.open + i * MINUTE for i in range((session.close - session.open) // MINUTE)]
