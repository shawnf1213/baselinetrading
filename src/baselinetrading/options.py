"""Options: which call to buy on a breakout, and how many. Pure functions, plus the frozen spec.

The signal is the stock breakout (orb.find_cross); options only change how it
is expressed: instead of shares, buy the first call strike at or above the
stock price, expiring in about a week. Alpaca takes no stop orders on
options, so the stop is managed by the engine: it sells the call when the
stock trades at or below the breakout's stop (the range low).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass

OCC_SYMBOL = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<date>\d{6})(?P<kind>[CP])(?P<strike>\d{8})$")
MULTIPLIER = 100  # shares per contract


def is_option(symbol: str) -> bool:
    return OCC_SYMBOL.match(symbol) is not None


def underlying_of(symbol: str) -> str:
    """The stock an OCC option symbol is on (the symbol itself for a stock)."""
    m = OCC_SYMBOL.match(symbol)
    return m.group("root") if m else symbol


@dataclass(frozen=True)
class OptionSpec:
    """Frozen rules for expressing the breakout with calls. Changing any value is a new trial."""

    name: str = "breakout-calls-weekly"
    min_days: int = 5  # expiry at least this many calendar days out (no same-day or next-day decay)
    max_days: int = 12
    max_premium_pct: float = 2.0  # premium paid is the risk: a call can expire worthless
    max_spread_pct: float = 15.0  # skip contracts whose bid-ask spread is wider than this share of the ask

    def fingerprint(self) -> str:
        text = json.dumps({k: str(v) for k, v in asdict(self).items()}, sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()[:16]


OPTION_SPEC = OptionSpec()


@dataclass(frozen=True)
class Contract:
    symbol: str
    expiration: dt.date
    strike: float


def choose_call(contracts: list[Contract], price: float, today: dt.date, spec: OptionSpec = OPTION_SPEC) -> Contract | None:
    """The earliest expiry in [min_days, max_days], and in it the lowest strike at or above the stock price."""
    eligible = [c for c in contracts if spec.min_days <= (c.expiration - today).days <= spec.max_days and c.strike >= price]
    if not eligible:
        return None
    expiry = min(c.expiration for c in eligible)
    return min((c for c in eligible if c.expiration == expiry), key=lambda c: c.strike)


def contracts_for(budget_usd: float, ask: float) -> int:
    """Whole contracts affordable within the budget at the ask (0 if none)."""
    if not (math.isfinite(ask) and ask > 0 and budget_usd > 0):
        return 0
    return int(budget_usd // (ask * MULTIPLIER))
