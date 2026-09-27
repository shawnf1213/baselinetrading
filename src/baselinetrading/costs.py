"""Transaction costs and the breakeven win rate.

A strategy has to clear its costs before it can have an edge, so every report
starts from two things computed here:

* round_trip_cost(): what one buy-then-sell costs, itemised, in dollars.
* breakeven_win_rate(): the win rate at which expected net P&L is zero.

Regulatory fees are charged per order and usually rounded UP to the cent, so
they behave like a fixed cost per trade: on a $20 sale a $0.000006 fee becomes
$0.01. That's why costs are computed in dollars from the actual order size,
never as a flat percentage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_UP, Decimal

from baselinetrading.config import CostConfig

BPS = 10_000  # 1 basis point (bp) = 0.01%
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class RoundTripCost:
    """Itemised cost of one long round trip (buy, then sell), in dollars."""

    notional_usd: float
    spread_usd: float
    slippage_usd: float
    commission_usd: float
    sec_fee_usd: float
    finra_taf_usd: float
    cat_fee_usd: float

    @property
    def fees_usd(self) -> float:
        return self.commission_usd + self.sec_fee_usd + self.finra_taf_usd + self.cat_fee_usd

    @property
    def total_usd(self) -> float:
        return self.spread_usd + self.slippage_usd + self.fees_usd

    @property
    def total_bps(self) -> float:
        """Total cost in basis points of the position's value."""
        return self.total_usd / self.notional_usd * BPS


def round_trip_cost(
    costs: CostConfig, *, notional_usd: float, price: float, exit_by_stop: bool = False
) -> RoundTripCost:
    """Cost of buying `notional_usd` worth of shares at `price` and selling them again.

    Spread: half the quoted spread on each side, so one full spread per round trip.
    Slippage: price drift between decision and fill, on both orders. Stop exits
        pay extra because a stop only becomes a market order after the price has
        already gone through it.
    Fees: SEC fee and FINRA TAF on the sell only; CAT fee and commission on both
        orders. Each is rounded up to the cent per order if the config says so.
    """
    if not (math.isfinite(notional_usd) and notional_usd > 0):
        raise ValueError(f"notional_usd must be a positive finite number, got {notional_usd!r}")
    if not (math.isfinite(price) and price > 0):
        raise ValueError(f"price must be a positive finite number, got {price!r}")
    shares = notional_usd / price
    slippage_bps = 2 * costs.slippage_bps_per_side + (costs.stop_extra_slippage_bps if exit_by_stop else 0.0)
    round_up = costs.round_each_fee_up_to_cent
    taf = min(_dec(shares) * _dec(costs.finra_taf_per_share_usd), _dec(costs.finra_taf_max_usd))
    return RoundTripCost(
        notional_usd=notional_usd,
        spread_usd=shares * costs.spread_usd_per_share,
        slippage_usd=notional_usd * slippage_bps / BPS,
        commission_usd=2 * costs.commission_per_order_usd,
        sec_fee_usd=_charged(_dec(notional_usd) * _dec(costs.sec_fee_per_million_usd) / 1_000_000, round_up),
        finra_taf_usd=_charged(taf, round_up),
        cat_fee_usd=2 * _charged(_dec(shares) * _dec(costs.cat_fee_per_share_usd), round_up),
    )


def breakeven_win_rate(*, avg_win: float, avg_loss: float, cost: float, loss_cost: float | None = None) -> float:
    """Win rate at which expected net P&L per trade is exactly zero.

    avg_win and avg_loss are GROSS sizes (before costs), both positive. `cost`
    is the round-trip cost of a trade; `loss_cost` optionally gives losers a
    different one (stop exits slip more) and defaults to `cost`. All in the same
    units: dollars or bps.

        net win  = avg_win  - cost
        net loss = avg_loss + loss_cost
        p* x net win = (1 - p*) x net loss   =>   p* = net loss / (net win + net loss)

    With no costs and avg_win == avg_loss this gives the familiar 50%. Returns
    math.inf when costs eat the whole win: then no win rate is profitable.

    For realised results, pass the averages of NET wins and losses with cost=0;
    then "win rate above p*" is exactly "positive expected value".
    """
    loss_cost = cost if loss_cost is None else loss_cost
    for name, value in (("avg_win", avg_win), ("avg_loss", avg_loss)):
        if not (math.isfinite(value) and value > 0):
            raise ValueError(f"{name} must be a positive finite number, got {value!r}")
    for name, value in (("cost", cost), ("loss_cost", loss_cost)):
        if not (math.isfinite(value) and value >= 0):
            raise ValueError(f"{name} must be a non-negative finite number, got {value!r}")
    net_win = avg_win - cost
    net_loss = avg_loss + loss_cost
    if net_win <= 0:
        return math.inf
    return net_loss / (net_win + net_loss)


def expected_net_per_trade(
    *, win_rate: float, avg_win: float, avg_loss: float, cost: float, loss_cost: float | None = None
) -> float:
    """Average net P&L per trade: p x (avg_win - cost) - (1 - p) x (avg_loss + loss_cost)."""
    loss_cost = cost if loss_cost is None else loss_cost
    return win_rate * (avg_win - cost) - (1 - win_rate) * (avg_loss + loss_cost)


def _dec(value: float) -> Decimal:
    # repr() gives the shortest decimal that round-trips, so 0.07 stays 0.07. Plain float
    # maths would round up 0.07 * 100 = 7.000000000000001 to 8 cents.
    return Decimal(repr(value))


def _charged(amount: Decimal, round_up: bool) -> float:
    """A fee as the broker charges it: rounded up to the next whole cent if round_up."""
    if round_up:
        amount = amount.quantize(_CENT, rounding=ROUND_UP)
    return float(amount)
