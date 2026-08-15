import os
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

                raw_output = self._normalize_result(raw_result, media_type)
                saved = self._save_results(raw_output, media_type, stem, idx, prefix, current, total)
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

        ref_key = self._profile.get("reference_param", "image_url")

        fal_input["prompt"] = prompt
        if item.reference_urls:
            fal_input[ref_key] = item.reference_urls[0]

        return fal_input

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

    def _save_results(self, raw_output, media_type: str, stem: str, idx: int, prefix: str = "", current: int = 0, total: int = 0) -> list[Path]:
        if raw_output is None:
            return []
        if not isinstance(raw_output, list):
            raw_output = [raw_output]

        ts = datetime.now().strftime("%y%m%d_%H%M%S")
        saved = []
        for i, item in enumerate(raw_output):
            if isinstance(item, str):
                self._emit(f"{prefix} ⬇️  Downloading...")
                ext = self._infer_extension(item, media_type)
                suffix = "" if len(raw_output) == 1 else f"_{i}"
                dest = self._output_dir / f"{ts}-{stem}-{idx}{suffix}{ext}"
                urllib.request.urlretrieve(item, dest)
                self._emit(f"{prefix} 💾 Saved: {dest.name}", current=current, total=total)
                saved.append(dest)
            elif hasattr(item, "read"):
                self._emit(f"{prefix} ⬇️  Saving file stream...")
                ext = ".mp4" if media_type == "video" else ".png"
                suffix = "" if len(raw_output) == 1 else f"_{i}"
                dest = self._output_dir / f"{ts}-{stem}-{idx}{suffix}{ext}"
                with open(dest, "wb") as f:
                    f.write(item.read())
                self._emit(f"{prefix} 💾 Saved: {dest.name}", current=current, total=total)
                saved.append(dest)
        return saved

    def _infer_extension(self, url: str, media_type: str) -> str:
        if media_type == "video":
            return ".mp4"
        for video_ext in (".mp4", ".mov", ".webm"):
            if video_ext in url.lower():
                return video_ext
        return ".png"

    def _emit(self, message: str, level: str = "info", current: int = 0, total: int = 0):
        if self._on_progress:
            self._on_progress(ProgressEvent(message=message, level=level, current=current, total=total))
