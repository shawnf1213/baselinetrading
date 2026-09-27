import copy
import io
import logging
import pickle

import pytest

from baselinetrading.config import ConfigError
from baselinetrading.credentials import REDACTED, RedactSecrets, load_credentials

KEY_ID = "PKTESTKEY0123456789"
SECRET = "s3cr3t-value-that-must-never-print"
PAPER_ENV = {"APCA_API_KEY_ID": KEY_ID, "APCA_API_SECRET_KEY": SECRET}


def test_paper_credentials_load():
    credentials = load_credentials(PAPER_ENV)
    assert credentials.key_id.reveal() == KEY_ID
    assert credentials.secret_key.reveal() == SECRET


def test_credentials_never_show_in_text():
    credentials = load_credentials(PAPER_ENV)
    for text in (
        repr(credentials),
        str(credentials),
        f"{credentials}",
        f"{credentials.secret_key}",
        "%s %r" % (credentials.key_id, credentials.secret_key),
    ):
        assert SECRET not in text
        assert KEY_ID not in text


def test_secrets_cannot_be_pickled_or_copied():
    secret = load_credentials(PAPER_ENV).secret_key
    with pytest.raises(TypeError):
        pickle.dumps(secret)
    with pytest.raises(TypeError):
        copy.deepcopy(secret)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, "APCA_API_KEY_ID is not set"),
        ({"APCA_API_KEY_ID": KEY_ID}, "APCA_API_SECRET_KEY is not set"),
        ({**PAPER_ENV, "APCA_API_SECRET_KEY": "   "}, "APCA_API_SECRET_KEY is empty"),
        ({**PAPER_ENV, "APCA_API_SECRET_KEY": SECRET + "\n"}, "leading or trailing whitespace"),
        ({**PAPER_ENV, "APCA_API_KEY_ID": "AKLIVEKEY0123456789"}, "not a paper-trading key"),
    ],
)
def test_missing_malformed_or_live_credentials_are_refused(env, expected):
    with pytest.raises(ConfigError) as caught:
        load_credentials(env)
    message = str(caught.value)
    assert expected in message
    for value in env.values():
        if value.strip():
            assert value.strip() not in message


def test_log_filter_redacts_messages_arguments_and_tracebacks():
    credentials = load_credentials(PAPER_ENV)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactSecrets(credentials.key_id, credentials.secret_key))
    logger = logging.getLogger("tests.redaction")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        logger.info("connecting with key %s", KEY_ID)
        logger.warning(f"raw secret in an f-string: {SECRET}")
        try:
            raise RuntimeError(f"server echoed {SECRET}")
        except RuntimeError:
            logger.exception("request failed")
        logger.info("bad format string %s %s", "only one argument")
    finally:
        logger.removeHandler(handler)
    output = stream.getvalue()
    assert SECRET not in output
    assert KEY_ID not in output
    assert output.count(REDACTED) == 3
    assert "RuntimeError" in output
    assert "log arguments dropped" in output
