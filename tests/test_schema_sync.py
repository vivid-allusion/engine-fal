"""Tests for scripts/check_schema_sync.py (offline — synthetic schemas).

The live fetch lives in load_schema() and is not exercised here.
check_toml()/extract_input()/renderable_branch()/is_media() are pure and fully
covered.
"""

import sys
import tomllib
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from check_schema_sync import (  # noqa: E402
    check_toml,
    declared_unrenderable,
    extract_input,
    is_media,
    renderable_branch,
    resolve_refs,
)

ALL_CLEAN_PROPS = {
    "prompt": {"type": "string"},
    "image_url": {"type": "string"},
    "audio_url": {"type": "string"},
    "reference_urls": {"type": "array", "items": {"type": "string"}},
    "duration": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
    "fps": {"type": "integer", "enum": [24, 48], "default": 24},
    "mode": {"type": "string", "enum": ["standard", "pro"], "default": "pro"},
    "draft": {"type": "boolean", "default": False},
    "negative_prompt": {"type": "string", "default": ""},
    "guidance": {"type": "number", "minimum": 1.5, "maximum": 10, "default": 4.5},
    "history": {"type": "array", "items": {"type": "object"}},
    "image_size": {
        "anyOf": [
            {"$ref": "#/components/schemas/ImageSize"},
            {
                "type": "string",
                "enum": ["square_hd", "landscape_16_9"],
                "default": "square_hd",
            },
        ]
    },
}

CLEAN_TOML = """\
endpoint = "test/model"

[general]
image_url_param = "image_url"
slots = ["audio_url", "reference_urls"]

# UNRENDERABLE: history

[params.duration]
label = "Duration"
type = "integer"
default = 5
min = 1
max = 20

[params.fps]
label = "FPS"
type = "select"
default = "24"
options = [
  { value = "24", label = "24 FPS" },
  { value = "48", label = "48 FPS" },
]

[params.mode]
label = "Mode"
type = "select"
default = "pro"
options = [
  { value = "pro", label = "Pro" },
  { value = "standard", label = "Standard" },
]

[params.draft]
label = "Draft"
type = "boolean"
default = false

[params.negative_prompt]
label = "Negative Prompt"
type = "text"
default = ""

[params.guidance]
label = "Guidance"
type = "number"
default = 4.5
min = 1.5
max = 10.0

[params.image_size]
label = "Image Size"
type = "select"
default = "square_hd"
options = [
  { value = "square_hd", label = "Square HD" },
  { value = "landscape_16_9", label = "16:9" },
]
"""


def _schema(props=None, required=("prompt",)):
    return {
        "paths": {
            "/test/model": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/TestInput"}
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "ImageSize": {
                    "type": "object",
                    "properties": {
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                    },
                },
                "TestInput": {
                    "properties": ALL_CLEAN_PROPS if props is None else props,
                    "required": list(required),
                },
            }
        },
    }


def _toml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "model.toml"
    path.write_text(content)
    return path


def _model(props=None, required=("prompt",)):
    return extract_input(_schema(props, required))


class TestResolveRefs:
    def test_allof_ref_resolves_type_and_enum(self):
        root = {
            "components": {"schemas": {"fps": {"type": "integer", "enum": [24, 48]}}}
        }
        node = {"allOf": [{"$ref": "#/components/schemas/fps"}], "default": 24}
        assert resolve_refs(node, root) == {
            "type": "integer",
            "enum": [24, 48],
            "default": 24,
        }

    def test_plain_dict_passes_through(self):
        assert resolve_refs({"type": "string"}, {}) == {"type": "string"}

    def test_anyof_prefers_the_renderable_branch(self):
        schema = _schema()
        node = {"anyOf": [{"$ref": "#/components/schemas/ImageSize"}, {"type": "string"}]}
        # flattening keeps the first branch's type (an object), so the union
        # needs renderable_branch() to find the string/enum shape the wizard can show
        assert resolve_refs(node, schema)["type"] == "object"
        assert renderable_branch(node, schema)["type"] == "string"

    def test_object_only_union_has_no_renderable_branch(self):
        schema = _schema()
        node = {
            "anyOf": [
                {"$ref": "#/components/schemas/ImageSize"},
                {"type": "null"},
            ]
        }
        assert renderable_branch(node, schema) == {}


class TestIsMedia:
    def test_url_string(self):
        assert is_media("image_url", {"type": "string"})

    def test_url_list(self):
        assert is_media("image_urls", {"type": "array", "items": {"type": "string"}})

    def test_url_list_of_objects_is_not_media(self):
        assert not is_media(
            "image_urls", {"type": "array", "items": {"type": "object"}}
        )

    def test_plain_key_is_not_media(self):
        assert not is_media("prompt", {"type": "string"})


