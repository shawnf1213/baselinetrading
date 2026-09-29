# Decision log (append-only)

Every choice that could be tuned to make results look better is recorded
here, with a date, **before** the data that would judge it is looked at. Git
history is the timestamp. Entries are never edited after the fact; a changed
decision gets a new entry that says what changed and why.

## 2026-09-27: Data splits (set before any data was downloaded)

- In-sample: 2016-01-04 to 2020-12-31
- Validation: 2021-01-01 to 2022-12-31
- Holdout: 2023-01-01 to 2026-08-31. Locked until the strategy is frozen.

Why: the splits are chronological, and the holdout is the most recent period
because that's the one most like live trading. Each split has a stress
period: 2018 volatility and the 2020 crash in-sample, the 2022 bear market
in validation.

Known contamination: you lived through 2023 to 2026 and know roughly how SPY
behaved.

## 2026-09-27: Cost assumptions

Values are in `config/settings.toml` `[costs]`, with reasons in
`docs/methodology.md` section 1. Unverified: whether CAT fees are rounded per
order or per day (we assume per order, the conservative choice), and how
fractional-share fills compare to the NBBO. Paper trading can't measure the
second, because paper fills are simulated.

## 2026-09-27: Simulated account size

`account.equity_cap_usd = 20.0`, the real account. At this size, fee rounding
alone costs about 20 bp per round trip (`python -m baselinetrading.edge`).
**Open question for you:** keep $20, which honestly tests the account you
have (the fees almost guarantee "no edge"), or set the amount you'd actually
be willing to fund if the test passes. Reports will show both.

## 2026-09-27: Simulated account size changed to $100,000

You'll test on Alpaca paper only, with the paper account's $100,000.
`account.equity_cap_usd = 100000.0`. At this size a round trip costs about
2.4 bp, of which fees are about 9%; slippage is now the main cost, and it is
an assumption that paper fills can't verify. No leverage: position size stays
at most the equity. Results at this size say nothing about a $20 account.

## 2026-09-28: Strategy C, intraday momentum (last half hour), frozen before any backtest

Chosen by you from `docs/strategies.md`.

**Mechanism** (from the literature; still to be restated in your own words):
leveraged ETFs must rebalance near the close in the direction of the day's
move, and options dealers who are short gamma hedge the same way (Baltussen,
Da, Lammers & Martens 2021). Those flows are forced, so the direction of the
day's move so far predicts the direction of the last half hour (Gao, Han, Li
& Zhou 2018). The counterparty is whoever takes the other side of those
forced trades.

