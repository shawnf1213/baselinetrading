import ast
import datetime as dt
import inspect
import pathlib

import pytest

import baselinetrading
from baselinetrading import broker as broker_module
from baselinetrading.broker import MUTATING_METHODS, AlpacaBroker, RiskBypass
from tests.fakes import FakeBroker
from tests.helpers import Clock, config, gateway

MANUAL_STOP = 495.0  # 1% below the fake price of 500


def buy(gw, source="manual", qty=100, stop=MANUAL_STOP):
    return gw.submit_entry(source, "SPY", qty=qty, stop_price=stop)


# --- the one code path ------------------------------------------------------------------


@pytest.mark.parametrize("method", MUTATING_METHODS)
def test_every_mutating_broker_method_is_guarded(method):
    assert getattr(getattr(AlpacaBroker, method), "risk_guarded", False)
    assert getattr(getattr(FakeBroker, method), "risk_guarded", False)


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("submit_market", ("SPY", "buy", 1, "x")),
        ("submit_stop_sell", ("SPY", 1, 400.0, "x")),
        ("cancel_order", ("1",)),
        ("cancel_all", ()),
        ("close_position", ("SPY",)),
    ],
)
def test_calling_the_broker_directly_is_refused(method, args):
    broker = FakeBroker()
    with pytest.raises(RiskBypass):
        getattr(broker, method)(*args)
    assert broker.mutations == []


def test_only_broker_py_may_create_a_trading_client():
    # alpaca_client.py builds one for the calendar only; it must never call anything that trades.
    package = pathlib.Path(baselinetrading.__file__).parent
    for path in package.glob("*.py"):
        source = path.read_text()
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        if "TradingClient" in imports:
            assert path.name in {"broker.py", "alpaca_client.py"}, path.name
        if path.name == "alpaca_client.py":
            for word in ("submit_order", "cancel_order", "close_position", "close_all_positions", "replace_order"):
                assert word not in source


def test_alpaca_broker_is_paper_only():
    source = inspect.getsource(AlpacaBroker.__init__)
    assert "paper=True" in source and "is_paper" in source
    assert "paper=False" not in inspect.getsource(broker_module)


# --- entries ------------------------------------------------------------------------------


def test_an_approved_entry_buys_and_places_the_stop(tmp_path):
    gw, broker, _ = gateway(tmp_path)
    outcome = buy(gw)
    assert outcome.ok, outcome
    assert [m[0] for m in broker.mutations] == ["submit_market", "submit_stop_sell"]
    assert broker.mutations[1][2:] == (100, MANUAL_STOP)
    assert gw.open_trade.source == "manual"
    kinds = [e["kind"] for e in gw.journal.recent(20)][::-1]
    assert kinds == ["order_request", "order_submitted", "fill", "stop_placed", "trade_opened", "risk_approved"]


def test_strategy_stop_is_computed_from_the_fill(tmp_path):
    gw, broker, _ = gateway(tmp_path)
    outcome = gw.submit_entry("strategy", "SPY", notional=50_000, stop_pct=1.0)
    assert outcome.ok
    assert broker.mutations[1][3] == 495.0
    assert gw.open_trade.source == "strategy"


@pytest.mark.parametrize("stop", [None, 500.0, 501.0, float("nan"), -1.0])
def test_an_entry_without_a_valid_stop_is_vetoed(tmp_path, stop):
    gw, broker, _ = gateway(tmp_path)
    outcome = buy(gw, stop=stop)
    assert not outcome.ok
    assert any("needs a stop" in r for r in outcome.reasons)
    assert broker.mutations == []


def test_one_position_per_symbol(tmp_path):
    gw, broker, _ = gateway(tmp_path, cfg=config(max_entries_per_day=5))
    assert buy(gw, qty=10).ok
    second = buy(gw, source="strategy", qty=10)
    assert not second.ok and any("one position per symbol" in r for r in second.reasons)
    assert len([m for m in broker.mutations if m[0] == "submit_market"]) == 1


