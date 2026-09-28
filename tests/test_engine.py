import datetime as dt

from baselinetrading.engine import Engine
from tests.helpers import Clock, SESSION, config, engine, session


def test_the_1530_decision_buys_through_the_gateway_with_its_inputs(tmp_path):
    eng, gw, broker, clock, _ = engine(tmp_path, clock=Clock("15:30"))
    clock.set("15:30", 10)
    eng.tick()
    signal = gw.journal.recent(1, {"signal"})[0]
    assert signal["action"] == "BUY" and signal["inputs"]["previous_close"] == 499.0
    assert gw.open_trade is not None and gw.open_trade.source == "strategy"
    assert [m[0] for m in broker.mutations] == ["submit_market", "submit_stop_sell"]
    assert eng.status["position"]["stop"] == broker.mutations[1][3]


def test_the_decision_happens_once(tmp_path):
    eng, gw, broker, clock, _ = engine(tmp_path, clock=Clock("15:30"))
    for seconds in (10, 20, 30):
        clock.set("15:30", seconds)
        eng.tick()
    assert len(gw.journal.recent(50, {"signal"})) == 1


def test_a_down_morning_records_no_trade(tmp_path):
    eng, gw, broker, clock, _ = engine(tmp_path, clock=Clock("15:30"), previous_close=510.0)
    clock.set("15:30", 10)
    eng.tick()
    assert gw.journal.recent(1, {"signal"})[0]["action"] == "NO_TRADE"
    assert broker.mutations == []


def test_a_gap_before_1530_means_no_trade(tmp_path):
    eng, gw, broker, clock, _ = engine(tmp_path, clock=Clock("15:30"), skip={"15:20"})
    clock.set("15:30", 10)
    eng.tick()
    signal = gw.journal.recent(1, {"signal"})[0]
    assert signal["action"] == "NO_TRADE" and "15:20" in signal["reason"]
    assert broker.mutations == []


def test_starting_after_1530_records_a_missed_decision(tmp_path):
    eng, gw, broker, clock, _ = engine(tmp_path, clock=Clock("15:40"))
    eng.tick()
    assert "missed" in gw.journal.recent(1, {"signal"})[0]["reason"]


def test_positions_are_flattened_at_1555(tmp_path):
    eng, gw, broker, clock, _ = engine(tmp_path, clock=Clock("15:30"))
    clock.set("15:30", 10)
    eng.tick()
    clock.set("15:55", 1)
    eng.tick()
    assert broker.positions() == []
    assert gw.closed_trades[-1].exit_reason == "flatten before the close"
    assert eng.status["pnl_today"]["strategy"]["trades"] == 1
    assert eng.status["pnl_today"]["manual"]["trades"] == 0


def test_the_daily_loss_limit_flattens_and_disarms(tmp_path):
    eng, gw, broker, clock, _ = engine(tmp_path, clock=Clock("15:30"))
    clock.set("15:30", 10)
    eng.tick()
    broker.last_equity = broker.equity + 6_000  # down $6,000 on the day, over the $5,000 limit
    clock.set("15:31")
    eng.tick()
    assert broker.positions() == []
    assert any("daily loss limit hit" in r for r in eng.status["disarmed_reasons"])


def test_status_explains_why_the_bot_is_disarmed(tmp_path):
    eng, gw, broker, clock, _ = engine(tmp_path, cfg=config(enabled=False))
    eng.tick()
    assert eng.status["armed"] is False
    assert any("trading.enabled = false" in r for r in eng.status["disarmed_reasons"])
    assert eng.status["entry"]["enabled"] is False


def test_status_is_armed_midday_with_healthy_data(tmp_path):
    eng, gw, broker, clock, _ = engine(tmp_path, clock=Clock("11:00"))
    clock.set("11:00", 20)
    eng.tick()
    assert eng.status["data"]["ok"] is True
    assert eng.status["armed"] is True, eng.status["disarmed_reasons"]
    assert eng.status["entry"]["enabled"] is True
    assert eng.status["breakeven"]["round_trip_cost_usd"] > 0


def test_a_live_gap_disables_manual_entry_with_the_reason(tmp_path):
    eng, gw, broker, clock, _ = engine(tmp_path, clock=Clock("11:00"), skip={"10:55"})
    clock.set("11:00", 20)
    eng.tick()
    assert eng.status["entry"]["enabled"] is False
    assert any("10:55" in r for r in eng.status["entry"]["reasons"])


def test_half_days_are_recorded_and_skipped(tmp_path):
    half = session(SESSION.date, close=dt.time(13, 0))
    eng, gw, broker, clock, _ = engine(tmp_path, sessions=[session(dt.date(2026, 9, 25)), half])
    eng.tick()
    assert "half day" in gw.journal.recent(1, {"signal"})[0]["reason"]


def test_a_broker_outage_is_shown_not_hidden(tmp_path):
    eng, gw, broker, clock, _ = engine(tmp_path)

    def down():
        raise ConnectionError("paper-api unreachable")

    broker.account = down
    eng.tick()
    assert eng.status["armed"] is False
    assert any("broker unreachable" in r for r in eng.status["disarmed_reasons"])
