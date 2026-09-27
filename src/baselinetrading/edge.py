"""The edge arithmetic for your account, computed from settings.toml.

    python -m baselinetrading.edge                   # uses config/settings.toml
    python -m baselinetrading.edge --price 650       # reference share price

Prints what the risk rules allow at the configured equity, what one round trip
costs at several position sizes, the breakeven win rates those costs imply, and
how many trades it takes to tell a real edge from luck. It reads no market
data: --price is a reference share price, not a quote.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from pathlib import Path

from baselinetrading.config import DEFAULT_CONFIG_PATH, Config, ConfigError, CostConfig, DateRange, load_config
from baselinetrading.costs import breakeven_win_rate, round_trip_cost
from baselinetrading.stats import trades_needed

COMPARISON_SIZES_USD = (100.0, 500.0, 2_000.0, 10_000.0)
REFERENCE_SIZE_USD = 2_000.0
STOP_DISTANCES_PCT = (0.10, 0.25, 0.50, 1.00)
TARGET_MULTIPLES = (1.0, 2.0)
EXAMPLE_STOP_PCT = 0.50
EDGE_TO_DETECT = 0.05  # win-rate points above breakeven
TRADING_DAYS_PER_YEAR = 252


def build_report(config: Config, *, price: float) -> str:
    costs = config.costs
    equity = config.account.equity_cap_usd
    sizes = sorted({equity, *COMPARISON_SIZES_USD})
    lines: list[str] = []
    add = lines.append

    add(f"EDGE ARITHMETIC: ${equity:,.2f} account, reference price ${price:,.2f}/share (an assumption, not a quote)")
    add("Cost model, from [costs] in settings.toml:")
    add(
        f"  spread ${costs.spread_usd_per_share:g}/share; slippage {costs.slippage_bps_per_side:g} bp per order, "
        f"+{costs.stop_extra_slippage_bps:g} bp on stop exits; commission ${costs.commission_per_order_usd:g}/order"
    )
    add(
        f"  SEC ${_plain(costs.sec_fee_per_million_usd)} per $1M sold; "
        f"FINRA TAF ${_plain(costs.finra_taf_per_share_usd)}/share sold (max ${_plain(costs.finra_taf_max_usd)}); "
        f"CAT ${_plain(costs.cat_fee_per_share_usd)}/share on every order"
    )
    add("  " + ("each fee rounded UP to the cent, per order" if costs.round_each_fee_up_to_cent else "fees not rounded"))
    add("")

    risk_pct = config.risk.max_risk_per_trade_pct
    loss_pct = config.risk.max_daily_loss_pct
    example_risk = equity * EXAMPLE_STOP_PCT / 100
    add(f"1. What the risk rules allow at ${equity:,.2f}")
    add(
        f"   max risk per trade {risk_pct:g}% = ${equity * risk_pct / 100:,.2f}; "
        f"max daily loss {loss_pct:g}% = ${equity * loss_pct / 100:,.2f}"
    )
    add(f"   largest position: ${equity:,.2f} (cash account, no leverage)")
    add(f"   The {risk_pct:g}% rule only limits size when the stop is wider than {risk_pct:g}%. Intraday stops are")
    add(f"   much tighter, so cash sets the size: a {EXAMPLE_STOP_PCT:.2f}% stop on a ${equity:,.2f} position risks")
    add(f"   ${example_risk:,.2f} ({EXAMPLE_STOP_PCT:g}% of the account), not ${equity * risk_pct / 100:,.2f}.")
    add("")

    add("2. Cost of one round trip (buy, then sell; exit not by a stop)")
    add(
        f"   {'position':>18} {'shares':>9} {'spread':>9} {'slippage':>9} {'fees':>9} {'total':>9}"
        f" {'total bp':>9} {'fees/total':>11}"
    )
    for size in sizes:
        cost = round_trip_cost(costs, notional_usd=size, price=price)
        add(
            f"   {_size_label(size, equity):>18} {size / price:>9.4f} {_usd(cost.spread_usd):>9}"
            f" {_usd(cost.slippage_usd):>9} {_usd(cost.fees_usd):>9} {_usd(cost.total_usd):>9}"
            f" {cost.total_bps:>9.1f} {cost.fees_usd / cost.total_usd:>11.0%}"
        )
    add(f"   A stop exit adds {costs.stop_extra_slippage_bps:g} bp: it becomes a market order after the price moved.")
    add("")

    add("3. Breakeven win rate = (stop + cost of a loss) / (target - cost of a win + stop + cost of a loss)")
    for multiple in TARGET_MULTIPLES:
        add(f"   target = {multiple:g} x stop (breakeven with zero costs: {1 / (1 + multiple):.1%})")
        add(f"   {'stop':>8}" + "".join(f"{_size_label(size, equity):>19}" for size in sizes))
        for stop_pct in STOP_DISTANCES_PCT:
            cells = "".join(f"{_pct(_breakeven(costs, size, price, stop_pct, multiple)):>19}" for size in sizes)
            add(f"   {stop_pct:>7.2f}%{cells}")
    add('   "never": the round-trip cost is at least the whole target, so no win rate is profitable.')
    add("")

    add("4. Trades needed to tell a real edge from luck (one-sided test, 5% false-positive rate, 80% power)")
    add(
        f"   example: {EXAMPLE_STOP_PCT:.2f}% stop, target = stop, "
        f"true win rate {EDGE_TO_DETECT * 100:g} points above breakeven"
    )
    for size in dict.fromkeys((equity, REFERENCE_SIZE_USD)):
        p = _breakeven(costs, size, price, EXAMPLE_STOP_PCT, 1.0)
        label = _size_label(size, equity)
        if math.isinf(p) or p + EDGE_TO_DETECT >= 1:
            add(f"   {label:>18}: breakeven {_pct(p)}; no room for a {EDGE_TO_DETECT * 100:g}-point edge above it")
            continue
        n = trades_needed(breakeven=p, true_win_rate=p + EDGE_TO_DETECT)
        add(
            f"   {label:>18}: breakeven {p:.1%}; a true {p + EDGE_TO_DETECT:.1%} needs ~{n:,} trades "
            f"(~{n / TRADING_DAYS_PER_YEAR:.1f} years of trading days)"
        )
    holdout = config.splits.holdout
    add("   Settled cash (T+1) allows at most one full-size round trip per trading day, fewer if the")
    add(f"   strategy skips days. Your holdout ({holdout.start} to {holdout.end}) spans {_weekdays(holdout):,}")
    add("   weekdays (holidays make it slightly fewer trading days): the most trades it can hold.")
    add("")

    yours = round_trip_cost(costs, notional_usd=equity, price=price)
    add("5. What this means")
    add(
        f"   At ${equity:,.2f}, fees are {yours.fees_usd / yours.total_usd:.0%} of the round-trip cost "
        f"({yours.total_bps:.1f} bp in total)."
    )
    if equity < REFERENCE_SIZE_USD:
        reference = round_trip_cost(costs, notional_usd=REFERENCE_SIZE_USD, price=price)
        p_yours = _breakeven(costs, equity, price, EXAMPLE_STOP_PCT, 1.0)
        p_reference = _breakeven(costs, REFERENCE_SIZE_USD, price, EXAMPLE_STOP_PCT, 1.0)
        add(
            f"   At ${REFERENCE_SIZE_USD:,.2f} they are {reference.fees_usd / reference.total_usd:.0%} "
            f"({reference.total_bps:.1f} bp in total): the cent rounding stops mattering."
        )
        add(
            f"   A {EXAMPLE_STOP_PCT:.2f}% stop with target = stop has to win {_pct(p_yours)} of trades at "
            f"${equity:,.2f}, vs {_pct(p_reference)} at ${REFERENCE_SIZE_USD:,.2f}."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print costs, breakeven win rates and sample sizes for your account.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="settings file (default: %(default)s)")
    parser.add_argument(
        "--price", type=float, default=650.0, help="reference share price, an assumption (default: %(default)s)"
    )
    args = parser.parse_args(argv)
    if not (math.isfinite(args.price) and args.price > 0):
        parser.error("--price must be a positive number")
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(build_report(config, price=args.price))
    return 0


def _breakeven(costs: CostConfig, size: float, price: float, stop_pct: float, multiple: float) -> float:
    stop_bps = stop_pct * 100  # 0.50% = 50 bp
    win_cost = round_trip_cost(costs, notional_usd=size, price=price).total_bps
    loss_cost = round_trip_cost(costs, notional_usd=size, price=price, exit_by_stop=True).total_bps
    return breakeven_win_rate(avg_win=multiple * stop_bps, avg_loss=stop_bps, cost=win_cost, loss_cost=loss_cost)


def _size_label(size: float, equity: float) -> str:
    return f"${size:,.2f}" + (" (you)" if size == equity else "")


def _usd(value: float) -> str:
    return f"${value:,.4f}" if value < 1 else f"${value:,.2f}"


def _plain(value: float) -> str:
    """0.000003 rather than 3e-06."""
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _pct(value: float) -> str:
    return "never" if math.isinf(value) else f"{value:.1%}"


def _weekdays(period: DateRange) -> int:
    days = (period.end - period.start).days + 1
    return sum(1 for offset in range(days) if (period.start + dt.timedelta(days=offset)).weekday() < 5)


if __name__ == "__main__":
    raise SystemExit(main())
