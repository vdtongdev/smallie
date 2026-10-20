import os

import pytest
from langsmith import utils as ls_utils

from app.core.config import Settings, export_tracing

KEYS = ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT")


@pytest.fixture(autouse=True)
def clean_env():
    saved = {key: os.environ.pop(key, None) for key in KEYS}
    ls_utils.get_env_var.cache_clear()
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    ls_utils.get_env_var.cache_clear()


def test_tracing_is_exported_for_the_sdk():
    assert not ls_utils.tracing_is_enabled()  # read once, so the SDK's cache holds "off"
    export_tracing(Settings(_env_file=None, langsmith_tracing=True, langsmith_api_key="lsv2-test"))
    assert ls_utils.tracing_is_enabled()
    assert os.environ["LANGSMITH_PROJECT"] == "smallie"


@pytest.mark.parametrize("fields", [{}, {"langsmith_tracing": True}])
def test_tracing_stays_off_by_default_and_without_a_key(fields):
    export_tracing(Settings(_env_file=None, **fields))
    assert not any(key in os.environ for key in KEYS)
    assert not ls_utils.tracing_is_enabled()
