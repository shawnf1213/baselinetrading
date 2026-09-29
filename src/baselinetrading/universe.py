"""The stock list: the most traded US company stocks that have options, by dollar volume.

Rule (docs/decisions.md, 2026-09-28): average daily dollar volume (volume x the
day's VWAP, unadjusted) over the last 20 closed sessions, among active Alpaca
assets with options. Funds (ETFs, ETNs, trusts) are left out, and so is any
stock missing a session, or one the option rules couldn't trade on the next
session: the call and the put they would pick must exist, and one contract of
each must cost at most the per-trade premium cap at the latest ask. The
ranking is printed; copying it into trading.symbols is a decision, made by
hand with an entry in the log.

    python -m baselinetrading.universe            # top 10 as of the latest close
    python -m baselinetrading.universe --top 15
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass

from baselinetrading.bars import ET
from baselinetrading.options import MULTIPLIER, OPTION_SPEC, OptionSpec, choose_call, choose_put

SESSIONS = 20
BATCH = 200  # symbols per bars request
FUND_NAME = re.compile(
    r"\b(ETFs?|ETNs?|fund|trust|iShares|SPDR|ProShares|Direxion|GraniteShares|Defiance|T-Rex|leveraged|inverse)\b"
    r"|\b\d(\.\d+)?x\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Ranked:
    symbol: str
    name: str
    dollar_volume: float  # average per session, USD
    last_close: float
    fund: bool


def is_fund(name: str, exchange: str) -> bool:
    """ETFs, ETNs and trusts: listed on NYSE Arca (almost only funds are), or named like one."""
    return exchange == "ARCA" or FUND_NAME.search(name) is not None


def rank_by_dollar_volume(days: Mapping[str, list[tuple[dt.date, float, float, float]]], sessions: list[dt.date],
                          names: Mapping[str, tuple[str, bool]]) -> list[Ranked]:
    """Average volume x VWAP over `sessions`, highest first. A symbol missing any of them is left out.

    days: symbol -> [(session date, volume, vwap, close)]; names: symbol -> (name, is a fund).
    """
    wanted = set(sessions)
    out = []
    for symbol, bars in days.items():
        by_day = {d: (volume, vwap, close) for d, volume, vwap, close in bars if d in wanted}
        if len(by_day) < len(wanted):
            continue
        average = sum(volume * vwap for volume, vwap, _ in by_day.values()) / len(by_day)
        name, fund = names.get(symbol, ("", False))
        out.append(Ranked(symbol, name, average, by_day[max(by_day)][2], fund))
    return sorted(out, key=lambda r: r.dollar_volume, reverse=True)


def untradeable(broker, symbol: str, price: float, day: dt.date, cap_usd: float,
                spec: OptionSpec = OPTION_SPEC) -> str | None:
    """Why the option rules couldn't buy one call and one put on `symbol` on `day` within the cap; None if they could."""
    for kind, choose in (("call", choose_call), ("put", choose_put)):
        contracts = broker.option_contracts(symbol, day + dt.timedelta(days=spec.min_days),
                                            day + dt.timedelta(days=spec.max_days), kind)
        contract = choose(contracts, price, day, spec)
        if contract is None:
            return f"no {kind} {spec.min_days}-{spec.max_days} days out"
        _, ask = broker.option_quote(contract.symbol)
        if not ask > 0:
            return f"no ask for {contract.symbol}"
        if ask * MULTIPLIER > cap_usd:
            return f"one {kind} ({contract.symbol}) costs ${ask * MULTIPLIER:,.0f}, over the ${cap_usd:,.0f} cap"
    return None


def main(argv: list[str] | None = None) -> None:
    from baselinetrading.alpaca_client import AlpacaFetcher
    from baselinetrading.broker import AlpacaBroker
    from baselinetrading.config import load_config
    from baselinetrading.credentials import load_credentials

    parser = argparse.ArgumentParser(description="Rank US stocks with options by average daily dollar volume.")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args(argv)

    config = load_config()
    cap = config.account.equity_cap_usd * config.risk.max_risk_per_trade_pct / 100
    creds = load_credentials()
    fetcher = AlpacaFetcher(creds, adjustment="raw")  # dollar volume at the prices actually traded
    broker = AlpacaBroker(creds)

    now = dt.datetime.now(ET)
    calendar = fetcher.sessions(now.date() - dt.timedelta(days=45), now.date() + dt.timedelta(days=10))
    sessions = [s.date for s in calendar if s.close <= now][-SESSIONS:]
    next_session = next(s.date for s in calendar if s.close > now)

    names = {symbol: (name, is_fund(name, exchange)) for symbol, name, exchange in broker.stocks_with_options()}
    symbols = sorted(names)
    start = dt.datetime.combine(sessions[0], dt.time(0), ET)
    # the free plan refuses SIP data from the last 15 minutes, even for a day that has closed
    end = min(dt.datetime.combine(sessions[-1], dt.time(23, 59), ET), now - dt.timedelta(minutes=16))
    days: dict[str, list[tuple[dt.date, float, float, float]]] = {}
    for i in range(0, len(symbols), BATCH):
        days.update(fetcher.daily_volumes(symbols[i:i + BATCH], start, end, "sip"))

    ranked = rank_by_dollar_volume(days, sessions, names)
    print(f"US stocks with options by average daily dollar volume, {sessions[0]} to {sessions[-1]} "
          f"({len(sessions)} sessions; {len(names)} assets with options, {len(ranked)} with every session). "
          f"Options checked for {next_session} against a ${cap:,.0f} premium cap.")
    stocks: list[Ranked] = []
    for n, r in enumerate((r for r in ranked if not r.fund), 1):
        if len(stocks) == args.top:
            break
        why = untradeable(broker, r.symbol, r.last_close, next_session, cap)
        if why is None:
            stocks.append(r)
        print(f"{n:3d}  {r.symbol:<6} ${r.dollar_volume / 1e9:6.2f}B a day  close {r.last_close:>9.2f}  {r.name}"
              + (f"\n       left out: {why}" if why else ""))
    cutoff = stocks[-1].dollar_volume if stocks else 0.0
    funds = [r for r in ranked if r.fund and r.dollar_volume >= cutoff]
    if funds:
        print("Funds left out that would have ranked in that list: "
              + ", ".join(f"{r.symbol} (${r.dollar_volume / 1e9:.2f}B, {r.name})" for r in funds))
    print("trading.symbols = [" + ", ".join(f'"{r.symbol}"' for r in stocks) + "]")


if __name__ == "__main__":
    main()
