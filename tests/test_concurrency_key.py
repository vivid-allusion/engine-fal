"""Regression gate: `concurrency` defaults to 1, absent = 1, never sent."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine_fal import Engine, EngineError, InputFile  # noqa: E402
from test_engine import _FakeError  # noqa: E402


class TestConcurrencyKey:
    """Absent `concurrency` behaves exactly like `concurrency: 1`."""

    @staticmethod
    def _frozen_datetime():
        from datetime import datetime as _dt

        class _Frozen(_dt):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 26, 12, 0, 0)

        return _Frozen

    @classmethod
    def _run_batch(cls, profile, out_dir):
        mock_fal = MagicMock()
        mock_client = MagicMock()
        mock_client.subscribe.side_effect = [
            {"images": [{"url": "https://a.com/ok-1.png"}]},
            RuntimeError("fail"),
            {"images": [{"url": "https://a.com/ok-3.png"}]},
        ]
        mock_fal.subscribe = mock_client.subscribe
        progress = []

        def fake_urlretrieve(url, target):
            Path(target).write_bytes(b"\x89PNG\r\n\x1a\n")

        with patch.dict("os.environ", {"FAL_KEY": "test_key_1234567890"}):
            with patch.dict("sys.modules", {"fal_client": mock_fal}):
                with patch("urllib.request.urlretrieve", fake_urlretrieve):
                    with patch(
                        "engine_fal.engine.datetime", cls._frozen_datetime()
                    ), patch(
                        "engine_fal.output_handler.datetime", cls._frozen_datetime()
                    ):
                        engine = Engine(profile, out_dir, on_progress=progress.append)
                        results = engine.run(
                            [
                                InputFile(path=Path(f"b{i}.md"), prompt=f"p{i}")
                                for i in range(3)
                            ]
                        )
        calls = [call.kwargs["arguments"] for call in mock_client.subscribe.call_args_list]
        return results, progress, calls

    def test_absent_equals_one_batch_identical(self, tmp_path):
        base = {
            "endpoint": "test/model",
            "media_type": "image",
            "parameters": {"seed": 512},
        }
        r0, p0, c0 = self._run_batch(base, tmp_path / "absent")
        r1, p1, c1 = self._run_batch({**base, "concurrency": 1}, tmp_path / "one")
        assert len(r0) == len(r1) == 3
        assert [r.status for r in r0] == [r.status for r in r1]
        assert [r.error_msg for r in r0] == [r.error_msg for r in r1]
        assert [r.source_path for r in r0] == [r.source_path for r in r1]
        names0 = [(r.path or r.expected_path).name for r in r0]
        names1 = [(r.path or r.expected_path).name for r in r1]
        assert names0 == names1
        assert [(e.message, e.level, e.current, e.total) for e in p0] == [
            (e.message, e.level, e.current, e.total) for e in p1
        ]
        assert c0 == c1
        statuses = [r.status for r in r0]
        assert statuses.count("ok") == 2 and statuses.count("error") == 1

    def test_concurrency_never_sent_to_provider(self, tmp_path):
        profile = {
            "endpoint": "test/model",
            "media_type": "image",
            "concurrency": 1,
            "parameters": {"seed": 512},
        }
        _, _, calls = self._run_batch(profile, tmp_path)
        assert calls
        assert all("concurrency" not in payload for payload in calls)

    def test_fatal_error_identity_absent_vs_one(self, tmp_path):
        profiles = [
            {"endpoint": "test/model", "media_type": "image"},
            {"endpoint": "test/model", "media_type": "image", "concurrency": 1},
        ]
        outcomes = []
        for i, profile in enumerate(profiles):
            mock_fal = MagicMock()
            mock_client = MagicMock()
            mock_client.subscribe.side_effect = _FakeError(
                status=402, detail="Payment required"
            )
            mock_fal.subscribe = mock_client.subscribe
            progress = []
            with patch.dict("os.environ", {"FAL_KEY": "test_key_1234567890"}):
                with patch.dict("sys.modules", {"fal_client": mock_fal}):
                    engine = Engine(
                        profile, tmp_path / f"fatal{i}", on_progress=progress.append
                    )
                    results = engine.run(
                        [InputFile(path=Path(f"{n}.md"), prompt="p") for n in range(3)]
                    )
            outcomes.append(
                (
                    [r.status for r in results],
                    [r.error_msg for r in results],
                    [(e.message, e.level) for e in progress],
                    mock_client.subscribe.call_count,
                )
            )
        assert outcomes[0] == outcomes[1]
        assert outcomes[0][3] == 1

    @pytest.mark.parametrize("bad", [0, -1, "two", True, 1.5])
    def test_invalid_concurrency_raises_loudly(self, tmp_path, bad):
        with patch.dict("os.environ", {"FAL_KEY": "test_key_1234567890"}):
            mock_fal = MagicMock()
            with patch.dict("sys.modules", {"fal_client": mock_fal}):
                engine = Engine(
                    {
                        "endpoint": "test/model",
                        "media_type": "image",
                        "concurrency": bad,
                    },
                    tmp_path,
                )
                with pytest.raises(EngineError, match="concurrency"):
                    engine.run([])

    def test_valid_values_parse_and_default(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key_1234567890"}):
            mock_fal = MagicMock()
            with patch.dict("sys.modules", {"fal_client": mock_fal}):
                absent = Engine(
                    {"endpoint": "test/model", "media_type": "image"}, tmp_path
                )
                explicit = Engine(
                    {
                        "endpoint": "test/model",
                        "media_type": "image",
                        "concurrency": 2,
                    },
                    tmp_path,
                )
                assert absent._concurrency == 1
                assert explicit._concurrency == 2