def test_the_account_is_split_and_symbols_trade_side_by_side(tmp_path):
    from tests.helpers import mark_healthy

    gw, broker, clock = gateway(tmp_path, cfg=config(symbols=("SPY", "AAPL"), max_entries_per_day=5))
    mark_healthy(gw, clock, broker.price)
    gw.healths["AAPL"].ok, gw.healths["AAPL"].checked_at, gw.healths["AAPL"].price = True, clock(), broker.price
    too_big = gw.submit_entry("manual", "SPY", qty=120, stop_price=499.0)  # $60k > half of $100k
    assert not too_big.ok and any("share of the account" in r for r in too_big.reasons)
    assert gw.submit_entry("manual", "SPY", qty=90, stop_price=499.0).ok
    assert gw.submit_entry("strategy", "AAPL", qty=90, stop_price=499.0).ok
    assert set(gw.open_trades) == {"SPY", "AAPL"}
    assert gw.exit_all("manual", "just SPY", symbol="SPY").ok
    assert [p.symbol for p in broker.positions()] == ["AAPL"] and set(gw.open_trades) == {"AAPL"}


def test_manual_and_strategy_share_the_daily_entry_limit(tmp_path):
    gw, broker, _ = gateway(tmp_path)  # max_entries_per_day = 1
    assert buy(gw, qty=10).ok
    gw.exit_all("manual", "done")
    later = gw.submit_entry("strategy", "SPY", notional=10_000, stop_pct=1.0)
    assert not later.ok and any("entry limit reached" in r for r in later.reasons)


def test_the_daily_loss_limit_vetoes_manual_orders(tmp_path):
    broker = FakeBroker()
    broker.equity = broker.last_equity - 5_001  # down more than 5% of $100k
    gw, _, _ = gateway(tmp_path, broker=broker)
    outcome = buy(gw, qty=10)
    assert not outcome.ok and any("daily loss limit hit" in r for r in outcome.reasons)
    assert broker.mutations == []


def test_risk_per_trade_is_capped_at_two_percent(tmp_path):
    gw, broker, _ = gateway(tmp_path)
    outcome = buy(gw, qty=190, stop=480.0)  # $95k position, $20 to the stop = $3,800 > $2,000
    assert not outcome.ok and any("exceeds 2% of equity" in r for r in outcome.reasons)


def test_no_leverage_even_though_buying_power_is_four_times_cash(tmp_path):
    gw, broker, _ = gateway(tmp_path)
    outcome = buy(gw, qty=300, stop=499.0)  # $150k
    assert not outcome.ok and any("share of the account" in r for r in outcome.reasons)


def test_stale_or_unhealthy_data_blocks_entries(tmp_path):
    gw, broker, clock = gateway(tmp_path)
    clock.now += dt.timedelta(minutes=5)  # the last health check is now too old
    outcome = buy(gw, qty=10)
    assert not outcome.ok and any("market data is not healthy" in r for r in outcome.reasons)
    assert broker.mutations == []


@pytest.mark.parametrize(("hhmm", "expected"), [("09:00", "hasn't opened"), ("15:50", "no new entries"), ("16:30", "closed for the day")])
def test_entries_outside_trading_hours_are_vetoed(tmp_path, hhmm, expected):
    gw, broker, clock = gateway(tmp_path, clock=Clock(hhmm))
    outcome = buy(gw, qty=10)
    assert not outcome.ok and any(expected in r for r in outcome.reasons)


def test_trading_disabled_in_settings_vetoes_everything(tmp_path):
    gw, broker, _ = gateway(tmp_path, cfg=config(enabled=False))
    outcome = buy(gw, qty=10)
    assert not outcome.ok and any("trading.enabled = false" in r for r in outcome.reasons)


def test_a_live_account_is_refused(tmp_path):
    gw, broker, _ = gateway(tmp_path, broker=FakeBroker(account_number="LIVE123"))
    outcome = buy(gw, qty=10)
    assert not outcome.ok and any("not a paper account" in r for r in outcome.reasons)


def test_symbols_outside_the_allowlist_are_vetoed(tmp_path):
    gw, broker, _ = gateway(tmp_path)
    outcome = gw.submit_entry("manual", "TSLA", qty=1, stop_price=MANUAL_STOP)
    assert not outcome.ok and any("not in the allowed symbols" in r for r in outcome.reasons)


def test_vetoes_are_journaled_with_their_source(tmp_path):
    gw, _, _ = gateway(tmp_path, cfg=config(enabled=False))
    buy(gw, qty=10)
    veto = gw.journal.recent(1, {"veto"})[0]
    assert veto["source"] == "manual" and veto["reasons"]


