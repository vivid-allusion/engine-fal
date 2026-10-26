"""N-in-flight execution — mirrors the gold engine's runner.

`run_batch` caps jobs in flight structurally: at most the profile's
`concurrency` futures are submitted at any moment. Per-item behaviour
reuses this engine's existing machinery verbatim (`_build_fal_input`,
`_request_with_retry`, `_save_results`, error classification) — the same
retry, backoff and circuit-breaker semantics as the sequential loop.
Results are written by input index; a failing item never disturbs jobs
still in flight; once a fatal error or the breaker ends the batch, no
queued item is submitted. Every failed item writes the same verbose
`_ERROR.log` the sequential loop writes.
"""

import concurrent.futures
import threading
import time
import traceback

from .datatypes import OutputFile
from .output_handler import _output_stem, _write_error_log


class BatchState:
    def __init__(self, circuit_max: int):
        self.circuit_max = circuit_max
        self.consecutive_conflicts = 0
        self._lock = threading.Lock()
        self._stop = False

    def is_stopped(self) -> bool:
        with self._lock:
            return self._stop

    def halt(self):
        with self._lock:
            self._stop = True

    def note_success(self):
        with self._lock:
            if not self._stop:
                self.consecutive_conflicts = 0

    def note_rate_limited(self) -> bool:
        with self._lock:
            self.consecutive_conflicts += 1
            return self.consecutive_conflicts >= self.circuit_max


def run_item(engine, fal_client, item, idx, total, state) -> list[OutputFile]:
    stem = item.path.stem
    current = idx + 1
    prefix = f"[{current}/{total}]"
    media_type = engine._profile.get("media_type", "")
    rel_dir = str(item.metadata.get("relative_dir", "") or "")
    dest_dir = engine._output_dir / rel_dir if rel_dir else engine._output_dir
    base = _output_stem(stem, engine._now_string())
    ext = engine._default_extension(media_type)
    expected = dest_dir / f"{base}-{idx}{ext}"

    def error_log(stage: str, error: str, payload: dict | None, tb: str = "n/a"):
        return _write_error_log(
            dest_dir,
            base,
            idx,
            platform=engine.PLATFORM,
            item=item,
            current=current,
            total=total,
            expected=expected,
            profile=engine._profile,
            prompt=prompt,
            stage=stage,
            error=error,
            traceback_text=tb,
            api_payload=payload,
        )

    engine._emit(f"{prefix} 📡 Calling {engine.PROVIDER_NAME} API...")
    prompt = f"{engine._prefix}{item.prompt}{engine._suffix}".strip()
    if not prompt:
        reason = "Empty prompt after applying prefix/suffix"
        log_path = error_log("empty_prompt", reason, None)
        return [
            OutputFile(
                source_path=item.path,
                status="error",
                error_msg=reason,
                media_type=media_type,
                expected_path=expected,
                metadata=engine._error_log_metadata(log_path),
            )
        ]

    engine._emit(f"{prefix} 📝 Markdown: {item.path.name}")

    fal_input: dict | None = None
    stage = "build_input"
    try:
        fal_input = engine._build_fal_input(
            dict(engine._profile.get("parameters", {})), prompt, item, media_type
        )
        stage = "provider_call"
        raw_result = engine._request_with_retry(
            fal_client, engine._profile["endpoint"], fal_input, prefix
        )
        engine._emit(f"{prefix} ✅ Response received")
        engine._emit(f"{prefix} 📦 Payload: {fal_input}")

        stage = "save"
        raw_output = engine._normalize_result(raw_result, media_type)
        saved = engine._save_results(
            raw_output,
            media_type,
            stem,
            idx,
            prefix,
            current,
            total,
            rel_dir,
            fal_input,
        )
        if not saved:
            reason = "No output returned from fal.ai"
            log_path = error_log("no_output", reason, fal_input)
            return [
                OutputFile(
                    source_path=item.path,
                    status="error",
                    error_msg=reason,
                    media_type=media_type,
                    expected_path=expected,
                    metadata=engine._error_log_metadata(log_path),
                )
            ]
        state.note_success()
        return [
            OutputFile(
                source_path=item.path,
                path=saved_path,
                status="ok",
                media_type=media_type,
                metadata={"api_payload": fal_input},
            )
            for saved_path in saved
        ]
    except Exception as exc:
        engine._emit(
            f"{prefix} Error: {exc}",
            level="error",
            current=current,
            total=total,
            api_payload=fal_input,
        )
        log_path = error_log(
            stage, f"{type(exc).__name__}: {exc}", fal_input, traceback.format_exc()
        )
        output = OutputFile(
            source_path=item.path,
            status="error",
            error_msg=str(exc),
            media_type=media_type,
            expected_path=expected,
            metadata=engine._error_log_metadata(log_path),
        )
        if engine._is_fatal(exc):
            engine._emit(
                f"{prefix} 🛑 Fatal provider error — aborting batch. "
                f"Check payment, quota, or API key. ({exc})",
                level="error",
            )
            state.halt()
            return [output]
        if engine._is_rate_limited(exc):
            tripped = state.note_rate_limited()
            if tripped:
                engine._emit(
                    f"{prefix} 🛑 Circuit breaker tripped after "
                    f"{state.consecutive_conflicts} consecutive rate-limited "
                    f"failures — aborting batch.",
                    level="error",
                )
                state.halt()
                return [output]
        else:
            state.note_success()
        return [output]


def run_batch(engine, inputs) -> list[OutputFile]:
    import fal_client

    engine._output_dir.mkdir(parents=True, exist_ok=True)

    total = len(inputs)
    state = BatchState(int(engine._profile.get("circuit_breaker_max", 3)))
    results: list[list[OutputFile] | None] = [None] * total
    inflight_cap = max(1, engine._concurrency)
    delay = float(engine._profile.get("delay_between_requests", 0.0))

    with concurrent.futures.ThreadPoolExecutor(max_workers=inflight_cap) as pool:
        pending: dict[concurrent.futures.Future, int] = {}
        next_idx = 0
        while pending or (next_idx < total and not state.is_stopped()):
            while (
                not state.is_stopped()
                and next_idx < total
                and len(pending) < inflight_cap
            ):
                if next_idx > 0 and delay > 0:
                    time.sleep(delay)
                future = pool.submit(
                    run_item,
                    engine,
                    fal_client,
                    inputs[next_idx],
                    next_idx,
                    total,
                    state,
                )
                pending[future] = next_idx
                next_idx += 1
            if not pending:
                break
            done, _ = concurrent.futures.wait(pending, return_when="FIRST_COMPLETED")
            for future in done:
                results[pending.pop(future)] = future.result()

    return [out for item in results if item is not None for out in item]
