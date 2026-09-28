"""Build a fully wired gateway around a FakeBroker, at a chosen time of day."""

import dataclasses
import datetime as dt

from baselinetrading.bars import ET
from baselinetrading.config import DEFAULT_CONFIG_PATH, load_config
from baselinetrading.gateway import OrderGateway
from baselinetrading.journal import Journal
from baselinetrading.risk import RiskManager
from tests.fakes import FakeBroker, session

TODAY = dt.date(2026, 9, 28)
SESSION = session(TODAY)


def config(enabled=True, **risk_changes):
    base = load_config(DEFAULT_CONFIG_PATH, today=TODAY)
    return dataclasses.replace(
        base,
        trading=dataclasses.replace(base.trading, enabled=enabled),
        risk=dataclasses.replace(base.risk, **risk_changes),
    )


class Clock:
    def __init__(self, hhmm="11:00"):
        self.set(hhmm)

    def set(self, hhmm, seconds=0):
        hour, minute = map(int, hhmm.split(":"))
        self.now = dt.datetime.combine(TODAY, dt.time(hour, minute, seconds), ET)

    def __call__(self):
        return self.now


def gateway(tmp_path, *, broker=None, clock=None, cfg=None, healthy=True, session_today=SESSION):
    broker = broker or FakeBroker()
    clock = clock or Clock()
    cfg = cfg or config()
    journal = Journal(tmp_path / "logs", clock)
    gw = OrderGateway(
        broker, RiskManager(cfg), journal, cfg, clock=clock, session_today=lambda: session_today,
        state_dir=tmp_path / "state", sleep=lambda s: None, fill_timeout=0.0,
    )
    if healthy:
        mark_healthy(gw, clock, broker.price)
    return gw, broker, clock


def mark_healthy(gw, clock, price):
    gw.health.ok, gw.health.reason, gw.health.checked_at, gw.health.price = True, "", clock(), price


def engine(tmp_path, *, clock=None, broker=None, cfg=None, skip=(), previous_close=499.0, sessions=None):
    """Engine + gateway + MarketData over fakes. The fake bars close at 500.01."""
    from baselinetrading.bars import Bar
    from baselinetrading.engine import Engine
    from baselinetrading.market_data import MarketData
    from tests.fakes import FakeFetcher

    clock = clock or Clock()
    cfg = cfg or config()
    yesterday = session(dt.date(2026, 9, 25))
    fetcher = FakeFetcher(sessions or [yesterday, SESSION])
    fetcher.skip = skip
    fetcher.daily[yesterday.date] = Bar(
        dt.datetime.combine(yesterday.date, dt.time(0), ET), previous_close, previous_close, previous_close,
        previous_close, 1.0,
    )
    data = MarketData(fetcher, cfg, cache_dir=tmp_path / "cache", now=clock, sleep=lambda s: None)
    holder = {}
    gw, broker, _ = gateway(tmp_path, broker=broker, clock=clock, cfg=cfg, healthy=False)
    gw._session_today = lambda: holder["engine"].session_today()
    eng = Engine(gw, data, symbol="SPY", clock=clock)
    holder["engine"] = eng
    eng._thread = type("Alive", (), {"is_alive": lambda self: True})()  # report "running" without a thread
    return eng, gw, broker, clock, fetcher
