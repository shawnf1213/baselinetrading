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

## Tuned-parameter trial count: 0
