import datetime as dt

from baselinetrading.options import Contract
from baselinetrading.universe import is_fund, rank_by_dollar_volume, untradeable

DAYS = [dt.date(2026, 9, 25), dt.date(2026, 9, 28)]
TODAY = dt.date(2026, 9, 29)


def test_stocks_rank_by_average_volume_times_vwap_and_need_every_session():
    days = {
        "AAA": [(DAYS[0], 100, 10.0, 10.0), (DAYS[1], 100, 10.0, 11.0)],  # $1,000 a day
        "BBB": [(DAYS[0], 50, 100.0, 99.0), (DAYS[1], 50, 100.0, 100.0)],  # $5,000 a day
        "CCC": [(DAYS[1], 1e9, 1.0, 1.0)],  # missing a session
    }
    ranked = rank_by_dollar_volume(days, DAYS, {"AAA": ("A Inc", False)})
    assert [r.symbol for r in ranked] == ["BBB", "AAA"]
    assert ranked[0].dollar_volume == 5000 and ranked[1].last_close == 11.0 and ranked[1].name == "A Inc"


def test_funds_are_recognised_by_exchange_or_name():
    assert is_fund("State Street SPDR S&P 500 ETF Trust", "ARCA")
    assert is_fund("Invesco QQQ Trust, Series 1", "NASDAQ")
    assert is_fund("Direxion Daily TSLA Bull 2X Shares", "NASDAQ")
    assert not is_fund("NVIDIA Corporation Common Stock", "NASDAQ")
    assert not is_fund("Taiwan Semiconductor Manufacturing Company Ltd. American Depositary Shares", "NYSE")


class OptionChain:
    """Calls and puts 7 days out at 95/100/105, all quoted at the same ask."""

    def __init__(self, ask, days_out=7):
        self.ask, self.days_out = ask, days_out

    def option_contracts(self, underlying, expires_from, expires_to, kind):
        exp = TODAY + dt.timedelta(days=self.days_out)
        letter = "C" if kind == "call" else "P"
        return [Contract(f"{underlying}{exp:%y%m%d}{letter}{k * 1000:08d}", exp, k) for k in (95, 100, 105)]

    def option_quote(self, symbol):
        return self.ask - 0.10, self.ask


def test_a_stock_is_tradeable_only_if_one_call_and_one_put_fit_the_premium_cap():
    assert untradeable(OptionChain(19.99), "AAA", 100.5, TODAY, 2000) is None
    assert untradeable(OptionChain(20.01), "AAA", 100.5, TODAY, 2000) == (
        "one call (AAA261006C00105000) costs $2,001, over the $2,000 cap")
    assert untradeable(OptionChain(5.00, days_out=17), "AAA", 100.5, TODAY, 2000) == "no call 5-12 days out"
