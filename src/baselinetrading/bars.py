"""Market data types, and the checks that decide whether data is fit to trade on.

The rule: a failed, incomplete or stale read is never a price. Anything that
hands out bars either returns bars that passed every check here, or raises
DataUnavailable. Code that catches DataUnavailable must skip the decision. It
must never substitute a value (a previous price, a zero, a cached guess).
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable
from dataclasses import dataclass
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MINUTE = dt.timedelta(minutes=1)
FULL_SESSION = dt.timedelta(hours=6, minutes=30)
_FUTURE_TOLERANCE = dt.timedelta(seconds=5)


class DataUnavailable(Exception):
    """The data needed for a decision is missing, malformed, gapped or stale. Don't trade."""


@dataclass(frozen=True)
class Bar:
    """One bar. `start` is timezone-aware; the bar covers [start, start + length)."""

    start: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def end(self) -> dt.datetime:
        """End of a 1-minute bar: its close is only known at this time."""
        return self.start + MINUTE


@dataclass(frozen=True)
class Session:
    """One regular trading session, from the exchange calendar (never inferred from data gaps)."""

    date: dt.date
    open: dt.datetime  # timezone-aware
    close: dt.datetime

    @property
    def is_full_day(self) -> bool:
        return self.close - self.open == FULL_SESSION


def bar_problems(bar: Bar) -> list[str]:
    """Everything wrong with a single bar; empty if it's plausible."""
    problems = []
    if bar.start.tzinfo is None or bar.start.utcoffset() is None:
        problems.append("timestamp has no timezone")
    elif bar.start.second or bar.start.microsecond:
        problems.append("timestamp is not on a whole minute")
    prices = (bar.open, bar.high, bar.low, bar.close)
    if not all(isinstance(p, (int, float)) and math.isfinite(p) and p > 0 for p in prices):
        problems.append(f"prices must be positive finite numbers, got {prices}")
    elif not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
        problems.append(f"inconsistent OHLC {prices}")
    if not (isinstance(bar.volume, (int, float)) and math.isfinite(bar.volume) and bar.volume >= 0):
        problems.append(f"volume must be a non-negative finite number, got {bar.volume!r}")
    return problems


def validate_minute_bars(
    bars: Iterable[Bar], *, start: dt.datetime, end: dt.datetime, max_missing: int
) -> tuple[Bar, ...]:
    """Check 1-minute bars covering [start, end) and return them sorted.

    Raises DataUnavailable if any bar is malformed or duplicated, lies outside
    the window, or if more than `max_missing` minutes of the window have no bar.
    A minute with no bar is a gap whatever the cause: on the IEX feed it can
    mean no IEX trade that minute rather than an outage, and we can't tell the
    two apart from the data, so both count.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("window bounds must be timezone-aware")
    if end <= start or (end - start) % MINUTE:
        raise ValueError(f"window must be a positive whole number of minutes, got {start} to {end}")
    ordered = sorted(bars, key=lambda b: b.start)
    for bar in ordered:
        problems = bar_problems(bar)
        if problems:
            raise DataUnavailable(f"bad bar at {bar.start}: {'; '.join(problems)}")
        if not start <= bar.start < end:
            raise DataUnavailable(f"bar at {bar.start} is outside the requested window {start} to {end}")
    starts = [b.start for b in ordered]
    if len(set(starts)) != len(starts):
        raise DataUnavailable(f"duplicate bars in window {start} to {end}")
    expected = (end - start) // MINUTE
    missing = expected - len(ordered)
    if missing > max_missing:
        present = set(starts)
        gaps = [start + i * MINUTE for i in range(expected) if start + i * MINUTE not in present]
        shown = ", ".join(g.astimezone(ET).strftime("%H:%M") for g in gaps[:5])
        more = f" and {len(gaps) - 5} more" if len(gaps) > 5 else ""
        raise DataUnavailable(
            f"{missing} of {expected} minutes missing in {_et(start)} to {_et(end)} "
            f"(allowed {max_missing}): {shown}{more} ET"
        )
    return tuple(ordered)


def check_fresh(bars: tuple[Bar, ...], *, now: dt.datetime, max_staleness: dt.timedelta) -> None:
    """Live check: the newest bar must have ended recently and not in the future.

    A bar that ends after `now` means the clock is wrong, and a wrong clock makes
    every staleness check meaningless, so it's refused too.
    """
    if not bars:
        raise DataUnavailable("no bars at all")
    newest = max(bar.end for bar in bars)
    if newest > now + _FUTURE_TOLERANCE:
        raise DataUnavailable(f"newest bar ends at {newest}, after the current time {now}: check the clock")
    age = now - newest
    if age > max_staleness:
        raise DataUnavailable(
            f"stale: newest bar ended {age.total_seconds():.0f}s ago (limit {max_staleness.total_seconds():.0f}s)"
        )


def _et(moment: dt.datetime) -> str:
    return moment.astimezone(ET).strftime("%Y-%m-%d %H:%M")