class TestExtractInput:
    def test_media_and_unrenderable_are_classified(self):
        model = _model()
        assert model.media == {"image_url", "audio_url", "reference_urls"}
        assert model.unrenderable == {"history"}
        assert model.required == ["prompt"]

    def test_enum_branch_makes_a_union_param_renderable(self):
        model = _model()
        assert "image_size" in model.shapes
        assert model.shapes["image_size"]["enum"] == ["square_hd", "landscape_16_9"]


class TestCheckToml:
    def test_clean_toml_passes(self, tmp_path):
        assert check_toml(_toml(tmp_path, CLEAN_TOML), _model()) == []

    def test_undeclared_server_param_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace('image_url_param = "image_url"\n', "")
        errors = check_toml(_toml(tmp_path, toml), _model())
        assert any("not declared" in error and "image_url" in error for error in errors)

    def test_unknown_toml_param_flagged(self, tmp_path):
        toml = CLEAN_TOML + """
[params.not_a_thing]
label = "Fake"
type = "boolean"
default = false
"""
        errors = check_toml(_toml(tmp_path, toml), _model())
        assert any("'not_a_thing' does not exist" in error for error in errors)

    def test_slot_not_in_schema_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace('slots = ["audio_url", "reference_urls"]', 'slots = ["bogus"]')
        errors = check_toml(_toml(tmp_path, toml), _model())
        assert any("slot 'bogus' does not exist" in error for error in errors)

    def test_media_declared_as_param_flagged(self, tmp_path):
        toml = CLEAN_TOML + """
[params.audio_url]
label = "Audio URL"
type = "text"
default = ""
"""
        errors = check_toml(_toml(tmp_path, toml), _model())
        assert any("declare it in [general]" in error for error in errors)

    def test_wrong_enum_options_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace('  { value = "48", label = "48 FPS" },\n', "")
        errors = check_toml(_toml(tmp_path, toml), _model())
        assert any("options" in error and "server enum" in error for error in errors)

    def test_wrong_integer_range_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace("max = 20", "max = 10")
        errors = check_toml(_toml(tmp_path, toml), _model())
        assert any("server maximum" in error for error in errors)

    def test_wrong_default_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace('default = "pro"', 'default = "standard"')
        errors = check_toml(_toml(tmp_path, toml), _model())
        assert any("server default" in error for error in errors)

    def test_select_vs_text_type_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace(
            '[params.mode]\nlabel = "Mode"\ntype = "select"',
            '[params.mode]\nlabel = "Mode"\ntype = "text"',
        )
        errors = check_toml(_toml(tmp_path, toml), _model())
        assert any("param 'mode': server type select" in error for error in errors)

    def test_required_param_marked_optional_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace(
            '[params.duration]\nlabel = "Duration"\ntype = "integer"',
            '[params.duration]\nlabel = "Duration"\ntype = "integer"\noptional = true',
        )
        errors = check_toml(
            _toml(tmp_path, toml), _model(required=("prompt", "duration"))
        )
        assert any("required server-side but marked optional" in error for error in errors)

    def test_missing_unrenderable_comment_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace("# UNRENDERABLE: history\n", "")
        errors = check_toml(_toml(tmp_path, toml), _model())
        assert any("UNRENDERABLE" in error and "missing" in error for error in errors)

    def test_invented_unrenderable_entry_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace("# UNRENDERABLE: history", "# UNRENDERABLE: history loras")
        errors = check_toml(_toml(tmp_path, toml), _model())
        assert any("UNRENDERABLE" in error and "invented" in error for error in errors)

    def test_unrenderable_param_declared_is_flagged(self, tmp_path):
        toml = CLEAN_TOML + """
[params.history]
label = "History"
type = "text"
default = ""
"""
        errors = check_toml(_toml(tmp_path, toml), _model())
        assert any("param 'history' has no renderable shape" in error for error in errors)

    def test_duration_param_name_must_exist(self, tmp_path):
        toml = CLEAN_TOML.replace(
            '[general]\nimage_url_param = "image_url"',
            '[general]\nimage_url_param = "image_url"\nduration_param_name = "clip_length"',
        )
        errors = check_toml(_toml(tmp_path, toml), _model())
        assert any("duration_param_name 'clip_length'" in error for error in errors)


class TestDeclaredUnrenderable:
    def test_reads_every_token(self, tmp_path):
        path = _toml(
            tmp_path,
            "# Server inputs with no wizard widget.\n# UNRENDERABLE: a b c\n",
        )
        assert declared_unrenderable(path) == {"a", "b", "c"}

    def test_empty_when_absent(self, tmp_path):
        assert declared_unrenderable(_toml(tmp_path, "endpoint = 'x'\n")) == set()
