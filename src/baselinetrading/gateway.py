"""The order gateway: the only object that holds the broker, used by the UI and the strategy.

frontend -> backend route -> OrderGateway -> RiskManager.execute -> broker

Every broker-changing call happens inside a `send` callback that only
RiskManager.execute runs. Entries are market buys followed, as soon as they
fill, by a protective stop for the filled quantity. Alpaca has no bracket orders
for fractional shares, so there is a short window without a stop. If the stop
can't be placed, the position is closed at once: a position without a stop is
not allowed to exist.
"""

from __future__ import annotations

import datetime as dt
import math
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from baselinetrading.bars import ET, Session
from baselinetrading.broker import Broker, OrderSnapshot, floor_qty
from baselinetrading.config import Config
from baselinetrading.costs import round_trip_cost
from baselinetrading.journal import Journal, Trade
from baselinetrading.risk import EntryOrder, ExitOrder, KillOrder, RiskContext, RiskManager

FILL_TIMEOUT_SECONDS = 15.0
STOP_ATTEMPTS = 3
HEALTH_MAX_AGE = dt.timedelta(seconds=45)


@dataclass
class HealthState:
    """Live data health, written by the engine and read by every entry decision."""

    ok: bool = False
    reason: str = "no data check has run yet"
    checked_at: dt.datetime | None = None
    last_bar_end: dt.datetime | None = None
    price: float | None = None


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str
    reasons: tuple[str, ...] = ()
    details: dict[str, Any] | None = None


