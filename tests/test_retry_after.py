"""Retry-After header policy — stub-only proofs (no real wait, no API)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine_fal.retry_policy as rp  # noqa: E402
from engine_fal import Engine  # noqa: E402


class _SleepLog:
    def __init__(self):
        self.sleeps = []

    def __call__(self, seconds):
        self.sleeps.append(seconds)


class _Fake429(Exception):
    def __init__(self, headers=None, status=429, wrap_in_response=False):
        self.status = status
        self.headers = None if wrap_in_response else headers
        if wrap_in_response:
            self.response = type("_Response", (), {"headers": headers})()
        super().__init__("Too many requests")


def run_with(make_exception, base_delay=0.1):
    log = _SleepLog()
    exception = make_exception()
    calls = {"n": 0}

    def subscribe(endpoint, arguments):
        calls["n"] += 1
        if calls["n"] == 1:
            raise exception
        return {"images": [{"url": "https://a.com/ok.png"}]}

    from unittest.mock import MagicMock, patch

    engine = Engine(
        {
            "endpoint": "test/model",
            "media_type": "image",
            "retry_base_delay": base_delay,
            "retry_on_429": 2,
        },
        "/tmp/opencode/retry-after-test",
    )
    engine._api_key = "k"
    client = MagicMock()
    client.subscribe.side_effect = subscribe
    with patch.object(rp.time, "sleep", log), patch.object(
        rp.random, "uniform", lambda *args, **kwargs: 0.0
    ):
        rp.RetryPolicy._request_with_retry(
            engine, client, "test/model", {"prompt": "p"}, "[1/1]"
        )
    return log.sleeps


def test_numeric_retry_after_is_honored():
    sleeps = run_with(lambda: _Fake429({"Retry-After": "0.42"}))
    assert len(sleeps) == 1
    assert sleeps[0] >= 0.42
    assert sleeps[0] < 1.0


def test_numeric_value_typed_as_number_is_honored():
    sleeps = run_with(lambda: _Fake429({"Retry-After": 0.42}))
    assert len(sleeps) == 1
    assert sleeps[0] >= 0.42
    assert sleeps[0] < 1.0


def test_backoff_larger_than_header_wins():
    sleeps = run_with(lambda: _Fake429({"Retry-After": "0.05"}), base_delay=10.0)
    assert sleeps[0] == 10.0


def test_absent_header_keeps_computed_backoff():
    sleeps = run_with(lambda: _Fake429({}))
    assert sleeps[0] == 0.1


def test_malformed_header_is_safe():
    sleeps = run_with(lambda: _Fake429({"Retry-After": "not-a-number"}))
    assert sleeps[0] == 0.1


def test_none_headers_is_safe():
    sleeps = run_with(lambda: _Fake429(None))
    assert sleeps[0] == 0.1


def test_non_mapping_headers_is_safe():
    sleeps = run_with(lambda: _Fake429("<html>no headers here</html>"))
    assert sleeps[0] == 0.1


def test_lowercase_header_name_is_honored():
    sleeps = run_with(lambda: _Fake429({"retry-after": "0.42"}))
    assert len(sleeps) == 1
    assert sleeps[0] >= 0.42
    assert sleeps[0] < 1.0


def test_headers_on_the_provider_response_are_honored():
    sleeps = run_with(
        lambda: _Fake429({"Retry-After": "0.42"}, wrap_in_response=True)
    )
    assert len(sleeps) == 1
    assert sleeps[0] >= 0.42
    assert sleeps[0] < 1.0


def test_http_date_form_is_honored():
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone

    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    sleeps = run_with(lambda: _Fake429({"Retry-After": format_datetime(when)}))
    assert len(sleeps) == 1
    assert 20 <= sleeps[0] <= 31


def test_malformed_http_date_is_safe():
    sleeps = run_with(lambda: _Fake429({"Retry-After": "Wed, 99 Hex 2026 99:99:99 FOO"}))
    assert sleeps[0] == 0.1


def test_header_helpers_never_raise():
    assert rp._retry_after_seconds(RuntimeError("no headers")) is None
    assert rp._retry_after_seconds(_Fake429(None)) is None
    assert rp._retry_after_seconds(_Fake429(["Retry-After"])) is None
