"""Settings: load config/settings.toml, and refuse to run if anything is off.

Fail-closed rules implemented here:

* A missing, malformed, out-of-range or unrecognised value is an error, and an
  error stops the program before it can trade. Problems are collected and
  reported together, so they can be fixed in one pass.
* Only boolean safety flags may be left out, and leaving one out always picks
  the restrictive behaviour (``trading.enabled`` -> false). Nothing else has a
  default, so a typo such as ``max_daily_los_pct`` shows up as an unknown key
  plus a missing one instead of quietly falling back to a built-in value.
* Some limits are rules, not settings (the HARD_* constants). The file may be
  stricter than them, never looser.
* Secrets never live in this file. Any key that looks like one is rejected;
  credentials come from environment variables (see credentials.py).
"""

from __future__ import annotations

import datetime as dt
import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Rules from the project spec, not settings.
HARD_MAX_RISK_PER_TRADE_PCT = 2.0
HARD_MAX_DAILY_LOSS_PCT = 5.0
REGULAR_SESSION_MINUTES = 390  # 09:30-16:00 ET

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.toml"

_SECTIONS = ("account", "risk", "trading", "costs", "splits")
_SECRET_LIKE = re.compile(r"secret|passw|token|api_?key|key_?id", re.IGNORECASE)
_SYMBOL = re.compile(r"[A-Z]{1,5}")
_HARD_LIMIT_NOTE = " (hard limit in code: the config may be stricter, never looser)"
_MISSING = object()


class ConfigError(Exception):
    """Configuration is missing, malformed or unsafe. The only correct response is to not trade."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("refusing to run; configuration problems:\n  - " + "\n  - ".join(self.problems))


@dataclass(frozen=True)
class AccountConfig:
    equity_cap_usd: float  # sizing uses min(broker equity, this), so paper behaves like the real account
    settlement_days: int  # cash-account rule: sale proceeds are unusable for this many trading days


@dataclass(frozen=True)
class RiskConfig:
    max_risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_entries_per_day: int
    no_new_entries_minutes_before_close: int
    flatten_minutes_before_close: int


@dataclass(frozen=True)
class TradingConfig:
    enabled: bool  # the only switch that allows orders; absent means False
    symbols: tuple[str, ...]  # allowlist: orders for anything else are vetoed


@dataclass(frozen=True)
class CostConfig:
    spread_usd_per_share: float
    slippage_bps_per_side: float
    stop_extra_slippage_bps: float
    commission_per_order_usd: float
    sec_fee_per_million_usd: float
    finra_taf_per_share_usd: float
    finra_taf_max_usd: float
    cat_fee_per_share_usd: float
    round_each_fee_up_to_cent: bool


@dataclass(frozen=True)
class DateRange:
    start: dt.date
    end: dt.date  # inclusive


@dataclass(frozen=True)
class SplitConfig:
    in_sample: DateRange
    validation: DateRange
    holdout: DateRange  # not to be read until the strategy is frozen


@dataclass(frozen=True)
class Config:
    account: AccountConfig
    risk: RiskConfig
    trading: TradingConfig
    costs: CostConfig
    splits: SplitConfig


def load_config(path: Path | str = DEFAULT_CONFIG_PATH, *, today: dt.date | None = None) -> Config:
    """Read and validate a settings file. Raises ConfigError on any problem."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError([f"cannot read {path}: {exc.strerror or exc}"]) from None
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError([f"{path} is not valid TOML: {exc}"]) from None
    return parse_config(raw, today=today)


