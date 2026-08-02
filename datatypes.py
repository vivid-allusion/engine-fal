from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class InputFile:
    path: Path
    prompt: str
    reference_urls: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class OutputFile:
    bullet_path: Path
    path: Path | None = None
    status: Literal["ok", "error"] = "ok"
    error_msg: str = ""
    media_type: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class ProgressEvent:
    message: str
    level: str = "info"


class EngineError(Exception):
    pass
