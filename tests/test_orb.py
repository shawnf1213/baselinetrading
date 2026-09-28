import datetime as dt

from baselinetrading.backtest import run_days, report, simulate_long
from baselinetrading.bars import ET, MINUTE, Bar
from baselinetrading.orb import ORB_SPEC, WAIT, decide_orb
from baselinetrading.strategy import BUY, NO_TRADE
from tests.fakes import session
from tests.helpers import SESSION, Clock, config, engine

DAY = session(dt.date(2020, 3, 2))


def at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return dt.datetime.combine(day.date, dt.time(h, m), ET)


def day_bars(day, until="16:00", price=500.0, **overrides):
    """Flat bars from 09:30; the opening range is 499.90-500.10. Overrides: {"HH_MM": (o, h, l, c)}."""
    bars, t = {}, at(day, "09:30")
    while t < at(day, until):
        key = t.astimezone(ET).strftime("%H:%M")
        bars[key] = Bar(t.astimezone(dt.timezone.utc), price, price + 0.1, price - 0.1, price, 1000.0)
        t += MINUTE
    for key, (o, h, l, c) in overrides.items():
        key = key.replace("_", ":")
        if key in bars:
            bars[key] = Bar(at(day, key).astimezone(dt.timezone.utc), o, h, l, c, 1000.0)
    return tuple(bars.values())


BREAKOUT = {"11_00": (500.0, 500.5, 500.0, 500.4)}


def test_waits_until_the_opening_range_is_complete():
    assert decide_orb(DAY, day_bars(DAY, until="09:33")).action == WAIT


def test_buys_on_the_first_close_above_the_range_high_with_the_range_low_as_stop():
    decision = decide_orb(DAY, day_bars(DAY, **BREAKOUT, **{"12_00": (501, 502, 501, 502)}))
    assert decision.action == BUY
    assert decision.stop_price == 499.9 and decision.inputs.range_high == 500.1
    assert decision.entry_at == at(DAY, "11:01")
    assert decision.inputs.breakout_time == "11:00"


def test_a_wick_above_the_high_is_not_a_breakout():
    decision = decide_orb(DAY, day_bars(DAY, until="11:05", **{"11_00": (500.0, 501.0, 500.0, 500.1)}))
    assert decision.action == WAIT


def test_no_breakout_by_1544_is_no_trade():
    assert decide_orb(DAY, day_bars(DAY)).action == NO_TRADE
    late = decide_orb(DAY, day_bars(DAY, **{"15_44": (500.0, 500.5, 500.0, 500.4)}))
    assert late.action == NO_TRADE  # the entry would fall in the no-new-entries window


def test_the_last_usable_breakout_bar_is_1543():
    decision = decide_orb(DAY, day_bars(DAY, **{"15_43": (500.0, 500.5, 500.0, 500.4)}))
    assert decision.action == BUY and decision.entry_at == at(DAY, "15:44")


def test_a_gap_means_no_trade_and_half_days_are_skipped():
    bars = tuple(b for b in day_bars(DAY, until="11:00") if b.start != at(DAY, "10:00"))
    assert decide_orb(DAY, bars).action == NO_TRADE
    half = session(DAY.date, close=dt.time(13, 0))
    assert "half day" in decide_orb(half, day_bars(DAY, until="11:00")).reason


def test_backtest_trade_exits_at_the_stop_or_at_1555():
    bars = day_bars(DAY, **BREAKOUT, **{"15_55": (501.0, 501.0, 501.0, 501.0)})
    timed = simulate_long(DAY, bars, at(DAY, "11:01"), 499.5, at(DAY, "15:55"), config())
    assert (timed.exit, timed.exit_reason) == (501.0, "time")
    stopped = simulate_long(DAY, day_bars(DAY, **BREAKOUT, **{"13_00": (500.0, 500.0, 499.0, 499.5)}),
                            at(DAY, "11:01"), 499.9, at(DAY, "15:55"), config())
    assert (stopped.exit, stopped.exit_reason) == (499.9, "stop")


# --- engine ---------------------------------------------------------------------------------


def serve(fetcher, bars):
    """Have the fake vendor return these bars, windowed like a real request."""
    fetcher.minute_bars = lambda symbol, start, end, feed: [b for b in bars if start <= b.start < end]


def orb_engine(tmp_path, hhmm, **kw):
    return engine(tmp_path, clock=Clock(hhmm), cfg=config(strategy="opening_range_breakout"), **kw)


def test_engine_buys_on_the_minute_the_breakout_bar_closes(tmp_path):
    eng, gw, broker, clock, fetcher = orb_engine(tmp_path, "11:01")
    serve(fetcher, day_bars(SESSION, until="11:01", **BREAKOUT))
    clock.set("11:01", 10)
    eng.tick()
    signal = gw.journal.recent(1, {"signal"})[0]
    assert signal["action"] == "BUY" and signal["strategy"] == "opening_range_breakout"
    assert [m[0] for m in broker.mutations] == ["submit_market", "submit_stop_sell"]
    assert broker.mutations[1][3] == 499.9  # stop at the range low


def test_engine_keeps_watching_without_a_breakout(tmp_path):
    eng, gw, broker, clock, fetcher = orb_engine(tmp_path, "11:01")
    serve(fetcher, day_bars(SESSION, until="11:01"))
    clock.set("11:01", 10)
    eng.tick()
    assert gw.journal.recent(5, {"signal"}) == [] and broker.mutations == []
    assert "watching" in eng.status["watching"]