def parse_config(raw: dict[str, Any], *, today: dt.date | None = None) -> Config:
    """Validate already-parsed settings. Raises ConfigError listing every problem found."""
    today = today or dt.date.today()
    problems: list[str] = []
    _reject_secret_like_keys(raw, "", problems)
    for name in raw:
        if name not in _SECTIONS and not _SECRET_LIKE.search(name):
            problems.append(f"unknown section [{name}]")

    account = _Table(raw, "account", problems)
    equity_cap_usd = account.number("equity_cap_usd", greater_than=0, at_most=10_000_000)
    settlement_days = account.integer("settlement_days", at_least=1, at_most=3)
    account.reject_unknown_keys()

    risk = _Table(raw, "risk", problems)
    max_risk_per_trade_pct = risk.number(
        "max_risk_per_trade_pct", greater_than=0, at_most=HARD_MAX_RISK_PER_TRADE_PCT, note=_HARD_LIMIT_NOTE
    )
    max_daily_loss_pct = risk.number(
        "max_daily_loss_pct", greater_than=0, at_most=HARD_MAX_DAILY_LOSS_PCT, note=_HARD_LIMIT_NOTE
    )
    max_entries_per_day = risk.integer("max_entries_per_day", at_least=1, at_most=20)
    no_new_entries = risk.integer("no_new_entries_minutes_before_close", at_least=2, at_most=REGULAR_SESSION_MINUTES)
    flatten = risk.integer("flatten_minutes_before_close", at_least=1, at_most=60)
    risk.reject_unknown_keys()
    if no_new_entries is not None and flatten is not None and no_new_entries <= flatten:
        problems.append(
            "risk.no_new_entries_minutes_before_close must be greater than "
            "flatten_minutes_before_close (stop opening trades before you start closing them)"
        )

    trading = _Table(raw, "trading", problems)
    enabled = trading.flag("enabled", when_absent=False)
    symbols = trading.symbols("symbols")
    trading.reject_unknown_keys()

    costs = _Table(raw, "costs", problems)
    # Spread and slippage must be positive: a zero-cost backtest is how people fool themselves.
    spread_usd_per_share = costs.number("spread_usd_per_share", greater_than=0, at_most=1)
    slippage_bps_per_side = costs.number("slippage_bps_per_side", greater_than=0, at_most=100)
    stop_extra_slippage_bps = costs.number("stop_extra_slippage_bps", at_least=0, at_most=500)
    commission_per_order_usd = costs.number("commission_per_order_usd", at_least=0, at_most=100)
    sec_fee_per_million_usd = costs.number("sec_fee_per_million_usd", at_least=0, at_most=1_000)
    finra_taf_per_share_usd = costs.number("finra_taf_per_share_usd", at_least=0, at_most=0.01)
    finra_taf_max_usd = costs.number("finra_taf_max_usd", at_least=0, at_most=100)
    cat_fee_per_share_usd = costs.number("cat_fee_per_share_usd", at_least=0, at_most=0.01)
    round_each_fee_up_to_cent = costs.flag("round_each_fee_up_to_cent", when_absent=True)
    costs.reject_unknown_keys()

    splits = _Table(raw, "splits", problems)
    in_sample = splits.date_range("in_sample")
    validation = splits.date_range("validation")
    holdout = splits.date_range("holdout")
    splits.reject_unknown_keys()
    if in_sample and validation and in_sample.end >= validation.start:
        problems.append("splits: validation must start after in_sample ends (chronological order, no overlap)")
    if validation and holdout and validation.end >= holdout.start:
        problems.append("splits: holdout must start after validation ends (chronological order, no overlap)")
    if holdout and holdout.end >= today:
        problems.append(f"splits.holdout ends {holdout.end}, which is not in the past; a holdout has to be data that exists")

    if problems:
        raise ConfigError(problems)
    # Every value below passed validation; a failed read would have raised above.
    return Config(
        account=AccountConfig(equity_cap_usd=equity_cap_usd, settlement_days=settlement_days),
        risk=RiskConfig(
            max_risk_per_trade_pct=max_risk_per_trade_pct,
            max_daily_loss_pct=max_daily_loss_pct,
            max_entries_per_day=max_entries_per_day,
            no_new_entries_minutes_before_close=no_new_entries,
            flatten_minutes_before_close=flatten,
        ),
        trading=TradingConfig(enabled=enabled, symbols=symbols),
        costs=CostConfig(
            spread_usd_per_share=spread_usd_per_share,
            slippage_bps_per_side=slippage_bps_per_side,
            stop_extra_slippage_bps=stop_extra_slippage_bps,
            commission_per_order_usd=commission_per_order_usd,
            sec_fee_per_million_usd=sec_fee_per_million_usd,
            finra_taf_per_share_usd=finra_taf_per_share_usd,
            finra_taf_max_usd=finra_taf_max_usd,
            cat_fee_per_share_usd=cat_fee_per_share_usd,
            round_each_fee_up_to_cent=round_each_fee_up_to_cent,
        ),
        splits=SplitConfig(in_sample=in_sample, validation=validation, holdout=holdout),
    )