class OrderGateway:
    def __init__(
        self,
        broker: Broker,
        risk: RiskManager,
        journal: Journal,
        config: Config,
        *,
        clock: Callable[[], dt.datetime],
        session_today: Callable[[], Session | None],
        state_dir: Path | None,
        sleep: Callable[[float], None] = time.sleep,
        fill_timeout: float = FILL_TIMEOUT_SECONDS,
    ) -> None:
        self._broker = broker
        self.risk = risk
        self.journal = journal
        self._config = config
        self._clock = clock
        self._session_today = session_today
        self._state_dir = state_dir
        self._sleep = sleep
        self._fill_timeout = fill_timeout
        self.lock = threading.RLock()
        self.symbols: tuple[str, ...] = config.trading.symbols
        self.healths: dict[str, HealthState] = {s: HealthState() for s in self.symbols}  # per symbol
        self.open_trades: dict[str, Trade] = {}  # one open trade per symbol (the account is split)
        self.closed_trades: list[Trade] = []
        self.entries_today = 0
        self.decided_today = False  # strategy C's single decision
        self.decided_symbols: set[str] = set()  # symbols the breakout is done with for today
        self.symbol_buys: dict[str, int] = {}  # the breakout's entries today, per symbol
        self.decided_specs: dict[str, str | None] = {}  # which spec made each replayed final decision
        self._day = self._today()
        self.kill_switch = self._kill_file().exists() if self._kill_file() else False
        self._replay_today()

    # --- reads --------------------------------------------------------------------------

    @property
    def health(self) -> HealthState:
        """Data health of the first symbol (the chart's)."""
        return self.healths[self.symbols[0]]

    @property
    def open_trade(self) -> Trade | None:
        """The first open trade, if any (single-symbol callers)."""
        return next(iter(self.open_trades.values()), None)

    def slice_usd(self, account) -> float:
        """Each symbol's share of the sizing equity: the account is split equally."""
        return self.risk.sizing_equity(account) / len(self.symbols)

    def context(self, symbol: str | None = None) -> RiskContext:
        self._roll_day()
        now = self._clock()
        health = self.healths.get(symbol or self.symbols[0]) or HealthState(reason=f"{symbol} is not a traded symbol")
        fresh = health.ok and health.checked_at is not None and now - health.checked_at <= HEALTH_MAX_AGE
        reason = health.reason if not health.ok else "the last data check is too old"
        return RiskContext(
            now=now,
            session=self._session_today(),
            account=self._broker.account(),
            positions=self._broker.positions(),
            open_orders=self._broker.open_orders(),
            data_ok=fresh,
            data_reason="" if fresh else reason,
            reference_price=health.price if fresh else None,
            kill_switch=self.kill_switch,
            entries_today=self.entries_today,
        )

    # --- entries ------------------------------------------------------------------------

    def submit_entry(
        self,
        source: str,
        symbol: str,
        *,
        qty: float | None = None,
        notional: float | None = None,
        stop_price: float | None = None,
        stop_pct: float | None = None,
        inputs: Any = None,
    ) -> Outcome:
        """Market buy with a mandatory stop: an absolute stop_price (manual) or stop_pct below the fill (strategy)."""
        with self.lock:
            ctx = self.context(symbol)
            price = ctx.reference_price
            if qty is None and notional is not None and price:
                qty = floor_qty(notional / price)
            # Brokers reject sub-penny stops (IEX bars can carry prices like 244.755): round down to the cent.
            planned_stop = math.floor(stop_price * 100) / 100 if isinstance(stop_price, (int, float)) and math.isfinite(stop_price) else stop_price
            if stop_pct is not None and price:
                planned_stop = math.floor(price * (1 - stop_pct / 100) * 100) / 100
            order = EntryOrder(source, symbol, qty if qty is not None else float("nan"), planned_stop)
            self.journal.record(
                "order_request", source, symbol=symbol, side="buy", qty=qty, stop_price=planned_stop,
                reference_price=price, inputs=inputs,
            )
            try:
                execution = self.risk.execute(order, ctx, lambda: self._enter(order, stop_pct))
            except Exception as exc:  # broker failure mid-flight: record it, never crash the caller
                self.journal.record("error", source, where="entry", error=f"{type(exc).__name__}: {exc}")
                return Outcome(False, f"entry failed: {exc}")
            if not execution.verdict.approved:
                self.journal.record("veto", source, symbol=symbol, reasons=execution.verdict.reasons)
                return Outcome(False, "vetoed by the risk manager", execution.verdict.reasons)
            self.journal.record("risk_approved", source, symbol=symbol, metrics=execution.verdict.metrics)
            return execution.result

    def _enter(self, order: EntryOrder, stop_pct: float | None) -> Outcome:
        client_id = f"{order.source}-{uuid.uuid4().hex[:12]}"
        self.entries_today += 1  # counts on submission, so a failed fill can't be retried in a loop
        submitted = self._broker.submit_market(order.symbol, "buy", order.qty, client_id)
        self.journal.record("order_submitted", order.source, order_id=submitted.id, client_order_id=client_id,
                            symbol=order.symbol, side="buy", qty=order.qty)
        filled = self._wait_for_fill(submitted)
        if filled.is_open:  # partial or no fill by the deadline: keep what filled, cancel the rest
            self._broker.cancel_order(filled.id)
            filled = self._broker.get_order(filled.id)
        if filled.filled_qty <= 0 or filled.filled_avg_price is None:
            self.journal.record("entry_unfilled", order.source, order_id=filled.id, status=filled.status)
            return Outcome(False, f"entry order {filled.status} with nothing filled")
        qty, entry = filled.filled_qty, filled.filled_avg_price
        self.journal.record("fill", order.source, order_id=filled.id, side="buy", qty=qty, price=entry,
                            partial=qty < order.qty)
        stop = order.stop_price if stop_pct is None else math.floor(entry * (1 - stop_pct / 100) * 100) / 100
        cost = round_trip_cost(self._config.costs, notional_usd=qty * entry, price=entry).total_usd
        trade = Trade(order.source, order.symbol, qty, entry, self._clock().isoformat(), stop, None, cost)
        self.open_trades[order.symbol] = trade
        if stop >= entry:
            self.journal.record("unprotected", order.source, reason=f"stop {stop} is not below the fill {entry}")
            self._close_everything(order.source, "stop would be above the fill", order.symbol)
            return Outcome(False, f"filled at {entry} but the stop {stop} is not below it; position closed")
        for attempt in range(1, STOP_ATTEMPTS + 1):
            try:
                stop_order = self._broker.submit_stop_sell(order.symbol, qty, stop, f"{client_id}-stop")
                trade.stop_order_id = stop_order.id
                self.journal.record("stop_placed", order.source, order_id=stop_order.id, qty=qty, stop_price=stop)
                self._persist_trade("trade_opened", trade)
                return Outcome(True, f"bought {qty:g} {order.symbol} at {entry:.2f}, stop {stop:.2f}",
                               details={"qty": qty, "entry": entry, "stop": stop})
            except Exception as exc:
                self.journal.record("error", order.source, where="stop", attempt=attempt, error=str(exc))
        self.journal.record("unprotected", order.source, reason="stop could not be placed; closing the position")
        self._close_everything(order.source, "stop could not be placed", order.symbol)
        return Outcome(False, "stop could not be placed; position closed")

    def _wait_for_fill(self, order: OrderSnapshot) -> OrderSnapshot:
        deadline = time.monotonic() + self._fill_timeout
        while order.is_open and time.monotonic() < deadline:
            self._sleep(0.5)
            order = self._broker.get_order(order.id)
        return order

    # --- exits and the kill switch --------------------------------------------------------

    def exit_all(self, source: str, reason: str, symbol: str | None = None) -> Outcome:
        """Close every position, or only `symbol`'s."""
        with self.lock:
            ctx = self.context()
            self.journal.record("order_request", source, side="exit_all" if symbol is None else "exit", symbol=symbol,
                                reason=reason)
            try:
                execution = self.risk.execute(ExitOrder(source, reason, symbol), ctx,
                                              lambda: self._close_everything(source, reason, symbol))
            except Exception as exc:
                self.journal.record("error", source, where="exit", error=f"{type(exc).__name__}: {exc}")
                return Outcome(False, f"exit failed: {exc}")
            if not execution.verdict.approved:
                return Outcome(False, "vetoed", execution.verdict.reasons)
            return execution.result

    def kill(self, reason: str) -> Outcome:
        """Engage the kill switch (persisted), cancel every order and flatten every position."""
        with self.lock:
            self.kill_switch = True
            if self._kill_file():
                self._kill_file().parent.mkdir(parents=True, exist_ok=True)
                self._kill_file().write_text(f"{self._clock().isoformat()} {reason}\n")
            self.journal.record("kill_switch", "manual", reason=reason)
            ctx = self.context()
            try:
                execution = self.risk.execute(KillOrder("manual", reason), ctx, lambda: self._close_everything("manual", f"kill switch: {reason}"))
            except Exception as exc:
                self.journal.record("error", "manual", where="kill", error=f"{type(exc).__name__}: {exc}")
                return Outcome(False, f"kill switch engaged, but flattening failed: {exc}. Check the Alpaca dashboard.")
            return execution.result

    def reset_kill(self) -> None:
        with self.lock:
            self.kill_switch = False
            if self._kill_file() and self._kill_file().exists():
                self._kill_file().unlink()
            self.journal.record("kill_switch_reset", "manual")

    def _close_everything(self, source: str, reason: str, symbol: str | None = None) -> Outcome:
        """Cancel orders first (a working stop holds the shares), then close positions: all, or one symbol's."""
        if symbol is None:
            self._broker.cancel_all()
        else:
            for order in self._broker.open_orders():
                if order.symbol == symbol:
                    self._broker.cancel_order(order.id)
        closed = []
        for position in self._broker.positions():
            if symbol is not None and position.symbol != symbol:
                continue
            order = self._broker.close_position(position.symbol)
            if order is not None:
                order = self._wait_for_fill(order)
                price = order.filled_avg_price
            else:
                price = None
            closed.append(position.symbol)
            self.journal.record("exit", source, symbol=position.symbol, qty=position.qty, price=price, reason=reason)
            if position.symbol in self.open_trades:
                self._finish_trade(position.symbol, price if price is not None else position.current_price, reason)
        message = f"closed {', '.join(closed)}" if closed else "no positions to close"
        return Outcome(True, message, details={"reason": reason})

    # --- reconciliation (called by the engine every tick) ----------------------------------

    def reconcile(self) -> None:
        """Match our trade record to the broker, and never leave a position without a stop."""
        with self.lock:
            positions = {p.symbol: p for p in self._broker.positions()}
            for trade in list(self.open_trades.values()):
                if trade.symbol in positions:
                    continue
                price, reason = None, "closed outside this app"
                if trade.stop_order_id:
                    stop = self._broker.get_order(trade.stop_order_id)
                    if stop.status == "filled":
                        price, reason = stop.filled_avg_price, "stop hit"
                self.journal.record("exit", trade.source, symbol=trade.symbol, qty=trade.qty, price=price, reason=reason)
                self._finish_trade(trade.symbol, price if price is not None else trade.entry_price, reason)
            open_orders = self._broker.open_orders()
            for symbol, position in positions.items():
                covered = sum(o.qty for o in open_orders if o.symbol == symbol and o.side == "sell" and o.type == "stop")
                if covered + 1e-9 < position.qty:
                    self.journal.record("unprotected", "system", symbol=symbol, qty=position.qty, stop_qty=covered)
                    self.exit_all("system", f"{symbol} position without a full stop")
                    return

    # --- internals -------------------------------------------------------------------------

    def _finish_trade(self, symbol: str, price: float, reason: str) -> None:
        trade = self.open_trades.pop(symbol, None)
        if trade is None:
            return
        trade.exit_price, trade.exit_time, trade.exit_reason = price, self._clock().isoformat(), reason
        self.closed_trades.append(trade)
        self._persist_trade("trade_closed", trade)

    def _persist_trade(self, kind: str, trade: Trade) -> None:
        self.journal.record(kind, trade.source, trade=trade, gross_pnl=trade.gross_pnl, net_pnl=trade.net_pnl)

    def _replay_today(self) -> None:
        opened: dict[str, Trade] = {}
        for event in self.journal.load_day(self._day):
            kind = event.get("kind")
            if kind == "order_submitted" and event.get("side") == "buy":
                self.entries_today += 1
            elif kind == "signal":
                if event.get("symbol") and event.get("action") == "BUY":
                    self.symbol_buys[event["symbol"]] = self.symbol_buys.get(event["symbol"], 0) + 1
                elif event.get("symbol"):
                    self.decided_symbols.add(event["symbol"])
                    self.decided_specs[event["symbol"]] = event.get("spec")
                else:
                    self.decided_today = True
            elif kind in ("trade_opened", "trade_closed"):
                trade = Trade(**event["trade"])
                if kind == "trade_opened":
                    opened[trade.entry_time] = trade
                else:
                    opened.pop(trade.entry_time, None)
                    self.closed_trades.append(trade)
        for trade in opened.values():
            self.open_trades[trade.symbol] = trade

    def _roll_day(self) -> None:
        today = self._today()
        if today != self._day:
            self._day, self.entries_today, self.decided_today, self.closed_trades = today, 0, False, []
            self.decided_symbols, self.symbol_buys = set(), {}

    def _today(self) -> dt.date:
        return self._clock().astimezone(ET).date()

    def _kill_file(self) -> Path | None:
        return None if self._state_dir is None else self._state_dir / "KILL_SWITCH"
