"""The broker boundary: plain snapshots in, guarded orders out. Alpaca PAPER only.

Every method that changes anything at the broker (submit, cancel, close) is
decorated with @requires_risk_manager. At call time it walks the call stack and
raises RiskBypass unless RiskManager.execute is on it. That makes "an order
reached Alpaca without passing the risk manager" impossible at runtime, not
just untested. Read-only methods (account, positions, orders) are unguarded.
"""

from __future__ import annotations

import datetime as dt
import functools
import math
import sys
from dataclasses import dataclass
from typing import Protocol

from baselinetrading.credentials import Credentials

PAPER_ACCOUNT_PREFIX = "PA"  # Alpaca paper account numbers start with PA


class RiskBypass(RuntimeError):
    """A broker-changing call was made without RiskManager.execute on the stack. Always a bug."""


class BrokerError(RuntimeError):
    """The broker refused or failed a request."""


def requires_risk_manager(method):
    """Refuse to run unless RiskManager.execute is somewhere up the call stack."""

    @functools.wraps(method)
    def guarded(*args, **kwargs):
        from baselinetrading.risk import RiskManager  # imported late: risk.py imports this module

        target = RiskManager.execute.__code__
        frame = sys._getframe(1)
        while frame is not None:
            if frame.f_code is target:
                return method(*args, **kwargs)
            frame = frame.f_back
        raise RiskBypass(f"{method.__qualname__} called without RiskManager.execute in the call stack")

    guarded.risk_guarded = True
    return guarded


@dataclass(frozen=True)
class AccountSnapshot:
    account_number: str
    equity: float
    last_equity: float  # equity at the previous close: today's P&L = equity - last_equity
    cash: float
    buying_power: float
    trading_blocked: bool

    @property
    def is_paper(self) -> bool:
        return self.account_number.startswith(PAPER_ACCOUNT_PREFIX)


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    unrealized_pl: float


@dataclass(frozen=True)
class OrderSnapshot:
    id: str
    client_order_id: str
    symbol: str
    side: str  # buy | sell
    type: str  # market | stop
    qty: float
    filled_qty: float
    filled_avg_price: float | None
    stop_price: float | None
    status: str  # new, accepted, partially_filled, filled, canceled, rejected, expired, ...
    submitted_at: dt.datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.status not in {"filled", "canceled", "expired", "rejected", "done_for_day", "replaced"}


class Broker(Protocol):
    def account(self) -> AccountSnapshot: ...
    def positions(self) -> list[PositionSnapshot]: ...
    def open_orders(self) -> list[OrderSnapshot]: ...
    def get_order(self, order_id: str) -> OrderSnapshot: ...
    def submit_market(self, symbol: str, side: str, qty: float, client_order_id: str) -> OrderSnapshot: ...
    def submit_stop_sell(self, symbol: str, qty: float, stop_price: float, client_order_id: str) -> OrderSnapshot: ...
    def cancel_order(self, order_id: str) -> None: ...
    def cancel_all(self) -> None: ...
    def close_position(self, symbol: str) -> OrderSnapshot | None: ...


MUTATING_METHODS = ("submit_market", "submit_stop_sell", "cancel_order", "cancel_all", "close_position")


