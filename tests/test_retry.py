"""Test the retry/backoff utility."""

from __future__ import annotations

import requests
from unittest.mock import MagicMock

from pipeline.retry import retry_api_call, _is_openai_transient


def test_retry_succeeds_first_try():
    @retry_api_call(max_retries=3, base_delay=0.01)
    def good():
        return "ok"

    assert good() == "ok"


def test_retry_succeeds_after_transient_failure():
    call_count = 0

    @retry_api_call(max_retries=3, base_delay=0.01)
    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise requests.exceptions.ConnectionError("connection reset")
        return "recovered"

    assert flaky() == "recovered"
    assert call_count == 3


def test_retry_exhausted_raises():
    @retry_api_call(max_retries=2, base_delay=0.01)
    def always_fail():
        raise requests.exceptions.Timeout("timed out")

    try:
        always_fail()
        assert False, "Should have raised"
    except requests.exceptions.Timeout:
        pass


def test_non_transient_error_not_retried():
    call_count = 0

    @retry_api_call(max_retries=3, base_delay=0.01)
    def bad():
        nonlocal call_count
        call_count += 1
        raise ValueError("not transient")

    try:
        bad()
    except ValueError:
        pass
    assert call_count == 1


def test_http_429_retried():
    call_count = 0

    @retry_api_call(max_retries=2, base_delay=0.01)
    def rate_limited():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            resp = MagicMock()
            resp.status_code = 429
            resp.headers = {"Retry-After": "0.01"}
            raise requests.exceptions.HTTPError(response=resp)
        return "ok"

    assert rate_limited() == "ok"
    assert call_count == 2


def test_is_openai_transient():
    class RateLimitError(Exception):
        pass
    class APIConnectionError(Exception):
        pass
    class ValueError_(Exception):
        pass

    assert _is_openai_transient(RateLimitError("rate limited")) is True
    assert _is_openai_transient(APIConnectionError("connection")) is True
    assert _is_openai_transient(ValueError_("some error")) is False
