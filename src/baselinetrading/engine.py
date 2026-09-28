"""The strategy engine: a background thread inside the backend.

Every couple of seconds it:
1. checks live data health (gap-free and fresh over the last 10 minutes);
2. reconciles positions with the broker (a stop hit, or a position without a stop);
3. flattens everything when the daily loss limit is hit or at the flatten time;
4. makes strategy C's one decision per day at 15:30:05-15:30:55 ET;
5. rebuilds the status snapshot the UI shows.

It never places orders itself: entries and exits go through the gateway, and
so through the risk manager, exactly like a click in the UI.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable
from typing import Any

from baselinetrading.bars import ET, MINUTE, DataUnavailable, Session
from baselinetrading.costs import round_trip_cost
from baselinetrading.gateway import OrderGateway
from baselinetrading.journal import summarize
from baselinetrading.market_data import MarketData
from baselinetrading.strategy import BUY, NO_TRADE, SPEC, Decision, StrategySpec, decide_from_data

TICK_SECONDS = 5.0  # ~10 broker reads per tick; Alpaca allows 200 requests a minute
HEALTH_EVERY = dt.timedelta(seconds=15)
HEALTH_LOOKBACK = dt.timedelta(minutes=10)
DECISION_DELAY = dt.timedelta(seconds=5)  # let the 15:29 bar arrive
DECISION_WINDOW = dt.timedelta(seconds=55)
STRATEGY_NOTIONAL_FRACTION = 0.98  # full account, leaving room for the price to move before the fill
_TIME_OF_DAY = ("market hasn't opened", "no new entries", "market is closed for the day")


class Engine:
    def __init__(
        self,
        gateway: OrderGateway,
        data: MarketData,
        *,
        symbol: str,
        clock: Callable[[], dt.datetime],
        spec: StrategySpec = SPEC,
    ) -> None:
        self.gateway = gateway
        self.data = data
        self.symbol = symbol
        self._clock = clock
        self._spec = spec
        self._config = gateway._config
        self._sessions: dict[dt.date, Session | None] = {}
        self._calendar_error: str | None = None
        self._last_health: dt.datetime | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_tick: dt.datetime | None = None
        self.last_error: str | None = None
        self.status: dict[str, Any] = {"state": "starting"}

    # --- lifecycle ------------------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="strategy-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(TICK_SECONDS)

    # --- one tick ---------------------------------------------------------------------------

    def tick(self) -> None:
        now = self._clock()
        try:
            session = self.session_today()
            self._check_health(session, now)
            self.gateway.reconcile()
            self._protective_exits(session, now)
            self._maybe_decide(session, now)
            self.last_error = None
        except Exception as exc:  # broker/API trouble: show it, keep ticking, never trade on it
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.gateway.journal.record("error", "system", where="engine", error=self.last_error)
        self.last_tick = now
        self.status = self.build_status()

    def session_today(self) -> Session | None:
        today = self._clock().astimezone(ET).date()
        if today not in self._sessions:
            try:
                self._sessions[today] = self.data.session_on(today)
                self._calendar_error = None
            except DataUnavailable as exc:
                self._calendar_error = str(exc)
                return None  # not cached: retried next tick
        return self._sessions[today]

    def _check_health(self, session: Session | None, now: dt.datetime) -> None:
        if self._last_health and now - self._last_health < HEALTH_EVERY:
            return
        self._last_health = now
        health = self.gateway.health
        health.checked_at = now
        if session is None:
            health.ok, health.reason, health.price = False, self._calendar_error or "market closed today", None
            return
        if not session.open <= now < session.close:
            health.ok, health.reason, health.price = False, "outside market hours", None
            return
        start = max(session.open, now.replace(second=0, microsecond=0) - HEALTH_LOOKBACK)
        try:
            bars = self.data.live_minute_bars(self.symbol, session, start)
        except DataUnavailable as exc:
            health.ok, health.reason, health.price = False, str(exc), None
            return
        health.ok, health.reason = True, ""
        health.price, health.last_bar_end = bars[-1].close, bars[-1].end

    def _protective_exits(self, session: Session | None, now: dt.datetime) -> None:
        ctx = self.gateway.context()
        if not ctx.positions:
            return
        if self.gateway.risk.loss_limit_hit(ctx.account):
            self.gateway.exit_all("system", "daily loss limit hit")
        elif session and now >= session.close - dt.timedelta(minutes=self._config.risk.flatten_minutes_before_close):
            self.gateway.exit_all("system", "flatten before the close")

    def _maybe_decide(self, session: Session | None, now: dt.datetime) -> None:
        if self.gateway.decided_today or session is None:
            return
        if not session.is_full_day:
            self._record(Decision(session.date, NO_TRADE, "half day: the strategy doesn't trade"))
            return
        entry = self._spec.at(session, self._spec.entry_time)
        if now < entry + DECISION_DELAY:
            return
        if now >= entry + DECISION_WINDOW:
            self._record(Decision(session.date, NO_TRADE, "missed the 15:30 decision (engine wasn't running then)"))
            return
        decision = decide_from_data(self.data, self.symbol, session, live=True, spec=self._spec)
        self._record(decision)
        if decision.action == BUY:
            sizing = self.gateway.risk.sizing_equity(self.gateway.context().account)
            self.gateway.submit_entry(
                "strategy", self.symbol, notional=sizing * STRATEGY_NOTIONAL_FRACTION,
                stop_pct=decision.stop_pct, inputs=decision.inputs,
            )

    def _record(self, decision: Decision) -> None:
        self.gateway.decided_today = True
        self.gateway.journal.record(
            "signal", "strategy", date=decision.date, action=decision.action, reason=decision.reason,
            inputs=decision.inputs, spec=self._spec.fingerprint(),
        )

    # --- what the UI shows ------------------------------------------------------------------

    def build_status(self) -> dict[str, Any]:
        gw = self.gateway
        now = self._clock()
        base = {
            "state": "running" if self.running else "stopped",
            "mode": "PAPER",
            "now": now.isoformat(),
            "symbol": self.symbol,
            "engine_error": self.last_error,
            "signals": gw.journal.recent(10, {"signal"}),
            "events": gw.journal.recent(40),
            "kill_switch": gw.kill_switch,
        }
        try:
            with gw.lock:
                ctx = gw.context()
        except Exception as exc:
            reason = f"broker unreachable: {type(exc).__name__}: {exc}"
            return {**base, "armed": False, "disarmed_reasons": [reason],
                    "entry": {"enabled": False, "reasons": [reason]}}
        risk = gw.risk
        session = ctx.session
        blockers = risk.entry_blockers(ctx)
        bot_reasons = [r for r in blockers if not r.startswith(_TIME_OF_DAY)]
        if session is not None and not (session.open <= now < session.close):
            bot_reasons = [r for r in bot_reasons if not r.startswith("market data is not healthy")]
        if session is not None and not session.is_full_day:
            bot_reasons.append("half day: the strategy doesn't trade")
        if gw.decided_today:
            last = gw.journal.recent(1, {"signal"})
            what = f"{last[0]['action']}: {last[0]['reason']}" if last else "done"
            bot_reasons.append(f"today's decision is made ({what})")
        if not self.running:
            bot_reasons.insert(0, "strategy engine is not running")
        if self.last_error:
            bot_reasons.append(f"last engine error: {self.last_error}")

        position = None
        if ctx.positions:
            p = ctx.positions[0]
            stops = [o for o in ctx.open_orders if o.symbol == p.symbol and o.type == "stop"]
            trade = gw.open_trade
            position = {
                "symbol": p.symbol, "qty": p.qty, "entry": p.avg_entry_price, "price": p.current_price,
                "unrealized_pl": p.unrealized_pl, "stop": stops[0].stop_price if stops else None,
                "source": trade.source if trade and trade.symbol == p.symbol else "unknown",
            }
        price = ctx.reference_price or (position["price"] if position else None)
        sizing = risk.sizing_equity(ctx.account)
        breakeven = None
        if price:
            cost = round_trip_cost(self._config.costs, notional_usd=sizing * STRATEGY_NOTIONAL_FRACTION, price=price)
            breakeven = {"round_trip_cost_usd": cost.total_usd, "round_trip_cost_bps": cost.total_bps,
                         "notional_usd": cost.notional_usd}
        entry_time = self._spec.at(session, self._spec.entry_time) if session else None
        return {
            **base,
            "armed": not bot_reasons,
            "disarmed_reasons": bot_reasons,
            "next_decision": entry_time.isoformat() if entry_time and not gw.decided_today else None,
            "entry": {"enabled": not blockers, "reasons": blockers},
            "data": {"ok": ctx.data_ok, "reason": ctx.data_reason, "price": ctx.reference_price,
                     "last_bar_end": gw.health.last_bar_end.isoformat() if gw.health.last_bar_end else None,
                     "feed": self.data.live_feed},
            "account": {"number": ctx.account.account_number, "paper": ctx.account.is_paper,
                        "equity": ctx.account.equity, "cash": ctx.account.cash, "sizing_equity": sizing,
                        "day_pnl": risk.day_pnl_usd(ctx.account),
                        "daily_loss_limit": risk.daily_loss_limit_usd(ctx.account)},
            "limits": {"max_risk_per_trade_usd": sizing * self._config.risk.max_risk_per_trade_pct / 100,
                       "entries_today": gw.entries_today, "max_entries_per_day": self._config.risk.max_entries_per_day},
            "position": position,
            "session": {"open": session.open.isoformat(), "close": session.close.isoformat()} if session else None,
            "pnl_today": summarize(gw.closed_trades),
            "breakeven": breakeven,
        }
