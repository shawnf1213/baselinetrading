"""The risk manager: one veto for every order, manual or strategy.

The account is split equally over the allowed symbols: at most one position
per symbol, each no bigger than its share of the sizing equity.

RiskManager.execute(request, context, send) is the only place a broker-changing
call can happen: it evaluates the request and runs `send` inside its own frame
only if approved (broker.requires_risk_manager checks for exactly that frame).

Entries are checked against every rule below. Exits and the kill switch are
always approved: reducing risk must never be blocked by the rules that limit
adding it (stale data, loss limit, kill switch). With trading.instrument =
"options", share purchases are refused from every source, manual included.

The reasons returned by entry_blockers() are the same strings the UI shows next
to disabled buy controls, so the screen and the code can't disagree.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from baselinetrading.bars import Session
from baselinetrading.broker import AccountSnapshot, OrderSnapshot, PositionSnapshot
from baselinetrading.config import Config
from baselinetrading.costs import round_trip_cost

MIN_NOTIONAL_USD = 1.0  # Alpaca's minimum for fractional orders
SOURCES = ("manual", "strategy", "system")
SHARES_REFUSED = 'options only (trading.instrument = "options"): share purchases are refused'


@dataclass(frozen=True)
class RiskContext:
    """Everything the rules look at, captured at one moment."""

    now: dt.datetime
    session: Session | None
    account: AccountSnapshot
    positions: list[PositionSnapshot]
    open_orders: list[OrderSnapshot]
    data_ok: bool
    data_reason: str
    reference_price: float | None  # latest validated live price; never client-supplied
    kill_switch: bool
    entries_today: int


@dataclass(frozen=True)
class EntryOrder:
    source: str
    symbol: str
    qty: float
    stop_price: float  # mandatory; checked against the reference price


@dataclass(frozen=True)
class OptionEntryOrder:
    """Buy `qty` call contracts on `underlying`; the engine sells them if the stock trades at or below stop_underlying."""

    source: str
    underlying: str
    contract: str
    qty: float
    bid: float
    ask: float
    stop_underlying: float
    max_spread_pct: float
    kind: str = "call"  # call: the stop is below the stock; put: above it


@dataclass(frozen=True)
class ExitOrder:
    source: str
    reason: str
    symbol: str | None = None  # None: every position


@dataclass(frozen=True)
class KillOrder:
    source: str
    reason: str


@dataclass(frozen=True)
class Verdict:
    approved: bool
    reasons: tuple[str, ...]
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Execution:
    verdict: Verdict
    result: Any = None


class RiskManager:
    def __init__(self, config: Config) -> None:
        self._config = config

    # --- numbers the UI also shows ----------------------------------------------------

    def sizing_equity(self, account: AccountSnapshot) -> float:
        """Equity used for sizing and limits: the account, capped by settings. Never buying power."""
        return min(account.equity, self._config.account.equity_cap_usd)

    def daily_loss_limit_usd(self, account: AccountSnapshot) -> float:
        start_of_day = min(account.last_equity, self._config.account.equity_cap_usd)
        return start_of_day * self._config.risk.max_daily_loss_pct / 100

    def day_pnl_usd(self, account: AccountSnapshot) -> float:
        """Today's P&L from the broker's own numbers, including anything done outside this app."""
        return account.equity - account.last_equity

    def loss_limit_hit(self, account: AccountSnapshot) -> bool:
        return self.day_pnl_usd(account) <= -self.daily_loss_limit_usd(account)

    # --- rules ------------------------------------------------------------------------

    def entry_blockers(self, ctx: RiskContext) -> list[str]:
        """Reasons no new position may be opened right now, whatever its size. Empty means allowed."""
        risk = self._config.risk
        reasons = []
        if not self._config.trading.enabled:
            reasons.append("trading is disabled in settings (trading.enabled = false)")
        if ctx.kill_switch:
            reasons.append("kill switch is engaged; reset it to trade again")
        if not ctx.account.is_paper:
            reasons.append(f"account {ctx.account.account_number} is not a paper account")
        if ctx.account.trading_blocked:
            reasons.append("the broker has blocked trading on this account")
        if ctx.session is None:
            reasons.append("market is closed today")
        else:
            last_entry = ctx.session.close - dt.timedelta(minutes=risk.no_new_entries_minutes_before_close)
            if ctx.now < ctx.session.open:
                reasons.append("market hasn't opened yet")
            elif ctx.now >= ctx.session.close:
                reasons.append("market is closed for the day")
            elif ctx.now >= last_entry:
                reasons.append(f"no new entries in the last {risk.no_new_entries_minutes_before_close} minutes")
        if not ctx.data_ok:
            reasons.append(f"market data is not healthy: {ctx.data_reason}")
        if ctx.entries_today >= risk.max_entries_per_day:
            reasons.append(f"entry limit reached: {ctx.entries_today} of {risk.max_entries_per_day} today")
        if self.loss_limit_hit(ctx.account):
            reasons.append(
                f"daily loss limit hit: {self.day_pnl_usd(ctx.account):+,.2f} vs "
                f"-{self.daily_loss_limit_usd(ctx.account):,.2f}"
            )
        return reasons

    def share_entry_blockers(self, ctx: RiskContext) -> list[str]:
        """entry_blockers plus the options-only rule: why no share purchase may be made (the manual ticket's reasons)."""
        reasons = self.entry_blockers(ctx)
        if self._config.trading.instrument == "options":
            reasons.append(SHARES_REFUSED)
        return reasons

    def evaluate_entry(self, order: EntryOrder, ctx: RiskContext) -> Verdict:
        reasons = self.share_entry_blockers(ctx)
        metrics: dict[str, float] = {}
        if order.source not in SOURCES:
            reasons.append(f"unknown order source {order.source!r}")
        symbols = self._config.trading.symbols
        if order.symbol not in symbols:
            reasons.append(f"{order.symbol} is not in the allowed symbols {list(symbols)}")
        from baselinetrading.options import underlying_of

        if any(underlying_of(p.symbol) == order.symbol for p in ctx.positions):
            reasons.append(f"one position per symbol: already holding {order.symbol}")
        if any(o.side == "buy" and o.symbol == order.symbol for o in ctx.open_orders):
            reasons.append(f"a buy order for {order.symbol} is already working")
        price = ctx.reference_price
        if price is None or not (math.isfinite(price) and price > 0):
            reasons.append("no valid live reference price")
            return Verdict(False, tuple(reasons), metrics)
        if not (isinstance(order.qty, (int, float)) and math.isfinite(order.qty) and order.qty > 0):
            reasons.append(f"quantity must be a positive number, got {order.qty!r}")
            return Verdict(False, tuple(reasons), metrics)
        stop = order.stop_price
        if not (isinstance(stop, (int, float)) and math.isfinite(stop) and 0 < stop < price):
            reasons.append(f"every entry needs a stop below the current price {price:.2f}, got {stop!r}")
            return Verdict(False, tuple(reasons), metrics)

        sizing = self.sizing_equity(ctx.account)
        notional = order.qty * price
        cost = round_trip_cost(self._config.costs, notional_usd=notional, price=price, exit_by_stop=True).total_usd
        trade_risk = order.qty * (price - stop) + cost
        max_risk = sizing * self._config.risk.max_risk_per_trade_pct / 100
        metrics.update(
            notional_usd=notional, risk_usd=trade_risk, max_risk_usd=max_risk, sizing_equity_usd=sizing, cost_usd=cost
        )
        if notional < MIN_NOTIONAL_USD:
            reasons.append(f"order value ${notional:,.2f} is below the ${MIN_NOTIONAL_USD:.0f} minimum")
        share = sizing / len(symbols)
        metrics.update(symbol_share_usd=share)
        if notional > share:
            reasons.append(f"order value ${notional:,.2f} exceeds {order.symbol}'s share of the account "
                           f"${share:,.2f} (sizing equity split over {len(symbols)} symbols)")
        if notional > ctx.account.cash:
            reasons.append(f"order value ${notional:,.2f} exceeds cash ${ctx.account.cash:,.2f} (no margin)")
        if trade_risk > max_risk:
            reasons.append(
                f"risk to stop ${trade_risk:,.2f} exceeds {self._config.risk.max_risk_per_trade_pct:g}% "
                f"of equity (${max_risk:,.2f})"
            )
        remaining = self.daily_loss_limit_usd(ctx.account) + self.day_pnl_usd(ctx.account)
        if trade_risk > remaining:
            reasons.append(f"risk to stop ${trade_risk:,.2f} exceeds the ${max(remaining, 0):,.2f} left of today's loss limit")
        return Verdict(not reasons, tuple(reasons), metrics)

    def evaluate_option_entry(self, order: OptionEntryOrder, ctx: RiskContext) -> Verdict:
        """Options: the premium is the risk (a call can expire worthless), so it must fit every limit."""
        from baselinetrading.options import MULTIPLIER, underlying_of

        reasons = list(self.entry_blockers(ctx))
        metrics: dict[str, float] = {}
        if order.source not in SOURCES:
            reasons.append(f"unknown order source {order.source!r}")
        symbols = self._config.trading.symbols
        if order.underlying not in symbols:
            reasons.append(f"{order.underlying} is not in the allowed symbols {list(symbols)}")
        if underlying_of(order.contract) != order.underlying:
            reasons.append(f"contract {order.contract} is not on {order.underlying}")
        if any(underlying_of(p.symbol) == order.underlying for p in ctx.positions):
            reasons.append(f"one position per symbol: already holding {order.underlying} or an option on it")
        if any(o.side == "buy" and underlying_of(o.symbol) == order.underlying for o in ctx.open_orders):
            reasons.append(f"a buy order for {order.underlying} is already working")
        price = ctx.reference_price
        if price is None or not (math.isfinite(price) and price > 0):
            reasons.append(f"no valid live price for {order.underlying}")
            return Verdict(False, tuple(reasons), metrics)
        if not (math.isfinite(order.ask) and order.ask > 0 and math.isfinite(order.bid) and 0 <= order.bid <= order.ask):
            reasons.append(f"no usable quote for {order.contract}: bid {order.bid!r}, ask {order.ask!r}")
            return Verdict(False, tuple(reasons), metrics)
        if not (float(order.qty).is_integer() and order.qty >= 1):
            reasons.append(f"options trade in whole contracts; the budget buys {order.qty!r}")
            return Verdict(False, tuple(reasons), metrics)
        stop_ok = math.isfinite(order.stop_underlying) and order.stop_underlying > 0 and (
            order.stop_underlying < price if order.kind == "call" else order.stop_underlying > price)
        if not stop_ok:
            where = "below" if order.kind == "call" else "above"
            reasons.append(f"the stop on {order.underlying} must be {where} its price {price:.2f} for a {order.kind}, "
                           f"got {order.stop_underlying!r}")
        spread_pct = (order.ask - order.bid) / order.ask * 100
        if spread_pct > order.max_spread_pct:
            reasons.append(f"{order.contract} spread {spread_pct:.1f}% of the ask is wider than {order.max_spread_pct:g}%")
        premium = order.qty * order.ask * MULTIPLIER
        sizing = self.sizing_equity(ctx.account)
        share = sizing / len(symbols)
        max_risk = sizing * self._config.risk.max_risk_per_trade_pct / 100
        remaining = self.daily_loss_limit_usd(ctx.account) + self.day_pnl_usd(ctx.account)
        metrics.update(premium_usd=premium, max_risk_usd=max_risk, symbol_share_usd=share, spread_pct=spread_pct)
        if premium > max_risk:
            reasons.append(f"premium ${premium:,.2f} exceeds {self._config.risk.max_risk_per_trade_pct:g}% of equity "
                           f"(${max_risk:,.2f}); a call can go to zero")
        if premium > share:
            reasons.append(f"premium ${premium:,.2f} exceeds {order.underlying}'s share of the account ${share:,.2f}")
        if premium > ctx.account.cash:
            reasons.append(f"premium ${premium:,.2f} exceeds cash ${ctx.account.cash:,.2f}")
        if premium > remaining:
            reasons.append(f"premium ${premium:,.2f} exceeds the ${max(remaining, 0):,.2f} left of today's loss limit")
        return Verdict(not reasons, tuple(reasons), metrics)

    # --- the single door to the broker -------------------------------------------------

    def execute(self, request: EntryOrder | OptionEntryOrder | ExitOrder | KillOrder, ctx: RiskContext,
                send: Callable[[], Any]) -> Execution:
        """Evaluate `request`; run `send` (which talks to the broker) only if approved."""
        if isinstance(request, EntryOrder):
            verdict = self.evaluate_entry(request, ctx)
        elif isinstance(request, OptionEntryOrder):
            verdict = self.evaluate_option_entry(request, ctx)
        elif isinstance(request, (ExitOrder, KillOrder)):
            if request.source not in SOURCES:
                return Execution(Verdict(False, (f"unknown order source {request.source!r}",)))
            verdict = Verdict(True, ())  # reducing risk is always allowed
        else:
            return Execution(Verdict(False, (f"unknown request type {type(request).__name__}",)))
        if not verdict.approved:
            return Execution(verdict)
        return Execution(verdict, send())