def test_engine_does_not_chase_a_breakout_it_saw_late(tmp_path):
    eng, gw, broker, clock, fetcher = orb_engine(tmp_path, "11:30")
    serve(fetcher, day_bars(SESSION, until="11:30", **BREAKOUT))
    clock.set("11:30", 10)
    eng.tick()
    assert "missed the breakout" in gw.journal.recent(1, {"signal"})[0]["reason"]
    assert broker.mutations == []


def test_engine_records_no_trade_at_the_cutoff(tmp_path):
    eng, gw, broker, clock, _ = orb_engine(tmp_path, "15:45")
    eng.tick()
    assert gw.journal.recent(1, {"signal"})[0]["action"] == "NO_TRADE"


# --- backtest report on a synthetic market ----------------------------------------------------


class TrendFetcher:
    """Every day breaks out at 10:30 and then drifts up (or down) into the close."""

    def __init__(self, days, up):
        self.calendar, self.up = days, up

    def minute_bars(self, symbol, start, end, feed):
        out, t = [], start
        while t < end:
            local = t.astimezone(ET)
            minutes = (local - dt.datetime.combine(local.date(), dt.time(9, 30), ET)).total_seconds() / 60
            p = 300.0 if minutes < 60 else 300.2 + (0.002 if self.up else -0.0005) * (minutes - 60)
            out.append(Bar(t.astimezone(dt.timezone.utc), p, p + 0.05, p - 0.05, p + 0.01, 1000.0))
            t += MINUTE
        return out

    def daily_bars(self, symbol, start, end, feed):
        return [Bar(dt.datetime.combine(d.date, dt.time(0), ET), 300, 301, 299, 300, 1e6)
                for d in self.calendar if start <= d.date <= end]

    def sessions(self, start, end):
        return [s for s in self.calendar if start <= s.date <= end]


def test_orb_backtest_report_runs_end_to_end(tmp_path):
    from baselinetrading.market_data import MarketData
    from tests.test_backtest import weekdays

    days = weekdays(dt.date(2019, 1, 7), 30)
    cfg = config(strategy="opening_range_breakout")
    data = MarketData(TrendFetcher(days, up=True), cfg, cache_dir=tmp_path,
                      now=lambda: dt.datetime(2026, 9, 28, 12, tzinfo=ET), sleep=lambda s: None)
    results = run_days(data, cfg, "SPY", days[5].date, days[-1].date, strategy="opening_range_breakout")
    trades = [d.trade for d in results if d.trade]
    assert len(trades) == len(results) and all(t.gross_bps > 0 for t in trades)
    closes = data.daily_closes("SPY", days[0].date, days[-1].date)
    text = report(results, closes, cfg, split="in_sample", trials=1, spec=ORB_SPEC)
    assert text.startswith(f"BACKTEST: {ORB_SPEC.name}") and "B3." in text




# --- adaptive breakout ------------------------------------------------------------------------

from baselinetrading.orb import ADAPTIVE_ORB_SPEC
from baselinetrading.journal import Trade


def test_the_range_steps_after_three_losses_in_a_row_and_wraps():
    minutes = ADAPTIVE_ORB_SPEC.range_minutes
    assert minutes([]) == 5
    assert minutes([False, False]) == 5
    assert minutes([False, False, False]) == 15
    assert minutes([False, False, True, False]) == 5  # a win resets the streak
    assert minutes([False] * 6) == 30
    assert minutes([False] * 9) == 5
    assert minutes([False] * 3 + [True]) == 15  # a win keeps the current length


def test_a_longer_range_moves_the_breakout_window():
    spec = ADAPTIVE_ORB_SPEC.day_spec([False] * 3)
    assert spec.range_end == dt.time(9, 45)
    early = day_bars(DAY, **{"09_40": (500.0, 500.2, 500.0, 500.15)}, **BREAKOUT)
    assert decide_orb(DAY, early).inputs.breakout_time == "09:40"  # a breakout for the 5-minute range
    decision = decide_orb(DAY, early, spec)
    assert decision.action == BUY and decision.inputs.breakout_time == "11:00"  # 09:40 was inside the range


def test_engine_remembers_losses_across_restarts(tmp_path):
    cfg = config(strategy="adaptive_opening_range_breakout")
    eng, gw, *_ = engine(tmp_path, cfg=cfg)
    for i in range(3):
        gw.closed_trades.append(Trade("strategy", "SPY", 1.0, 500.0, f"t{i}", 499.0, None, 1.0, 499.0, "x", "stop hit"))
    eng.tick()
    assert eng.status["range_minutes"] == 15
    again, *_ = engine(tmp_path, cfg=cfg)
    assert again.active_orb_spec().range_end == dt.time(9, 45)


def test_adaptive_backtest_changes_the_range_after_losing_streaks(tmp_path):
    from baselinetrading.market_data import MarketData
    from tests.test_backtest import weekdays

    days = weekdays(dt.date(2019, 1, 7), 20)
    cfg = config(strategy="adaptive_opening_range_breakout")
    data = MarketData(TrendFetcher(days, up=False), cfg, cache_dir=tmp_path,
                      now=lambda: dt.datetime(2026, 9, 28, 12, tzinfo=ET), sleep=lambda s: None)
    results = run_days(data, cfg, "SPY", days[5].date, days[-1].date, strategy="adaptive_opening_range_breakout")
    trades = [d.trade for d in results if d.trade]
    assert trades and all(t.net_bps < 0 for t in trades)
    # Losing every day: 5-minute range for 3 days, then 15, then 30 (and on).
    assert [d.trade.entry for d in results[:3]] == [trades[0].entry] * 3
