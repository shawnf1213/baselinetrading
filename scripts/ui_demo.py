"""A stand-in backend for working on the web UI: canned status, chart bars and journal. No broker, no keys.

    .venv/Scripts/python.exe scripts/ui_demo.py                    # http://127.0.0.1:8010 (serves frontend/dist)
    .venv/Scripts/python.exe scripts/ui_demo.py --scenario closed
    npm --prefix frontend run dev:demo                             # live-reloading UI on :5173, proxied here

Any token unlocks it. Scenarios: "trading" (mid-session, a call and a put open), "disarmed" (kill switch
engaged) and "closed" (after the close, flat). The kill switch and its reset switch between "trading" and
"disarmed"; every other order route answers without doing anything.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import random
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from baselinetrading.bars import ET
from baselinetrading.risk import SHARES_REFUSED

DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
PRICES = {"NVDA": 228.86, "META": 715.62, "TSLA": 357.45, "AAPL": 338.40, "SPCX": 145.47,
          "AMD": 607.87, "INTC": 116.03, "MSFT": 509.22, "AVGO": 349.57, "GOOGL": 342.75}
TODAY = dt.datetime.now(ET).date()


def at(hhmm: str, day: dt.date = TODAY) -> dt.datetime:
    h, m, *s = (int(x) for x in hhmm.split(":"))
    return dt.datetime.combine(day, dt.time(h, m, s[0] if s else 0), ET)


def iso(hhmm: str) -> str:
    return at(hhmm).isoformat()


def watching(high: float, low: float, entries: int = 0) -> str:
    text = f"watching for a cross above {high:.2f} (stop would be {low:.2f}); or below {low:.2f} for puts"
    return text + (f"; entries today {entries} of 3" if entries else "")


def option(contract: str, qty: int, entry: float, last: float, stop: float) -> dict[str, Any]:
    stock, put = contract[:contract.index("2")], "P" in contract[-9:]
    return {"symbol": contract, "underlying": stock, "qty": qty, "entry": entry, "price": last,
            "unrealized_pl": round((last - entry) * qty * 100, 2), "stop": None, "stop_underlying": stop,
            "stop_note": f"{stock} {'≥' if put else '≤'} {stop:.2f}", "source": "strategy"}


def signal(hhmm: str, symbol: str, action: str, reason: str, high: float, low: float, minutes: int = 5,
           entry: int | None = None, when: str | None = None, close: float | None = None) -> dict[str, Any]:
    event = {"ts": iso(hhmm), "kind": "signal", "source": "strategy", "date": TODAY.isoformat(), "action": action,
             "reason": reason, "inputs": {"range_high": high, "range_low": low, "range_pct": (high - low) / low * 100,
                                          "range_volume": 1_250_000, "breakout_time": when, "breakout_close": close},
             "spec": "4fb0f5c98c9ebd4a", "strategy": "adaptive_opening_range_breakout", "range_minutes": minutes,
             "symbol": symbol}
    return event | ({"entry_number": entry} if entry else {})


def event(hhmm: str, kind: str, source: str, **fields: Any) -> dict[str, Any]:
    return {"ts": iso(hhmm), "kind": kind, "source": source, **fields}


SIGNALS = [
    signal("11:31:05", "AVGO", "BUY", "11:30 bar closed at 351.20, crossing the range high 350.85", 350.85, 347.10,
           entry=2, when="11:30", close=351.20),
    signal("11:06:05", "TSLA", "BUY_PUT", "11:05 bar closed at 356.10, crossing below the range low 356.40", 361.20,
           356.40, entry=2, when="11:05", close=356.10),
    signal("11:03:05", "INTC", "BUY_PUT", "11:02 bar closed at 115.20, crossing below the range low 115.35", 116.40,
           115.35, entry=3, when="11:02", close=115.20),
    signal("10:47:05", "META", "BUY", "10:46 bar closed at 720.10, crossing the range high 719.80", 719.80, 711.25,
           entry=1, when="10:46", close=720.10),
    signal("10:12:05", "NVDA", "BUY", "10:11 bar closed at 229.05, crossing the range high 228.90", 228.90, 226.40,
           entry=1, when="10:11", close=229.05),
    signal("09:58:05", "AMD", "BUY", "09:57 bar closed at 612.90, crossing the range high 612.40", 612.40, 603.10,
           minutes=15, entry=1, when="09:57", close=612.90),
]

EVENTS = [
    event("11:40:05", "veto", "strategy", symbol="AMD261009C00615000",
          reasons=["AMD261009C00615000 spread 17.2% of the ask is wider than 15%"]),
    event("11:39:14", "trade_closed", "strategy", gross_pnl=-300.0, net_pnl=-340.0),
    event("11:39:14", "exit", "system", symbol="AVGO261009C00352500", qty=4, price=3.55,
          reason="AVGO traded at 347.05, at or below the stop 347.10"),
    event("11:31:06", "trade_opened", "strategy", gross_pnl=None, net_pnl=None),
    event("11:31:06", "fill", "strategy", order_id="8f1c2d", side="buy", qty=4, price=4.30,
          symbol="AVGO261009C00352500", partial=False),
    event("11:31:05", "order_submitted", "strategy", order_id="8f1c2d", symbol="AVGO261009C00352500",
          underlying="AVGO", side="buy", qty=4),
    event("11:31:05", "risk_approved", "strategy", symbol="AVGO261009C00352500"),
    event("11:31:05", "order_request", "strategy", symbol="AVGO261009C00352500", underlying="AVGO", side="buy",
          qty=4, bid=4.20, ask=4.30, stop_underlying=347.10, reference_price=351.20, option_spec="a706cb84095d9236"),
    SIGNALS[0],
    event("11:06:07", "trade_opened", "strategy", gross_pnl=None, net_pnl=None),
    event("11:06:06", "fill", "strategy", order_id="77ab01", side="buy", qty=2, price=8.90,
          symbol="TSLA261009P00355000", partial=False),
    SIGNALS[1],
    event("10:58:12", "error", "system", where="engine", error="APIError: rate limit exceeded; retrying next tick"),
    SIGNALS[2],
]

POSITIONS = [option("NVDA261009C00230000", 3, 5.35, 6.10, 226.40), option("TSLA261009P00355000", 2, 8.90, 8.15, 361.20)]

WATCH = {  # symbol: (range minutes, holding, engine status, range high, range low, entries today)
    "NVDA": (5, True, "holding (entry 1 of 3 today)", 228.90, 226.40, 1),
    "META": (5, False, watching(719.80, 711.25, 1), 719.80, 711.25, 1),
    "TSLA": (5, True, "holding (entry 2 of 3 today)", 361.20, 356.40, 2),
    "AAPL": (5, False, watching(340.10, 336.95), 340.10, 336.95, 0),
    "SPCX": (5, False, "data unavailable: newest bar ended 11:38:00, 250 s ago (limit 90 s)", 146.30, 144.10, 0),
    "AMD": (15, False, watching(612.40, 603.10, 1), 612.40, 603.10, 1),
    "INTC": (5, False, "BUY_PUT: 11:02 bar closed at 115.20, crossing below the range low 115.35", 116.40, 115.35, 3),
    "MSFT": (30, False, watching(512.00, 505.60), 512.00, 505.60, 0),
    "AVGO": (5, False, watching(350.85, 347.10, 2), 350.85, 347.10, 2),
    "GOOGL": (5, False, watching(344.20, 340.90), 344.20, 340.90, 0),
}


class Demo:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.prices = dict(PRICES)
        self._bars: dict[str, list[dict[str, float]]] = {}

    def now(self) -> dt.datetime:
        return at("16:20:04") if self.scenario == "closed" else at("11:42:10")

    def tick(self) -> None:
        for symbol in self.prices:
            self.prices[symbol] = round(self.prices[symbol] * (1 + random.gauss(0, 0.00025)), 2)

    def status(self) -> dict[str, Any]:
        s, closed = self.scenario, self.scenario == "closed"
        kill = s == "disarmed"
        symbols = []
        for symbol, (minutes, holding, text, high, low, entries) in WATCH.items():
            decided = closed or symbol == "INTC"
            ok = not closed and symbol != "SPCX"
            symbols.append({
                "symbol": symbol, "range_minutes": minutes, "range_high": high, "range_low": low,
                "entries": entries, "max_entries": 3, "price": self.prices[symbol], "data_ok": ok,
                "data_reason": "" if ok else ("outside market hours" if closed else text.split(": ", 1)[1]),
                "decided": decided,
                "status": ("NO_TRADE: no fresh cross above the range high before 15:44" if closed and symbol != "INTC"
                           else text),
                "holding": holding and s == "trading",
            })
        positions = POSITIONS if s == "trading" else []
        day_pnl = {"trading": 184.30, "disarmed": -587.90, "closed": 109.30}[s]
        reasons = (["kill switch is engaged; reset it to trade again"] if kill
                   else ["market is closed for the day", "every symbol's decision for today is made"] if closed else [])
        entry_reasons = (["market is closed for the day"] if closed else []) + reasons[:1 if kill else 0] + [SHARES_REFUSED]
        return {
            "state": "running", "mode": "PAPER", "now": self.now().isoformat(), "symbol": "NVDA",
            "strategy": "adaptive_opening_range_breakout", "instrument": "options", "engine_error": None,
            "signals": SIGNALS, "events": EVENTS, "kill_switch": kill,
            "armed": not reasons, "disarmed_reasons": reasons,
            "next_decision": None if closed else iso("09:35"),
            "range_minutes": 5, "watching": None, "symbols": symbols,
            "entry": {"enabled": False, "reasons": entry_reasons},
            "data": {"ok": not closed, "reason": "outside market hours" if closed else "",
                     "price": self.prices["NVDA"], "last_bar_end": iso("11:42"), "feed": "iex"},
            "account": {"number": "PA0DEMO00000", "paper": True, "equity": 100_000 + day_pnl, "cash": 95_712.00,
                        "sizing_equity": 100_000.0, "symbol_share": 10_000.0, "day_pnl": day_pnl,
                        "daily_loss_limit": 5_000.0},
            "limits": {"max_risk_per_trade_usd": 2_000.0, "entries_today": {"trading": 8, "disarmed": 6, "closed": 11}[s],
                       "max_entries_per_day": 20},
            "position": positions[0] if positions else None, "positions": positions,
            "session": {"open": iso("09:30"), "close": iso("16:00")},
            "pnl_today": {
                "strategy": {"trades": 6, "wins": 2, "gross_pnl": 312.40, "costs": 203.10, "net_pnl": 109.30,
                             "win_rate": 2 / 6, "win_rate_ci": [0.0968, 0.7000]},
                "manual": {"trades": 0, "wins": 0, "gross_pnl": 0.0, "costs": 0.0, "net_pnl": 0.0, "win_rate": None,
                           "win_rate_ci": None},
            },
            "breakeven": {"round_trip_cost_usd": 3.21, "round_trip_cost_bps": 3.3, "notional_usd": 9_800.0},
        }

    def bars(self, symbol: str) -> list[dict[str, float]]:
        """Yesterday's session and today's so far: a seeded random walk that ends at the demo price."""
        if symbol not in self._bars:
            rng = random.Random(symbol)
            end = at("16:00") if self.scenario == "closed" else at("11:42")
            yesterday = TODAY - dt.timedelta(days=1)
            minutes = [at("09:30", yesterday) + dt.timedelta(minutes=i) for i in range(390)]
            minutes += [m for m in (at("09:30") + dt.timedelta(minutes=i) for i in range(390)) if m < end]
            price, path = 1.0, []
            for i, start in enumerate(minutes):
                gap = rng.gauss(0, 0.004) if i == 390 else 0.0  # the overnight gap
                open_ = price * (1 + gap)
                close = open_ * (1 + rng.gauss(0.00002, 0.0009))
                high = max(open_, close) * (1 + abs(rng.gauss(0, 0.0004)))
                low = min(open_, close) * (1 - abs(rng.gauss(0, 0.0004)))
                u = abs((start.hour * 60 + start.minute) - 765) / 390  # U-shaped volume through the day
                path.append((start, open_, high, low, close, int(rng.uniform(0.6, 1.4) * (40_000 + 260_000 * u * u))))
                price = close
            scale = PRICES[symbol] / price
            self._bars[symbol] = [{"time": int(t.timestamp()), "open": round(o * scale, 2), "high": round(h * scale, 2),
                                   "low": round(lo * scale, 2), "close": round(c * scale, 2), "volume": v}
                                  for t, o, h, lo, c, v in path]
        return self._bars[symbol]


def create_app(demo: Demo) -> FastAPI:
    app = FastAPI(title="baselinetrading UI demo")

    def authorized(request: Request) -> None:
        if not request.headers.get("authorization", "").removeprefix("Bearer ").strip():
            raise HTTPException(401, "missing or wrong token")

    def answer(message: str, ok: bool = False) -> dict[str, Any]:
        return {"ok": ok, "message": message, "reasons": [], "details": None}

    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/status")
    def status(request: Request) -> dict[str, Any]:
        authorized(request)
        return demo.status()

    @app.get("/api/bars")
    def bars(request: Request, symbol: str = "NVDA") -> dict[str, Any]:
        authorized(request)
        if symbol not in PRICES:
            raise HTTPException(400, f"{symbol} is not a traded symbol")
        return {"symbol": symbol, "bars": demo.bars(symbol)}

    @app.post("/api/orders")
    @app.post("/api/positions/close")
    async def orders(request: Request) -> dict[str, Any]:
        authorized(request)
        return answer("demo backend: nothing was sent")

    @app.post("/api/kill")
    async def kill(request: Request) -> dict[str, Any]:
        authorized(request)
        if (await request.json()).get("confirm") != "FLATTEN":
            raise HTTPException(400, 'confirm must be "FLATTEN"')
        demo.scenario = "disarmed"
        return answer("demo: kill switch engaged (nothing was sent)", ok=True)

    @app.post("/api/kill/reset")
    async def reset(request: Request) -> dict[str, Any]:
        authorized(request)
        if (await request.json()).get("confirm") != "RESET":
            raise HTTPException(400, 'confirm must be "RESET"')
        demo.scenario = "trading"
        return answer("kill switch reset; entries allowed again if nothing else blocks them", ok=True)

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        if not socket.query_params.get("token"):
            await socket.close(code=4401)
            return
        await socket.accept()
        try:
            while True:
                demo.tick()
                await socket.send_json(demo.status())
                await asyncio.sleep(2)
        except (WebSocketDisconnect, RuntimeError):
            return

    if DIST.exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            return FileResponse(DIST / "index.html")

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", choices=("trading", "disarmed", "closed"), default="trading")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    uvicorn.run(create_app(Demo(args.scenario)), host="127.0.0.1", port=args.port)
