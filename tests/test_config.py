import copy
import datetime as dt
import re

import pytest

from baselinetrading.config import DEFAULT_CONFIG_PATH, ConfigError, load_config, parse_config

TODAY = dt.date(2026, 9, 27)

VALID = {
    "account": {"equity_cap_usd": 20.0, "settlement_days": 1},
    "risk": {
        "max_risk_per_trade_pct": 2.0,
        "max_daily_loss_pct": 5.0,
        "max_entries_per_day": 1,
        "no_new_entries_minutes_before_close": 15,
        "flatten_minutes_before_close": 5,
    },
    "trading": {"enabled": False, "symbols": ["SPY"], "strategy": "last_half_hour", "instrument": "shares"},
    "costs": {
        "spread_usd_per_share": 0.01,
        "slippage_bps_per_side": 1.0,
        "stop_extra_slippage_bps": 2.0,
        "commission_per_order_usd": 0.0,
        "sec_fee_per_million_usd": 20.60,
        "finra_taf_per_share_usd": 0.000195,
        "finra_taf_max_usd": 9.79,
        "cat_fee_per_share_usd": 0.000003,
        "round_each_fee_up_to_cent": True,
    },
    "data": {
        "live_feed": "iex",
        "research_feed": "sip",
        "adjustment": "all",
        "max_missing_minutes": 0,
        "max_staleness_seconds": 90,
        "fetch_attempts": 3,
    },
    "splits": {
        "in_sample": {"start": dt.date(2016, 1, 4), "end": dt.date(2020, 12, 31)},
        "validation": {"start": dt.date(2021, 1, 1), "end": dt.date(2022, 12, 31)},
        "holdout": {"start": dt.date(2023, 1, 1), "end": dt.date(2026, 8, 31)},
    },
}
SAFETY_FLAGS = {("trading", "enabled"), ("costs", "round_each_fee_up_to_cent")}
REQUIRED_KEYS = [
    (section, key) for section, table in VALID.items() for key in table if (section, key) not in SAFETY_FLAGS
]


def valid():
    return copy.deepcopy(VALID)


def parse(raw):
    return parse_config(raw, today=TODAY)


def problems_for(raw):
    with pytest.raises(ConfigError) as caught:
        parse(raw)
    return caught.value.problems


def test_valid_settings_parse():
    config = parse(valid())
    assert config.risk.max_risk_per_trade_pct == 2.0
    assert config.trading.symbols == ("SPY",)
    assert config.splits.holdout.end == dt.date(2026, 8, 31)


def test_committed_settings_file_is_valid():
    config = load_config(DEFAULT_CONFIG_PATH, today=TODAY)
    assert config.account.equity_cap_usd == 100_000.0


def test_missing_file_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.toml", today=TODAY)