# --- fills, stops, exits ---------------------------------------------------------------------


def test_a_partial_fill_gets_a_stop_for_the_filled_quantity_only(tmp_path):
    broker = FakeBroker()
    broker.fill_fraction = 0.5
    gw, _, _ = gateway(tmp_path, broker=broker)
    assert buy(gw, qty=100).ok
    assert ("cancel_order", "1") in broker.mutations
    assert broker.mutations[-1][:3] == ("submit_stop_sell", "SPY", 50.0)


def test_an_unfilled_entry_is_cancelled_and_leaves_nothing(tmp_path):
    broker = FakeBroker()
    broker.fill_fraction = 0.0
    gw, _, _ = gateway(tmp_path, broker=broker)
    outcome = buy(gw, qty=100)
    assert not outcome.ok and broker.positions() == []
    assert gw.entries_today == 1  # counted anyway, so it can't be retried in a loop


def test_if_the_stop_cannot_be_placed_the_position_is_closed(tmp_path):
    broker = FakeBroker()
    broker.fail_stops = True
    gw, _, _ = gateway(tmp_path, broker=broker)
    outcome = buy(gw, qty=100)
    assert not outcome.ok and "position closed" in outcome.message
    assert broker.positions() == []
    assert gw.journal.recent(50, {"unprotected"})


def test_exit_cancels_the_stop_before_selling(tmp_path):
    gw, broker, _ = gateway(tmp_path)
    buy(gw, qty=100)
    broker.price = 503.0
    assert gw.exit_all("manual", "take profit").ok
    names = [m[0] for m in broker.mutations]
    assert names[-2:] == ["cancel_all", "close_position"]
    trade = gw.closed_trades[-1]
    assert trade.gross_pnl == pytest.approx(300.0)
    assert trade.net_pnl < trade.gross_pnl  # modelled costs are charged even on paper


def test_exits_are_allowed_even_when_entries_are_blocked(tmp_path):
    gw, broker, clock = gateway(tmp_path)
    buy(gw, qty=100)
    gw.health.ok, gw.health.reason = False, "feed down"
    broker.equity = broker.last_equity - 10_000
    clock.set("15:58")
    assert gw.exit_all("system", "flatten").ok
    assert broker.positions() == []


def test_a_stop_hit_is_recorded_as_a_closed_trade(tmp_path):
    gw, broker, _ = gateway(tmp_path)
    buy(gw, qty=100)
    broker.price = 494.0
    broker.trigger_stop(494.5)
    gw.reconcile()
    trade = gw.closed_trades[-1]
    assert trade.exit_reason == "stop hit" and trade.exit_price == 494.5


def test_a_position_without_a_stop_is_flattened(tmp_path):
    gw, broker, _ = gateway(tmp_path)
    broker.open_position_outside_app("SPY", 10, 500.0)  # e.g. bought on the Alpaca website
    gw.reconcile()
    assert broker.positions() == []
    assert gw.journal.recent(50, {"unprotected"})


def test_the_kill_switch_flattens_and_blocks_until_reset(tmp_path):
    gw, broker, _ = gateway(tmp_path, cfg=config(max_entries_per_day=5))
    buy(gw, qty=100)
    assert gw.kill("testing").ok
    assert broker.positions() == [] and broker.open_orders() == []
    blocked = buy(gw, qty=10)
    assert any("kill switch is engaged" in r for r in blocked.reasons)
    gw.reset_kill()
    assert buy(gw, qty=10).ok


def test_the_kill_switch_survives_a_restart(tmp_path):
    gw, broker, clock = gateway(tmp_path)
    gw.kill("testing")
    restarted, _, _ = gateway(tmp_path, broker=broker, clock=clock)
    assert restarted.kill_switch is True


def test_a_restart_keeps_the_days_entry_count_and_trades(tmp_path):
    gw, broker, clock = gateway(tmp_path)
    buy(gw, qty=100)
    gw.exit_all("manual", "done")
    restarted, _, _ = gateway(tmp_path, broker=broker, clock=clock)
    assert restarted.entries_today == 1
    assert len(restarted.closed_trades) == 1 and restarted.closed_trades[0].source == "manual"
