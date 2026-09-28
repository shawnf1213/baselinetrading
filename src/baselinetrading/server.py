"""FastAPI backend: the only thing the browser talks to. The browser never talks to Alpaca.

    export BASELINE_UI_TOKEN=...   # 24+ characters; the UI asks for it once
    uvicorn baselinetrading.server:app --host 127.0.0.1 --port 8000

Routes (all but /api/health need the token):
    GET  /api/status               everything the UI shows, incl. why the bot is disarmed
    GET  /api/bars                 chart bars (display only, never used for decisions)
    POST /api/orders               the ONE manual order route: buy with a mandatory stop
    POST /api/positions/close      close the position (manual exit)
    POST /api/kill                 kill switch: cancel everything, flatten, block entries
    POST /api/kill/reset           re-allow entries
    WS   /ws?token=...             status pushed every few seconds

If settings, credentials or the broker connection fail at startup, the server
still starts, serves the UI and reports the problems, but every order route
returns 503. Nothing is inferred from a missing value.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hmac
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from baselinetrading.bars import ET
from baselinetrading.config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from baselinetrading.gateway import Outcome

TOKEN_VAR = "BASELINE_UI_TOKEN"
MIN_TOKEN_LENGTH = 24
ROOT = DEFAULT_CONFIG_PATH.parents[1]
FRONTEND_DIST = ROOT / "frontend" / "dist"


@dataclass
class Runtime:
    """What the routes need. `engine` is None when startup failed; `problems` says why."""

    engine: Any = None
    problems: list[str] = field(default_factory=list)

    @property
    def gateway(self):
        return self.engine.gateway if self.engine else None


def build_runtime() -> Runtime:
    """Wire the real thing: settings, paper credentials, Alpaca, data, journal, gateway, engine."""
    try:
        config = load_config()
        from baselinetrading.credentials import load_credentials

        credentials = load_credentials()
    except ConfigError as exc:
        return Runtime(problems=exc.problems)
    try:
        from baselinetrading.alpaca_client import AlpacaFetcher
        from baselinetrading.broker import AlpacaBroker
        from baselinetrading.engine import Engine
        from baselinetrading.gateway import OrderGateway
        from baselinetrading.journal import Journal
        from baselinetrading.market_data import MarketData
        from baselinetrading.risk import RiskManager

        def clock() -> dt.datetime:
            return dt.datetime.now(ET)

        broker = AlpacaBroker(credentials)
        data = MarketData(
            AlpacaFetcher(credentials, adjustment=config.data.adjustment), config, cache_dir=ROOT / "data" / "cache"
        )
        journal = Journal(ROOT / "logs", clock)
        engine_ref: dict[str, Any] = {}
        gateway = OrderGateway(
            broker, RiskManager(config), journal, config, clock=clock,
            session_today=lambda: engine_ref["engine"].session_today(), state_dir=ROOT / "state",
        )
        engine = Engine(gateway, data, symbol=config.trading.symbols[0], clock=clock)
        engine_ref["engine"] = engine
        return Runtime(engine=engine)
    except Exception as exc:
        return Runtime(problems=[f"could not start: {type(exc).__name__}: {exc}"])


class BuyRequest(BaseModel):
    symbol: str
    qty: float | None = Field(default=None, gt=0)
    notional: float | None = Field(default=None, gt=0)
    stop_price: float | None = None  # required; validated by the risk manager so the reason is shown


class CloseRequest(BaseModel):
    reason: str = "manual close"


class Confirm(BaseModel):
    confirm: str
    reason: str = "kill switch from the UI"


def create_app(runtime_factory=build_runtime, *, token: str | None = None, start_engine: bool = True) -> FastAPI:
    token = token if token is not None else os.environ.get(TOKEN_VAR, "")
    token_problem = None
    if len(token) < MIN_TOKEN_LENGTH:
        token_problem = f"{TOKEN_VAR} must be set to a secret of at least {MIN_TOKEN_LENGTH} characters"

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = runtime_factory()
        if token_problem:
            runtime.problems.insert(0, token_problem)
        app.state.runtime = runtime
        if runtime.engine and start_engine:
            runtime.engine.start()
        yield
        if runtime.engine and start_engine:
            runtime.engine.stop()

    app = FastAPI(title="baselinetrading", lifespan=lifespan)

    def authorized(request: Request) -> None:
        if token_problem:
            raise HTTPException(503, token_problem)
        header = request.headers.get("authorization", "")
        if not hmac.compare_digest(header, f"Bearer {token}"):
            raise HTTPException(401, "missing or wrong token")

    def gateway(request: Request):
        runtime: Runtime = request.app.state.runtime
        if runtime.gateway is None:
            raise HTTPException(503, "not running: " + "; ".join(runtime.problems))
        return runtime.gateway

    def status_of(runtime: Runtime) -> dict[str, Any]:
        if runtime.engine is None:
            return {"state": "not running", "mode": "PAPER", "armed": False, "disarmed_reasons": runtime.problems,
                    "entry": {"enabled": False, "reasons": runtime.problems}, "signals": [], "events": []}
        status = dict(runtime.engine.status)
        if runtime.problems:
            status["armed"] = False
            status["disarmed_reasons"] = runtime.problems + status.get("disarmed_reasons", [])
        return status

    def outcome(result: Outcome) -> JSONResponse:
        body = {"ok": result.ok, "message": result.message, "reasons": list(result.reasons), "details": result.details}
        return JSONResponse(body, status_code=200 if result.ok else 422)

    async def refresh(request: Request) -> None:
        engine = request.app.state.runtime.engine
        if engine:
            await asyncio.to_thread(lambda: setattr(engine, "status", engine.build_status()))

    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/status", dependencies=[Depends(authorized)])
    def status(request: Request) -> dict[str, Any]:
        return status_of(request.app.state.runtime)

    @app.get("/api/bars", dependencies=[Depends(authorized)])
    def bars(request: Request) -> dict[str, Any]:
        runtime: Runtime = request.app.state.runtime
        if runtime.engine is None:
            raise HTTPException(503, "not running")
        engine = runtime.engine
        now = dt.datetime.now(ET)
        try:
            sessions = [s for s in engine.data.sessions(now.date() - dt.timedelta(days=7), now.date()) if s.open <= now]
            out = []
            for session in sessions[-2:]:
                out += engine.data.display_bars(engine.symbol, session, now)
        except Exception as exc:
            raise HTTPException(502, f"chart data unavailable: {exc}") from None
        return {"symbol": engine.symbol, "bars": [
            {"time": int(b.start.timestamp()), "open": b.open, "high": b.high, "low": b.low, "close": b.close,
             "volume": b.volume} for b in out]}

    @app.post("/api/orders", dependencies=[Depends(authorized)])
    async def place_order(body: BuyRequest, request: Request) -> JSONResponse:
        gw = gateway(request)
        result = await asyncio.to_thread(
            gw.submit_entry, "manual", body.symbol, qty=body.qty, notional=body.notional, stop_price=body.stop_price
        )
        await refresh(request)
        return outcome(result)

    @app.post("/api/positions/close", dependencies=[Depends(authorized)])
    async def close_position(body: CloseRequest, request: Request) -> JSONResponse:
        gw = gateway(request)
        result = await asyncio.to_thread(gw.exit_all, "manual", body.reason)
        await refresh(request)
        return outcome(result)

    @app.post("/api/kill", dependencies=[Depends(authorized)])
    async def kill(body: Confirm, request: Request) -> JSONResponse:
        if body.confirm != "FLATTEN":
            raise HTTPException(400, 'confirm must be "FLATTEN"')
        gw = gateway(request)
        result = await asyncio.to_thread(gw.kill, body.reason)
        await refresh(request)
        return outcome(result)

    @app.post("/api/kill/reset", dependencies=[Depends(authorized)])
    async def reset_kill(body: Confirm, request: Request) -> JSONResponse:
        if body.confirm != "RESET":
            raise HTTPException(400, 'confirm must be "RESET"')
        gw = gateway(request)
        await asyncio.to_thread(gw.reset_kill)
        await refresh(request)
        return outcome(Outcome(True, "kill switch reset; entries allowed again if nothing else blocks them"))

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        supplied = socket.query_params.get("token", "")
        if token_problem or not hmac.compare_digest(supplied, token):
            await socket.close(code=4401)
            return
        await socket.accept()
        try:
            while True:
                await socket.send_json(status_of(socket.app.state.runtime))
                await asyncio.sleep(2)
        except (WebSocketDisconnect, RuntimeError):
            return

    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()
