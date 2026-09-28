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

## Tuned-parameter trial count: 0
