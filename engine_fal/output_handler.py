"""Fal response normalization, output naming and file saving.

Also home of the verbose per-item failure log: every failed item writes
``<base>-<idx>_ERROR.log`` beside the media's would-be destination, with
secrets masked.
"""

import json
import re
import traceback  # noqa: F401 - re-exported for call sites
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .datatypes import InputFile

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


_MASKED = "***masked***"
_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|(?:^|[_-])key(?:$|[_-])|token|secret|password|authorization|credential)",
    re.I,
)
_KEY_LIKE_VALUE_RE = re.compile(r"[A-Za-z0-9-]{20,}")

_ERROR_LOG_NOTE = (
    "Written automatically on a failed generation — see the office/MEMORY error-log convention."
)


def _mask_secrets(value: Any, key: str = "") -> Any:
    """Recursively mask key-like values so no API key can reach a log.

    A string is masked when its dict key looks secret-bearing (``api_key``,
    ``token``, ...) or when the value itself looks like a bare API key. URLs
    and ordinary text pass through untouched.
    """
    if isinstance(value, dict):
        return {k: _mask_secrets(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_secrets(v, key) for v in value]
    if isinstance(value, str):
        if key and _SECRET_KEY_RE.search(key):
            return _MASKED
        if _KEY_LIKE_VALUE_RE.fullmatch(value):
            return _MASKED
    return value


def _write_error_log(
    dest_dir: Path,
    base: str,
    idx: int,
    *,
    platform: str,
    item: InputFile,
    current: int,
    total: int,
    expected: Path | None,
    profile: dict,
    prompt: str,
    stage: str,
    error: str,
    traceback_text: str,
    api_payload: dict | None,
) -> Path | None:
    """Compose and write the verbose ``_ERROR.log`` for one failed item.

    Written on every per-item failure — provider exception, empty-prompt
    skip, no-output response, or save/download failure — independent of any
    logging flags, beside the media's would-be destination. Secrets are
    masked and no environment data is dumped. Returns the written path, or
    None when the write itself failed (a failed log must never mask the
    generation failure it reports).
    """
    path = dest_dir / f"{base}-{idx}_ERROR.log"
    duration_key = str(profile.get("duration_param_name", "duration"))
    raw_duration = item.metadata.get("duration")
    fps = item.metadata.get("fps")
    effective_duration = api_payload.get(duration_key) if api_payload else None
    media_lines = ["media:", "  primary:"]
    media_lines += [f"    {url}" for url in item.reference_urls] or ["    (none)"]
    for slot, urls in item.references.items():
        media_lines.append(f"  slot {slot}:")
        media_lines += [f"    {url}" for url in urls] or ["    (none)"]
    lines = [
        f"{path.name} — generation failure",
        f"when: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"item: {current} of {total}",
        f"source: {item.path.resolve()}",
        f"dest dir: {dest_dir.resolve()}",
        f"expected: {expected.resolve() if expected else 'n/a'}",
        f"platform: {platform}",
        f"endpoint: {profile.get('endpoint', '')}",
        "profile:",
        json.dumps(_mask_secrets(profile), indent=2, ensure_ascii=False),
        f"duration: raw={raw_duration if raw_duration is not None else 'n/a'} "
        f"effective={effective_duration if effective_duration is not None else 'n/a'} "
        f"fps={fps if fps is not None else 'n/a'}",
        *media_lines,
        "prompt:",
        prompt,
        f"error stage: {stage}",
        f"error: {error}",
        "traceback:",
        traceback_text,
        "api payload:",
        json.dumps(_mask_secrets(api_payload), indent=2, ensure_ascii=False)
        if api_payload is not None
        else "null",
        f"note: {_ERROR_LOG_NOTE}",
    ]
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return None
    return path


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

        ts = self._now_string()
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
