import pytest

from baselinetrading.stats import ABOVE, BELOW, INCONCLUSIVE, compare_to_breakeven, trades_needed, wilson_interval


def test_wilson_matches_the_published_value():
    low, high = wilson_interval(50, 100)
    assert low == pytest.approx(0.4038, abs=1e-4)
    assert high == pytest.approx(0.5962, abs=1e-4)


def test_wilson_stays_inside_zero_and_one():
    assert wilson_interval(0, 10)[0] == 0.0
    assert wilson_interval(10, 10)[1] == 1.0


def test_more_trades_narrow_the_interval():
    small = wilson_interval(6, 10)
    large = wilson_interval(600, 1000)
    assert large[1] - large[0] < (small[1] - small[0]) / 5


@pytest.mark.parametrize(("wins", "n", "confidence"), [(0, 0, 0.95), (11, 10, 0.95), (-1, 10, 0.95), (5, 10, 1.0)])
def test_wilson_rejects_nonsense(wins, n, confidence):
    with pytest.raises(ValueError):
        wilson_interval(wins, n, confidence=confidence)


def test_a_good_looking_small_sample_is_inconclusive():
    # 7 wins in 10 looks like 70%, but the interval runs from about 40% to 89%.
    verdict = compare_to_breakeven(7, 10, 0.55)
    assert verdict.verdict == INCONCLUSIVE
    assert "INCONCLUSIVE" in verdict.describe()


def test_verdicts_need_the_whole_interval_on_one_side():
    assert compare_to_breakeven(700, 1000, 0.55).verdict == ABOVE
    assert compare_to_breakeven(400, 1000, 0.55).verdict == BELOW


def test_trades_needed_for_a_five_point_edge_over_a_coin_flip():
    assert trades_needed(breakeven=0.50, true_win_rate=0.55) == 617


def test_smaller_edges_need_more_trades():
    assert trades_needed(breakeven=0.50, true_win_rate=0.52) > trades_needed(breakeven=0.50, true_win_rate=0.55)


@pytest.mark.parametrize(("breakeven", "true_rate"), [(0.55, 0.55), (0.55, 0.50), (0.0, 0.5), (0.5, 1.0)])
def test_trades_needed_rejects_nonsense(breakeven, true_rate):
    with pytest.raises(ValueError):
        trades_needed(breakeven=breakeven, true_win_rate=true_rate)
