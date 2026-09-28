"""Walk-forward backtest of strategy C, net of costs, against the three baselines.

    python -m baselinetrading.backtest --split in_sample
    python -m baselinetrading.backtest --split validation
    python -m baselinetrading.backtest --split holdout --unlock-holdout <spec fingerprint>   # once, ever

Walk-forward by construction: each day is decided by strategy.decide_entry()
from data that existed at 15:30 that day, the same function the live engine
uses. The strategy fits no parameters, so there's nothing to re-fit through
time. Fills:
  entry = open of the 15:30 bar;
  stop  = 1% below the entry, checked on every bar from 15:30 to 15:54. A bar
          whose low reaches the stop exits at the lower of the stop and that
          bar's open (a gap fills worse). If a bar touches both, the stop wins;
  exit  = open of the 15:55 bar otherwise.
Costs come from costs.round_trip_cost() at the configured position size.

Every run is appended to results/trials.jsonl (commit it). The report's
significance level is Šidák-corrected for the number of distinct strategy
specs ever tried, and the holdout can be run only once per spec.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from baselinetrading.bars import ET, Bar, DataUnavailable, Session
from baselinetrading.config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config
from baselinetrading.costs import BPS, breakeven_win_rate, round_trip_cost
from baselinetrading.market_data import MarketData
from baselinetrading.stats import compare_to_breakeven, trades_needed
from baselinetrading.strategy import BUY, SPEC, StrategySpec, decide_entry

RESULTS_DIR = DEFAULT_CONFIG_PATH.parents[1] / "results"
NOTIONAL_FRACTION = 0.98  # same as the live engine
RANDOM_RUNS = 2000
BOOTSTRAP_RUNS = 2000
SEED = 20260928
FORWARD_MINUTES = (1, 5, 15)


@dataclass(frozen=True)
class SimTrade:
    date: dt.date
    entry: float
    exit: float
    exit_reason: str  # "stop" or "time"
    notional: float
    gross_bps: float
    cost_bps: float

    @property
    def net_bps(self) -> float:
        return self.gross_bps - self.cost_bps

    @property
    def net_usd(self) -> float:
        return self.notional * self.net_bps / BPS


def simulate_trade(
    session: Session, bars: tuple[Bar, ...], config: Config, spec: StrategySpec = SPEC
) -> SimTrade:
    """Long from the 15:30 open to the 15:55 open, with the 1% stop. `bars` must cover 15:30-15:56 gap-free."""
    entry_bar = spec.at(session, spec.entry_time)
    exit_bar = spec.at(session, spec.exit_time)
    by_start = {b.start: b for b in bars}
    entry = by_start[entry_bar].open
    stop = math.floor(entry * (1 - spec.stop_pct / 100) * 100) / 100
    exit_price, reason = by_start[exit_bar].open, "time"
    for bar in sorted(bars, key=lambda b: b.start):
        if entry_bar <= bar.start < exit_bar and bar.low <= stop:
            exit_price, reason = min(stop, bar.open), "stop"
            break
    notional = config.account.equity_cap_usd * NOTIONAL_FRACTION
    cost = round_trip_cost(config.costs, notional_usd=notional, price=entry, exit_by_stop=reason == "stop")
    return SimTrade(session.date, entry, exit_price, reason, notional, (exit_price / entry - 1) * BPS, cost.total_bps)


@dataclass
class DayResult:
    date: dt.date
    action: str
    reason: str
    signal_return: float | None
    trade: SimTrade | None  # the strategy's trade, if it bought
    always_long: SimTrade | None  # the same trade taken regardless of the signal (for baseline B3)
    forward_bps: dict[int, float] | None  # forward returns from the entry for baseline B1
    open_price: float | None
    close_price: float | None


def run_days(data: MarketData, config: Config, symbol: str, start: dt.date, end: dt.date,
             spec: StrategySpec = SPEC, feed: str | None = None) -> list[DayResult]:
    feed = feed or data.research_feed
    sessions = data.sessions(start, end)
    closes = data.daily_closes(symbol, start - dt.timedelta(days=10), end)
    calendar = data.sessions(start - dt.timedelta(days=10), start - dt.timedelta(days=1)) + sessions
    previous = {s.date: calendar[i - 1].date for i, s in enumerate(calendar) if i > 0}
    results = []
    for session in sessions:
        results.append(_one_day(data, config, symbol, session, closes.get(previous.get(session.date)), spec, feed))
    return results


def _one_day(data, config, symbol, session, previous_close, spec, feed) -> DayResult:
    def skip(reason: str) -> DayResult:
        return DayResult(session.date, "SKIP", reason, None, None, None, None, None, None)

    if not session.is_full_day:
        return skip("half day")
    if previous_close is None:
        return skip("no previous close")
    try:
        signal = data.minute_bars(symbol, session, spec.at(session, spec.signal_window_start), spec.at(session, spec.signal_time), feed=feed)
        entry = data.minute_bars(symbol, session, spec.at(session, spec.entry_window_start), spec.at(session, spec.entry_time), feed=feed)
        trade_bars = data.minute_bars(
            symbol, session, spec.at(session, spec.entry_time), spec.at(session, spec.exit_time) + dt.timedelta(minutes=1), feed=feed
        )
        day_open = data.minute_bars(symbol, session, session.open, session.open + dt.timedelta(minutes=1), feed=feed)[0].open
    except DataUnavailable as exc:
        return skip(f"data unavailable: {exc}")
    decision = decide_entry(session, previous_close, signal, entry, spec)
    always = simulate_trade(session, trade_bars, config, spec)
    first = trade_bars[0].open
    forward = {m: (trade_bars[m - 1].close / first - 1) * BPS for m in FORWARD_MINUTES}
    close_price = trade_bars[-1].close  # 15:55 close; the official close is not needed for the intraday baseline
    return DayResult(
        session.date, decision.action, decision.reason,
        decision.inputs.signal_return if decision.inputs else None,
        always if decision.action == BUY else None, always, forward, day_open, close_price,
    )


# --- the report ----------------------------------------------------------------------------


def bootstrap_mean_ci(values: list[float], rng: random.Random, runs: int = BOOTSTRAP_RUNS, confidence: float = 0.95):
    if len(values) < 2:
        return (math.nan, math.nan)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(runs))
    lo = means[int((1 - confidence) / 2 * runs)]
    hi = means[int((1 + confidence) / 2 * runs) - 1]
    return lo, hi


def report(days: list[DayResult], closes: dict[dt.date, float], config: Config, *, split: str, trials: int,
           spec: StrategySpec = SPEC, feed: str = "sip") -> str:
    rng = random.Random(SEED)
    trades = [d.trade for d in days if d.trade]
    eligible = [d for d in days if d.always_long]
    skipped = [d for d in days if d.action == "SKIP"]
    alpha = 1 - (1 - 0.05) ** (1 / max(trials, 1))
    confidence = 1 - alpha
    lines = []
    add = lines.append
    add(f"BACKTEST: strategy C ({spec.fingerprint()}) on {split}, feed {feed}, ${config.account.equity_cap_usd:,.0f} account")

    # Breakeven first, always.
    if trades:
        nets = [t.net_bps for t in trades]
        wins = [n for n in nets if n > 0]
        losses = [-n for n in nets if n <= 0]
        cost_bps = statistics.fmean(t.cost_bps for t in trades)
        if wins and losses:
            be = breakeven_win_rate(avg_win=statistics.fmean(wins), avg_loss=statistics.fmean(losses), cost=0.0)
            add(f"BREAKEVEN WIN RATE: {be:.1%}  (avg net win {statistics.fmean(wins):.2f} bp, avg net loss "
                f"{statistics.fmean(losses):.2f} bp; costs {cost_bps:.2f} bp per round trip)")
        else:
            be = math.nan
            add(f"BREAKEVEN WIN RATE: undefined (all trades {'won' if wins else 'lost'}); costs {cost_bps:.2f} bp per round trip")
    else:
        add("BREAKEVEN WIN RATE: no trades")
        be = math.nan
    add(f"Trials so far (distinct specs): {trials}; significance level {alpha:.4f} (Šidák), confidence {confidence:.2%}")
    add("")
    add(f"Days: {len(days)}   trades: {len(trades)}   no-trade: {len(eligible) - len(trades)}   skipped: {len(skipped)}")
    reasons: dict[str, int] = {}
    for d in skipped:
        key = d.reason.split(":")[0]
        reasons[key] = reasons.get(key, 0) + 1
    for key, count in sorted(reasons.items()):
        add(f"   skipped, {key}: {count}")
    if not trades:
        return "\n".join(lines)
    stops = sum(1 for t in trades if t.exit_reason == "stop")
    add(f"Stop exits: {stops}")
    add("")

    n = len(trades)
    win_count = sum(1 for t in trades if t.net_bps > 0)
    add("Strategy, net of costs")
    if not math.isnan(be):
        verdict = compare_to_breakeven(win_count, n, be, confidence=confidence)
        add(f"   {verdict.describe()}")
        if verdict.verdict == "INCONCLUSIVE" and 0 < be < 0.95:
            need = trades_needed(breakeven=be, true_win_rate=min(be + 0.05, 0.999), alpha=alpha)
            add(f"   n = {n} is too small to conclude: a 5-point edge needs ~{need:,} trades at this significance level")
    mean_net = statistics.fmean(t.net_bps for t in trades)
    lo, hi = bootstrap_mean_ci([t.net_bps for t in trades], rng)
    add(f"   mean net per trade {mean_net:+.2f} bp, 95% bootstrap CI {lo:+.2f} to {hi:+.2f} bp")
    add(f"   mean gross per trade {statistics.fmean(t.gross_bps for t in trades):+.2f} bp")
    total_net = sum(t.net_usd for t in trades)
    add(f"   total net P&L ${total_net:+,.2f} ({total_net / config.account.equity_cap_usd:+.2%} of the account, no compounding)")
    add("")

    # B1: the no-change forecast.
    add("B1. No-change forecast: do signal days move up after 15:30 more than zero?")
    buy_days = [d for d in days if d.trade]
    for m in FORWARD_MINUTES:
        values = [d.forward_bps[m] for d in buy_days]
        lo_m, hi_m = bootstrap_mean_ci(values, rng)
        add(f"   +{m:>2} min: mean {statistics.fmean(values):+.2f} bp (95% CI {lo_m:+.2f} to {hi_m:+.2f})")
    gross = [t.gross_bps for t in trades]
    lo_g, hi_g = bootstrap_mean_ci(gross, rng)
    b1 = lo > 0
    add(f"   to exit: gross {statistics.fmean(gross):+.2f} bp (CI {lo_g:+.2f} to {hi_g:+.2f}); "
        f"net CI {'excludes' if b1 else 'includes'} zero -> {'PASS' if b1 else 'FAIL'}")
    add("")

    # B2: buy-and-hold over the same period.
    add("B2. Buy-and-hold over the same period")
    dates = sorted(d.date for d in days)
    in_range = sorted(k for k in closes if dates[0] <= k <= dates[-1])
    earlier = sorted(k for k in closes if k < dates[0])
    b2 = False
    if in_range and earlier:
        start_close, end_close = closes[earlier[-1]], closes[in_range[-1]]
        bh_cost = round_trip_cost(config.costs, notional_usd=config.account.equity_cap_usd * NOTIONAL_FRACTION, price=start_close)
        bh = (end_close / start_close - 1) - bh_cost.total_bps / BPS
        strat = total_net / (config.account.equity_cap_usd * NOTIONAL_FRACTION)
        intraday = sum(d.close_price / d.open_price - 1 for d in days if d.open_price and d.close_price)
        b2 = strat > bh
        add(f"   (a) close-to-close buy-and-hold, net: {bh:+.2%}   strategy: {strat:+.2%} -> {'PASS' if b2 else 'FAIL'}")
        add(f"   (b) open-to-15:55 every day, gross, diagnostic only: {intraday:+.2%}")
    else:
        add("   not enough daily closes to compute")
    add("")

    # B3: random days, same entry time, hold, size and costs.
    add("B3. Random entries: the same trade on randomly chosen days (same count, size, hold, costs)")
    pool = [d.always_long.net_bps for d in eligible]
    target = sum(t.net_bps for t in trades)
    beats = sum(1 for _ in range(RANDOM_RUNS) if sum(rng.sample(pool, n)) >= target) if len(pool) >= n else RANDOM_RUNS
    p_value = (beats + 1) / (RANDOM_RUNS + 1)
    b3 = p_value < alpha
    add(f"   always-long mean {statistics.fmean(pool):+.2f} bp over {len(pool)} days; p = {p_value:.4f} "
        f"vs level {alpha:.4f} -> {'PASS' if b3 else 'FAIL'}")
    add("")

    passed = b1 and b2 and b3
    add(f"EDGE: {'YES, all three baselines beaten' if passed else 'NONE, not all three baselines beaten'} "
        f"(B1 {'pass' if b1 else 'fail'}, B2 {'pass' if b2 else 'fail'}, B3 {'pass' if b3 else 'fail'})")
    if split == "validation" and mean_net <= 0:
        add("PRE-COMMITTED RULE: validation mean net P&L <= 0. The strategy is dead. Don't retune it.")
    return "\n".join(lines)


# --- trial ledger and holdout lock ----------------------------------------------------------


def ledger_path() -> Path:
    return RESULTS_DIR / "trials.jsonl"


def read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_ledger(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest strategy C on one pre-committed split.")
    parser.add_argument("--split", choices=("in_sample", "validation", "holdout"), required=True)
    parser.add_argument("--feed", choices=("sip", "iex"), help="default: the research feed from settings")
    parser.add_argument("--unlock-holdout", metavar="FINGERPRINT", help="the frozen spec's fingerprint")
    args = parser.parse_args(argv)
    try:
        config = load_config()
        from baselinetrading.credentials import load_credentials

        credentials = load_credentials()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    fingerprint = SPEC.fingerprint()
    path = ledger_path()
    ledger = read_ledger(path)
    if args.split == "holdout":
        if args.unlock_holdout != fingerprint:
            print(f"refusing: the holdout needs --unlock-holdout {fingerprint} (the frozen spec's fingerprint)", file=sys.stderr)
            return 2
        if any(e["split"] == "holdout" and e["spec"] == fingerprint for e in ledger):
            print("refusing: this spec has already been run on the holdout; a second look makes it validation data",
                  file=sys.stderr)
            return 2

    from baselinetrading.alpaca_client import AlpacaFetcher

    data = MarketData(
        AlpacaFetcher(credentials, adjustment=config.data.adjustment), config,
        cache_dir=DEFAULT_CONFIG_PATH.parents[1] / "data" / "cache", holdout_unlocked=args.split == "holdout",
    )
    period = getattr(config.splits, args.split)
    feed = args.feed or config.data.research_feed
    symbol = config.trading.symbols[0]
    try:
        days = run_days(data, config, symbol, period.start, period.end, feed=feed)
        closes = data.daily_closes(symbol, period.start - dt.timedelta(days=10), period.end)
    except DataUnavailable as exc:
        print(f"DATA UNAVAILABLE: {exc}", file=sys.stderr)
        return 1
    trials = len({e["spec"] for e in ledger} | {fingerprint})
    text = report(days, closes, config, split=args.split, trials=trials, feed=feed)
    print(text)
    trades = [d.trade for d in days if d.trade]
    append_ledger(path, {
        "ts": dt.datetime.now(ET).isoformat(), "spec": fingerprint, "split": args.split, "feed": feed,
        "trades": len(trades), "mean_net_bps": statistics.fmean(t.net_bps for t in trades) if trades else None,
    })
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"{args.split}-{feed}-{fingerprint}.txt").write_text(text + "\n")
    (RESULTS_DIR / f"{args.split}-{feed}-{fingerprint}-trades.json").write_text(
        json.dumps([{**asdict(t), "net_bps": t.net_bps} for t in trades], default=str, indent=1)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
