# baselinetrading

A learning project: an intraday trading bot whose first job is to find out,
honestly, whether a simple strategy has an edge after costs. It trades
**Alpaca paper only**. An edge means beating three baselines net of costs:
the no-change forecast, buy-and-hold, and random entries (see
[docs/methodology.md](docs/methodology.md)).

## Status

| # | Module | State |
|---|---|---|
| 0 | Edge definition: cost model, breakeven win rate, sample sizes | done |
| 1 | Project structure, fail-closed config, credentials | done |
| 2 | Market data with staleness and gap detection | next |
| 3 | Strategy (separate from execution) | waiting on your choice, see [docs/strategies.md](docs/strategies.md) |
| 4 | Backtester: costs, walk-forward, baselines, trial ledger | |
| 5 | Risk manager with order veto | |
| 6 | Execution, Alpaca paper only | |
| 7 | Logging of signals, orders and fills, with their inputs | |
| 8 | Daily P&L summary against the breakeven bar | |

## Setup

Python 3.11 or newer.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest                     # tests
python -m baselinetrading.edge       # the cost / breakeven / sample-size arithmetic
```

Credentials come **only** from environment variables, and only paper keys
are accepted (Alpaca paper key IDs start with `PK`):

```bash
cp .env.example .env                 # .env is git-ignored; fill in your paper keys
set -a; source .env; set +a
```

Nothing in module 1 needs the keys yet.

## Safety: rules in code, not settings

These can't be switched off in `config/settings.toml`:

- Max risk per trade ≤ 2% and max daily loss ≤ 5% are hard ceilings. The
  config may set them lower, never higher.
- Any missing, malformed, out-of-range or unknown setting stops the program
  before it can trade, and all problems are listed at once. `nan`/`inf`,
  quoted booleans and typo'd keys are all caught.
- The only settings that can be left out are safety flags, and leaving one
  out picks the restrictive side: missing `trading.enabled` means **off**.
- Credentials in the settings file are rejected. Secrets print as
  `********`, can't be pickled, and a logging filter scrubs them from log
  lines and tracebacks.
- Live key IDs (`AK…`) are refused.
- Coming in later modules: one position at a time, a stop on every order,
  a kill switch, and no trading on stale or gapped data.

## Constraints that shape the design

- **Paper only, sized at $100,000.** A round trip costs about 2.4 bp there,
  mostly assumed slippage. At $20 it would be about 22 bp, 90% of it
  per-order fee rounding, so results here don't carry over to a tiny
  account. Run `python -m baselinetrading.edge` for the full table.
- **Cash-account settlement (T+1)** allows one full-size round trip per
  trading day. The FINRA pattern-day-trader rule was replaced on 2026-06-04,
  but settled-cash rules for cash accounts still apply.
- **Alpaca doesn't support bracket orders for fractional shares.** The stop
  is placed after the entry fills, and the execution module flattens the
  position if the stop isn't confirmed.
- **The free data feed is IEX-only in real time**, about 2.5% of volume.
  Consolidated (SIP) history is free if it's more than 15 minutes old, so
  backtests use SIP, while live decisions use IEX. We'll measure that gap.

## Layout

```
config/settings.toml        every setting, validated; git history = record of changes
docs/methodology.md         what counts as an edge; anti-self-deception rules
docs/strategies.md          three candidate strategies and a recommendation
docs/decisions.md           pre-commitment log (splits, costs, strategy choice)
src/baselinetrading/
  config.py                 fail-closed settings loader
  credentials.py            env-only paper credentials, masking, log redaction
  costs.py                  itemised round-trip cost, breakeven win rate
  stats.py                  Wilson intervals, verdicts, trades needed
  edge.py                   prints the arithmetic for your settings
tests/                      one test file per module
```
