"""Seedream 5.5 catalog entries (offline)."""

import tomllib
from pathlib import Path

IMG = Path(__file__).resolve().parent.parent / "engine_fal/endpoints/IMG-Models"
SIZES = ["square_hd", "square", "portrait_4_3", "portrait_16_9", "landscape_4_3",
         "landscape_16_9", "auto_1K", "auto_1.5K", "auto_2K"]


def load(name):
    return tomllib.loads((IMG / name).read_text())


def test_text_to_image():
    d = load("seedream-5.5.toml")
    assert d["endpoint"] == "bytedance/seedream/v5.5/text-to-image"
    assert d["label"] == "Seedream 5.5"
    assert "general" not in d


def test_edit_takes_image_urls():
    d = load("seedream-5.5-edit.toml")
    assert d["endpoint"] == "bytedance/seedream/v5.5/edit"
    assert d["label"] == "Seedream 5.5 · Edit"
    assert d["general"]["image_url_param"] == "image_urls"


def test_settings_match_fal():
    for name in ("seedream-5.5.toml", "seedream-5.5-edit.toml"):
        p = load(name)["params"]
        assert [o["value"] for o in p["image_size"]["options"]] == SIZES
        assert p["image_size"]["default"] == "auto_2K"
        assert (p["num_images"]["min"], p["num_images"]["max"]) == (1, 6)
        assert p["watermark"]["default"] is False
        assert p["sync_mode"]["hidden"] is True
        assert not p["watermark"].get("hidden")
        assert not p["enable_safety_checker"].get("hidden")


def test_layerize_takes_one_image_url():
    d = load("seedream-5.5-layerize.toml")
    assert d["endpoint"] == "bytedance/seedream/v5.5/layerize"
    assert d["label"] == "Seedream 5.5 · Layerize"
    assert d["general"]["image_url_param"] == "image_url"


def test_layerize_settings_match_fal():
    p = load("seedream-5.5-layerize.toml")["params"]
    assert [o["value"] for o in p["image_size"]["options"]] == ["auto", "auto_1K", "auto_1.5K", "auto_2K"]
    assert p["image_size"]["default"] == "auto"
    assert (p["max_images"]["min"], p["max_images"]["max"], p["max_images"]["default"]) == (2, 100, 30)
    assert [o["value"] for o in p["output_format"]["options"]] == ["png", "jpeg"]
    assert p["watermark"]["default"] is False
    assert p["sync_mode"]["hidden"] and p["enable_safety_checker"]["hidden"]
    assert not p["enable_text_recognition"].get("hidden")
