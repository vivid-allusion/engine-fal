"""Tripo3D picture-to-3D entry (offline)."""

import tomllib
from pathlib import Path

from engine_fal import Engine, InputFile

TOML = (Path(__file__).resolve().parent.parent
        / "engine_fal/endpoints/IMG-Models/tripo3d-v2.5-image-to-3d.toml")
IMG = "https://example.com/hamburger.png"
GLB = "https://example.com/tripo_model.glb"


def engine(tmp_path):
    d = tomllib.loads(TOML.read_text())
    eng = Engine({**d["general"], "endpoint": d["endpoint"]}, tmp_path)
    eng._input_props_cache = {"image_url": {"type": "string"}}
    return eng


def test_sends_one_picture_and_no_prompt(tmp_path):
    item = InputFile(path=Path("a.md"), prompt="a hamburger", reference_urls=[IMG])
    assert engine(tmp_path)._build_fal_input({}, "a hamburger", item, "image") == {"image_url": IMG}


def test_saves_the_mesh_as_glb(tmp_path):
    eng = engine(tmp_path)
    reply = {"task_id": "1", "model_mesh": {"url": GLB},
             "rendered_image": {"url": "https://example.com/p.webp"}}
    urls = eng._normalize_result(reply, "image")
    assert urls == [GLB]
    assert eng._infer_extension(urls[0], "image") == eng._default_extension("image") == ".glb"