class AlpacaBroker:
    """Broker on Alpaca's paper endpoint. There is no live mode to switch on."""

    def __init__(self, credentials: Credentials) -> None:
        from alpaca.trading.client import TradingClient

        from alpaca.data.historical.option import OptionHistoricalDataClient

        self._client = TradingClient(credentials.key_id.reveal(), credentials.secret_key.reveal(), paper=True)
        self._options = OptionHistoricalDataClient(credentials.key_id.reveal(), credentials.secret_key.reveal())
        account = self.account()
        if not account.is_paper:
            raise BrokerError(f"account {account.account_number} is not a paper account; refusing to start")

    def account(self) -> AccountSnapshot:
        a = self._client.get_account()
        return AccountSnapshot(
            account_number=str(a.account_number),
            equity=float(a.equity),
            last_equity=float(a.last_equity),
            cash=float(a.cash),
            buying_power=float(a.buying_power),
            trading_blocked=bool(a.trading_blocked or a.account_blocked or a.trade_suspended_by_user),
        )

    def positions(self) -> list[PositionSnapshot]:
        return [
            PositionSnapshot(
                symbol=p.symbol,
                qty=float(p.qty),
                avg_entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price or p.avg_entry_price),
                unrealized_pl=float(p.unrealized_pl or 0.0),
            )
            for p in self._client.get_all_positions()
        ]

    def open_orders(self) -> list[OrderSnapshot]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        return [_order(o) for o in self._client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))]

    def get_order(self, order_id: str) -> OrderSnapshot:
        return _order(self._client.get_order_by_id(order_id))

    def option_contracts(self, underlying: str, expires_from: dt.date, expires_to: dt.date, kind: str = "call") -> list:
        """Tradable `kind` ("call" or "put") contracts on `underlying` expiring in [expires_from, expires_to]. Read-only."""
        from alpaca.trading.requests import GetOptionContractsRequest

        from baselinetrading.options import Contract

        out, token = [], None
        while True:
            page = self._client.get_option_contracts(GetOptionContractsRequest(
                underlying_symbols=[underlying], type=kind, expiration_date_gte=expires_from,
                expiration_date_lte=expires_to, limit=1000, page_token=token,
            ))
            out += [Contract(c.symbol, c.expiration_date, float(c.strike_price))
                    for c in page.option_contracts or [] if c.tradable]
            token = page.next_page_token
            if not token:
                return out

    def option_quote(self, symbol: str) -> tuple[float, float]:
        """Latest (bid, ask) for an option contract. Read-only."""
        from alpaca.data.requests import OptionLatestQuoteRequest

        quote = self._options.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=symbol))[symbol]
        return float(quote.bid_price), float(quote.ask_price)

    def stocks_with_options(self) -> list[tuple[str, str, str]]:
        """(symbol, name, exchange) of every active, tradable US equity with listed options. Read-only."""
        from alpaca.trading.enums import AssetClass, AssetStatus
        from alpaca.trading.requests import GetAssetsRequest

        assets = self._client.get_all_assets(GetAssetsRequest(
            status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY, attributes="options_enabled"))
        return [(a.symbol, a.name or "", getattr(a.exchange, "value", str(a.exchange))) for a in assets if a.tradable]

    @requires_risk_manager
    def submit_market(self, symbol: str, side: str, qty: float, client_order_id: str) -> OrderSnapshot:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        request = MarketOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide(side), time_in_force=TimeInForce.DAY, client_order_id=client_order_id
        )
        return _order(self._client.submit_order(request))

    @requires_risk_manager
    def submit_stop_sell(self, symbol: str, qty: float, stop_price: float, client_order_id: str) -> OrderSnapshot:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import StopOrderRequest

        request = StopOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,  # fractional stops only support DAY
            stop_price=stop_price,
            client_order_id=client_order_id,
        )
        return _order(self._client.submit_order(request))

    @requires_risk_manager
    def cancel_order(self, order_id: str) -> None:
        self._client.cancel_order_by_id(order_id)

    @requires_risk_manager
    def cancel_all(self) -> None:
        self._client.cancel_orders()

    @requires_risk_manager
    def close_position(self, symbol: str) -> OrderSnapshot | None:
        return _order(self._client.close_position(symbol))


def _order(o) -> OrderSnapshot:
    def num(value) -> float | None:
        return None if value is None else float(value)

    return OrderSnapshot(
        id=str(o.id),
        client_order_id=str(o.client_order_id),
        symbol=o.symbol,
        side=str(getattr(o.side, "value", o.side)),
        type=str(getattr(o.type, "value", o.type)),
        qty=num(o.qty) or 0.0,
        filled_qty=num(o.filled_qty) or 0.0,
        filled_avg_price=num(o.filled_avg_price),
        stop_price=num(o.stop_price),
        status=str(getattr(o.status, "value", o.status)),
        submitted_at=o.submitted_at,
    )


def floor_qty(qty: float, decimals: int = 4) -> float:
    """Round a share quantity down, so rounding never increases size."""
    factor = 10**decimals
    return math.floor(qty * factor) / factor
