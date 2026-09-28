"""Strategy A: opening range breakout. Frozen spec in docs/decisions.md.

Rules (v1, pre-committed as suggested in docs/strategies.md, section A):
- The opening range is the high and low of the 09:30-09:35 bars.
- From 09:35 on, the first 1-minute bar that closes above the range high is
  the breakout. Buy at the next bar's open.
- The stop is the range low. Exit at the stop or at 15:55. No target.
- One entry per day, and the entry must come before 15:45 (the risk manager's
  no-new-entries window), so the last usable breakout bar is 15:43.

Like strategy C it only decides: the risk manager can veto it and the
gateway carries it out. decide_orb() is pure: given the bars that existed at
some minute, it returns BUY, WAIT (no breakout yet) or NO_TRADE, with the
inputs behind the answer.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass

from baselinetrading.bars import ET, MINUTE, Bar, Session
from baselinetrading.strategy import BUY, NO_TRADE, _hm, _window_problem

WAIT = "WAIT"


@dataclass(frozen=True)
class OrbSpec:
    """The frozen rules. Changing any value is a new strategy and a new trial."""

    name: str = "opening-range-breakout-5m"
    range_start: dt.time = dt.time(9, 30)
    range_end: dt.time = dt.time(9, 35)
    last_entry: dt.time = dt.time(15, 45)  # the entry (next bar's open) must be before this
    exit_time: dt.time = dt.time(15, 55)
    gaps_after_range: str = "allowed"  # v2 (2026-09-28): missing minutes after the range can't trigger, don't cancel

    def fingerprint(self) -> str:
        text = json.dumps({k: str(v) for k, v in asdict(self).items()}, sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def at(self, session: Session, moment: dt.time) -> dt.datetime:
        return dt.datetime.combine(session.date, moment, ET)


ORB_SPEC = OrbSpec()


@dataclass(frozen=True)
class AdaptiveOrbSpec:
    """Strategy A2: the breakout, whose opening range length steps after losing streaks.

    The adapting rule is itself frozen, so the whole thing can be backtested:
    after `losses_to_step` losing trades in a row (net of costs), the range
    moves to the next length in `ladder`, wrapping around. A win resets the
    streak but keeps the current length. Changing any value is a new trial.
    """

    name: str = "adaptive-opening-range-breakout"
    ladder: tuple[int, ...] = (5, 15, 30)  # opening range lengths in minutes
    losses_to_step: int = 3
    range_start: dt.time = dt.time(9, 30)
    last_entry: dt.time = dt.time(15, 45)
    exit_time: dt.time = dt.time(15, 55)
    gaps_after_range: str = "allowed"

    def fingerprint(self) -> str:
        text = json.dumps({k: str(v) for k, v in asdict(self).items()}, sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def range_minutes(self, outcomes: list[bool]) -> int:
        """The range length to use after these past trades (True = won net of costs), oldest first."""
        step = streak = 0
        for won in outcomes:
            streak = 0 if won else streak + 1
            if streak == self.losses_to_step:
                step, streak = (step + 1) % len(self.ladder), 0
        return self.ladder[step]

    def day_spec(self, outcomes: list[bool]) -> OrbSpec:
        """The plain breakout rules for the next day, given the trades so far."""
        start = dt.datetime.combine(dt.date(2000, 1, 3), self.range_start)
        end = (start + dt.timedelta(minutes=self.range_minutes(outcomes))).time()
        return OrbSpec(name=self.name, range_start=self.range_start, range_end=end,
                       last_entry=self.last_entry, exit_time=self.exit_time)


ADAPTIVE_ORB_SPEC = AdaptiveOrbSpec()


@dataclass(frozen=True)
class OrbInputs:
    """Everything the decision was based on, for the log."""

    range_high: float
    range_low: float
    range_pct: float  # (high - low) / low, in percent: the risk per share
    range_volume: float
    breakout_time: str | None  # HH:MM of the bar that closed above the high
    breakout_close: float | None


@dataclass(frozen=True)
class OrbDecision:
    date: dt.date
    action: str  # BUY, WAIT or NO_TRADE
    reason: str
    inputs: OrbInputs | None = None
    stop_price: float | None = None  # set only for BUY: the range low
    entry_at: dt.datetime | None = None  # set only for BUY: the bar whose open is the entry


def decide_orb(session: Session, bars: tuple[Bar, ...], spec: OrbSpec = ORB_SPEC,
               *, until: dt.datetime | None = None) -> OrbDecision:
    """The breakout decision from 1-minute bars starting at 09:30.

    `bars` are the bars that existed at the time of the decision, and `until`
    is the end of the last completed minute (default: the last bar's end).
    The opening range must be complete, with no missing minute. After it,
    missing minutes are allowed: on the free IEX feed a single stock often has
    minutes without an IEX trade, and a minute with no bar simply can't be a
    breakout. Only the first breakout counts and later bars are never read, so
    passing a whole day (as the backtest does) can't leak the future.
    """

    def result(action: str, reason: str, inputs: OrbInputs | None = None, **kw) -> OrbDecision:
        return OrbDecision(session.date, action, reason, inputs, **kw)

    if not session.is_full_day:
        return result(NO_TRADE, "half day: the strategy doesn't trade")
    start = spec.at(session, spec.range_start)
    range_end = spec.at(session, spec.range_end)
    last_breakout_end = spec.at(session, spec.last_entry) - MINUTE  # entry at that bar's open, before the cutoff
    until = until or (bars[-1].end if bars else start)
    if until < range_end:
        return result(WAIT, "the opening range isn't complete yet")
    opening = tuple(b for b in bars if b.start < range_end)
    problem = _window_problem(opening, start, range_end)
    if problem:
        return result(NO_TRADE, f"opening range: {problem}")
    later = [b for b in bars if b.start >= range_end]
    if [b.start for b in later] != sorted({b.start for b in later}):
        return result(NO_TRADE, "bars after the opening range are duplicated or out of order")

    high, low = max(b.high for b in opening), min(b.low for b in opening)
    base = dict(range_high=high, range_low=low, range_pct=(high - low) / low * 100,
                range_volume=sum(b.volume for b in opening))
    for bar in later:
        if bar.end > min(last_breakout_end, until):
            break
        if bar.close > high:
            inputs = OrbInputs(**base, breakout_time=_hm(bar.start), breakout_close=bar.close)
            return result(BUY, f"{_hm(bar.start)} bar closed at {bar.close:.2f}, above the range high {high:.2f}",
                          inputs, stop_price=low, entry_at=bar.end)
    inputs = OrbInputs(**base, breakout_time=None, breakout_close=None)
    if until >= last_breakout_end:
        return result(NO_TRADE, f"no close above the range high {high:.2f} before {_hm(last_breakout_end)}", inputs)
    return result(WAIT, f"watching for a close above {high:.2f} (stop would be {low:.2f})", inputs)
