import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .datatypes import EngineError, InputFile, OutputFile, ProgressEvent


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
        on_progress: "Callable[[str], None] | None" = None,
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

        for idx, item in enumerate(inputs):
            stem = item.path.stem
            current = idx + 1
            prefix = f"[{current}/{total}]"
            rel_dir = str(item.metadata.get("relative_dir", "") or "")

            self._emit(f"{prefix} 📡 Calling Fal.ai API...")
            prompt = f"{self._prefix}{item.prompt}{self._suffix}".strip()
            if not prompt:
                output = OutputFile(
                    source_path=item.path,
                    status="error",
                    error_msg="Empty prompt after applying prefix/suffix",
                    media_type=media_type,
                )
                results.append(output)
                continue

            try:
                fal_input = self._build_fal_input(
                    params, prompt, item, media_type
                )
                raw_result = fal_client.subscribe(endpoint, arguments=fal_input)
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
                    )
                else:
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
                self._emit(f"{prefix} Error: {exc}", level="error", current=current, total=total)
                output = OutputFile(
                    source_path=item.path,
                    status="error",
                    error_msg=str(exc),
                    media_type=media_type,
                )

            results.append(output)

        return results

    def _validate_preflight(self):
        if not self._profile.get("endpoint"):
            raise EngineError("Missing or empty 'endpoint' in profile")
        try:
            import fal_client
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

    def _build_fal_input(
        self, params: dict, prompt: str, item: InputFile, media_type: str
    ) -> dict:
        fal_input = dict(params)
        fal_input.pop("prompt_prefix", None)
        fal_input.pop("prompt_suffix", None)

        if any(isinstance(value, str) for value in fal_input.values()):
            props = self._input_props()
            fal_input = {
                key: self._coerce_param_value(key, value, props)
                for key, value in fal_input.items()
            }

        ref_key = self._profile.get("reference_param", "image_url")
        reference_multi = self._profile.get("reference_param_multi", False)

        fal_input["prompt"] = prompt
        if item.reference_urls:
            fal_input[ref_key] = (
                list(item.reference_urls) if reference_multi else item.reference_urls[0]
            )

        return fal_input

    def _input_props(self) -> dict:
        """Best-effort fetch of the endpoint's Input JSON-schema properties.

        Used to coerce scalar params (integer/number/boolean) that
        wizard-composed profiles ship as strings. ``$ref``/``allOf``
        fragments are resolved to concrete type dicts. Cached per Engine
        instance; returns {} when the schema is unavailable.
        """
        if self._input_props_cache is not None:
            return self._input_props_cache
        props: dict = {}
        try:
            endpoint = str(self._profile.get("endpoint", ""))
            url = (
                "https://fal.ai/api/openapi/queue/openapi.json?endpoint_id="
                f"{urllib.parse.quote(endpoint)}"
            )
            with urllib.request.urlopen(url, timeout=10) as resp:
                schema = json.loads(resp.read().decode("utf-8"))
            raw = self._input_schema_properties(schema)
            props = {
                key: self._resolve_schema_refs(value, schema)
                for key, value in raw.items()
                if isinstance(value, dict)
            }
        except Exception:
            props = {}
        if not isinstance(props, dict):
            props = {}
        self._input_props_cache = props
        return props

    def _input_schema_properties(self, schema: dict) -> dict:
        """Return the Input schema properties, tolerating any schema name."""
        schemas = schema.get("components", {}).get("schemas", {}) or {}
        for name in ("Input", *schemas.keys()):
            entry = schemas.get(name)
            if (
                name.endswith("Input")
                and isinstance(entry, dict)
                and isinstance(entry.get("properties"), dict)
            ):
                return entry["properties"]
        return {}

    def _resolve_schema_refs(self, node, root: dict):
        """Resolve ``$ref``/``allOf`` fragments into a concrete schema dict."""
        if not isinstance(node, dict):
            return node
        resolved: dict = {}
        if "$ref" in node and isinstance(node["$ref"], str):
            target = root
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
                for key, value in merged.items():
                    resolved.setdefault(key, value)
        return resolved

    def _coerce_param_value(self, key: str, value, props: dict):
        """Coerce a scalar parameter against the endpoint's schema type.

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
                ext = ".mp4" if media_type == "video" else ".png"
                suffix = "" if len(raw_output) == 1 else f"_{i}"
                dest = dest_dir / f"{ts}-{stem}-{idx}{suffix}{ext}"
                with open(dest, "wb") as f:
                    f.write(item.read())
                self._emit(
                    f"{prefix} 💾 Saved: {dest.name}",
                    current=current,
                    total=total,
                    saved_path=dest,
                    api_payload=api_payload,
                )
                saved.append(dest)
        return saved

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