**Rules** (all times ET; decisions use only bars that have closed):
1. Trade only on full sessions (09:30-16:00). Skip half days.
2. Signal: r = (close of the 09:59 bar) / (previous session's official close) - 1.
   Previous close comes from the SIP daily bar, split- and dividend-adjusted.
3. Long if r > 0; otherwise no trade that day. Long-only.
4. Entry: at 15:30, market buy for the full account (no leverage). Backtest fill
   = open of the 15:30 bar, plus half-spread and slippage.
5. Exit: market sell at 15:55 (your "flat before the close" rule). Backtest
   fill = open of the 15:55 bar, minus costs. Not chosen: the closing auction
   (market-on-close). It may capture more of the effect but breaks your rule.
6. Protective stop: 1.0% below the entry fill, placed as soon as the entry
   fills. Backtest: if a bar's low reaches the stop, fill at the lower of the
   stop and that bar's open, minus stop slippage.
7. Data: 09:30-10:00 and 15:00-15:30 must be gap-free (max_missing_minutes = 0),
   and live bars must be fresh. Otherwise no entry that day. Gaps and stale
   data block entries, **never exits**: an open position is always flattened.

**Parameters:** signal time 10:00, threshold 0, entry 15:30, exit 15:55 and
stop 1.0%. All fixed by the paper or by your rules, not tuned. The stop is our
only free choice: about 4 times a typical last-half-hour move, so it should
fire only on unusual days.

**Abandon if:** mean net P&L per trade on validation is <= 0, or it fails any
of the three baselines on validation. No retuning afterwards.

## 2026-09-28: Strategy A, opening range breakout, frozen before any backtest

Chosen by you after strategy C, because you want the bot watching the whole
day rather than making one decision at 15:30. Spec fingerprint
`261a98ff001fa7ff`; selected with `trading.strategy = "opening_range_breakout"`.

**Mechanism** (from `docs/strategies.md`, still to be restated in your own
words): overnight news and order imbalances get priced at the open. A close
out of the opening range suggests the imbalance hasn't been absorbed, and
large orders worked through the day by execution algorithms keep pushing the
same way. The counterparty is whoever fades the move.

**Rules** (all times ET; decisions use only bars that have closed):
1. Trade only on full sessions. Skip half days.
2. Opening range: high and low of the 09:30-09:34 bars.
3. Breakout: the first 1-minute bar from 09:35 whose close is above the range
   high. Long-only. At most one entry per day.
4. Entry: market buy for the full account at the next bar's open. The last
   usable breakout bar is 15:43, so the entry comes before 15:45 (the risk
   manager's no-new-entries window). Live, the engine enters only on the
   minute the breakout bar closes; if it saw the breakout late, no trade.
5. Stop: the range low, placed as soon as the entry fills. Backtest fill rule
   as for strategy C.
6. Exit: market sell at 15:55. No profit target.
7. Data: every bar from 09:30 must be gap-free and fresh. Otherwise no entry.

**Parameters:** range 5 minutes, entry on a close, stop at the range low, exit
15:55, no target, as pre-committed in `docs/strategies.md`. Not tuned.

**Baseline B3 for this strategy:** the same trade entered at the 09:35 open
every day (same stop and exit), whether or not a breakout came.

**Abandon if:** mean net P&L per trade on validation is <= 0, or it fails any
of the three baselines on validation. No retuning afterwards.

## 2026-09-28: Strategy A2, adaptive opening range breakout, frozen before any backtest

Strategy A failed in-sample (win rate 29.6% against a 32.7% breakeven; mean
net -2.27 bp per trade). You asked for the strategy to change slightly after
every 3 losses. Hand-tweaking after each streak would chase noise, so the
adapting rule itself is frozen here and tested as one new strategy. Spec
fingerprint `da212df8e0cbc847`; selected with
`trading.strategy = "adaptive_opening_range_breakout"`.

**Rules:** strategy A's rules, except the opening range length. It starts at 5
minutes. After 3 losing trades in a row (net of costs), it moves to the next
length in 5 -> 15 -> 30 minutes, then back to 5. A win resets the streak and
keeps the current length. Live, every closed strategy trade is saved to
`state/adaptive_orb_outcomes.json`, so a restart keeps the streak. The
backtest replays days in order with the same rule.

**Parameters:** ladder (5, 15, 30) and 3 losses, as proposed in the thread and
accepted. Not tuned. This is the third distinct spec (trial count 3).

**Abandon if:** as for A and C.

## 2026-09-28: Split account over 8 stocks; breakout v2 (gaps after the range)

You want the bot to trade like a day trader across several stocks, with the
account split between them, and every paper trade feeding back into the data.

**Account:** `trading.symbols` = SPY, AAPL, NVDA, AMD, MSFT, TSLA, META, AMZN.
Sizing equity is split equally (8 x $12,500 at $100,000). At most one position
per symbol, each no bigger than its share; several symbols can be held at once.
`risk.max_entries_per_day = 20` across all symbols. Risk per trade (2%) and the
daily loss limit (5%) are unchanged and apply to the whole account.

**Strategy:** A2 (adaptive breakout) runs on every symbol independently: each
has its own opening range, breakout, stop, one entry per day and its own
loss streak (range 5 -> 15 -> 30 after 3 losses in a row on that symbol).

**Rule change, v2:** the free IEX feed often has minutes with no trade for a
single stock (AMD missed 3-42 minutes a day in mid-September). v1 cancelled
the day on any missing minute. v2 still requires a complete opening range,
but a later missing minute simply can't be a breakout, and live data health
accepts gaps as long as the newest bar is fresh. The backtest does the same
(next available bar for fills). Because this changes the rules, v2 specs have
new fingerprints: A v2 `6508347fbb9962bf`, A2 v2 `0fdc4b1982b381f4`.

**Backtest:** each symbol with its $12,500 share, reported per symbol plus a
summary (`results/*-summary.txt`).

## 2026-09-28: Breakout v3, re-entries on fresh crosses

You want the bot to day-trade like a person, not take one shot per stock and
give up. v2 took the first close above the range high and was then done for
the day; a restart after that close, or a stop-out, meant no more trades.

**v3 entry rule:** buy whenever a bar closes above the range high after the
previous bar closed at or below it (a fresh cross), while flat in that stock,
up to 3 entries per stock per day. Stop at the range low, exit at 15:55 as
before. The first entry of a day is the same as v2's. A stop-out re-arms the
stock for the next cross; an engine that starts late waits for the next cross
instead of skipping the day. Decisions recorded today under older rules no
longer block a stock after a restart.

Fingerprints: A v3 `1903aa2a5f90d8bc`, A2 v3 `4fb0f5c98c9ebd4a` (live).
Switched live at 11:25 ET on 2026-09-28, before its backtest finished, at
your request ("fix it"): paper money only.

## 2026-09-28: Breakouts traded with call options

You want to day trade options, not shares, built and live at once. Selected
with `trading.instrument = "options"`; spec `breakout-calls-weekly`
(`options.OptionSpec`).

**Rules:** the signal is unchanged (breakout v3 on each stock). On a BUY the
bot buys calls instead of shares: the earliest expiry 5-12 calendar days out,
and in it the lowest strike at or above the stock price. Whole contracts, as
many as fit in min(2% of equity, the stock's share), so the most a trade can
lose is its premium. Skip if the bid-ask spread is wider than 15% of the ask.
Alpaca takes no stop orders on options: the engine sells the calls when the
stock's latest 1-minute close is at or below the breakout stop (the range low,
checked about every 15 seconds), and everything is sold at 15:55 as before.
If the backend stops, nothing protects an open call.

**Not backtested.** Alpaca's option history starts in February 2024, inside
the stock strategies' locked holdout, and the free plan has no historical
option quotes for spreads. Live paper trading from 2026-09-28 is the test;
every fill is journaled with the quote it was taken at.

## 2026-09-28: Puts on crosses below the range

You want puts used too. With `instrument = "options"`, a fresh cross below the
opening range low (a close below it after a close at or above it) buys puts:
earliest expiry 5-12 days out, highest strike at or below the price, same
premium limits. The stop is the range high: the engine sells the puts when
the stock's latest close is at or above it. Calls and puts share the 3
entries per stock per day and the one-position-per-stock rule. With
`instrument = "shares"` down-crosses are ignored (long-only). Live-tested
only, like the calls.

## 2026-09-28: When to abandon the options strategy

Written before anyone in this thread looked at the option trades' results
(you may have seen today's P&L in the app). Every stock strategy has an abandon
rule; the options strategy had none, and live paper trading is its only test.

**What counts:** option trades the strategy opens from 2026-09-29 on, calls
and puts together, under signal A2 v3 (`4fb0f5c98c9ebd4a`) and option rules
`breakout-calls-weekly` (`a706cb84095d9236`), on the stock list in force at the
2026-09-29 open. Today's trades don't count: the rules changed during the day
(shares, then calls, then puts).

**Net P&L per trade:** (exit fill - entry fill) x contracts x 100, minus the
bid-ask spread, counted once. If paper buys fill at the ask, the fills already
paid the spread and nothing more comes off; if they fill nearer the mid, the
spread quoted at entry is subtracted, as the app does now. Which case applies
is read from the journal's fills against the recorded quotes, not from the
P&L. Regulatory fees (cents per contract) are ignored.

**Checkpoint:** after the close on 2026-10-26 (the 20th full session from
2026-09-29) or when the 100th trade closes, whichever is later. Checked once.

**Abandon if:**
- at the checkpoint, the mean net P&L per trade is <= 0; or
- at any point before it, these trades have lost $20,000 in total (20% of
  the $100,000 sizing equity). This can end the test early, never pass it.

**If it passes,** it keeps paper trading, nothing more. There is no option
history for the three baselines, so this is a weaker test than the stock
strategies faced. Real money would need its own entry and a stricter bar (the
95% interval of the mean above zero).

**No retuning during the test.** Changing the signal, the option rules, the
stock list or the risk settings starts a new test with a new entry. Dropping
calls or puts after seeing their results counts as a change. Bug fixes that
make the bot follow these rules as written don't restart it; each gets an
entry.

## 2026-09-28: Backtest results so far (in-sample only)

The log recorded strategy A's failure and nothing else. These are all the
results in `results/`. The trial ledger `results/trials.jsonl` has 27 runs of 4
distinct specs.

| Spec | Stocks | Trades | Mean net per trade (95% CI) | Baselines |
|---|---|---|---|---|
| A v1 `261a98ff001fa7ff` | SPY | 1,052 | -2.27 bp (-5.00 to +0.65) | B1 fail, B2 not computed, B3 pass |
| A2 v1 `da212df8e0cbc847` | SPY | 963 | -2.45 bp (-5.46 to +0.77) | all three fail |
| A2 v2 `0fdc4b1982b381f4` | 8 x $12,500 | 6,290 | total +$41,856 | no stock passes all three |
| A2 v3 `4fb0f5c98c9ebd4a` (live) | 8 x $12,500 | 6,904 | total +$43,988 | no stock passes all three |

A2 v3 per stock:

| Stock | Trades | Mean net per trade (95% CI) | B1 | B2 | B3 |
|---|---|---|---|---|---|
| SPY | 1,199 | -3.06 bp (-5.96 to -0.18) | fail | fail | fail |
| AAPL | 837 | +0.64 bp (-5.26 to +6.33) | fail | fail | pass |
| NVDA | 819 | +8.41 bp (-1.32 to +17.83) | fail | fail | fail |
| AMD | 750 | +11.61 bp (-3.74 to +27.02) | fail | fail | pass |
| MSFT | 873 | -3.11 bp (-8.74 to +2.20) | fail | fail | fail |
| TSLA | 749 | +26.29 bp (+11.66 to +40.88) | pass | fail | pass |
| META | 876 | -1.42 bp (-8.41 to +5.48) | fail | fail | fail |
| AMZN | 801 | +9.62 bp (+3.10 to +16.43) | pass | fail | pass |

What this says:
- Nothing has been run on validation, so no abandon rule has been applied
  yet. In-sample, every spec on every stock fails at least one baseline: no
  edge by this project's definition.
- The positive totals come from TSLA, AMD, NVDA and AMZN, which rose many
  times over in 2016-2020. A long-only breakout on them collects part of that
  drift, and buy-and-hold (B2) collects all of it: TSLA's buy-and-hold made
  +1,429% against the strategy's +193% of its share. These stocks were also
  picked in 2026, knowing how they turned out.
- Only TSLA and AMZN have a 95% interval above zero, and both fail B2.
- These are long-only share backtests. They say nothing about puts, option
  spreads or time decay.
- A2 v2 ran twice on 2026-09-28 (10:11 and 10:58). The first run priced
  per-share costs on split-adjusted prices, which made split stocks look up to
  40x too expensive (NVDA -24.4 bp, then +7.2 bp). The table uses the second
  run; the ledger keeps both.
- Frozen but never run: C, A v2 `6508347fbb9962bf`, A v3 `1903aa2a5f90d8bc`.

**Distinct specs backtested: 4** (the ledger's count, which sets each
report's significance level), plus the options strategy, tested live only.
The tuned-parameter count at the end of this log stays 0: no parameter has
been tuned.

## 2026-09-28: Ten most traded stocks, options only

You want the 10 highest-volume stocks and option trades only, no share
purchases.

**Stock list rule:** US company stocks with listed options, ranked by
average daily dollar volume (shares traded x the day's VWAP) over the 20
sessions from 2026-08-31 to 2026-09-28. Dollar volume, not share count: share
count ranks cheap stocks first, and their options have the widest spreads for
their price. Left out:
- funds: SPY ($33.7B a day) and QQQ ($24.3B) would have ranked 1st and 3rd;
- stocks the option rules can't buy: one contract of the call or put they
  would pick for 2026-09-29 must cost at most the $2,000 premium cap. MU (2nd,
  one call $4,546) and SNDK (3rd, $8,380) fail.

`python -m baselinetrading.universe` reproduces the ranking.

**List:** NVDA, META, TSLA, AAPL, SPCX, AMD, INTC, MSFT, AVGO, GOOGL. Out:
SPY (a fund) and AMZN (13th). In: SPCX, INTC, AVGO, GOOGL. The list stays
fixed until a new entry; the ranking is not re-run automatically. This is the
list in force at the 2026-09-29 open, the options test's starting list.

**Sizing:** the account splits 10 ways ($10,000 each). The premium cap stays
min(2% of equity, the stock's share) = $2,000. `risk.max_entries_per_day`
stays 20: ten stocks at 3 entries each could reach 30, so the account limit
binds on busy days.

**Options only:** with `instrument = "options"`, the risk manager now refuses
every share purchase, from the strategy and from the manual ticket, which
shows that reason. Exits are unaffected.

**Bug fix, loss streaks:** option trades were saved to
`state/adaptive_orb_outcomes.json` under the contract symbol (for example
`SPY261005C00769000`), so they never counted toward their stock's 3-loss
streak, and under options the range never adapted. They now count for the
stock, including the ones saved today. No stock on the new list has 3 losses
in a row, so every one starts 2026-09-29 with the 5-minute range.

## Tuned-parameter trial count: 0
