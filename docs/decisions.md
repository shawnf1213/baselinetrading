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

## PENDING: Strategy choice

Fill this in before any strategy backtest:
- Strategy (A, B or C from `docs/strategies.md`):
- Mechanism in your own words (who loses to you, and why they keep doing it):
- Exact rules:
- Every parameter, its pre-committed value, and why that value:
- What result would make you abandon it:

## Tuned-parameter trial count: 0
