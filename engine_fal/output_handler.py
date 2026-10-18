"""Fal response normalization and file saving.
"""

import re
import urllib.request
from datetime import datetime
from pathlib import Path


_EMBEDDED_TIMESTAMP_RE = re.compile(r"(?<![0-9])[0-9]{6}_[0-9]{6}(?![0-9])")


def _output_stem(stem: str, ts: str) -> str:
    """Name the output file after its source stem and the generation timestamp.

    When the stem already embeds a ``YYMMDD_HHMMSS`` timestamp, the first
    occurrence is replaced in place with ``ts`` — re-running the same bullet
    keeps one date in the name instead of stacking a second one. Stems
    without an embedded timestamp keep the historical ``{ts}-{stem}``
    prepend.
    """
    replaced, count = _EMBEDDED_TIMESTAMP_RE.subn(ts, stem, count=1)
    return replaced if count else f"{ts}-{stem}"


class FalOutputHandler:
    """Normalize provider results and save their files."""

    def _infer_extension(self, url: str, media_type: str) -> str:
        if media_type == "video":
            return ".mp4"
        for video_ext in (".mp4", ".mov", ".webm"):
            if video_ext in url.lower():
                return video_ext
        return ".png"

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
        named_stem = _output_stem(stem, ts)
        saved = []
        for i, item in enumerate(raw_output):
            if isinstance(item, str):
                self._emit(f"{prefix} ⬇️  Downloading...")
                ext = self._infer_extension(item, media_type)
                suffix = "" if len(raw_output) == 1 else f"_{i}"
                dest = dest_dir / f"{named_stem}-{idx}{suffix}{ext}"
                temp = dest_dir / f".{dest.name}.part"
                try:
                    urllib.request.urlretrieve(item, temp)
                except Exception:
                    temp.unlink(missing_ok=True)
                    raise
                # Stage → announce → reveal: the Generator stamps the staged
                # file inside this callback before the rename below, so the
                # final name never appears unstamped (ProgressEvent.final_path).
                self._emit(
                    f"{prefix} 💾 Saved: {dest.name}",
                    current=current,
                    total=total,
                    saved_path=temp,
                    api_payload=api_payload,
                    final_path=dest,
                )
                temp.replace(dest)
                saved.append(dest)
            elif hasattr(item, "read"):
                self._emit(f"{prefix} ⬇️  Saving file stream...")
                data = item.read()
                ext = self._sniff_extension(data, media_type)
                suffix = "" if len(raw_output) == 1 else f"_{i}"
                dest = dest_dir / f"{named_stem}-{idx}{suffix}{ext}"
                temp = dest_dir / f".{dest.name}.part"
                try:
                    with open(temp, "wb") as f:
                        f.write(data)
                except Exception:
                    temp.unlink(missing_ok=True)
                    raise
                # Stage → announce → reveal (ProgressEvent.final_path).
                self._emit(
                    f"{prefix} 💾 Saved: {dest.name}",
                    current=current,
                    total=total,
                    saved_path=temp,
                    api_payload=api_payload,
                    final_path=dest,
                )
                temp.replace(dest)
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
