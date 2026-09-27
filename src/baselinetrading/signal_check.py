"""Show strategy C's decision for one session, with every input behind it. Places no orders.

    python -m baselinetrading.signal_check --date 2026-09-25   # a past session (research feed)
    python -m baselinetrading.signal_check --live              # today; run between 15:30:05 and 15:30:59 ET

Past dates inside the in-sample and validation splits work; the holdout stays locked.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import sys

from baselinetrading.alpaca_client import AlpacaFetcher
from baselinetrading.bars import ET, DataUnavailable
from baselinetrading.config import ConfigError, load_config
from baselinetrading.credentials import load_credentials
from baselinetrading.data_check import CACHE_DIR
from baselinetrading.market_data import HoldoutLocked, MarketData
from baselinetrading.strategy import SPEC, decide_from_data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    when = parser.add_mutually_exclusive_group(required=True)
    when.add_argument("--date", type=dt.date.fromisoformat, help="a past session, YYYY-MM-DD")
    when.add_argument("--live", action="store_true", help="today, on the live feed")
    args = parser.parse_args(argv)
    try:
        config = load_config()
        credentials = load_credentials()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    data = MarketData(AlpacaFetcher(credentials, adjustment=config.data.adjustment), config, cache_dir=CACHE_DIR)
    day = dt.datetime.now(ET).date() if args.live else args.date
    try:
        session = data.session_on(day)
        if session is None:
            print(f"{day}: market closed")
            return 0
        decision = decide_from_data(data, config.trading.symbols[0], session, live=args.live)
    except (DataUnavailable, HoldoutLocked) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    print(f"{decision.date} {config.trading.symbols[0]} spec {SPEC.fingerprint()}: {decision.action} ({decision.reason})")
    if decision.inputs:
        for name, value in dataclasses.asdict(decision.inputs).items():
            print(f"  {name}: {value:.6g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
