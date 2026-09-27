# Methodology: what counts as an edge, and how we keep from fooling ourselves

These are the rules. The backtester (module 4) and the daily summary
(module 8) implement them. If the code and this file disagree, the code has
a bug.

## 1. Costs come first

Every backtest report starts with the **breakeven win rate**, and every win
rate is compared to it, not to 50%.

```
net win  = target - cost of a winning round trip
net loss = stop   + cost of a losing round trip   (stop exits slip more)

breakeven win rate p* = net loss / (net win + net loss)
expected net P&L per trade = (net win + net loss) x (win rate - p*)
```

With no costs and target = stop, p* = 50%. If costs eat the whole target,
no win rate is profitable, and the report says "never".

Costs are itemised in `src/baselinetrading/costs.py` and set in
`config/settings.toml`:

| Cost | Charged on | Model |
|---|---|---|
| Spread | both orders | half the quoted spread each side (SPY: $0.01/share) |
| Slippage | both orders | bp of notional per order; extra on stop exits |
| SEC fee | sells | $20.60 per $1M (FY2026 rate from 2026-04-04) |
| FINRA TAF | sells | $0.000195/share (2026), capped at $9.79 |
| CAT fee | both orders | $0.000003/share (Alpaca's pass-through rate) |
| Rounding | each fee, each order | **up** to the next cent |

The rounding is what matters at $20. A $20 sale owes about $0.0004 in SEC fee
and $0.000006 in TAF, and each is charged as $0.01. Whether CAT is rounded
per order or once per day isn't documented clearly; we assume per order,
the conservative choice. If it's per day, a $20 round trip costs about 17 bp
instead of 22 bp. The conclusion doesn't change.

Run `python -m baselinetrading.edge` to print the full arithmetic for your
settings.

**Paper trading doesn't charge these fees**, and its fills are simulated, so
paper P&L flatters you. The daily summary subtracts the modelled fees, and
measures slippage as the decision price vs the fill price.

## 2. What "edge" means here

A strategy has an edge only if, **net of costs, on data it wasn't developed
on**, it beats all three baselines. Beating one or two is not enough.

### B1. The no-change forecast: does the signal know anything?

The market's own forecast of the next bar is the current price. If a
signal can't beat that, it adds noise to the market's estimate.

- For every signal, compute the forward return in the signal's direction,
  from the fill (the next bar's open) to 1, 5, 15 and 30 minutes later, and
  to the strategy's actual exit.
- Under the no-change forecast the expected value of that return is zero.
  The signal passes only if the mean forward return over its holding period
  is above zero with a 95% confidence interval that excludes zero, and it
  is still above zero after costs.
- The hit rate is compared with the base rate of up-moves at the same time
  of day, not with 50%.

This checks the signal alone, apart from stops, targets and sizing.

### B2. Buy-and-hold: would doing nothing clever have been better?

Same capital, same period, same cost model (one buy at the start, one sell
at the end). Two versions:

- **(a) Close-to-close buy-and-hold** is what your money would otherwise do.
  The strategy must beat it on **net total return**. Sharpe ratio and max
  drawdown are reported alongside for context.
- **(b) Open-to-close ("intraday") buy-and-hold** holds the ETF only during
  market hours. This is a diagnostic: beating (b) but not (a) means the
  strategy beats intraday market exposure and still loses to doing nothing.

A headwind you should know about: a well-documented pattern is that much of
the US equity premium since the 1990s was earned **overnight**, not during
the trading day (e.g. Cooper, Cliff & Gulen 2008; Lou, Polk & Skouras 2019).
A strategy that's flat by the close gives that up by design. This makes (a)
a high bar, and it's the right bar, because holding SPY is the alternative.

### B3. Random entries: is it the entries, or just the exits, sizing and time of day?

Monte Carlo, at least 1,000 runs. Each run uses the same trading days and the
same number of trades. Each random trade enters at a uniformly random minute
of the strategy's allowed entry window and has the same size and the same
holding time as the real trade it replaces. The cost model is identical.
The p-value is the share of random runs whose net P&L is at least the
strategy's. The strategy passes only if p is below the significance level
from section 5, which is adjusted for the number of variants tried.

## 3. Statistics: every number comes with its uncertainty

- **Win rates** are reported with n, a Wilson 95% interval, the breakeven,
  and a verdict (`src/baselinetrading/stats.py`): ABOVE or BELOW only when
  the whole interval is on one side of breakeven. **Otherwise the verdict is
  INCONCLUSIVE**, and it takes evidence to change that.
- **Mean net P&L per trade** is reported with a bootstrap 95% interval. Win
  sizes vary from trade to trade, so the win rate alone isn't enough. Both
  checks have to pass.
- **Sample size.** Telling a win rate 5 points above breakeven apart from
  luck (one-sided 5% test, 80% power) takes about 470 to 620 trades. At
  settled-cash limits that's roughly one trade per trading day, so 2 to 2.5
  years of trades. `python -m baselinetrading.edge` prints this for your
  settings.

## 4. Data splits and the holdout lock

The splits were set in `config/settings.toml` before any data was
downloaded (git history is the timestamp):

| Split | Dates | Used for |
|---|---|---|
| In-sample | 2016-01-04 to 2020-12-31 | building and debugging the frozen spec |
| Validation | 2021-01-01 to 2022-12-31 | one check of the frozen spec |
| Holdout | 2023-01-01 to 2026-08-31 | **one** final run, after the strategy is frozen |

The backtester will refuse to load holdout data unless it's given the hash
of the frozen strategy spec, and every holdout read is appended to a ledger
in git. A second look at the holdout makes it validation data, and then
there's no holdout left.

You've already lived through 2023 to 2026 and know how SPY did. Don't let
that memory pick the strategy.

## 5. No lookahead, anywhere

1. A decision at the close of bar *t* uses only bars up to and including *t*.
   The earliest fill is the open of bar *t+1*, plus slippage.
2. A bar exists only after its end time has passed and the feed has
   delivered it. Nothing inside bar *t* may use bar *t*'s high, low or close.
3. Daily reference values (previous close, opening range) come only from
   periods that have finished.
4. If a bar touches both the stop and the target, assume the stop came
   first. Bars don't record which happened first, so take the pessimistic
   order.
5. Any parameter that is ever fitted uses only data before the decision
   time (expanding or rolling window).
6. Forward returns (labels) are for evaluation only. They are never inputs.
7. The trading calendar (holidays, half days) comes from the exchange
   calendar, never from gaps in the data.

## 6. Count every parameter

Every distinct combination of parameter values that gets backtested counts
as one trial, including ones you only glanced at. The backtester appends
each run to a trial ledger (committed to git). Reports print the trial count
*K* and use the Šidák-adjusted significance level α = 1 − 0.95^(1/K).

Why it matters: after 20 variants, the chance that at least one looks
significant at 5% by luck alone is 1 − 0.95^20 = **64%**. With 3 parameters
at 5 values each, that's 125 variants, and a good-looking backtest is almost
guaranteed.

## 7. When a strategy is dead

- If the validation mean net P&L per trade is ≤ 0, or validation is
  significantly worse than in-sample, the strategy is **dead**. We write down
  why and stop. No tweaking parameters on validation and re-running: that
  turns validation into in-sample data.
- The holdout runs once, on the frozen strategy. Its result is the result.
- "It would have worked if we'd skipped 2022" is retuning.

## 8. What paper trading can and can't show (preview of the promotion gate)

At about one trade per trading day, 100 paper trades take about 5 months.
That sample can only detect an edge of roughly 12 win-rate points, far larger
than anything realistic. **Paper trading can't prove an edge in any
reasonable time.** Its job is to show that the live system does what the
backtest assumed: fills and slippage within the modelled costs, no missed
stops, correct behaviour on stale data and disconnects, and results inside
the backtest's predicted range. The evidence for the edge itself comes from
the holdout. The exact number of paper trades and the pass criterion will
be committed in `docs/decisions.md` before paper trading starts.
