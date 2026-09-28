"""The journal: every signal, veto, order, fill and exit, with the inputs behind it.

Events go to logs/journal-YYYY-MM-DD.jsonl (one JSON object per line, append
only) and to an in-memory buffer for the UI. Manual and strategy events are
written identically, tagged with `source`. Replaying today's file on startup
restores entry counts, trades and the kill switch, so a restart can't reset the
day's limits.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import math
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from baselinetrading.bars import ET
from baselinetrading.stats import wilson_interval


@dataclass
class Trade:
    source: str
    symbol: str
    qty: float
    entry_price: float
    entry_time: str
    stop_price: float | None
    stop_order_id: str | None
    cost_usd: float  # modelled round-trip cost (paper trading charges no fees)
    exit_price: float | None = None
    exit_time: str | None = None
    exit_reason: str | None = None
    underlying: str | None = None  # options: the stock the contract is on (the trade's key in the gateway)
    stop_underlying: float | None = None  # options: the bot sells when the stock trades at or below this
    multiplier: float = 1.0  # options: 100 shares per contract

    @property
    def key(self) -> str:
        """The symbol this trade occupies: the stock, whether held as shares or as an option on it."""
        return self.underlying or self.symbol

    @property
    def gross_pnl(self) -> float | None:
        return None if self.exit_price is None else (self.exit_price - self.entry_price) * self.qty * self.multiplier

    @property
    def net_pnl(self) -> float | None:
        gross = self.gross_pnl
        return None if gross is None else gross - self.cost_usd


class Journal:
    def __init__(self, directory: Path | None, clock: Callable[[], dt.datetime], keep: int = 500) -> None:
        self._dir = directory
        self._clock = clock
        self._recent: collections.deque[dict] = collections.deque(maxlen=keep)
        self._lock = threading.Lock()

    def record(self, kind: str, source: str = "system", **fields: Any) -> dict:
        now = self._clock()
        event = {"ts": now.isoformat(), "kind": kind, "source": source, **_jsonable(fields)}
        with self._lock:
            self._recent.append(event)
            if self._dir is not None:
                self._dir.mkdir(parents=True, exist_ok=True)
                with open(self._path(now.astimezone(ET).date()), "a", encoding="utf-8") as f:
                    f.write(json.dumps(event) + "\n")
        return event

    def recent(self, n: int = 50, kinds: set[str] | None = None) -> list[dict]:
        with self._lock:
            events = [e for e in self._recent if kinds is None or e["kind"] in kinds]
        return events[-n:][::-1]

    def load_day(self, day: dt.date) -> list[dict]:
        """Replay a day's events from disk into the buffer and return them. Unreadable lines are skipped."""
        if self._dir is None or not self._path(day).exists():
            return []
        events = []
        for line in self._path(day).read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        with self._lock:
            self._recent.extend(events)
        return events

    def _path(self, day: dt.date) -> Path:
        return self._dir / f"journal-{day.isoformat()}.jsonl"


def summarize(trades: list[Trade]) -> dict[str, dict]:
    """Closed-trade P&L per source, so manual and strategy results are never mixed."""
    out: dict[str, dict] = {}
    for source in ("manual", "strategy"):
        closed = [t for t in trades if t.source == source and t.exit_price is not None]
        wins = sum(1 for t in closed if t.net_pnl > 0)
        entry = {
            "trades": len(closed),
            "wins": wins,
            "gross_pnl": sum(t.gross_pnl for t in closed),
            "costs": sum(t.cost_usd for t in closed),
            "net_pnl": sum(t.net_pnl for t in closed),
            "win_rate": wins / len(closed) if closed else None,
            "win_rate_ci": list(wilson_interval(wins, len(closed))) if closed else None,
        }
        out[source] = entry
    return out


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
