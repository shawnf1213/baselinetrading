import dataclasses
import math

import pytest

from baselinetrading.config import CostConfig
from baselinetrading.costs import breakeven_win_rate, expected_net_per_trade, round_trip_cost

COSTS = CostConfig(
    spread_usd_per_share=0.01,
    slippage_bps_per_side=1.0,
    stop_extra_slippage_bps=2.0,
    commission_per_order_usd=0.0,
    sec_fee_per_million_usd=20.60,
    finra_taf_per_share_usd=0.000195,
    finra_taf_max_usd=9.79,
    cat_fee_per_share_usd=0.000003,
    round_each_fee_up_to_cent=True,
)


def with_costs(**changes):
    return dataclasses.replace(COSTS, **changes)


def test_twenty_dollar_round_trip_is_itemised():
    cost = round_trip_cost(COSTS, notional_usd=20.0, price=650.0)
    shares = 20.0 / 650.0
    assert cost.spread_usd == pytest.approx(shares * 0.01)
    assert cost.slippage_usd == pytest.approx(20.0 * 2 / 10_000)  # 1 bp on each order
    assert cost.sec_fee_usd == 0.01  # $0.000412, rounded up
    assert cost.finra_taf_usd == 0.01  # $0.000006, rounded up
    assert cost.cat_fee_usd == 0.02  # $0.0000001 per order, rounded up on the buy and on the sell
    assert cost.total_bps == pytest.approx(22.15, abs=0.01)


def test_without_rounding_fees_are_proportional():
    cost = round_trip_cost(with_costs(round_each_fee_up_to_cent=False), notional_usd=20.0, price=650.0)
    assert cost.sec_fee_usd == pytest.approx(20.0 * 20.60 / 1_000_000)
    assert cost.finra_taf_usd == pytest.approx(20.0 / 650.0 * 0.000195)


def test_a_zero_fee_rate_is_not_rounded_up_to_a_cent():
    cost = round_trip_cost(with_costs(sec_fee_per_million_usd=0.0), notional_usd=20.0, price=650.0)
    assert cost.sec_fee_usd == 0.0


def test_rounding_is_exact_at_whole_cents():
    # 2000 x 35 / 1e6 = 0.07 exactly; naive float rounding gives ceil(7.000000000000001) = 8 cents.
    cost = round_trip_cost(with_costs(sec_fee_per_million_usd=35.0), notional_usd=2_000.0, price=100.0)
    assert cost.sec_fee_usd == 0.07


def test_taf_is_capped_per_trade():
    cost = round_trip_cost(COSTS, notional_usd=10_000_000.0, price=10.0)  # 1M shares: $195 uncapped
    assert cost.finra_taf_usd == 9.79


def test_commission_is_charged_on_both_orders():
    cost = round_trip_cost(with_costs(commission_per_order_usd=1.0), notional_usd=20.0, price=650.0)
    assert cost.commission_usd == 2.0


def test_stop_exits_pay_extra_slippage():
    normal = round_trip_cost(COSTS, notional_usd=20.0, price=650.0)
    stopped = round_trip_cost(COSTS, notional_usd=20.0, price=650.0, exit_by_stop=True)
    assert stopped.slippage_usd - normal.slippage_usd == pytest.approx(20.0 * 2.0 / 10_000)


def test_fixed_fees_dominate_small_positions():
    small = round_trip_cost(COSTS, notional_usd=20.0, price=650.0)
    large = round_trip_cost(COSTS, notional_usd=2_000.0, price=650.0)
    assert small.fees_usd / small.total_usd > 0.8
    assert large.total_bps < small.total_bps / 5


@pytest.mark.parametrize(
    ("notional", "price"), [(0.0, 650.0), (-20.0, 650.0), (20.0, 0.0), (math.nan, 650.0), (20.0, math.inf)]
)
def test_impossible_orders_raise(notional, price):
    with pytest.raises(ValueError):
        round_trip_cost(COSTS, notional_usd=notional, price=price)


def test_breakeven_without_costs_is_the_textbook_number():
    assert breakeven_win_rate(avg_win=1.0, avg_loss=1.0, cost=0.0) == 0.5
    assert breakeven_win_rate(avg_win=2.0, avg_loss=1.0, cost=0.0) == pytest.approx(1 / 3)


def test_costs_raise_the_breakeven():
    # 50 bp target and stop, 22 bp round trip: net win 28, net loss 72 -> 72%.
    assert breakeven_win_rate(avg_win=50.0, avg_loss=50.0, cost=22.0) == pytest.approx(0.72)


def test_losers_can_carry_a_higher_cost():
    # net win 50 - 22 = 28, net loss 50 + 24 = 74
    assert breakeven_win_rate(avg_win=50.0, avg_loss=50.0, cost=22.0, loss_cost=24.0) == pytest.approx(74 / 102)


@pytest.mark.parametrize("cost", [10.0, 22.0])
def test_costs_that_eat_the_whole_win_make_breakeven_impossible(cost):
    assert breakeven_win_rate(avg_win=10.0, avg_loss=10.0, cost=cost) == math.inf


@pytest.mark.parametrize(("win", "loss", "cost"), [(50.0, 50.0, 22.0), (80.0, 40.0, 15.0), (30.0, 60.0, 3.0)])
def test_expected_value_is_zero_exactly_at_breakeven(win, loss, cost):
    p = breakeven_win_rate(avg_win=win, avg_loss=loss, cost=cost)
    assert expected_net_per_trade(win_rate=p, avg_win=win, avg_loss=loss, cost=cost) == pytest.approx(0.0, abs=1e-9)
    assert expected_net_per_trade(win_rate=p + 0.01, avg_win=win, avg_loss=loss, cost=cost) > 0
    assert expected_net_per_trade(win_rate=p - 0.01, avg_win=win, avg_loss=loss, cost=cost) < 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"avg_win": 0.0, "avg_loss": 1.0, "cost": 0.0},
        {"avg_win": 1.0, "avg_loss": -1.0, "cost": 0.0},
        {"avg_win": 1.0, "avg_loss": 1.0, "cost": -0.1},
        {"avg_win": math.nan, "avg_loss": 1.0, "cost": 0.0},
        {"avg_win": 1.0, "avg_loss": 1.0, "cost": 0.0, "loss_cost": math.inf},
    ],
)
def test_breakeven_rejects_nonsense(kwargs):
    with pytest.raises(ValueError):
        breakeven_win_rate(**kwargs)
