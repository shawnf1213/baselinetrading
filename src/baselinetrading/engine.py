"""The strategy engine: a background thread inside the backend.

Every couple of seconds it:
1. checks live data health (gap-free and fresh over the last 10 minutes);
2. reconciles positions with the broker (a stop hit, or a position without a stop);
3. flattens everything when the daily loss limit is hit or at the flatten time;
4. runs the configured strategy: C's one decision at 15:30:05-15:30:55 ET, or
   the opening range breakout on every symbol, checked once a minute until 15:44;
5. rebuilds the status snapshot the UI shows.

It never places orders itself: entries and exits go through the gateway, and
so through the risk manager, exactly like a click in the UI.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from collections.abc import Callable
from typing import Any

from baselinetrading.bars import ET, MINUTE, DataUnavailable, Session
from baselinetrading.costs import round_trip_cost
from baselinetrading.gateway import OrderGateway
from baselinetrading.journal import summarize
from baselinetrading.market_data import MarketData
from baselinetrading.options import is_put, underlying_of
from baselinetrading.orb import ADAPTIVE_ORB_SPEC, BUY_PUT, ORB_SPEC, WAIT, OrbSpec, find_cross
from baselinetrading.strategy import BUY, NO_TRADE, SPEC, Decision, StrategySpec, decide_from_data

TICK_SECONDS = 5.0  # ~10 broker reads per tick; Alpaca allows 200 requests a minute
HEALTH_EVERY = dt.timedelta(seconds=15)
HEALTH_LOOKBACK = dt.timedelta(minutes=10)
DECISION_DELAY = dt.timedelta(seconds=5)  # let the 15:29 bar arrive
DECISION_WINDOW = dt.timedelta(seconds=55)
STRATEGY_NOTIONAL_FRACTION = 0.98  # full account, leaving room for the price to move before the fill
BREAKOUTS = ("opening_range_breakout", "adaptive_opening_range_breakout")
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
        strategy: str | None = None,
        orb_spec: OrbSpec = ORB_SPEC,
    ) -> None:
        self.gateway = gateway
        self.data = data
        self.symbol = symbol  # the chart's symbol, and strategy C's only one
        self.symbols: tuple[str, ...] = gateway.symbols  # every symbol the breakout trades
        self._clock = clock
        self._spec = spec
        self._config = gateway._config
        self.strategy = strategy or self._config.trading.strategy
        self._orb_spec = orb_spec
        self._orb_checked: dict[str, dt.datetime] = {}  # per symbol: the minute last evaluated
        self.watching: dict[str, str] = {}  # per symbol: the breakout's latest WAIT reason
        self.ranges: dict[str, tuple[dt.date, float, float]] = {}  # per symbol: (session date, range high, range low)
        # The adaptive breakout's memory: every closed strategy trade, oldest first.
        state_dir = gateway._state_dir
        self._outcomes_path = state_dir / "adaptive_orb_outcomes.json" if state_dir else None
        self._outcomes: list[dict] = self._load_outcomes()
        if self.strategy in BREAKOUTS:  # a "done for today" made under other rules doesn't bind these ones
            current = (ADAPTIVE_ORB_SPEC if self.strategy == "adaptive_opening_range_breakout" else orb_spec).fingerprint()
            for symbol, spec in list(gateway.decided_specs.items()):
                if spec != current:
                    gateway.decided_symbols.discard(symbol)
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
            self._option_stops()
            if self.strategy in BREAKOUTS:
                self._note_outcomes()
                self._maybe_decide_orb(session, now)
            else:
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
        """Per symbol: recent live bars that parse, and a fresh newest bar.

        The breakout strategies accept missing minutes (a single stock often has
        minutes without an IEX trade); strategy C keeps its gap-free rule.
        """
        if self._last_health and now - self._last_health < HEALTH_EVERY:
            return
        self._last_health = now
        symbols = self.symbols if self.strategy in BREAKOUTS else (self.symbol,)
        for symbol in symbols:
            health = self.gateway.healths[symbol]
            health.checked_at = now
            if session is None:
                health.ok, health.reason, health.price = False, self._calendar_error or "market closed today", None
                continue
            if not session.open <= now < session.close:
                health.ok, health.reason, health.price = False, "outside market hours", None
                continue
            start = max(session.open, now.replace(second=0, microsecond=0) - HEALTH_LOOKBACK)
            try:
                bars = self.data.live_minute_bars(symbol, session, start, allow_gaps=self.strategy in BREAKOUTS)
            except DataUnavailable as exc:
                health.ok, health.reason, health.price = False, str(exc), None
                continue
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

    def _option_stops(self) -> None:
        """Alpaca takes no stop orders on options: sell a call when its stock's latest price is at or below the stop."""
        for key, trade in list(self.gateway.open_trades.items()):
            if not trade.stop_underlying:
                continue
            health = self.gateway.healths.get(key)
            if not (health and health.ok and health.price is not None):
                continue
            put = is_put(trade.symbol)
            if (health.price >= trade.stop_underlying) if put else (health.price <= trade.stop_underlying):
                where = "at or above" if put else "at or below"
                self.gateway.exit_all("system", f"{key} traded at {health.price:.2f}, {where} the stop "
                                                f"{trade.stop_underlying:.2f}", symbol=key)

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

    def _maybe_decide_orb(self, session: Session | None, now: dt.datetime) -> None:
        """Once a minute from the end of the opening range, for every symbol that's flat: buy when the bar
        that just closed crossed above that symbol's range high, with that symbol's share of the account.
        Up to the spec's max entries a day per symbol; a stop-out re-arms it for the next cross."""
        if session is None:
            self.watching = {}
            return
        gw = self.gateway
        minute = now.replace(second=0, microsecond=0)
        for symbol in self.symbols:
            if symbol in gw.decided_symbols:
                self.watching.pop(symbol, None)
                continue
            spec = self.active_orb_spec(symbol)
            if not session.is_full_day:
                self._record(Decision(session.date, NO_TRADE, "half day: the strategy doesn't trade"), symbol)
                continue
            if now < spec.at(session, spec.range_end) + DECISION_DELAY:
                continue
            buys = gw.symbol_buys.get(symbol, 0)
            if now >= spec.at(session, spec.last_entry):
                if buys:
                    gw.decided_symbols.add(symbol)
                else:
                    reason = self.watching.get(symbol) or "no cross was seen (engine wasn't running or data was unavailable)"
                    self._record(Decision(session.date, NO_TRADE, f"no entry before {spec.last_entry:%H:%M}: {reason}"), symbol)
                continue
            if buys >= spec.max_entries:
                gw.decided_symbols.add(symbol)
                continue
            if symbol in gw.open_trades:
                self.watching[symbol] = f"holding (entry {buys} of {spec.max_entries} today)"
                continue
            if self._orb_checked.get(symbol) == minute or now - minute < DECISION_DELAY:
                continue
            self._orb_checked[symbol] = minute
            try:
                bars = self.data.live_minute_bars(symbol, session, session.open, allow_gaps=True, fresh=False)
            except DataUnavailable as exc:  # retried next minute; the status shows why
                self.watching[symbol] = f"data unavailable: {exc}"
                continue
            options = self._config.trading.instrument == "options"
            decision = find_cross(session, bars, spec, after=minute - MINUTE, until=minute)
            if decision.inputs:  # the range levels, for the watchlist and the chart
                self.ranges[symbol] = (session.date, decision.inputs.range_high, decision.inputs.range_low)
            if options and decision.action == WAIT:  # puts: the mirror image, a cross below the range low
                down = find_cross(session, bars, spec, after=minute - MINUTE, until=minute, direction="down")
                if down.action == BUY_PUT:
                    decision = down
                else:
                    decision = type(decision)(decision.date, WAIT, f"{decision.reason}; or below {down.inputs.range_low:.2f} "
                                              f"for puts" if down.inputs else decision.reason, decision.inputs)
            if decision.action == WAIT:
                self.watching[symbol] = decision.reason + (f"; entries today {buys} of {spec.max_entries}" if buys else "")
                continue
            self.watching.pop(symbol, None)
            self._record(decision, symbol)
            if decision.action in (BUY, BUY_PUT):
                account = gw.context(symbol).account
                share = gw.slice_usd(account)
                if options:
                    budget = min(share, gw.risk.sizing_equity(account) * self._config.risk.max_risk_per_trade_pct / 100)
                    gw.submit_option_entry("strategy", symbol, stop_underlying=decision.stop_price, budget_usd=budget,
                                           inputs=decision.inputs, kind="put" if decision.action == BUY_PUT else "call")
                else:
                    gw.submit_entry(
                        "strategy", symbol, notional=share * STRATEGY_NOTIONAL_FRACTION,
                        stop_price=decision.stop_price, inputs=decision.inputs,
                    )

    def _symbol_outcomes(self, symbol: str) -> list[bool]:
        # Records from before the split account have no symbol; they were SPY's. An option trade counts for its
        # stock (records from 2026-09-28 hold the contract symbol).
        return [o["won"] for o in self._outcomes if underlying_of(o.get("symbol", "SPY")) == symbol]

    def active_orb_spec(self, symbol: str | None = None) -> OrbSpec:
        """Today's breakout rules; for the adaptive strategy, the range length the symbol's loss record gives."""
        if self.strategy == "adaptive_opening_range_breakout":
            return ADAPTIVE_ORB_SPEC.day_spec(self._symbol_outcomes(symbol or self.symbols[0]))
        return self._orb_spec

    def range_minutes(self, symbol: str) -> int:
        spec = self.active_orb_spec(symbol)
        span = dt.datetime.combine(dt.date.min, spec.range_end) - dt.datetime.combine(dt.date.min, spec.range_start)
        return int(span.total_seconds() // 60)

    def _load_outcomes(self) -> list[dict]:
        if self._outcomes_path is None or not self._outcomes_path.exists():
            return []
        return json.loads(self._outcomes_path.read_text(encoding="utf-8"))

    def _note_outcomes(self) -> None:
        """Remember each newly closed strategy trade, so each symbol's loss streak survives restarts."""
        seen = {o["entry_time"] for o in self._outcomes}
        new = [t for t in self.gateway.closed_trades
               if t.source == "strategy" and t.net_pnl is not None and t.entry_time not in seen]
        if not new:
            return
        self._outcomes += [{"symbol": t.key, "entry_time": t.entry_time, "net_pnl": t.net_pnl, "won": t.net_pnl > 0,
                            **({"contract": t.symbol} if t.underlying else {})} for t in new]
        if self._outcomes_path is not None:
            self._outcomes_path.parent.mkdir(parents=True, exist_ok=True)
            self._outcomes_path.write_text(json.dumps(self._outcomes, indent=1), encoding="utf-8")

    def _record(self, decision, symbol: str | None = None) -> None:
        extra: dict[str, Any] = {}
        if self.strategy == "adaptive_opening_range_breakout":
            spec = ADAPTIVE_ORB_SPEC
            extra["range_minutes"] = self.range_minutes(symbol or self.symbols[0])
        else:
            spec = self._orb_spec if self.strategy in BREAKOUTS else self._spec
        if symbol is None:
            self.gateway.decided_today = True
        elif decision.action in (BUY, BUY_PUT):
            self.gateway.symbol_buys[symbol] = self.gateway.symbol_buys.get(symbol, 0) + 1
            extra.update(symbol=symbol, entry_number=self.gateway.symbol_buys[symbol])
        else:
            self.gateway.decided_symbols.add(symbol)
            extra["symbol"] = symbol
        self.gateway.journal.record(
            "signal", "strategy", date=decision.date, action=decision.action, reason=decision.reason,
            inputs=decision.inputs, spec=spec.fingerprint(), strategy=self.strategy, **extra,
        )

    # --- what the UI shows ------------------------------------------------------------------

    def build_status(self) -> dict[str, Any]:
        gw = self.gateway
        now = self._clock()
        breakout = self.strategy in BREAKOUTS
        base = {
            "state": "running" if self.running else "stopped",
            "mode": "PAPER",
            "now": now.isoformat(),
            "symbol": self.symbol,
            "strategy": self.strategy,
            "instrument": self._config.trading.instrument,
            "engine_error": self.last_error,
            "signals": gw.journal.recent(max(10, 2 * len(self.symbols)), {"signal"}),
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
        share_blockers = risk.share_entry_blockers(ctx)
        bot_reasons = [r for r in blockers if not r.startswith(_TIME_OF_DAY)]
        if breakout:  # data health is per symbol and shown per symbol
            bot_reasons = [r for r in bot_reasons if not r.startswith("market data is not healthy")]
        if session is not None and not (session.open <= now < session.close):
            bot_reasons = [r for r in bot_reasons if not r.startswith("market data is not healthy")]
        if session is not None and not session.is_full_day:
            bot_reasons.append("half day: the strategy doesn't trade")
        if not breakout and gw.decided_today:
            last = gw.journal.recent(1, {"signal"})
            what = f"{last[0]['action']}: {last[0]['reason']}" if last else "done"
            bot_reasons.append(f"today's decision is made ({what})")
        if breakout and session is not None and all(s in gw.decided_symbols for s in self.symbols):
            bot_reasons.append("every symbol's decision for today is made")
        if not self.running:
            bot_reasons.insert(0, "strategy engine is not running")
        if self.last_error:
            bot_reasons.append(f"last engine error: {self.last_error}")

        positions = []
        for p in ctx.positions:
            stops = [o for o in ctx.open_orders if o.symbol == p.symbol and o.type == "stop"]
            trade = next((t for t in gw.open_trades.values() if t.symbol == p.symbol), None)
            positions.append({
                "symbol": p.symbol, "underlying": underlying_of(p.symbol), "qty": p.qty, "entry": p.avg_entry_price,
                "price": p.current_price, "unrealized_pl": p.unrealized_pl, "stop": stops[0].stop_price if stops else None,
                "stop_underlying": trade.stop_underlying if trade else None,  # options: the stock level the bot sells at
                "source": trade.source if trade else "unknown",
                "stop_note": (f"{trade.underlying} {'≥' if is_put(trade.symbol) else '≤'} {trade.stop_underlying:.2f}"
                              if trade and trade.stop_underlying else None),
            })
        sizing = risk.sizing_equity(ctx.account)
        share = gw.slice_usd(ctx.account) if breakout else sizing
        price = ctx.reference_price or (positions[0]["price"] if positions else None)
        breakeven = None
        if price:
            cost = round_trip_cost(self._config.costs, notional_usd=share * STRATEGY_NOTIONAL_FRACTION, price=price)
            breakeven = {"round_trip_cost_usd": cost.total_usd, "round_trip_cost_bps": cost.total_bps,
                         "notional_usd": cost.notional_usd}
        symbols = []
        if breakout:
            last_signal = {}
            for event in reversed(gw.journal.recent(200, {"signal"})):
                if event.get("symbol"):
                    last_signal[event["symbol"]] = event
            for symbol in self.symbols:
                health = gw.healths[symbol]
                signal = last_signal.get(symbol) if symbol in gw.decided_symbols else None
                day, high, low = self.ranges.get(symbol, (None, None, None))
                today = session is not None and day == session.date
                symbols.append({
                    "symbol": symbol, "range_minutes": self.range_minutes(symbol),
                    "range_high": high if today else None, "range_low": low if today else None,
                    "entries": gw.symbol_buys.get(symbol, 0), "max_entries": self.active_orb_spec(symbol).max_entries,
                    "price": health.price, "data_ok": health.ok, "data_reason": health.reason,
                    "decided": symbol in gw.decided_symbols,
                    "status": (f"{signal['action']}: {signal['reason']}" if signal
                               else self.watching.get(symbol, "waiting for the opening range")),
                    "holding": symbol in gw.open_trades or any(p["symbol"] == symbol for p in positions),
                })
            orb = self.active_orb_spec()
            entry_time = orb.at(session, orb.range_end) if session else None
            next_decision = entry_time.isoformat() if entry_time and len(gw.decided_symbols) < len(self.symbols) else None
        else:
            entry_time = self._spec.at(session, self._spec.entry_time) if session else None
            next_decision = entry_time.isoformat() if entry_time and not gw.decided_today else None
        return {
            **base,
            "armed": not bot_reasons,
            "disarmed_reasons": bot_reasons,
            "next_decision": next_decision,
            "range_minutes": self.range_minutes(self.symbols[0]) if breakout else None,
            "watching": self.watching.get(self.symbols[0]) if breakout else None,
            "symbols": symbols,
            "entry": {"enabled": not share_blockers, "reasons": share_blockers},  # the manual ticket buys shares
            "data": {"ok": ctx.data_ok, "reason": ctx.data_reason, "price": ctx.reference_price,
                     "last_bar_end": gw.health.last_bar_end.isoformat() if gw.health.last_bar_end else None,
                     "feed": self.data.live_feed},
            "account": {"number": ctx.account.account_number, "paper": ctx.account.is_paper,
                        "equity": ctx.account.equity, "cash": ctx.account.cash, "sizing_equity": sizing,
                        "symbol_share": share,
                        "day_pnl": risk.day_pnl_usd(ctx.account),
                        "daily_loss_limit": risk.daily_loss_limit_usd(ctx.account)},
            "limits": {"max_risk_per_trade_usd": sizing * self._config.risk.max_risk_per_trade_pct / 100,
                       "entries_today": gw.entries_today, "max_entries_per_day": self._config.risk.max_entries_per_day},
            "position": positions[0] if positions else None,
            "positions": positions,
            "session": {"open": session.open.isoformat(), "close": session.close.isoformat()} if session else None,
            "pnl_today": summarize(gw.closed_trades),
            "breakeven": breakeven,
        }
