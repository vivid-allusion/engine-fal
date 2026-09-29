import json
import os
import random
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .datatypes import EngineError, InputFile, OutputFile, ProgressEvent

_MODELS_API = "https://api.fal.ai/v1/models"


class Engine:
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
        self._input_props_cache: dict | None = None

    def run(self, inputs: list[InputFile]) -> list[OutputFile]:
        self._validate_preflight()

        import fal_client

        endpoint = self._profile["endpoint"]
        params = dict(self._profile.get("parameters", {}))
        media_type = self._profile.get("media_type", "")
        self._output_dir.mkdir(parents=True, exist_ok=True)

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
            ts = datetime.now().strftime("%y%m%d_%H%M%S")
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
        try:
            import fal_client  # noqa: F401
        except ImportError:
            raise EngineError(
                "fal-client SDK not installed. Run: pip install fal-client"
            ) from None
        if not self._resolve_api_key():
            raise EngineError(
                f"{self.API_KEY_ENV_VAR} not set in environment or .env file"
            )

    def _resolve_api_key(self) -> str:
        key = self._api_key or os.environ.get(self.API_KEY_ENV_VAR, "")
        if key and self.API_KEY_ENV_VAR not in os.environ:
            os.environ[self.API_KEY_ENV_VAR] = key
        return key

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

    def _request_with_retry(
        self, fal_client, endpoint: str, fal_input: dict, prefix: str
    ) -> Any:
        max_retries = int(self._profile.get("retry_on_429", 4))
        base_delay = float(self._profile.get("retry_base_delay", 2.0))
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return fal_client.subscribe(endpoint, arguments=fal_input)
            except Exception as exc:
                rate = self._classify_error(exc)
                if rate != "retryable" or attempt >= max_retries:
                    raise
                last_exc = exc
                # High-demand outages resolve slower than a plain 429 burst —
                # stretch the delay so we stop hammering the model.
                long_backoff = self._is_high_demand(exc)
                sleep = base_delay * (8 if long_backoff else 2) ** attempt + random.uniform(
                    0, 1
                )
                label = "high-demand outage" if long_backoff else "429 rate limit"
                self._emit(
                    f"{prefix} ⏳ {label}, retry {attempt + 1}/{max_retries} in {sleep:.1f}s"
                )
                time.sleep(sleep)
        raise last_exc

    @classmethod
    def _classify_error(cls, exc: Exception) -> str:
        """Classify a provider exception.

        Returns one of ``"retryable"`` (transient load/rate/throttle), ``"fatal"``
        (billing, auth, quota — re-running will not help), or ``"other"``.
        """
        text = str(exc).lower()
        status = getattr(exc, "status", None)
        if status is None:
            status = getattr(exc, "status_code", None)
        if status in (402, 403, 401, 409):
            return "fatal"
        if any(
            token in text
            for token in (
                "insufficient",
                "payment",
                "billing",
                "balance",
                "quota",
                "no more",
                "unauthorized",
                "forbidden",
            )
        ):
            return "fatal"
        if status == 429:
            return "retryable"
        if cls._is_high_demand(exc):
            return "retryable"
        if any(
            token in text
            for token in ("ratelimit", "rate limit", "throttl", "too many")
        ):
            return "retryable"
        return "other"

    @staticmethod
    def _is_high_demand(exc: Exception) -> bool:
        """Detect provider 'service unavailable' / high-demand spikes."""
        text = str(exc).lower()
        return any(
            token in text
            for token in (
                "high demand",
                "currently unavailable",
                "e003",
                "503",
                "service unavailable",
            )
        )

    def _is_rate_limited(self, exc: Exception) -> bool:
        return self._classify_error(exc) == "retryable"

    def _is_fatal(self, exc: Exception) -> bool:
        return self._classify_error(exc) == "fatal"

    def _build_fal_input(
        self, params: dict, prompt: str, item: InputFile, media_type: str
    ) -> dict:
        props = self._input_props()
        fal_input = {k: self._coerce_param_value(k, v, props) for k, v in params.items()}
        fal_input.pop("prompt_prefix", None)
        fal_input.pop("prompt_suffix", None)
        # fps is vehicle metadata (frame rate), never a provider input.
        fal_input.pop("fps", None)

        fal_input["prompt"] = prompt
        ref_key = self._reference_key()
        if item.reference_urls:
            if self._profile.get("reference_param_multi", False):
                fal_input[ref_key] = list(item.reference_urls)
            else:
                fal_input[ref_key] = self._coerce_media_value(ref_key, item.reference_urls)

        slot_scalar = set(self._profile.get("slot_scalar", []) or [])
        for slot, urls in item.references.items():
            if not urls and slot not in self._required_slots():
                continue
            fal_input[slot] = self._shape_slot_value(slot, urls, slot_scalar)

        duration = item.metadata.get("duration")
        if duration is not None:
            key = str(self._profile.get("duration_param_name", "duration"))
            fal_input[key] = self._coerce_param_value(key, duration, props)

        return fal_input

    def _shape_slot_value(self, key: str, urls: list[str], slot_scalar: set[str]):
        """Shape a named secondary-media slot.

        String-valued slots (``slot_scalar`` in the profile, or a schema that
        says ``string``) receive a single URL; every other named slot is an
        array and receives the full list. Falls back to a list when neither the
        profile nor the live schema says otherwise.
        """
        if not urls:
            return []
        if key in slot_scalar:
            return urls[0]
        param = self._input_props().get(key, {})
        param_type = param.get("type") if isinstance(param, dict) else None
        if param_type == "string":
            return urls[0]
        return list(urls)

    def _reference_key(self) -> str:
        """Primary input key: profile's image_url_param, else reference_param,
        else the provider default image_url."""
        for key in ("image_url_param", "reference_param"):
            value = self._profile.get(key)
            if value:
                return str(value)
        return "image_url"

    def _required_slots(self) -> set[str]:
        return set(self._profile.get("required_slots") or [])

    def _input_props(self) -> dict:
        """Best-effort fetch of the endpoint's Input JSON-schema properties.

        Used to shape media params (single URL vs list) and scalar params
        (int vs float vs string) against the model schema. ``$ref``/``allOf``
        fragments are resolved to concrete type dicts. Cached per Engine
        instance; returns {} when the schema is unavailable.
        """
        if self._input_props_cache is not None:
            return self._input_props_cache
        try:
            props = self._fetch_input_props()
        except Exception:
            props = {}
        if not isinstance(props, dict):
            props = {}
        self._input_props_cache = props
        return props

    def _fetch_input_props(self) -> dict:
        """Fetch the endpoint's OpenAPI schema from the fal models API.

        ``GET /v1/models?endpoint_id=<endpoint>&expand=openapi-3.0`` returns the
        model's OpenAPI document; its POST request body schema names the Input
        properties. The API key is optional but sent when present.
        """
        endpoint = str(self._profile.get("endpoint", ""))
        if not endpoint:
            return {}
        url = f"{_MODELS_API}?" + urllib.parse.urlencode(
            {"endpoint_id": endpoint, "expand": "openapi-3.0"}
        )
        request = urllib.request.Request(url)
        key = self._resolve_api_key()
        if key:
            request.add_header("Authorization", f"Key {key}")
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = payload.get("models") or []
        if not models:
            return {}
        return self._parse_input_props(models[0].get("openapi") or {})

    def _parse_input_props(self, schema: dict) -> dict:
        """Extract Input properties from a fal OpenAPI document."""
        components = (schema.get("components") or {}).get("schemas") or {}
        raw: dict = {}
        ref = self._request_body_ref(schema)
        if ref and isinstance(components.get(ref), dict):
            raw = components[ref].get("properties") or {}
        if not raw:
            for name, body in components.items():
                if name.endswith("Input") and isinstance(body, dict):
                    raw = body.get("properties") or {}
                    break
        return {
            key: self._resolve_schema_refs(value, schema)
            for key, value in raw.items()
            if isinstance(value, dict)
        }

    @staticmethod
    def _request_body_ref(schema: dict) -> str | None:
        """Return the schema name referenced by the POST request body, if any."""
        paths = schema.get("paths") or {}
        for operations in paths.values():
            if not isinstance(operations, dict):
                continue
            post = operations.get("post")
            if not isinstance(post, dict):
                continue
            content = (post.get("requestBody") or {}).get("content") or {}
            for body in content.values():
                ref = ((body or {}).get("schema") or {}).get("$ref")
                if isinstance(ref, str):
                    return ref.lstrip("#/").split("/")[-1]
        return None

    def _resolve_schema_refs(self, node: Any, root: dict) -> Any:
        """Resolve ``$ref``/``allOf`` fragments into a concrete schema dict."""
        if not isinstance(node, dict):
            return node
        resolved: dict = {}
        if "$ref" in node and isinstance(node["$ref"], str):
            target: Any = root
            for part in node["$ref"].lstrip("#/").split("/"):
                target = target.get(part) if isinstance(target, dict) else None
                if target is None:
                    break
            if isinstance(target, dict):
                resolved.update(self._resolve_schema_refs(target, root))
        resolved.update({k: v for k, v in node.items() if k not in ("$ref", "allOf")})
        for fragment in node.get("allOf", []):
            merged = self._resolve_schema_refs(fragment, root)
            if isinstance(merged, dict):
                for k, v in merged.items():
                    resolved.setdefault(k, v)
        return resolved

    def _coerce_param_value(self, key: str, value: Any, props: dict) -> Any:
        """Coerce a scalar parameter against the model's schema type.

        Integer-typed params (duration, fps, ...) receive an int when the
        profile ships a numeric string; number/boolean params coerce
        likewise. Non-convertible values (e.g. token durations like
        ``auto``) pass through verbatim, as do all values when the schema
        is unavailable.
        """
        param = props.get(key, {})
        param_type = param.get("type") if isinstance(param, dict) else None
        if not isinstance(value, str):
            return value
        if param_type == "integer":
            try:
                return int(value)
            except ValueError:
                return value
        if param_type == "number":
            try:
                return float(value)
            except ValueError:
                return value
        if param_type == "boolean":
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "1", "on"}:
                return True
            if lowered in {"false", "no", "0", "off"}:
                return False
        return value

    def _coerce_media_value(self, key: str, urls: list[str]) -> str | list[str]:
        """Shape URLs for a media parameter against the model's schema.

        String-typed params (image_url, start_image_url, ...) receive a single
        URL; array-typed params (image_urls, ...) receive the full list. When
        the schema is unknown, a single URL degrades to a string and multiple
        URLs stay a list.

        Raises:
            EngineError: multiple URLs routed to a string-typed param — the
                provider can only accept one, so the extra media must never
                be silently dropped.
        """
        if not urls:
            return []
        param = self._input_props().get(key, {})
        param_type = param.get("type") if isinstance(param, dict) else None
        if param_type == "array":
            return urls
        if param_type == "string":
            if len(urls) > 1:
                raise EngineError(
                    f"Param '{key}' accepts a single media URL but {len(urls)} "
                    f"were provided — fix the markdown: {urls}"
                )
            return urls[0]
        return urls[0] if len(urls) == 1 else urls

    def _normalize_result(self, result, media_type: str) -> list:
        if not result:
            return []
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            if "images" in result:
                return [img["url"] for img in result["images"]]
            if "video" in result:
                return [result["video"]["url"]]
            if "url" in result:
                return [result["url"]]
        return []

    def _save_results(
        self,
        raw_output,
        media_type: str,
        stem: str,
        idx: int,
        prefix: str = "",
        current: int = 0,
        total: int = 0,
        rel_dir: str = "",
        api_payload: dict | None = None,
    ) -> list[Path]:
        if raw_output is None:
            return []
        if not isinstance(raw_output, list):
            raw_output = [raw_output]

        dest_dir = self._output_dir / rel_dir if rel_dir else self._output_dir
        dest_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%y%m%d_%H%M%S")
        saved = []
        for i, item in enumerate(raw_output):
            if isinstance(item, str):
                self._emit(f"{prefix} ⬇️  Downloading...")
                ext = self._infer_extension(item, media_type)
                suffix = "" if len(raw_output) == 1 else f"_{i}"
                dest = dest_dir / f"{ts}-{stem}-{idx}{suffix}{ext}"
                urllib.request.urlretrieve(item, dest)
                self._emit(
                    f"{prefix} 💾 Saved: {dest.name}",
                    current=current,
                    total=total,
                    saved_path=dest,
                    api_payload=api_payload,
                )
                saved.append(dest)
            elif hasattr(item, "read"):
                self._emit(f"{prefix} ⬇️  Saving file stream...")
                data = item.read()
                ext = self._sniff_extension(data, media_type)
                suffix = "" if len(raw_output) == 1 else f"_{i}"
                dest = dest_dir / f"{ts}-{stem}-{idx}{suffix}{ext}"
                with open(dest, "wb") as f:
                    f.write(data)
                self._emit(
                    f"{prefix} 💾 Saved: {dest.name}",
                    current=current,
                    total=total,
                    saved_path=dest,
                    api_payload=api_payload,
                )
                saved.append(dest)
        return saved

    def _sniff_extension(self, data: bytes, media_type: str) -> str:
        """Determine the real file extension from the stream's magic bytes.

        Providers often return bytes whose type differs from the requested
        format (e.g. JPEG data for a model run with ``output_format`` set) —
        saving it under a guessed ``.png`` name produces files no viewer
        matches by extension. Falls back to the profile's ``output_format``
        parameter, then to the media-type default.
        """
        for magic, ext in (
            (b"\xff\xd8\xff", ".jpg"),
            (b"\x89PNG\r\n\x1a\n", ".png"),
            (b"GIF87a", ".gif"),
            (b"GIF89a", ".gif"),
        ):
            if data.startswith(magic):
                return ext
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp"
        if data[4:8] == b"ftyp":
            return ".mp4"
        output_format = (
            str(self._profile.get("parameters", {}).get("output_format", "") or "")
            .strip()
            .lstrip(".")
            .lower()
        )
        if output_format and all(c.isalnum() for c in output_format):
            return f".{output_format}"
        return ".mp4" if media_type == "video" else ".png"

    def _infer_extension(self, url: str, media_type: str) -> str:
        if media_type == "video":
            return ".mp4"
        for video_ext in (".mp4", ".mov", ".webm"):
            if video_ext in url.lower():
                return video_ext
        return ".png"

    def _emit(
        self,
        message: str,
        level: str = "info",
        current: int = 0,
        total: int = 0,
        saved_path: Path | None = None,
        api_payload: dict | None = None,
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
                )
            )
