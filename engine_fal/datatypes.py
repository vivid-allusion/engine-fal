from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class InputFile:
    path: Path
    prompt: str
    reference_urls: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    references: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class OutputFile:
    source_path: Path
    path: Path | None = None
    status: Literal["ok", "error"] = "ok"
    error_msg: str = ""
    media_type: str = ""
    metadata: dict = field(default_factory=dict)
    expected_path: Path | None = None


@dataclass
class ProgressEvent:
    message: str
    level: str = "info"
    current: int = 0
    total: int = 0
    saved_path: Path | None = None
    api_payload: dict | None = None
    # ADDITIVE field (appended last — positional order safe): the file's real
    # destination while `saved_path` stages it as a hidden `.<name>.part`. The
    # Generator stamps `saved_path` inside the progress callback; the Engine
    # renames the staged file to `final_path` only after that callback
    # returns, so the final name never appears unstamped.
    final_path: Path | None = None


class EngineError(Exception):
    pass
