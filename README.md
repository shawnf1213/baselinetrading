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
| 2 | Market data with staleness and gap detection | done |
| 3 | Strategies (separate from execution): C, last-half-hour momentum; A, opening range breakout; A2, adaptive breakout over the 10 most traded stocks, bought as calls and puts (the default: `trading.strategy`, `trading.instrument = "options"`) | done; rules in [docs/decisions.md](docs/decisions.md) |
| 4 | Backtester: costs, walk-forward, baselines, trial ledger, holdout lock | done (needs Alpaca data to run) |
| 5 | Risk manager with order veto (manual and strategy) | done |
| 6 | Execution: order gateway, engine, FastAPI backend, React UI | done |
| 7 | Journal of signals, orders and fills, with inputs and source | done |
| 8 | Daily P&L per source against the breakeven bar (in the UI) | done |

## Setup

Python 3.11+ and Node 20+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env               # fill in PAPER keys and a UI token; .env is git-ignored
set -a; source .env; set +a
python -m pytest                   # all tests, no network needed
```

Command-line tools:

```bash
python -m baselinetrading.edge                           # cost / breakeven / sample-size arithmetic
python -m baselinetrading.data_check                     # fetch one recent session on both feeds
python -m baselinetrading.signal_check --date 2021-06-01 # a strategy decision and its inputs, no orders
python -m baselinetrading.backtest --split in_sample     # the backtest report
python -m baselinetrading.universe                       # the most traded stocks the option rules can buy
```

## The trading client (web UI)

On this PC (Windows), `scripts/start-bot.ps1` loads the keys from your user
environment, serves the UI and API on http://127.0.0.1:8000, and restarts the
server if it stops. Start it in its own window; close the window to stop it:

```powershell
Start-Process powershell -ArgumentList '-NoExit', '-File', 'scripts\start-bot.ps1' -WindowStyle Minimized
```

From your phone or laptop, privately: Tailscale Serve forwards
`https://<pc-name>.<tailnet>.ts.net` to the server on this PC. Only devices
signed in to your Tailscale account can open it; the server still listens only
on 127.0.0.1, and no firewall rule or router port is opened. Log in with the
same `BASELINE_UI_TOKEN`. Set up once (it survives restarts):

```powershell
tailscale serve --bg 8000          # turn it off: tailscale serve --https=443 off
```

Local, with live reload while developing:

```bash
uvicorn baselinetrading.server:app --host 127.0.0.1 --port 8000   # backend + strategy engine
npm --prefix frontend install && npm --prefix frontend run dev     # UI on http://127.0.0.1:5173
```

Local, as it will be deployed (one process serves UI and API):

```bash
npm --prefix frontend run build
uvicorn baselinetrading.server:app --host 127.0.0.1 --port 8000   # open http://127.0.0.1:8000
```

Docker:

```bash
docker build -t baselinetrading .
docker run --env-file .env -p 127.0.0.1:8000:8000 \
  -v "$PWD/logs:/app/logs" -v "$PWD/state:/app/state" baselinetrading
```

Before exposing it beyond localhost, put it behind HTTPS: the token travels in
every request. Use an always-on host; one that sleeps when idle would miss the
breakouts from 09:35 and the 15:55 exit. The strategy engine runs inside the backend
process, so the UI shows "engine is not running" if it stops.

The web UI (styled after baselineev.com; it works on a phone too):
- Top bar: paper account, armed / idle / disarmed, market open, data health, the ET clock, and Lock.
- Today at a glance: the account's P&L, open and closed P&L, entries used, and the daily loss limit.
- Chart (TradingView Lightweight Charts, 1-minute) of the stock picked in the watchlist or the tabs above
  it, with the opening range (where calls and puts trigger) and any stop. It's for display only;
  decisions use validated data.
- Watchlist of every traded stock: price, opening range, state, entries used, and what happens next.
- Open positions, options shown as stock, call or put, strike and expiry, each closable.
- Kill switch: type FLATTEN to confirm.
- Manual order ticket (a market buy with a mandatory stop); with options only, a note says why it's off.
- Closed trades today per source (manual vs strategy), the strategy decisions with their inputs, and the
  journal.

Every disabled control lists why, using the risk manager's own wording. To work on the UI without the
bot, `python scripts/ui_demo.py` serves canned data (a stand-in backend on :8010, any token) and
`npm --prefix frontend run dev:demo` runs the live-reloading UI against it.

**One path to the broker.** Browser -> FastAPI -> OrderGateway ->
RiskManager.execute -> Alpaca. The browser never sees the Alpaca keys and
never talks to Alpaca. Every method that changes anything at the broker
raises `RiskBypass` unless `RiskManager.execute` is on the call stack. Tests
call every POST route and fail if any broker change happens outside it.

**Paper only.** The trading client is created with `paper=True`, and startup
refuses an account whose number doesn't start with `PA`. There's no live
setting to flip.

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
- The account is split equally over `trading.symbols`: at most one position per
  symbol, each within its share.
- Coming in later modules: a stop on every order,
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
  bars.py                   Bar/Session types; gap, sanity and staleness checks
  market_data.py            fetch with retries, cache past sessions, holdout lock
  alpaca_client.py          thin alpaca-py adapter (paper trading client only)
  data_check.py             end-to-end data check with your keys
  strategy.py               strategy C: frozen spec, pure 15:30 decision, stop price
  signal_check.py           prints one session's decision and its inputs
  broker.py                 broker snapshots; AlpacaBroker (paper); the risk-manager call-stack guard
  risk.py                   RiskManager: every rule, one veto, the only door to the broker
  gateway.py                OrderGateway: entry + stop, exits, kill switch, reconciliation
  journal.py                JSONL journal (source-tagged), replay on restart, P&L per source
  engine.py                 background strategy engine and the UI status snapshot
  server.py                 FastAPI: auth, one order route, kill switch, WebSocket, serves the UI
  backtest.py               walk-forward backtest with costs, baselines and a trial ledger
  options.py                which call or put to buy, and how many contracts
  universe.py               ranks stocks with options by dollar volume (for trading.symbols)
frontend/                   React + Vite + TypeScript UI (dark theme)
scripts/start-bot.ps1       runs the server on this PC, restarting it if it stops
tests/                      one test file per module
```
