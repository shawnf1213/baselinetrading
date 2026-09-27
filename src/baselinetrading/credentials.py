"""Alpaca credentials: read from environment variables only, masked everywhere else.

The key ID and secret come from APCA_API_KEY_ID and APCA_API_SECRET_KEY (the
names Alpaca's own tools use). They are wrapped in Secret, which prints as
'********' in repr(), str(), f-strings and tracebacks and refuses to be pickled
or deep-copied. RedactSecrets is a logging filter that scrubs the raw values
from every log line as a second line of defence.

This phase is paper-only. Alpaca paper key IDs start with "PK" and live ones
with "AK", so any key ID that doesn't start with "PK" is refused. The execution
module will add its own checks; this one stops a live key at the door.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from baselinetrading.config import ConfigError

KEY_ID_VAR = "APCA_API_KEY_ID"
SECRET_KEY_VAR = "APCA_API_SECRET_KEY"
PAPER_KEY_PREFIX = "PK"
REDACTED = "********"


class Secret:
    """A string that won't show itself in repr(), str(), f-strings or logs."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """The raw value. Call this only where it is handed to the API client."""
        return self._value

    def __repr__(self) -> str:
        return f"Secret({REDACTED!r})"

    __str__ = __repr__

    def __reduce__(self):
        # Blocks pickle, copy and dataclasses.asdict: all are routes to a file or a log.
        raise TypeError("Secret values cannot be pickled or copied")


@dataclass(frozen=True)
class Credentials:
    key_id: Secret
    secret_key: Secret


def load_credentials(environ: Mapping[str, str] | None = None) -> Credentials:
    """Read paper-trading credentials from the environment. Raises ConfigError if absent or unsafe.

    Error messages name the variable, never its value.
    """
    env = os.environ if environ is None else environ
    problems: list[str] = []
    values: dict[str, str] = {}
    for var in (KEY_ID_VAR, SECRET_KEY_VAR):
        value = env.get(var)
        if value is None:
            problems.append(f"environment variable {var} is not set")
        elif not value.strip():
            problems.append(f"environment variable {var} is empty")
        elif value != value.strip():
            problems.append(f"environment variable {var} has leading or trailing whitespace (copy-paste?)")
        else:
            values[var] = value
    key_id = values.get(KEY_ID_VAR)
    if key_id is not None and not key_id.startswith(PAPER_KEY_PREFIX):
        problems.append(
            f"{KEY_ID_VAR} is not a paper-trading key (Alpaca paper key IDs start with "
            f"{PAPER_KEY_PREFIX!r}); this project only trades paper"
        )
    if problems:
        raise ConfigError(problems)
    return Credentials(key_id=Secret(values[KEY_ID_VAR]), secret_key=Secret(values[SECRET_KEY_VAR]))


class RedactSecrets(logging.Filter):
    """Logging filter that replaces secret values with ******** in messages and tracebacks.

    Attach it to handlers (handler.addFilter), not loggers: a logger's filters
    don't see records that propagate up from child loggers.
    """

    def __init__(self, *secrets: Secret) -> None:
        super().__init__()
        self._values = tuple(s.reveal() for s in secrets if s.reveal())

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a bad format string must not crash the caller; drop the args instead
            message = f"{record.msg} [log arguments dropped: could not be formatted]"
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        for value in self._values:
            message = message.replace(value, REDACTED)
            if record.exc_text:
                record.exc_text = record.exc_text.replace(value, REDACTED)
            if record.stack_info:
                record.stack_info = record.stack_info.replace(value, REDACTED)
        record.msg, record.args = message, None
        return True
