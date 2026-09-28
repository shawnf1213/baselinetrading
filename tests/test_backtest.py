import datetime as dt
import random

import pytest

from baselinetrading import backtest
from baselinetrading.bars import ET, MINUTE, Bar
from baselinetrading.backtest import report, run_days, simulate_trade
from baselinetrading.config import DEFAULT_CONFIG_PATH, load_config
from baselinetrading.market_data import HoldoutLocked, MarketData
from tests.fakes import session

CONFIG = load_config(DEFAULT_CONFIG_PATH, today=dt.date(2026, 9, 28))
DAY = session(dt.date(2020, 3, 2))


def at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return dt.datetime.combine(day.date, dt.time(h, m), ET)


def bar(day, hhmm, o, h, l, c):
    return Bar(at(day, hhmm).astimezone(dt.timezone.utc), o, h, l, c, 1000.0)


def flat_trade_bars(day, price=500.0, **overrides):
    bars = {}
    t = at(day, "15:30")
    while t < at(day, "15:56"):
        key = t.strftime("%H:%M")
        bars[key] = bar(day, key, price, price + 0.1, price - 0.1, price)
        t += MINUTE
    for key, values in overrides.items():
        bars[key.replace("_", ":")] = bar(day, key.replace("_", ":"), *values)
    return tuple(bars.values())


def test_time_exit_at_the_1555_open():
    bars = flat_trade_bars(DAY, **{"15_55": (502.0, 502.0, 502.0, 502.0)})
    trade = simulate_trade(DAY, bars, CONFIG)
    assert (trade.entry, trade.exit, trade.exit_reason) == (500.0, 502.0, "time")
    assert trade.gross_bps == pytest.approx(40.0)
    assert trade.net_bps < trade.gross_bps


def test_stop_exit_at_the_stop_or_worse_on_a_gap():
    touched = simulate_trade(DAY, flat_trade_bars(DAY, **{"15_40": (499.0, 499.0, 494.0, 494.5)}), CONFIG)
    assert (touched.exit, touched.exit_reason) == (495.0, "stop")
    gapped = simulate_trade(DAY, flat_trade_bars(DAY, **{"15_40": (490.0, 491.0, 489.0, 490.0)}), CONFIG)
    assert gapped.exit == 490.0  # opened below the stop: filled at the open, not the stop


def test_a_bar_touching_stop_and_higher_prices_counts_as_the_stop():
    trade = simulate_trade(DAY, flat_trade_bars(DAY, **{"15_31": (500.0, 510.0, 494.0, 509.0)}), CONFIG)
    assert trade.exit_reason == "stop"


def test_stop_exits_pay_the_extra_slippage():
    time_exit = simulate_trade(DAY, flat_trade_bars(DAY), CONFIG)
    stop_exit = simulate_trade(DAY, flat_trade_bars(DAY, **{"15_40": (499.0, 499.0, 494.0, 494.5)}), CONFIG)
    assert stop_exit.cost_bps - time_exit.cost_bps == pytest.approx(CONFIG.costs.stop_extra_slippage_bps, abs=0.01)


# --- a synthetic market where the signal does (or doesn't) work ---------------------------------


class PathFetcher:
    """Each day: morning drifts up or down; the last half hour follows it if `works`, otherwise it's random."""

    def __init__(self, days, works, seed=1):
        rng = random.Random(seed)
        self.calendar = days
        self.plan = {}
        price = 300.0
        for d in days:
            up = rng.random() < 0.5
            morning = 1.0 if up else -1.0
            if works:
                last = 0.6 if up else -0.6
            else:
                last = rng.choice((0.6, -0.6))
            self.plan[d.date] = (price, morning, last)
            price += morning + last

    def _price(self, day, moment):
        base, morning, last = self.plan[day]
        minutes = (moment - dt.datetime.combine(day, dt.time(9, 30), ET)).total_seconds() / 60
        p = base + morning * min(minutes, 30) / 30
        if minutes >= 360:
            p += last * min(minutes - 360, 25) / 25
        return round(p, 2)

    def minute_bars(self, symbol, start, end, feed):
        out, t = [], start
        while t < end:
            d = t.astimezone(ET).date()
            o, c = self._price(d, t), self._price(d, t + MINUTE)
            out.append(Bar(t.astimezone(dt.timezone.utc), o, max(o, c) + 0.01, min(o, c) - 0.01, c, 1000.0))
            t += MINUTE
        return out

    def daily_bars(self, symbol, start, end, feed):
        out = []
        for d in self.calendar:
            if start <= d.date <= end:
                base, morning, last = self.plan[d.date]
                close = base + morning + last
                out.append(Bar(dt.datetime.combine(d.date, dt.time(0), ET), base, max(base, close) + 2, min(base, close) - 2, close, 1e6))
        return out

    def sessions(self, start, end):
        return [s for s in self.calendar if start <= s.date <= end]


def weekdays(start, count):
    days, d = [], start
    while len(days) < count:
        if d.weekday() < 5:
            days.append(session(d))
        d += dt.timedelta(days=1)
    return days


def run(tmp_path, works):
    days = weekdays(dt.date(2019, 1, 7), 160)
    fetcher = PathFetcher(days, works)
    data = MarketData(fetcher, CONFIG, cache_dir=tmp_path, now=lambda: dt.datetime(2026, 9, 28, 12, tzinfo=ET),
                      sleep=lambda s: None)
    results = run_days(data, CONFIG, "SPY", days[5].date, days[-1].date)
    closes = data.daily_closes("SPY", days[0].date, days[-1].date)
    return results, report(results, closes, CONFIG, split="in_sample", trials=1)


def test_the_report_starts_with_the_breakeven_and_finds_a_real_effect(tmp_path):
    results, text = run(tmp_path, works=True)
    lines = text.splitlines()
    assert lines[1].startswith("BREAKEVEN WIN RATE")
    trades = [d.trade for d in results if d.trade]
    assert trades and all(t.gross_bps > 0 for t in trades)
    assert "B1. No-change forecast" in text and "-> PASS" in text.split("B2.")[0]
    assert "B3." in text and "B3 pass" in text


def test_no_effect_means_no_edge(tmp_path):
    _, text = run(tmp_path, works=False)
    assert "EDGE: NONE" in text


def test_the_backtest_cannot_touch_the_holdout(tmp_path):
    days = weekdays(dt.date(2024, 1, 8), 10)
    data = MarketData(PathFetcher(days, True), CONFIG, cache_dir=tmp_path,
                      now=lambda: dt.datetime(2026, 9, 28, 12, tzinfo=ET), sleep=lambda s: None)
    with pytest.raises(HoldoutLocked):
        run_days(data, CONFIG, "SPY", days[0].date, days[-1].date)


def test_the_holdout_needs_the_fingerprint_and_runs_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APCA_API_KEY_ID", "PKTEST000000000000")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "secret")
    monkeypatch.setattr(backtest, "RESULTS_DIR", tmp_path)
    assert backtest.main(["--split", "holdout"]) == 2
    assert "needs --unlock-holdout" in capsys.readouterr().err
    backtest.append_ledger(tmp_path / "trials.jsonl", {"spec": backtest.SPEC.fingerprint(), "split": "holdout"})
    assert backtest.main(["--split", "holdout", "--unlock-holdout", backtest.SPEC.fingerprint()]) == 2
    assert "already been run on the holdout" in capsys.readouterr().err
