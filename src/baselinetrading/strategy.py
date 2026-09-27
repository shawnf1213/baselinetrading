"""Strategy C: intraday momentum, last half hour. Frozen spec in docs/decisions.md.

The strategy only decides. It places no orders and knows nothing about the
broker; the risk manager (module 5) can veto anything it proposes, and the
execution module (module 6) carries it out.

The core, decide_entry(), is a pure function of data that already exists at
15:30: the previous close, the 09:30-10:00 bars and the 15:00-15:30 bars.
Anything missing, inconsistent or unexpected makes it return NO_TRADE with the
reason; it never guesses. Every decision carries the inputs behind it, so a
losing trade can be diagnosed later rather than only counted.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from dataclasses import asdict, dataclass

from baselinetrading.bars import ET, MINUTE, Bar, DataUnavailable, Session
from baselinetrading.market_data import MarketData

BUY = "BUY"
NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class StrategySpec:
    """The frozen rules. Changing any value is a new strategy and a new trial."""

    name: str = "intraday-momentum-last-half-hour"
    signal_window_start: dt.time = dt.time(9, 30)
    signal_time: dt.time = dt.time(10, 0)  # r uses the close of the bar ending here
    entry_window_start: dt.time = dt.time(15, 0)  # must be gap-free before entering
    entry_time: dt.time = dt.time(15, 30)
    exit_time: dt.time = dt.time(15, 55)
    threshold: float = 0.0  # long when r > threshold
    stop_pct: float = 1.0  # protective stop below the entry fill

    def fingerprint(self) -> str:
        """Hash of the spec. The backtester will unlock the holdout only for a recorded fingerprint."""
        text = json.dumps({k: str(v) for k, v in asdict(self).items()}, sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def at(self, session: Session, moment: dt.time) -> dt.datetime:
        return dt.datetime.combine(session.date, moment, ET)


SPEC = StrategySpec()


@dataclass(frozen=True)
class SignalInputs:
    """Everything the decision was based on, for the log."""

    previous_close: float
    signal_price: float  # close of the last bar before signal_time
    signal_return: float  # signal_price / previous_close - 1
    reference_price: float  # close of the last bar before entry_time
    signal_window_volume: float
    entry_window_volume: float
    entry_window_range_pct: float  # (high - low) / low over the entry window, in percent


@dataclass(frozen=True)
class Decision:
    date: dt.date
    action: str  # BUY or NO_TRADE
    reason: str
    inputs: SignalInputs | None = None
    stop_pct: float | None = None  # set only for BUY


def decide_entry(
    session: Session,
    previous_close: float,
    signal_bars: tuple[Bar, ...],
    entry_bars: tuple[Bar, ...],
    spec: StrategySpec = SPEC,
) -> Decision:
    """The 15:30 decision. Pure: the same inputs always give the same answer.

    signal_bars must be exactly the 1-minute bars from 09:30 to 10:00 and
    entry_bars exactly those from 15:00 to 15:30, both complete. Anything else
    is NO_TRADE; a bar from after 15:30 is lookahead and is refused too.
    """

    def no_trade(reason: str, inputs: SignalInputs | None = None) -> Decision:
        return Decision(session.date, NO_TRADE, reason, inputs)

    if not session.is_full_day:
        return no_trade("half day: the last half hour isn't 15:30-16:00")
    problem = _window_problem(signal_bars, spec.at(session, spec.signal_window_start), spec.at(session, spec.signal_time))
    if problem:
        return no_trade(f"signal window: {problem}")
    problem = _window_problem(entry_bars, spec.at(session, spec.entry_window_start), spec.at(session, spec.entry_time))
    if problem:
        return no_trade(f"entry window: {problem}")
    if not (isinstance(previous_close, (int, float)) and math.isfinite(previous_close) and previous_close > 0):
        return no_trade(f"previous close is not a usable price: {previous_close!r}")

    signal_price = signal_bars[-1].close
    low = min(b.low for b in entry_bars)
    inputs = SignalInputs(
        previous_close=previous_close,
        signal_price=signal_price,
        signal_return=signal_price / previous_close - 1,
        reference_price=entry_bars[-1].close,
        signal_window_volume=sum(b.volume for b in signal_bars),
        entry_window_volume=sum(b.volume for b in entry_bars),
        entry_window_range_pct=(max(b.high for b in entry_bars) - low) / low * 100,
    )
    if inputs.signal_return > spec.threshold:
        return Decision(session.date, BUY, f"morning return {inputs.signal_return:+.3%} > 0", inputs, spec.stop_pct)
    return no_trade(f"morning return {inputs.signal_return:+.3%} is not above 0 (long-only)", inputs)


def stop_price(entry_fill: float, spec: StrategySpec = SPEC) -> float:
    """Protective stop for a filled entry, rounded down to the cent (never tighter than the rule)."""
    if not (math.isfinite(entry_fill) and entry_fill > 0):
        raise ValueError(f"entry fill must be a positive price, got {entry_fill!r}")
    return math.floor(entry_fill * (1 - spec.stop_pct / 100) * 100) / 100


def decide_from_data(
    data: MarketData, symbol: str, session: Session, *, live: bool, spec: StrategySpec = SPEC
) -> Decision:
    """Fetch what decide_entry needs and call it. Unavailable data means NO_TRADE, with the reason.

    live=True reads today's bars on the live feed with the staleness check;
    live=False reads a past session on the research feed (backtests).
    HoldoutLocked is deliberately not caught: touching the holdout is a
    research-process error, not a "skip this day".
    """
    if not session.is_full_day:
        return Decision(session.date, NO_TRADE, "half day: the last half hour isn't 15:30-16:00")
    try:
        previous_close = data.previous_close(symbol, session)
        signal = data.minute_bars(
            symbol,
            session,
            spec.at(session, spec.signal_window_start),
            spec.at(session, spec.signal_time),
            feed=data.live_feed if live else data.research_feed,
        )
        if live:
            entry = data.live_minute_bars(symbol, session, spec.at(session, spec.entry_window_start))
        else:
            entry = data.minute_bars(
                symbol,
                session,
                spec.at(session, spec.entry_window_start),
                spec.at(session, spec.entry_time),
                feed=data.research_feed,
            )
    except DataUnavailable as exc:
        return Decision(session.date, NO_TRADE, f"data unavailable: {exc}")
    return decide_entry(session, previous_close, signal, entry, spec)


def _window_problem(bars: tuple[Bar, ...], start: dt.datetime, end: dt.datetime) -> str | None:
    expected = [start + i * MINUTE for i in range((end - start) // MINUTE)]
    starts = [b.start for b in bars]
    if starts != expected:
        missing = sorted(set(expected) - set(starts))
        extra = sorted(set(starts) - set(expected))
        if extra:
            return f"{len(extra)} bar(s) outside {_hm(start)}-{_hm(end)}, first at {_hm(extra[0])}"
        if missing:
            return f"{len(missing)} minute(s) missing, first at {_hm(missing[0])}"
        return "bars are duplicated or out of order"
    return None


def _hm(moment: dt.datetime) -> str:
    return moment.astimezone(ET).strftime("%H:%M")
