# Candidate strategies (v1): pick one on its mechanism, before any backtest

Rules of the choice (from `docs/methodology.md`):

- Pick on **why it should work**: who is on the other side, and why they keep
  losing to you. Don't pick on how it backtests; nothing has been backtested.
- Fewer knobs are better. Every parameter value tried counts as a trial.
- All three are **long-only**. A cash account can't short, so half of each
  strategy's signals are unavailable.
- Instrument: **SPY**, as fractional shares. It has the tightest spread and
  deepest liquidity of any ETF, and both Alpaca and Robinhood trade it
  fractionally.
- Cost numbers below come from `python -m baselinetrading.edge` at $20 and at
  $2,000, with the default cost model (22 bp per $20 round trip, 2.6 bp at
  $2,000).

---

## A. Opening range breakout (ORB)

**Rules (v1).** The opening range is the high and low of 09:30 to 09:35 ET.
After that, if a 1-minute bar closes above the range high, buy at the next
bar's open. The stop is the range low. Exit at the stop, at a target of
k × risk, or at the flatten time (15:55). One entry per day.

**Mechanism.** The open is when overnight news and order imbalances get
priced. A move out of the opening range suggests the imbalance hasn't been
absorbed yet. Large orders are worked through the day by execution
algorithms, which could keep pushing the price the same way. You profit from
continuation. Your counterparties are the traders fading the move.

**Why it might not work.**
- It's one of the most widely known retail strategies (Toby Crabel's 1990
  book, plus countless courses), so it's crowded. Recent SSRN working papers
  report profitable ORB variants. Treat those as hypotheses, not evidence:
  they're the authors' own backtests.
- Breakout systems usually win less than half their trades and depend on a
  few big trend days. High variance means you need more trades to tell
  anything apart from luck.
- False breakouts on choppy days. On wide-range (gap or news) days the stop
  sits far away.
- The first minutes have the widest spreads and the noisiest prices. The
  free real-time IEX feed sees about 2.5% of volume, so its opening range
  can differ by a few cents from the consolidated range used in the backtest.

**Knobs:** range length (5/15/30 min), entry confirmation (close vs touch),
stop placement (range low vs midpoint), target (k × risk or hold to the
flatten time), filters (gap, volume). That's easily 4-5 knobs, and dozens of
combinations if you let yourself try them.

**At your size:** the stop is the range width, often a few tenths of a
percent on SPY (we'll measure it). At a 0.25-0.50% stop with target = stop,
breakeven is **73-95% at $20** and 53-57% at $2,000.

## B. VWAP pullback (trend continuation)

**Rules (v1).** After 10:00, call it an uptrend if the price has been above
VWAP for N minutes and VWAP is rising. When the price pulls back to within
X bp of VWAP and the next bar closes higher, buy. The stop is Y bp below
VWAP. Exit at the target or the flatten time.

**Mechanism as usually told.** Institutions benchmark their execution to
VWAP, so buyers step in near VWAP on up days and it acts as support.

**Why it might not work.**
- The mechanism is weak. VWAP execution algorithms follow the day's volume
  curve; they don't buy because the price touched VWAP. "Support at VWAP" is
  mostly trader folklore with little rigorous evidence behind it.
- On range days the price crosses VWAP over and over. That means many
  whipsaws and many round trips of costs.
- **Data problem specific to this strategy:** live VWAP from the free IEX
  feed is built from about 2.5% of volume and isn't the true VWAP. The stop
  sits just below VWAP, so the measurement error is about the size of the
  stop.

**Knobs:** trend lookback, slope threshold, touch tolerance, confirmation
rule, stop offset, target. At 5-6 knobs it's the easiest of the three to
overfit.

**At your size:** stops are about 0.10-0.25%, so breakeven with target = stop
is **"never" to 95% at $20**, and 57-66% at $2,000. This is the worst fit for
a small account.

## C. Intraday momentum: the last half hour

**Rules (v1).** At 15:30 ET, compute the return from yesterday's close to
10:00 today. If it's positive, buy at the next bar's open. Exit at the
flatten time (15:55). If it's negative, do nothing (long-only). Add a wide
protective stop, sized from recent volatility so it only fires on unusual
moves. Your rules require it; the published strategy doesn't have one.

**Mechanism.** Gao, Han, Li & Zhou (2018, *Journal of Financial Economics*)
found that the market's first-half-hour return, measured from the previous
close, predicts its last-half-hour return. Two explanations are documented:
- **Hedging flows** (Baltussen, Da, Lammers & Martens 2021, *JFE*). Leveraged
  ETFs must rebalance near the close in the direction of the day's move, and
  options dealers who are short gamma hedge the same way. These flows are
  mechanical and forced, so they're predictable by construction. This is the
  only strategy of the three with a concrete counterparty whose losses we can
  name.
- **Late-informed and infrequently rebalancing investors** trading late in
  the day (Bogousslavsky 2016).

**Why it might not work.**
- The effect is small per trade, a few basis points before costs in the
  original sample (take that as an order of magnitude; we'll measure it
  in-sample).
- It was published in 2018. On average, anomaly returns shrink substantially
  after publication (McLean & Pontiff 2016). Expect it to be weaker, maybe
  zero.
- **Your "flat before the close" rule cuts it short.** The paper holds to the
  16:00 close, and leveraged-ETF rebalancing largely trades in the closing
  auction. Exiting at 15:55 may miss much of the effect. A market-on-close
  exit would capture it but breaks your rule; that's a decision to make
  before testing, not after.
- Long-only trades only about half the days.

**Knobs:** effectively none if we take the published rule as-is. Every
deviation (15:55 exit, long-only, the protective stop's width) is forced by
your constraints and fixed in advance, not tuned.

**At your size:** there's no fixed target, so compare the cost to the
effect. A 22 bp round trip at $20 is very likely bigger than the whole
effect. At $2,000 (2.6 bp) it's a real open question.

---

## Side by side

| | A. ORB | B. VWAP pullback | C. Last half hour |
|---|---|---|---|
| Mechanism | plausible, crowded | weak / folklore | documented, named counterparty |
| Knobs to overfit | 4-5 | 5-6 | ~0 |
| Fits one trade/day (T+1) | yes | awkward (wants several) | exactly |
| Sensitive to IEX-vs-SIP data | somewhat (range levels) | badly (VWAP) | least (prices only) |
| Cost vs typical stop at $20 | fatal | fatal | fatal vs the effect size |
| Bot machinery it exercises | most (intraday stops, triggers) | most | least (one entry, one exit) |

## Recommendation

**C** is the most honest test of the methodology. It has a mechanism with a
named counterparty, almost nothing to tune, and it fits the one-trade-a-day
settlement limit exactly. The likely result is "no edge after costs at $20",
and possibly at $2,000 too. That's a real answer, and learning it cheaply is
the point of the project.

Pick **A** if you'd rather build the more classic day-trading machinery
(intraday stop management, breakout triggers). Then pre-commit a single spec
(for example the 5-minute range, entry on a close above, stop at the range
low, exit at 15:55) and accept that you get **one** shot at it.

I'd skip **B**: the weakest mechanism, the most knobs, and a data problem
that hits it directly.

Whichever you pick, write the mechanism in your own words in
`docs/decisions.md` before any backtest runs.
