import os
import urllib.request
import uuid
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

        import fal

        client = fal.Client(key=self._resolve_api_key())
        endpoint = self._profile["endpoint"]
        params = dict(self._profile.get("parameters", {}))
        media_type = self._profile.get("media_type", "")
        self._output_dir.mkdir(parents=True, exist_ok=True)

        results: list[OutputFile] = []
        total = len(inputs)

        for idx, item in enumerate(inputs):
            self._emit(f"Processing bullet {idx + 1}/{total}...")
            prompt = f"{self._prefix}{item.prompt}{self._suffix}".strip()
            if not prompt:
                output = OutputFile(
                    bullet_path=item.path,
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
                raw_output = client.run(endpoint, arguments=fal_input)

                saved = self._save_results(raw_output, media_type)
                if not saved:
                    output = OutputFile(
                        bullet_path=item.path,
                        status="error",
                        error_msg="No output returned from fal",
                        media_type=media_type,
                    )
                else:
                    for saved_path in saved:
                        results.append(
                            OutputFile(
                                bullet_path=item.path,
                                path=saved_path,
                                status="ok",
                                media_type=media_type,
                            )
                        )
                    continue

            except Exception as exc:
                output = OutputFile(
                    bullet_path=item.path,
                    status="error",
                    error_msg=str(exc),
                    media_type=media_type,
                )

            results.append(output)

        return results

    def _validate_preflight(self):
        if "endpoint" not in self._profile:
            raise EngineError("Missing 'endpoint' in profile")
        try:
            import fal
        except ImportError:
            raise EngineError(
                "fal SDK not installed. Run: pip install fal"
            ) from None
        if not self._resolve_api_key():
            raise EngineError(
                f"{self.API_KEY_ENV_VAR} not set in environment or .env file"
            )

    def _resolve_api_key(self) -> str:
        if self._api_key:
            return self._api_key
        return os.environ.get(self.API_KEY_ENV_VAR, "")

    def _build_fal_input(
        self, params: dict, prompt: str, item: InputFile, media_type: str
    ) -> dict:
        fal_input = dict(params)
        fal_input.pop("prompt_prefix", None)
        fal_input.pop("prompt_suffix", None)

        if media_type == "image":
            fal_input["prompt"] = prompt
            if item.reference_urls:
                fal_input["image_url"] = item.reference_urls[0]
        elif media_type == "video":
            fal_input["prompt"] = prompt
            if item.reference_urls:
                fal_input["image_url"] = item.reference_urls[0]
            for key in ("duration", "fps"):
                if key in item.metadata:
                    fal_input[key] = item.metadata[key]
        else:
            fal_input["prompt"] = prompt

        return fal_input

    def _save_results(self, raw_output, media_type: str) -> list[Path]:
        if raw_output is None:
            return []
        if isinstance(raw_output, str):
            raw_output = [raw_output]

        saved = []
        for item in raw_output:
            if isinstance(item, str):
                ext = self._infer_extension(item, media_type)
                dest = self._output_dir / f"{uuid.uuid4().hex[:12]}{ext}"
                urllib.request.urlretrieve(item, dest)
                saved.append(dest)
            elif hasattr(item, "read"):
                ext = ".mp4" if media_type == "video" else ".png"
                dest = self._output_dir / f"{uuid.uuid4().hex[:12]}{ext}"
                with open(dest, "wb") as f:
                    f.write(item.read())
                saved.append(dest)
        return saved

    def _infer_extension(self, url: str, media_type: str) -> str:
        if media_type == "video":
            return ".mp4"
        for video_ext in (".mp4", ".mov", ".webm"):
            if video_ext in url.lower():
                return video_ext
        return ".png"

    def _emit(self, message: str, level: str = "info"):
        if self._on_progress:
            self._on_progress(message)
