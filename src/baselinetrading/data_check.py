"""Check the market data path end to end with your Alpaca paper keys.

    set -a; source .env; set +a
    python -m baselinetrading.data_check

For the most recent completed session, it fetches the windows strategy C uses
(09:30-10:00 and 15:30-15:55 ET) on both feeds and reports gaps and the price
difference between IEX and SIP. It places no orders. Nothing it reads is in
the holdout.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from baselinetrading.alpaca_client import AlpacaFetcher
from baselinetrading.bars import ET, DataUnavailable, Session
from baselinetrading.config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from baselinetrading.credentials import load_credentials
from baselinetrading.market_data import MarketData

CACHE_DIR = DEFAULT_CONFIG_PATH.parents[1] / "data" / "cache"
WINDOWS = (("09:30", "10:00"), ("15:30", "15:55"))


def main() -> int:
    try:
        config = load_config()
        credentials = load_credentials()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    data = MarketData(AlpacaFetcher(credentials, adjustment=config.data.adjustment), config, cache_dir=Path(CACHE_DIR))
    symbol = config.trading.symbols[0]
    today = dt.datetime.now(ET).date()
    try:
        recent = data.sessions(max(today - dt.timedelta(days=10), config.splits.holdout.end + dt.timedelta(days=1)), today)
        past = [s for s in recent if s.date < today]
        if not past:
            print("no completed session after the holdout yet", file=sys.stderr)
            return 1
        session = past[-1]
        print(f"{symbol} on {session.date}: session {_hm(session.open)}-{_hm(session.close)} ET"
              + ("" if session.is_full_day else " (half day)"))
        print(f"  previous close (SIP daily bar): {data.previous_close(symbol, session):.2f}")
    except DataUnavailable as exc:
        print(f"DATA UNAVAILABLE: {exc}", file=sys.stderr)
        return 1
    for start, end in WINDOWS:
        if _at(session, end) > session.close:
            print(f"  {start}-{end}: after the close on this half day (strategy C skips half days)")
            continue
        closes = {}
        for feed in ("sip", "iex"):
            try:
                bars = data.minute_bars(symbol, session, _at(session, start), _at(session, end), feed=feed)
                closes[feed] = bars[-1].close
                print(f"  {start}-{end} {feed}: {len(bars)} bars, no gaps, last close {bars[-1].close:.2f}")
            except DataUnavailable as exc:
                print(f"  {start}-{end} {feed}: REFUSED: {exc}")
        if len(closes) == 2:
            print(f"  {start}-{end} last close IEX minus SIP: {closes['iex'] - closes['sip']:+.3f}")
    return 0


def _at(session: Session, hhmm: str) -> dt.datetime:
    hour, minute = map(int, hhmm.split(":"))
    return dt.datetime.combine(session.date, dt.time(hour, minute), ET)


def _hm(moment: dt.datetime) -> str:
    return moment.astimezone(ET).strftime("%H:%M")


if __name__ == "__main__":
    raise SystemExit(main())
