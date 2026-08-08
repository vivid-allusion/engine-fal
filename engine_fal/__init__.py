from pathlib import Path

from .engine import Engine
from .datatypes import InputFile, OutputFile, ProgressEvent, EngineError

_STANDBY_DIR = Path(__file__).parent / "profiles" / "standby"


def list_standby_profiles() -> list[Path]:
    """Return sorted list of standby YAML profile paths shipped with this engine."""
    if not _STANDBY_DIR.is_dir():
        return []
    return sorted(_STANDBY_DIR.glob("*.yaml"))


__all__ = [
    "Engine",
    "InputFile",
    "OutputFile",
    "ProgressEvent",
    "EngineError",
    "list_standby_profiles",
]
