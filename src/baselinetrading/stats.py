"""Small samples mislead. These helpers attach uncertainty to every win rate.

* wilson_interval(): confidence interval for a win rate that still works at small n.
* compare_to_breakeven(): above / below / inconclusive, never a bare point estimate.
* trades_needed(): how many trades before an edge of a given size can be told apart
  from luck at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

ABOVE = "ABOVE breakeven"
BELOW = "BELOW breakeven"
INCONCLUSIVE = "INCONCLUSIVE"


def wilson_interval(wins: int, n: int, *, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a win rate of wins/n.

    Unlike the textbook p +/- z*sqrt(p(1-p)/n), it stays inside [0, 1] and holds
    up at small n or at win rates near 0% or 100%.
    """
    if n <= 0 or not 0 <= wins <= n:
        raise ValueError(f"need 0 <= wins <= n and n > 0, got wins={wins}, n={n}")
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be between 0 and 1, got {confidence}")
    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    p = wins / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half_width = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    # At 0 or n wins the bound is exactly 0 or 1; floating point would leave ~1e-17.
    low = 0.0 if wins == 0 else center - half_width
    high = 1.0 if wins == n else center + half_width
    return low, high


@dataclass(frozen=True)
class WinRateVerdict:
    wins: int
    n: int
    ci_low: float
    ci_high: float
    breakeven: float
    confidence: float
    verdict: str  # ABOVE, BELOW or INCONCLUSIVE

    @property
    def win_rate(self) -> float:
        return self.wins / self.n

    def describe(self) -> str:
        breakeven = "impossible (costs exceed the average win)" if math.isinf(self.breakeven) else f"{self.breakeven:.1%}"
        return (
            f"win rate {self.win_rate:.1%} ({self.wins}/{self.n}), {self.confidence:.0%} CI "
            f"{self.ci_low:.1%}-{self.ci_high:.1%}; breakeven {breakeven} -> {self.verdict}"
        )


def compare_to_breakeven(wins: int, n: int, breakeven: float, *, confidence: float = 0.95) -> WinRateVerdict:
    """Compare a win rate to breakeven. The default answer is INCONCLUSIVE.

    Only when the whole confidence interval sits on one side of breakeven does the
    verdict become ABOVE or BELOW. This does not correct for trying several
    strategy variants; the backtester's trial ledger does that by raising
    `confidence`.
    """
    low, high = wilson_interval(wins, n, confidence=confidence)
    if low > breakeven:
        verdict = ABOVE
    elif high < breakeven:
        verdict = BELOW
    else:
        verdict = INCONCLUSIVE
    return WinRateVerdict(wins, n, low, high, breakeven, confidence, verdict)


def trades_needed(*, breakeven: float, true_win_rate: float, alpha: float = 0.05, power: float = 0.80) -> int:
    """Trades needed before a one-sided test can tell `true_win_rate` apart from `breakeven`.

    `power` is the chance of detecting the edge if it's really there. Uses the
    normal approximation to the binomial. Example: breakeven 50%, true win rate
    55% -> 617 trades, roughly two and a half years at one trade per trading day.
    """
    if not (0 < breakeven < 1 and 0 < true_win_rate < 1):
        raise ValueError(f"win rates must be strictly between 0 and 1, got {breakeven} and {true_win_rate}")
    if true_win_rate <= breakeven:
        raise ValueError("an edge has to be above breakeven to be detectable")
    if not (0 < alpha < 1 and 0 < power < 1):
        raise ValueError("alpha and power must be strictly between 0 and 1")
    z_alpha = NormalDist().inv_cdf(1 - alpha)
    z_power = NormalDist().inv_cdf(power)
    numerator = z_alpha * math.sqrt(breakeven * (1 - breakeven)) + z_power * math.sqrt(
        true_win_rate * (1 - true_win_rate)
    )
    return math.ceil((numerator / (true_win_rate - breakeven)) ** 2)