def test_invalid_toml_is_refused(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text("[risk\nmax_daily_loss_pct = 5.0\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(path, today=TODAY)


def test_toml_nan_limit_is_refused(tmp_path):
    # TOML has literal nan. A NaN loss limit fails open: `loss > nan` is always False.
    text, count = re.subn(
        r"max_daily_loss_pct\s*=\s*[\d.]+", "max_daily_loss_pct = nan", DEFAULT_CONFIG_PATH.read_text()
    )
    assert count == 1
    path = tmp_path / "settings.toml"
    path.write_text(text)
    with pytest.raises(ConfigError, match="max_daily_loss_pct must be a finite number"):
        load_config(path, today=TODAY)


@pytest.mark.parametrize(("section", "key"), REQUIRED_KEYS)
def test_every_non_flag_value_is_required(section, key):
    raw = valid()
    del raw[section][key]
    assert f"{section}.{key} is missing (required; there is no default)" in problems_for(raw)


def test_missing_trading_switch_means_trading_disabled():
    raw = valid()
    del raw["trading"]["enabled"]
    assert parse(raw).trading.enabled is False


def test_missing_fee_rounding_flag_means_conservative_rounding():
    raw = valid()
    del raw["costs"]["round_each_fee_up_to_cent"]
    assert parse(raw).costs.round_each_fee_up_to_cent is True


@pytest.mark.parametrize("value", ["true", "yes", 1, 0, 1.0])
def test_trading_switch_must_be_a_real_boolean(value):
    raw = valid()
    raw["trading"]["enabled"] = value
    assert any("trading.enabled must be true or false" in p for p in problems_for(raw))


@pytest.mark.parametrize(("key", "value"), [("max_risk_per_trade_pct", 2.5), ("max_daily_loss_pct", 5.01)])
def test_risk_limits_above_the_hard_ceiling_are_refused(key, value):
    raw = valid()
    raw["risk"][key] = value
    assert any(p.startswith(f"risk.{key} must be at most") and "hard limit" in p for p in problems_for(raw))


@pytest.mark.parametrize("value", [0, -1.0, float("nan"), float("inf"), True, "2.0"])
def test_malformed_risk_numbers_are_refused(value):
    raw = valid()
    raw["risk"]["max_risk_per_trade_pct"] = value
    assert any(p.startswith("risk.max_risk_per_trade_pct") for p in problems_for(raw))


@pytest.mark.parametrize("key", ["spread_usd_per_share", "slippage_bps_per_side"])
def test_zero_spread_or_slippage_is_refused(key):
    raw = valid()
    raw["costs"][key] = 0.0
    assert any(p.startswith(f"costs.{key} must be greater than 0") for p in problems_for(raw))


def test_a_typo_is_reported_as_unknown_and_missing():
    raw = valid()
    raw["risk"]["max_daily_los_pct"] = raw["risk"].pop("max_daily_loss_pct")
    problems = problems_for(raw)
    assert "risk.max_daily_los_pct is not a recognised setting (typo?)" in problems
    assert "risk.max_daily_loss_pct is missing (required; there is no default)" in problems


def test_unknown_section_is_refused():
    raw = valid()
    raw["strategy"] = {"opening_range_minutes": 5}
    assert "unknown section [strategy]" in problems_for(raw)


def test_missing_section_is_reported_once():
    raw = valid()
    del raw["risk"]
    problems = problems_for(raw)
    assert "missing section [risk]" in problems
    assert not [p for p in problems if p.startswith("risk.")]


@pytest.mark.parametrize(
    ("section", "key"), [("trading", "api_key"), ("account", "alpaca_secret"), ("trading", "APCA_API_KEY_ID")]
)
def test_credentials_in_the_settings_file_are_refused(section, key):
    raw = valid()
    raw[section][key] = "PKXXXXXXXX"
    problems = problems_for(raw)
    assert any("looks like a credential" in p for p in problems)
    assert not any("PKXXXXXXXX" in p for p in problems)


def test_every_problem_is_reported_at_once():
    raw = valid()
    raw["risk"]["max_risk_per_trade_pct"] = 3.0
    raw["account"]["equity_cap_usd"] = -5
    raw["trading"]["enabled"] = "true"
    assert len(problems_for(raw)) == 3


def test_entries_must_stop_before_flattening_starts():
    raw = valid()
    raw["risk"]["no_new_entries_minutes_before_close"] = 5
    raw["risk"]["flatten_minutes_before_close"] = 5
    assert any("must be greater than flatten_minutes_before_close" in p for p in problems_for(raw))


def test_settlement_cannot_be_switched_off():
    raw = valid()
    raw["account"]["settlement_days"] = 0
    assert any(p.startswith("account.settlement_days") for p in problems_for(raw))


@pytest.mark.parametrize("symbols", [[], ["spy"], ["SPY", "SPY"], "SPY", ["TOOLONG"], [1]])
def test_bad_symbol_lists_are_refused(symbols):
    raw = valid()
    raw["trading"]["symbols"] = symbols
    assert any(p.startswith("trading.symbols") for p in problems_for(raw))


def test_overlapping_splits_are_refused():
    raw = valid()
    raw["splits"]["validation"]["start"] = dt.date(2020, 6, 1)
    assert any("chronological" in p for p in problems_for(raw))


def test_holdout_must_already_be_in_the_past():
    raw = valid()
    raw["splits"]["holdout"]["end"] = TODAY
    assert any("not in the past" in p for p in problems_for(raw))


def test_split_with_a_time_of_day_is_refused():
    raw = valid()
    raw["splits"]["in_sample"]["start"] = dt.datetime(2016, 1, 4, 9, 30)
    assert any(p.startswith("splits.in_sample") for p in problems_for(raw))


def test_split_dates_may_be_iso_strings():
    raw = valid()
    raw["splits"]["in_sample"] = {"start": "2016-01-04", "end": "2020-12-31"}
    assert parse(raw).splits.in_sample.start == dt.date(2016, 1, 4)


@pytest.mark.parametrize(("key", "value"), [("live_feed", "IEX"), ("research_feed", "delayed_sip"), ("adjustment", 1)])
def test_data_choices_must_be_exact(key, value):
    raw = valid()
    raw["data"][key] = value
    assert any(p.startswith(f"data.{key} must be one of") for p in problems_for(raw))
