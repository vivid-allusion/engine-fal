import importlib
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .datatypes import EngineError, InputFile, OutputFile, ProgressEvent
from .input_builder import FalInputBuilder
from .output_handler import FalOutputHandler
from .retry_policy import RetryPolicy


class Engine(FalInputBuilder, FalOutputHandler, RetryPolicy):
    PLATFORM: str = "fal"
    PROVIDER_NAME: str = "Fal.ai"
    PROVIDER_HOMEPAGE: str = "https://fal.ai"
    API_KEY_ENV_VAR: str = "FAL_KEY"
    API_KEY_PATTERN: str = r"^[A-Za-z0-9-]{20,}$"

    def __init__(
        self,
        profile: dict,
        output_dir: str | Path,
        api_key: str | None = None,
        on_progress: Callable[[str], None] | None = None,
    ):
        self._profile = profile
        self._output_dir = Path(output_dir)
        self._api_key = api_key
        self._on_progress = on_progress
        self._prefix = profile.get("prompt_prefix", "")
        self._suffix = profile.get("prompt_suffix", "")
        self._concurrency = profile.get("concurrency", 1)
        self._input_props_cache: dict | None = None

    def run(self, inputs: list[InputFile]) -> list[OutputFile]:
        self._validate_preflight()

        import fal_client

        endpoint = self._profile["endpoint"]
        params = dict(self._profile.get("parameters", {}))
        media_type = self._profile.get("media_type", "")
        self._output_dir.mkdir(parents=True, exist_ok=True)

        if self._concurrency > 1:
            from . import runner

            return runner.run_batch(self, inputs)

        results: list[OutputFile] = []
        total = len(inputs)
        consecutive_conflicts = 0
        circuit_max = int(self._profile.get("circuit_breaker_max", 3))

        for idx, item in enumerate(inputs):
            stem = item.path.stem
            current = idx + 1
            prefix = f"[{current}/{total}]"
            rel_dir = str(item.metadata.get("relative_dir", "") or "")
            dest_dir = self._output_dir / rel_dir if rel_dir else self._output_dir
            ts = self._now_string()
            ext = self._default_extension(media_type)
            expected = dest_dir / f"{ts}-{stem}-{idx}{ext}"

            delay = float(self._profile.get("delay_between_requests", 0.0))
            if idx > 0 and delay > 0:
                time.sleep(delay)

            self._emit(f"{prefix} 📡 Calling Fal.ai API...")
            prompt = f"{self._prefix}{item.prompt}{self._suffix}".strip()
            if not prompt:
                output = OutputFile(
                    source_path=item.path,
                    status="error",
                    error_msg="Empty prompt after applying prefix/suffix",
                    media_type=media_type,
                    expected_path=expected,
                )
                results.append(output)
                continue

            self._emit(f"{prefix} 📝 Markdown: {item.path.name}")

            fal_input: dict | None = None
            try:
                fal_input = self._build_fal_input(params, prompt, item, media_type)
                raw_result = self._request_with_retry(
                    fal_client, endpoint, fal_input, prefix
                )
                self._emit(f"{prefix} ✅ Response received")
                self._emit(f"{prefix} 📦 Payload: {fal_input}")

                raw_output = self._normalize_result(raw_result, media_type)
                saved = self._save_results(
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
                    output = OutputFile(
                        source_path=item.path,
                        status="error",
                        error_msg="No output returned from fal.ai",
                        media_type=media_type,
                        expected_path=expected,
                    )
                else:
                    consecutive_conflicts = 0
                    for saved_path in saved:
                        results.append(
                            OutputFile(
                                source_path=item.path,
                                path=saved_path,
                                status="ok",
                                media_type=media_type,
                                metadata={"api_payload": fal_input},
                            )
                        )
                    continue

            except Exception as exc:
                self._emit(
                    f"{prefix} Error: {exc}",
                    level="error",
                    current=current,
                    total=total,
                    api_payload=fal_input,
                )
                output = OutputFile(
                    source_path=item.path,
                    status="error",
                    error_msg=str(exc),
                    media_type=media_type,
                    expected_path=expected,
                )
                # A billing/auth failure will reject every remaining item —
                # stop the batch instead of churning through all of them.
                if self._is_fatal(exc):
                    self._emit(
                        f"{prefix} 🛑 Fatal provider error — aborting batch. "
                        f"Check payment, quota, or API key. ({exc})",
                        level="error",
                    )
                    results.append(output)
                    break
                # Transient conflicts exhausted per-item retries. Trip a
                # circuit breaker after N consecutive ones instead of grinding
                # the whole batch since it may fail the same way repeatedly.
                if self._is_rate_limited(exc):
                    consecutive_conflicts += 1
                    if consecutive_conflicts >= circuit_max:
                        self._emit(
                            f"{prefix} 🛑 Circuit breaker tripped after "
                            f"{consecutive_conflicts} consecutive rate-limited "
                            f"failures — aborting batch.",
                            level="error",
                        )
                        results.append(output)
                        break
                else:
                    consecutive_conflicts = 0

            results.append(output)

        return results

    def _validate_preflight(self):
        if not self._profile.get("endpoint"):
            raise EngineError("Missing or empty 'endpoint' in profile")
        if (
            isinstance(self._concurrency, bool)
            or not isinstance(self._concurrency, int)
            or self._concurrency < 1
        ):
            raise EngineError(
                f"Invalid 'concurrency' in profile: {self._concurrency!r} — "
                "must be a whole number >= 1"
            )
        try:
            importlib.import_module("fal_client")
        except ImportError:
            raise EngineError(
                "fal-client SDK not installed. Run: pip install fal-client"
            ) from None
        if not self._resolve_api_key():
            raise EngineError(
                f"{self.API_KEY_ENV_VAR} not set in environment or .env file"
            )

    def _now_string(self) -> str:
        return datetime.now().strftime("%y%m%d_%H%M%S")

    def _default_extension(self, media_type: str) -> str:
        """Profile-driven extension used for precomputed output names."""
        output_format = (
            str(self._profile.get("parameters", {}).get("output_format", "") or "")
            .strip()
            .lstrip(".")
            .lower()
        )
        if output_format and all(c.isalnum() for c in output_format):
            return f".{output_format}"
        return ".mp4" if media_type == "video" else ".png"

    def _emit(
        self,
        message: str,
        level: str = "info",
        current: int = 0,
        total: int = 0,
        saved_path: Path | None = None,
        api_payload: dict | None = None,
        final_path: Path | None = None,
    ):
        if self._on_progress:
            self._on_progress(
                ProgressEvent(
                    message=message,
                    level=level,
                    current=current,
                    total=total,
                    saved_path=saved_path,
                    api_payload=api_payload,
                    final_path=final_path,
                )
            )
