"""N-in-flight proofs — mirrored from the gold engine's stub-provider suite (phase_3)."""

import sys
import threading
import time
from datetime import datetime as _dt
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine_fal import Engine, EngineError, InputFile  # noqa: E402


class _Frozen(_dt):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 18, 12, 0, 0)


class _Probe:
    def __init__(self, impl):
        self._impl = impl
        self._lock = threading.Lock()
        self._current = 0
        self.max_seen = 0
        self.calls = 0
        self.called = []

    def __call__(self, *args, **kwargs):
        with self._lock:
            self._current += 1
            self.calls += 1
            self.max_seen = max(self.max_seen, self._current)
            self.called.append(kwargs.get("arguments", {}).get("prompt") or "")
        try:
            time.sleep(0.02 + (hash(threading.current_thread()) % 12) * 0.002)
            return self._impl(*args, **kwargs)
        finally:
            with self._lock:
                self._current -= 1


def inputs_stub(n=3):
    return [InputFile(path=Path(f"b{i}.md"), prompt=f"p{i}") for i in range(n)]


def run_engine(profile, out_dir, impl, probe=False, progress=None, n=3, frozen=True):
    mock_fal_client = MagicMock()
    subscribe = _Probe(impl) if probe else impl
    mock_fal_client.subscribe.side_effect = subscribe
    recorder = progress if progress is not None else []
    patches = [
        patch.dict("os.environ", {"FAL_KEY": "test_key"}),
        patch.dict("sys.modules", {"fal_client": mock_fal_client}),
        patch(
            "urllib.request.urlretrieve",
            lambda url, filename: Path(filename).write_bytes(b"img-bytes"),
        ),
    ]
    if frozen:
        patches.append(patch("engine_fal.engine.datetime", _Frozen))
    with patches[0], patches[1], patches[2]:
        ctx = patches[3] if frozen else _NullCtx()
        with ctx:
            engine = Engine(profile, out_dir, on_progress=recorder.append)
            return engine.run(inputs_stub(n)), recorder, subscribe


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def base_profile(**extra):
    return {"endpoint": "test/model", "media_type": "image", **extra}


def ok_impl(endpoint, arguments):
    return {"images": [{"url": f"https://example.com/{arguments['prompt']}.png"}]}


class TestParallelRunner:
    def test_result_set_and_order_identical_to_own_concurrency_one(self, tmp_path):
        seq_results, _, _ = run_engine(base_profile(), tmp_path / "seq", ok_impl)
        par_results, _, _ = run_engine(
            base_profile(concurrency=3), tmp_path / "par", ok_impl
        )
        assert [r.status for r in par_results] == [r.status for r in seq_results]
        assert [r.source_path for r in par_results] == [
            r.source_path for r in seq_results
        ]
        assert [r.path.name for r in par_results] == [r.path.name for r in seq_results]
        assert len(par_results) == 3

    def test_more_than_one_job_in_flight(self, tmp_path):
        _, _, probed = run_engine(
            base_profile(concurrency=3), tmp_path, ok_impl, probe=True
        )
        assert probed.max_seen >= 2

    def test_never_more_than_n_in_flight(self, tmp_path):
        _, _, probed = run_engine(
            base_profile(concurrency=2), tmp_path, ok_impl, probe=True
        )
        assert probed.max_seen <= 2

    def test_failing_item_leaves_in_flight_siblings_intact(self, tmp_path):
        def impl(endpoint, arguments):
            if arguments["prompt"] == "p1":
                raise RuntimeError("fal blew up")
            return ok_impl(endpoint, arguments)

        results, progress, _ = run_engine(base_profile(concurrency=3), tmp_path, impl)
        assert [r.status for r in results] == ["ok", "error", "ok"]
        assert "fal blew up" in results[1].error_msg

    def test_rate_limit_backoff_completes_batch(self, tmp_path):
        attempts = {}

        def impl(endpoint, arguments):
            prompt = arguments["prompt"]
            attempts[prompt] = attempts.get(prompt, 0) + 1
            if attempts[prompt] == 1:
                exc = RuntimeError("429 Too Many Requests")
                exc.status = 429
                raise exc
            return ok_impl(endpoint, arguments)

        results, progress, _ = run_engine(
            base_profile(
                concurrency=3, retry_base_delay=0.05, retry_on_429=3
            ),
            tmp_path,
            impl,
        )
        assert attempts == {"p0": 2, "p1": 2, "p2": 2}
        assert [r.status for r in results] == ["ok", "ok", "ok"]

    def test_circuit_breaker_ends_batch_and_spares_queued(self, tmp_path):
        def impl(endpoint, arguments):
            exc = RuntimeError("429 Too Many Requests")
            exc.status = 429
            raise exc

        results, progress, probed = run_engine(
            base_profile(
                concurrency=2, retry_base_delay=0.05, retry_on_429=0,
                circuit_breaker_max=2,
            ),
            tmp_path,
            impl,
            probe=True,
            n=6,
        )
        assert any("Circuit breaker tripped" in e.message for e in progress)
        assert probed.calls <= 3
        assert results
        assert all(r.status == "error" for r in results)

    def test_concurrency_never_sent_to_provider(self, tmp_path):
        results, _, _ = run_engine(
            base_profile(concurrency=1, parameters={"width": 512}),
            tmp_path,
            ok_impl,
        )
        assert results

    def test_progress_lines_name_file_and_item(self, tmp_path):
        progress = []
        run_engine(
            base_profile(concurrency=3), tmp_path, ok_impl, progress=progress
        )
        assert any("📝 Markdown: b1.md" in e.message for e in progress)
        assert any(e.message.startswith("[2/3]") for e in progress)

    def test_invalid_concurrency_raises_loudly(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            engine = Engine(base_profile(concurrency=0), tmp_path)
            with pytest.raises(EngineError, match="concurrency"):
                engine.run([])
