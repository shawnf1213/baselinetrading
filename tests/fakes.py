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


# --- a scriptable fake broker, guarded exactly like AlpacaBroker ------------------------

import itertools

from baselinetrading.broker import AccountSnapshot, OrderSnapshot, PositionSnapshot, requires_risk_manager


class FakeBroker:
    """In-memory paper broker. Market orders fill at `price` (or partially / not at all)."""

    def __init__(self, *, cash: float = 100_000.0, price: float = 500.0, account_number: str = "PA123TEST"):
        self.cash = cash
        self.equity = cash
        self.last_equity = cash
        self.price = price
        self.account_number = account_number
        self.trading_blocked = False
        self.positions_ = {}  # symbol -> [qty, avg]
        self.orders = {}
        self.fill_fraction = 1.0  # 0.5 = partial fill, 0.0 = never fills
        self.fail_stops = False
        self.mutations = []
        self._ids = itertools.count(1)

    # reads
    def account(self):
        return AccountSnapshot(self.account_number, self.equity, self.last_equity, self.cash, self.cash * 4, self.trading_blocked)

    def positions(self):
        return [
            PositionSnapshot(s, q, avg, self.price, (self.price - avg) * q) for s, (q, avg) in self.positions_.items()
        ]

    def open_orders(self):
        return [o for o in self.orders.values() if o.is_open]

    def get_order(self, order_id):
        return self.orders[order_id]

    # writes
    @requires_risk_manager
    def submit_market(self, symbol, side, qty, client_order_id):
        self.mutations.append(("submit_market", symbol, side, qty))
        filled = qty if side == "sell" else round(qty * self.fill_fraction, 4)
        status = "filled" if filled == qty else ("partially_filled" if filled else "accepted")
        order = self._store(symbol, side, "market", qty, filled, self.price if filled else None, None, status, client_order_id)
        if filled:
            self._apply(symbol, side, filled, self.price)
        return order

    @requires_risk_manager
    def submit_stop_sell(self, symbol, qty, stop_price, client_order_id):
        self.mutations.append(("submit_stop_sell", symbol, qty, stop_price))
        if self.fail_stops:
            raise ConnectionError("simulated stop rejection")
        return self._store(symbol, "sell", "stop", qty, 0.0, None, stop_price, "accepted", client_order_id)

    @requires_risk_manager
    def cancel_order(self, order_id):
        self.mutations.append(("cancel_order", order_id))
        self._cancel(order_id)

    @requires_risk_manager
    def cancel_all(self):
        self.mutations.append(("cancel_all",))
        for order_id in list(self.orders):
            self._cancel(order_id)

    @requires_risk_manager
    def close_position(self, symbol):
        self.mutations.append(("close_position", symbol))
        if symbol not in self.positions_:
            return None
        qty = self.positions_[symbol][0]
        order = self._store(symbol, "sell", "market", qty, qty, self.price, None, "filled", f"close-{symbol}")
        self._apply(symbol, "sell", qty, self.price)
        return order

    # test helpers (not broker API)
    def trigger_stop(self, price):
        for order in list(self.orders.values()):
            if order.type == "stop" and order.is_open and price <= order.stop_price:
                self.orders[order.id] = _replace(order, status="filled", filled_qty=order.qty, filled_avg_price=price)
                self._apply(order.symbol, "sell", order.qty, price)

    def open_position_outside_app(self, symbol, qty, price):
        self._apply(symbol, "buy", qty, price)

    def _store(self, symbol, side, type_, qty, filled, fill_price, stop_price, status, client_order_id):
        order = OrderSnapshot(str(next(self._ids)), client_order_id, symbol, side, type_, qty, filled, fill_price, stop_price, status)
        self.orders[order.id] = order
        return order

    def _cancel(self, order_id):
        order = self.orders[order_id]
        if order.is_open:
            self.orders[order_id] = _replace(order, status="canceled")

    def _apply(self, symbol, side, qty, price):
        held, avg = self.positions_.get(symbol, [0.0, 0.0])
        if side == "buy":
            self.cash -= qty * price
            self.positions_[symbol] = [held + qty, (held * avg + qty * price) / (held + qty)]
        else:
            self.cash += qty * price
            left = round(held - qty, 6)
            if left <= 0:
                self.positions_.pop(symbol, None)
            else:
                self.positions_[symbol] = [left, avg]
        self.equity = self.cash + sum(q * self.price for q, _ in self.positions_.values())


def _replace(order, **changes):
    import dataclasses

    return dataclasses.replace(order, **changes)