class _Table:
    """Typed access to one [section]. Every problem is recorded; nothing is guessed.

    A failed read returns None and adds a problem, and parse_config raises before
    any None can reach a Config.
    """

    def __init__(self, raw: dict[str, Any], name: str, problems: list[str]) -> None:
        self._name = name
        self._problems = problems
        self._read: set[str] = set()
        data = raw.get(name, _MISSING)
        self._present = isinstance(data, dict)
        if data is _MISSING:
            problems.append(f"missing section [{name}]")
        elif not self._present:
            problems.append(f"[{name}] must be a table, got {data!r}")
        self._data: dict[str, Any] = data if self._present else {}

    def number(
        self,
        key: str,
        *,
        at_most: float,
        greater_than: float | None = None,
        at_least: float | None = None,
        note: str = "",
    ) -> float | None:
        value = self._required(key)
        if value is _MISSING:
            return None
        # bool is a subclass of int in Python, so `true` would otherwise pass as 1.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return self._problem(key, f"must be a number, got {value!r}")
        value = float(value)
        # TOML allows `nan` and `inf`. A NaN limit fails open: every comparison
        # with NaN is False, so `if loss > limit: halt()` would never fire.
        if not math.isfinite(value):
            return self._problem(key, f"must be a finite number, got {value}")
        if greater_than is not None and value <= greater_than:
            return self._problem(key, f"must be greater than {greater_than:g}, got {value:g}{note}")
        if at_least is not None and value < at_least:
            return self._problem(key, f"must be at least {at_least:g}, got {value:g}{note}")
        if value > at_most:
            return self._problem(key, f"must be at most {at_most:g}, got {value:g}{note}")
        return value

    def integer(self, key: str, *, at_least: int, at_most: int) -> int | None:
        value = self._required(key)
        if value is _MISSING:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            return self._problem(key, f"must be a whole number, got {value!r}")
        if not at_least <= value <= at_most:
            return self._problem(key, f"must be between {at_least} and {at_most}, got {value}")
        return value

    def flag(self, key: str, *, when_absent: bool) -> bool | None:
        """A boolean safety flag. If absent it takes `when_absent`, which must be the restrictive choice."""
        self._read.add(key)
        if key not in self._data:
            return when_absent
        value = self._data[key]
        if not isinstance(value, bool):
            return self._problem(key, f"must be true or false (unquoted), got {value!r}")
        return value

    def symbols(self, key: str) -> tuple[str, ...] | None:
        value = self._required(key)
        if value is _MISSING:
            return None
        if not isinstance(value, list) or not value:
            return self._problem(key, f"must be a non-empty list of ticker symbols, got {value!r}")
        bad = [s for s in value if not (isinstance(s, str) and _SYMBOL.fullmatch(s))]
        if bad:
            return self._problem(key, f"has invalid symbols {bad!r} (expected 1-5 uppercase letters)")
        if len(set(value)) != len(value):
            return self._problem(key, f"lists a symbol more than once: {value!r}")
        return tuple(value)

    def date_range(self, key: str) -> DateRange | None:
        value = self._required(key)
        if value is _MISSING:
            return None
        if not isinstance(value, dict) or set(value) != {"start", "end"}:
            return self._problem(key, "must be { start = YYYY-MM-DD, end = YYYY-MM-DD } with nothing else")
        start, end = _as_date(value["start"]), _as_date(value["end"])
        if start is None or end is None:
            return self._problem(key, f"start and end must be dates like 2016-01-04, got {value!r}")
        if start > end:
            return self._problem(key, f"starts after it ends ({start} > {end})")
        return DateRange(start=start, end=end)

    def reject_unknown_keys(self) -> None:
        for key in self._data:
            if key not in self._read and not _SECRET_LIKE.search(key):
                self._problem(key, "is not a recognised setting (typo?)")

    def _required(self, key: str) -> Any:
        self._read.add(key)
        if key in self._data:
            return self._data[key]
        if self._present:  # a missing section is already reported once; don't list every key
            self._problem(key, "is missing (required; there is no default)")
        return _MISSING

    def _problem(self, key: str, message: str) -> None:
        self._problems.append(f"{self._name}.{key} {message}")
        return None


def _as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):  # a datetime is also a date; a time of day makes no sense here
        return None
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _reject_secret_like_keys(table: dict[str, Any], prefix: str, problems: list[str]) -> None:
    for key, value in table.items():
        path = f"{prefix}{key}"
        if _SECRET_LIKE.search(key):
            problems.append(
                f"{path} looks like a credential; secrets come from environment variables, never settings.toml"
            )
        if isinstance(value, dict):
            _reject_secret_like_keys(value, f"{path}.", problems)
