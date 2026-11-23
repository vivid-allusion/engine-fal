"""fal depth-map models (offline)."""

import sys
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from engine_fal import Engine, InputFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from check_schema_sync import check_toml, extract_input  # noqa: E402

DEPTH = ROOT / "engine_fal/endpoints/DEPTH-Models"
IMG = ROOT / "engine_fal/endpoints/IMG-Models"
ENDPOINTS = {
    "fal-ai/image-preprocessors/depth-anything/v2",
    "fal-ai/image-preprocessors/zoe",
    "fal-ai/imageutils/depth",
    "fal-ai/imageutils/marigold-depth",
    "fal-ai/marigold-v2",
    "speridlabs/iris-z",
}
PIC = "https://example.com/street.png"


def tomls(folder):
    return {p.name: tomllib.loads(p.read_text()) for p in folder.glob("*.toml")}


def test_one_depth_toml_per_fal_depth_model_and_none_in_img():
    depth = tomls(DEPTH)
    assert {d["endpoint"] for d in depth.values()} == ENDPOINTS
    assert len(depth) == len(ENDPOINTS)
    assert not ENDPOINTS & {d["endpoint"] for d in tomls(IMG).values()}


@pytest.mark.parametrize("name", sorted(p.name for p in DEPTH.glob("*.toml")))
def test_depth_toml_shape(name):
    d = tomllib.loads((DEPTH / name).read_text())
    assert (d["category"], d["platform"]) == ("DEPTH", "fal")
    assert d["label"].strip()
    assert d["general"] == {"image_url_param": "image_url", "send_prompt": False}
    assert d["pricing"]["currency"] == "USD" and d["pricing"]["note"]
    params = d.get("params", {})
    assert not params.get("depth_and_normal", {}).get("default")
    pt = params.get("prediction_type")
    if pt:
        assert {o["value"] for o in pt["options"]} == {"depth", "see-through-depth"}
        assert pt["default"] == "depth"


def test_sends_one_picture_and_no_prompt(tmp_path):
    d = tomllib.loads((DEPTH / "marigold-v2.toml").read_text())
    eng = Engine({**d["general"], "endpoint": d["endpoint"]}, tmp_path)
    eng._input_props_cache = {"image_url": {"type": "string"}}
    item = InputFile(path=Path("a.md"), prompt="the street", reference_urls=[PIC])
    assert eng._build_fal_input({}, "the street", item, "image") == {"image_url": PIC}


def test_single_image_reply_is_saved_as_png(tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32 + b"depth"
    src = tmp_path / "depth.png"
    src.write_bytes(png)
    npy = tmp_path / "depth.npy"
    npy.write_bytes(b"\x93NUMPY")
    reply = {"image": {"url": src.as_uri(), "content_type": "image/png"},
             "depth_map": {"url": npy.as_uri()}, "seed": 7}
    fc = MagicMock()
    fc.subscribe.return_value = reply
    out = tmp_path / "out"
    with patch.dict("os.environ", {"FAL_KEY": "test_key"}), \
            patch.dict("sys.modules", {"fal_client": fc}):
        eng = Engine({"endpoint": "speridlabs/iris-z", "image_url_param": "image_url",
                      "send_prompt": False}, out)
        eng._input_props_cache = {"image_url": {"type": "string"}}
        res = eng.run([InputFile(path=Path("a.md"), prompt="x", reference_urls=[PIC])])
    assert [r.status for r in res] == ["ok"]
    assert res[0].path.suffix == ".png" and res[0].path.read_bytes() == png


def _marigold_schema():
    return {"components": {"schemas": {"Input": {"properties": {
        "image_url": {"type": "string"},
        "prediction_type": {"type": "string", "default": "depth",
                            "enum": ["depth", "see-through-depth", "normals", "albedo"]},
    }}}}}


def _toml(tmp_path, category):
    p = tmp_path / "m.toml"
    p.write_text(f'''category = "{category}"
[general]
image_url_param = "image_url"
[params.prediction_type]
type = "select"
default = "depth"
options = [{{ value = "depth" }}, {{ value = "see-through-depth" }}]
''')
    return p


def test_schema_gate_lets_a_depth_model_drop_non_depth_kinds(tmp_path):
    assert check_toml(_toml(tmp_path, "DEPTH"), extract_input(_marigold_schema())) == []


def test_schema_gate_still_wants_the_full_enum_elsewhere(tmp_path):
    errors = check_toml(_toml(tmp_path, "IMG"), extract_input(_marigold_schema()))
    assert any("prediction_type" in e for e in errors)
