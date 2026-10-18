"""Stub-only Retry-After proofs for fal's existing retry loop."""

import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine_fal import Engine


@pytest.mark.parametrize(
    ("header", "lower", "upper"),
    [
        ("0.5", 0.5, None),
        (
            format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30)),
            20,
            31,
        ),
    ],
)
def test_retry_after_is_never_shortened(tmp_path, header, lower, upper):
    engine = Engine(
        {"endpoint": "test/model", "retry_on_429": 1, "retry_base_delay": 0},
        tmp_path,
        api_key="test_key",
    )
    error = RuntimeError("429 Too Many Requests")
    error.status = 429
    error.response = SimpleNamespace(headers={"Retry-After": header})
    client = MagicMock()
    client.subscribe.side_effect = [error, {"ok": True}]
    with patch("engine_fal.engine.time.sleep") as sleep, patch(
        "engine_fal.engine.random.uniform", return_value=0
    ):
        result = engine._request_with_retry(client, "test/model", {}, "[1/1] x.md")
    assert result == {"ok": True}
    assert client.subscribe.call_count == 2
    assert sleep.call_count == 1
    delay = sleep.call_args.args[0]
    assert delay >= lower
    if upper is not None:
        assert delay <= upper
