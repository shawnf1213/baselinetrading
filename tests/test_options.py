import datetime as dt

from baselinetrading.options import Contract, choose_call, contracts_for, is_option, underlying_of
from tests.helpers import Clock, SESSION, config, engine, gateway
from tests.test_orb import BREAKOUT, day_bars, serve

TODAY = dt.date(2026, 9, 28)


def c(expiry_days, strike):
    exp = TODAY + dt.timedelta(days=expiry_days)
    return Contract(f"TSLA{exp:%y%m%d}C{int(strike * 1000):08d}", exp, strike)


def test_the_weekly_call_just_at_or_above_the_price_is_chosen():
    contracts = [c(0, 360), c(3, 370), c(7, 365), c(7, 370), c(7, 375), c(14, 370)]
    assert choose_call(contracts, 369.42, TODAY) == c(7, 370)
    assert choose_call([c(0, 370), c(30, 370)], 369.0, TODAY) is None  # nothing 5-12 days out


def test_whole_contracts_within_the_budget():
    assert contracts_for(2000, 5.00) == 4
    assert contracts_for(2000, 25.00) == 0
    assert is_option("TSLA261002C00370000") and underlying_of("TSLA261002C00370000") == "TSLA"
    assert not is_option("TSLA") and underlying_of("TSLA") == "TSLA"


def opts_gateway(tmp_path, **kw):
    return gateway(tmp_path, cfg=config(instrument="options", **kw))


def test_an_option_entry_buys_calls_within_two_percent_and_tracks_the_stock_stop(tmp_path):
    gw, broker, _ = opts_gateway(tmp_path)
    outcome = gw.submit_option_entry("strategy", "SPY", stop_underlying=497.505, budget_usd=2000)
    assert outcome.ok, outcome.reasons
    market = [m for m in broker.mutations if m[0] == "submit_market"]
    assert len(market) == 1 and is_option(market[0][1]) and market[0][3] == 4  # 4 x $500 = $2,000
    assert "submit_stop_sell" not in [m[0] for m in broker.mutations]  # no broker stops on options
    trade = gw.open_trades["SPY"]
    assert trade.stop_underlying == 497.5 and trade.multiplier == 100 and trade.key == "SPY"
    gw.reconcile()  # a managed option position is not "unprotected"
    assert gw.open_trades and broker.positions()


def test_premium_over_the_risk_limit_or_a_wide_spread_is_vetoed(tmp_path):
    gw, broker, _ = opts_gateway(tmp_path)
    broker.option_bid, broker.option_ask = 3.00, 5.00  # 40% spread
    wide = gw.submit_option_entry("strategy", "SPY", stop_underlying=497.5, budget_usd=2000)
    assert not wide.ok and any("spread" in r for r in wide.reasons)
    broker.option_bid, broker.option_ask = 4.90, 5.00
    big = gw.submit_option_entry("strategy", "SPY", stop_underlying=497.5, budget_usd=5000)
    assert not big.ok and any("a call can go to zero" in r for r in big.reasons)
    assert not [m for m in broker.mutations if m[0] == "submit_market"]


def test_the_engine_buys_calls_on_a_breakout_and_sells_them_on_the_stocks_stop(tmp_path):
    cfg = config(strategy="opening_range_breakout", instrument="options")
    eng, gw, broker, clock, fetcher = engine(tmp_path, clock=Clock("11:01"), cfg=cfg)
    serve(fetcher, day_bars(SESSION, until="11:01", **BREAKOUT))
    clock.set("11:01", 10)
    eng.tick()
    assert "SPY" in gw.open_trades and is_option(gw.open_trades["SPY"].symbol)
    assert eng.status["symbols"][0]["holding"] is True
    gw.healths["SPY"].price = 499.80  # the stock falls to its range low (499.90)
    eng.tick()
    assert gw.open_trades == {} and gw.closed_trades[-1].exit_reason.startswith("SPY traded at 499.80")
    assert broker.positions() == []


def test_the_engine_buys_puts_on_a_cross_below_the_range_and_sells_them_if_the_stock_rises(tmp_path):
    cfg = config(strategy="opening_range_breakout", instrument="options")
    eng, gw, broker, clock, fetcher = engine(tmp_path, clock=Clock("11:01"), cfg=cfg)
    serve(fetcher, day_bars(SESSION, until="11:01", **{"11_00": (500.0, 500.0, 499.5, 499.6)}))  # below 499.90
    clock.set("11:01", 10)
    eng.tick()
    trade = gw.open_trades["SPY"]
    assert "P" in trade.symbol[3:] and trade.stop_underlying == 500.1  # stop at the range high
    assert gw.journal.recent(1, {"signal"})[0]["action"] == "BUY_PUT"
    assert eng.status["positions"][0]["stop_note"] == "SPY ≥ 500.10"
    gw.healths["SPY"].price = 500.20
    eng.tick()
    assert gw.open_trades == {} and "at or above the stop" in gw.closed_trades[-1].exit_reason


def test_shares_mode_ignores_crosses_below_the_range(tmp_path):
    eng, gw, broker, clock, fetcher = engine(tmp_path, clock=Clock("11:01"), cfg=config(strategy="opening_range_breakout"))
    serve(fetcher, day_bars(SESSION, until="11:01", **{"11_00": (500.0, 500.0, 499.5, 499.6)}))
    clock.set("11:01", 10)
    eng.tick()
    assert broker.mutations == [] and gw.journal.recent(5, {"signal"}) == []


def test_closing_by_contract_symbol_closes_the_option(tmp_path):
    gw, broker, _ = opts_gateway(tmp_path)
    assert gw.submit_option_entry("strategy", "SPY", stop_underlying=497.5, budget_usd=2000).ok
    contract = gw.open_trades["SPY"].symbol
    assert gw.exit_all("manual", "from the UI", symbol=contract).ok
    assert broker.positions() == [] and gw.open_trades == {}
