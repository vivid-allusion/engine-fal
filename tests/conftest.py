import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine_fal.engine import Engine


@pytest.fixture(autouse=True)
def _offline_schema_fetch():
    """Keep unit tests offline: never call the fal models API."""
    with patch.object(Engine, "_fetch_input_props", return_value={}):
        yield
