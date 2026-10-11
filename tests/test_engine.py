import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine_fal import Engine, EngineError, InputFile, OutputFile, ProgressEvent  # noqa: E402
import engine_fal.metadata as meta  # noqa: E402


def _write_temp(url, filename):
    """urlretrieve stand-in that really creates the staged file."""
    Path(filename).write_bytes(b"img-bytes")
    return (None, None)


class TestDatatypes:
    def test_inputfile_defaults(self):
        f = InputFile(path=Path("test.md"), prompt="hello")
        assert f.path == Path("test.md")
        assert f.prompt == "hello"
        assert f.reference_urls == []
        assert f.references == {}
        assert f.metadata == {}

    def test_outputfile_defaults(self):
        o = OutputFile(source_path=Path("test.md"))
        assert o.source_path == Path("test.md")
        assert o.path is None
        assert o.status == "ok"
        assert o.error_msg == ""
        assert o.media_type == ""
        assert o.metadata == {}
        assert o.expected_path is None

    def test_outputfile_error_status(self):
        o = OutputFile(
            source_path=Path("test.md"),
            status="error",
            error_msg="timeout",
            media_type="image",
        )
        assert o.status == "error"
        assert o.error_msg == "timeout"
        assert o.media_type == "image"

    def test_progress_event_defaults(self):
        e = ProgressEvent(message="processing")
        assert e.message == "processing"
        assert e.level == "info"
        assert e.current == 0
        assert e.total == 0
        assert e.final_path is None

    def test_progress_event_with_progress(self):
        e = ProgressEvent(message="done", current=3, total=5)
        assert e.current == 3
        assert e.total == 5

    def test_engine_error_is_exception(self):
        with pytest.raises(EngineError):
            raise EngineError("test")


class TestMetadata:
    def test_provider_name(self):
        assert meta.PROVIDER_NAME == "Fal.ai"

    def test_platform(self):
        assert meta.PLATFORM == "fal"

    def test_api_key_env_var(self):
        assert meta.API_KEY_ENV_VAR == "FAL_KEY"

    def test_api_key_pattern(self):
        assert meta.API_KEY_PATTERN == r"^[A-Za-z0-9-]{20,}$"

    def test_provider_homepage(self):
        assert meta.PROVIDER_HOMEPAGE == "https://fal.ai"

    def test_metadata_matches_engine_class(self):
        assert Engine.PLATFORM == meta.PLATFORM
        assert Engine.PROVIDER_NAME == meta.PROVIDER_NAME
        assert Engine.API_KEY_ENV_VAR == meta.API_KEY_ENV_VAR
        assert Engine.API_KEY_PATTERN == meta.API_KEY_PATTERN


class TestEngineInitPurity:
    def test_init_stores_attributes(self):
        profile = {"endpoint": "test/model", "media_type": "image"}
        engine = Engine(profile, "/tmp/out")
        assert engine._profile == profile
        assert engine._output_dir == Path("/tmp/out")
        assert engine._api_key is None
        assert engine._on_progress is None
        assert engine._prefix == ""
        assert engine._suffix == ""

    def test_init_extracts_prefix_suffix(self):
        profile = {
            "endpoint": "test/model",
            "prompt_prefix": "Turn this into ",
            "prompt_suffix": " in oil painting style",
        }
        engine = Engine(profile, "/tmp/out")
        assert engine._prefix == "Turn this into "
        assert engine._suffix == " in oil painting style"


class TestEnginePreflight:
    def test_missing_endpoint_raises(self):
        engine = Engine({"media_type": "image"}, "/tmp/out")
        with pytest.raises(EngineError, match="Missing or empty 'endpoint'"):
            engine.run([])

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_api_key_raises(self):
        with patch.dict("sys.modules", {"fal_client": MagicMock()}):
            engine = Engine({"endpoint": "test/model"}, "/tmp/out")
            with pytest.raises(EngineError, match="not set"):
                engine.run([])

    def test_fal_client_not_installed_raises(self):
        with patch.dict("sys.modules", {"fal_client": None}):
            engine = Engine({"endpoint": "test/model"}, "/tmp/out")
            with pytest.raises(EngineError, match="fal-client SDK not installed"):
                engine.run([])


class TestEngineRun:
    def test_empty_inputs(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"},
                    tmp_path,
                )
                results = engine.run([])
                assert results == []

    def test_run_calls_progress_callback(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {
                "images": [{"url": "https://example.com/out.png"}]
            }

            progress_calls = []
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"},
                    tmp_path,
                    on_progress=progress_calls.append,
                )
                engine.run([InputFile(path=Path("b.md"), prompt="test")])

            assert len(progress_calls) >= 1
            first = progress_calls[0]
            assert isinstance(first, ProgressEvent)
            assert "Calling Fal.ai API" in first.message

    def test_applies_prefix_suffix(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {}
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {
                        "endpoint": "test/model",
                        "media_type": "image",
                        "prompt_prefix": "PREFIX: ",
                        "prompt_suffix": " :SUFFIX",
                    },
                    tmp_path,
                )
                engine.run([InputFile(path=Path("b.md"), prompt="hello")])
                call_args = mock_fal_client.subscribe.call_args
                prompt_sent = call_args[1]["arguments"]["prompt"]
                assert prompt_sent == "PREFIX: hello :SUFFIX"

    def test_empty_prompt_after_prefix_suffix_is_error(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {
                        "endpoint": "test/model",
                        "media_type": "image",
                        "prompt_prefix": "",
                        "prompt_suffix": "",
                    },
                    tmp_path,
                )
                results = engine.run([InputFile(path=Path("b.md"), prompt="  ")])
                assert len(results) == 1
                assert results[0].status == "error"
                assert "Empty prompt" in results[0].error_msg

    def test_per_markdown_error_returns_error_outputfile(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.side_effect = RuntimeError("API timeout")
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"},
                    tmp_path,
                )
                results = engine.run([InputFile(path=Path("b.md"), prompt="test")])
                assert len(results) == 1
                assert results[0].status == "error"
                assert "API timeout" in results[0].error_msg

    def test_partial_success_mixed_batch(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.side_effect = [
                {"images": [{"url": "https://a.com/ok.png"}]},
                RuntimeError("fail"),
                {"images": [{"url": "https://a.com/ok2.png"}]},
            ]
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                with patch("urllib.request.urlretrieve", side_effect=_write_temp):
                    engine = Engine(
                        {"endpoint": "test/model", "media_type": "image"},
                        tmp_path,
                    )
                    markdown_files = [
                        InputFile(path=Path(f"b{i}.md"), prompt="test")
                        for i in range(3)
                    ]
                    results = engine.run(markdown_files)
                    statuses = [r.status for r in results]
                    assert statuses.count("ok") == 2
                    assert statuses.count("error") == 1

    def test_save_results_downloads_urls(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {
                "images": [{"url": "https://example.com/img.png"}]
            }
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                with patch("urllib.request.urlretrieve") as mock_retrieve:
                    mock_retrieve.side_effect = _write_temp
                    engine = Engine(
                        {"endpoint": "test/model", "media_type": "image"},
                        tmp_path,
                    )
                    results = engine.run(
                        [InputFile(path=Path("b.md"), prompt="test")]
                    )
                    assert len(results) == 1
                    assert results[0].status == "ok"
                    assert results[0].path is not None
                    assert results[0].path.exists()
                    mock_retrieve.assert_called_once()

    def test_save_results_mirrors_relative_dir(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {
                "images": [{"url": "https://example.com/img.png"}]
            }
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                with patch("urllib.request.urlretrieve") as mock_retrieve:
                    mock_retrieve.side_effect = _write_temp
                    engine = Engine(
                        {"endpoint": "test/model", "media_type": "image"},
                        tmp_path,
                    )
                    results = engine.run(
                        [
                            InputFile(
                                path=Path("b.md"),
                                prompt="test",
                                metadata={"relative_dir": "scene1/nested"},
                            )
                        ]
                    )
                    assert len(results) == 1
                    assert results[0].status == "ok"
                    assert results[0].path is not None
                    assert results[0].path.parent == tmp_path / "scene1" / "nested"

    def test_save_stages_hidden_then_reveals_after_the_event(self, tmp_path):
        observed: dict[str, object] = {}

        def on_event(event):
            if event.saved_path is None:
                return
            observed["temp_name"] = event.saved_path.name
            observed["temp_exists"] = event.saved_path.exists()
            observed["temp_bytes"] = event.saved_path.read_bytes()
            observed["final_visible"] = event.final_path.exists()

        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {
                "images": [{"url": "https://example.com/img.png"}]
            }
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                with patch("urllib.request.urlretrieve") as mock_retrieve:
                    mock_retrieve.side_effect = _write_temp
                    engine = Engine(
                        {"endpoint": "test/model", "media_type": "image"},
                        tmp_path,
                    )
                    engine._on_progress = on_event
                    results = engine.run(
                        [InputFile(path=Path("b.md"), prompt="test")]
                    )

        dest = results[0].path
        assert observed["temp_name"] == f".{dest.name}.part"
        assert observed["temp_exists"] is True
        assert observed["temp_bytes"] == b"img-bytes"
        assert observed["final_visible"] is False
        assert dest.exists()
        assert dest.read_bytes() == b"img-bytes"
        assert list(tmp_path.glob(".*.part")) == []

    def test_video_result_normalization(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {
                "video": {"url": "https://example.com/out.mp4"}
            }
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                with patch("urllib.request.urlretrieve") as mock_retrieve:
                    mock_retrieve.side_effect = _write_temp
                    engine = Engine(
                        {"endpoint": "test/model", "media_type": "video"},
                        tmp_path,
                    )
                    results = engine.run(
                        [InputFile(path=Path("b.md"), prompt="test")]
                    )
                    assert len(results) == 1
                    assert results[0].status == "ok"
                    mock_retrieve.assert_called_once()

    def test_none_output_returns_error(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = None
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"},
                    tmp_path,
                )
                results = engine.run(
                    [InputFile(path=Path("b.md"), prompt="test")]
                )
                assert len(results) == 1
                assert results[0].status == "error"
                assert "No output" in results[0].error_msg

    def test_video_media_type_uses_image_url(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {
                "video": {"url": "https://example.com/out.mp4"}
            }
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                with patch("urllib.request.urlretrieve"):
                    engine = Engine(
                        {"endpoint": "test/model", "media_type": "video"},
                        tmp_path,
                    )
                    engine.run([
                        InputFile(
                            path=Path("b.md"),
                            prompt="test",
                            reference_urls=["https://ref.com/img.jpg"],
                            metadata={"duration": 5, "fps": 24},
                        )
                    ])
                    fal_input = mock_fal_client.subscribe.call_args[1]["arguments"]
                    assert fal_input["image_url"] == "https://ref.com/img.jpg"
                    assert fal_input["duration"] == 5
                    # fps is generator metadata, never a provider input
                    assert "fps" not in fal_input

    def test_reference_param_from_profile(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {
                "images": [{"url": "https://a.com/ok.png"}]
            }
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                with patch("urllib.request.urlretrieve"):
                    engine = Engine(
                        {
                            "endpoint": "test/model",
                            "media_type": "image",
                            "reference_param": "start_image_url",
                        },
                        tmp_path,
                    )
                    engine.run([
                        InputFile(
                            path=Path("b.md"),
                            prompt="test",
                            reference_urls=["https://ref.com/img.jpg"],
                        )
                    ])
                    arguments = mock_fal_client.subscribe.call_args[1]["arguments"]
                    assert arguments["start_image_url"] == "https://ref.com/img.jpg"

    def test_reference_param_multi_from_profile(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {
                "images": [{"url": "https://a.com/ok.png"}]
            }
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                with patch("urllib.request.urlretrieve"):
                    engine = Engine(
                        {
                            "endpoint": "fal-ai/nano-banana-2/edit",
                            "media_type": "image",
                            "reference_param": "image_urls",
                            "reference_param_multi": True,
                        },
                        tmp_path,
                    )
                    engine.run([
                        InputFile(
                            path=Path("b.md"),
                            prompt="Reframe the provided image",
                            reference_urls=[
                                "https://ref.com/img1.jpg",
                                "https://ref.com/img2.jpg",
                            ],
                        )
                    ])
                    arguments = mock_fal_client.subscribe.call_args[1]["arguments"]
                    assert arguments["image_urls"] == [
                        "https://ref.com/img1.jpg",
                        "https://ref.com/img2.jpg",
                    ]

    def test_error_emits_progress_event_with_current_total(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.side_effect = RuntimeError("fail")
            progress_calls = []
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"},
                    tmp_path,
                    on_progress=progress_calls.append,
                )
                engine.run([InputFile(path=Path("b.md"), prompt="test")])
            error_events = [e for e in progress_calls if e.level == "error"]
            assert len(error_events) == 1
            assert error_events[0].current == 1
            assert error_events[0].total == 1


class TestPromptLogging:
    def test_markdown_filename_is_emitted_to_progress(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {}
            progress_calls = []
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {
                        "endpoint": "test/model",
                        "media_type": "image",
                        "prompt_prefix": "PREFIX: ",
                        "prompt_suffix": " :SUFFIX",
                    },
                    tmp_path,
                    on_progress=progress_calls.append,
                )
                engine.run([InputFile(path=Path("b.md"), prompt="hello")])

            markdowns = [c.message for c in progress_calls if "Markdown:" in c.message]
            assert markdowns == ["[1/1] 📝 Markdown: b.md"]

    def test_empty_prompt_is_not_logged(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            progress_calls = []
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"},
                    tmp_path,
                    on_progress=progress_calls.append,
                )
                engine.run([InputFile(path=Path("b.md"), prompt="   ")])

            assert not any("Markdown:" in c.message for c in progress_calls)


class TestExpectedPath:
    """Error results must carry the destination the run would have used."""

    def _run_error(self, tmp_path, profile: dict | None = None):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.side_effect = RuntimeError("API timeout")
            full_profile = {"endpoint": "test/model", "media_type": "image"}
            full_profile.update(profile or {})
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(full_profile, tmp_path)
                return engine.run([InputFile(path=Path("b.md"), prompt="test")])

    def test_exception_error_carries_expected_path(self, tmp_path):
        results = self._run_error(tmp_path)
        assert results[0].status == "error"
        assert results[0].expected_path is not None
        assert results[0].expected_path.parent == tmp_path
        assert results[0].expected_path.name.startswith("2")
        assert results[0].expected_path.stem.endswith("-b-0")

    def test_expected_path_uses_output_format_extension(self, tmp_path):
        results = self._run_error(
            tmp_path, profile={"parameters": {"output_format": "jpg"}}
        )
        assert results[0].expected_path.suffix == ".jpg"

    def test_empty_prompt_error_carries_expected_path(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {}
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"}, tmp_path
                )
                results = engine.run([InputFile(path=Path("b.md"), prompt="  ")])
        assert results[0].status == "error"
        assert results[0].expected_path is not None
        assert results[0].expected_path.stem.endswith("-b-0")

    def test_no_output_error_carries_expected_path(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {}
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"}, tmp_path
                )
                results = engine.run([InputFile(path=Path("b.md"), prompt="test")])
        assert results[0].status == "error"
        assert results[0].expected_path is not None

    def test_error_event_carries_api_payload(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.side_effect = RuntimeError("API timeout")
            progress_calls = []
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "video"},
                    tmp_path,
                    on_progress=progress_calls.append,
                )
                engine.run([InputFile(path=Path("b.md"), prompt="test")])

        errors = [c for c in progress_calls if c.level == "error"]
        assert errors
        assert errors[0].api_payload is not None
        assert errors[0].api_payload["prompt"] == "test"


class TestStreamSaveExtension:
    """Stream outputs must be saved under the extension their bytes claim."""

    def _run_stream(self, tmp_path, data: bytes, profile: dict | None = None):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = [BytesIO(data)]
            full_profile = {"endpoint": "test/model", "media_type": "image"}
            full_profile.update(profile or {})
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(full_profile, tmp_path)
                return engine.run([InputFile(path=Path("b.md"), prompt="test")])

    def test_jpeg_bytes_saved_with_jpg_extension(self, tmp_path):
        results = self._run_stream(tmp_path, b"\xff\xd8\xff" + b"jpegdata")
        assert results[0].status == "ok"
        assert results[0].path.suffix == ".jpg"
        assert results[0].path.read_bytes() == b"\xff\xd8\xff" + b"jpegdata"

    def test_png_bytes_saved_with_png_extension(self, tmp_path):
        results = self._run_stream(tmp_path, b"\x89PNG\r\n\x1a\n" + b"pngdata")
        assert results[0].path.suffix == ".png"

    def test_gif_bytes_saved_with_gif_extension(self, tmp_path):
        results = self._run_stream(tmp_path, b"GIF89a" + b"gifdata")
        assert results[0].path.suffix == ".gif"

    def test_webp_bytes_saved_with_webp_extension(self, tmp_path):
        results = self._run_stream(tmp_path, b"RIFF\x00\x00\x00\x00WEBP" + b"data")
        assert results[0].path.suffix == ".webp"

    def test_mp4_bytes_saved_with_mp4_extension(self, tmp_path):
        results = self._run_stream(tmp_path, b"\x00\x00\x00\x18ftyp" + b"mp4data")
        assert results[0].path.suffix == ".mp4"

    def test_unknown_bytes_use_output_format_param(self, tmp_path):
        results = self._run_stream(
            tmp_path,
            b"\xde\xad\xbe\xef",
            profile={"parameters": {"output_format": "jpg"}},
        )
        assert results[0].path.suffix == ".jpg"

    def test_unknown_bytes_fall_back_to_media_type_default(self, tmp_path):
        results = self._run_stream(tmp_path, b"\xde\xad\xbe\xef")
        assert results[0].path.suffix == ".png"


class TestBuildFalInput:
    """Slot routing + per-Markdown-file duration override for video payloads."""

    def _build(self, profile, item, params=None, schema=None):
        engine = Engine(profile, "/tmp/out")
        if schema is not None:
            engine._input_props_cache = {
                key: engine._resolve_schema_refs(value, schema)
                for key, value in schema.items()
            }
        return engine._build_fal_input(params or {}, item.prompt, item, "video")

    SEEDANCE_SCHEMA = {
        "image": {"type": "string"},
        "reference_images": {"type": "array"},
        "reference_videos": {"type": "array"},
        "reference_audios": {"type": "array"},
    }

    def test_seedance_shaped_payload_dict(self):
        profile = {
            "endpoint": "fal-ai/seedance/v1/pro/image-to-video",
            "image_url_param": "image",
            "duration_param_name": "duration",
        }
        item = InputFile(
            path=Path("b.md"),
            prompt="a cinematic pan",
            reference_urls=["https://ref.com/start.jpg"],
            references={"reference_images": ["https://ref.com/img.jpg"]},
            metadata={"duration": "auto"},
        )
        payload = self._build(profile, item, schema=self.SEEDANCE_SCHEMA)
        assert payload == {
            "prompt": "a cinematic pan",
            "image": "https://ref.com/start.jpg",
            "reference_images": ["https://ref.com/img.jpg"],
            "duration": "auto",
        }

    def test_primary_key_prefers_image_url_param(self):
        profile = {"image_url_param": "start_image_url", "reference_param": "image_urls"}
        item = InputFile(
            path=Path("b.md"), prompt="p", reference_urls=["https://x.com/a.jpg"]
        )
        payload = self._build(profile, item)
        assert payload["start_image_url"] == "https://x.com/a.jpg"
        assert "image_urls" not in payload

    def test_primary_key_falls_back_to_reference_param(self):
        profile = {"reference_param": "image_urls"}
        item = InputFile(
            path=Path("b.md"), prompt="p", reference_urls=["https://x.com/a.jpg"]
        )
        payload = self._build(profile, item)
        assert payload["image_urls"] == "https://x.com/a.jpg"

    def test_primary_key_falls_back_to_image_url(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(
            path=Path("b.md"), prompt="p", reference_urls=["https://x.com/a.jpg"]
        )
        payload = self._build(profile, item)
        assert payload["image_url"] == "https://x.com/a.jpg"

    def test_named_slots_route_to_own_keys(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(
            path=Path("b.md"),
            prompt="p",
            references={
                "reference_images": ["https://x.com/i.jpg"],
                "reference_videos": ["https://x.com/v.mp4"],
            },
        )
        payload = self._build(profile, item)
        assert payload["reference_images"] == ["https://x.com/i.jpg"]
        assert payload["reference_videos"] == ["https://x.com/v.mp4"]

    def test_slot_scalar_profile_key_forces_single_url(self):
        profile = {"endpoint": "test/model", "slot_scalar": ["end_image_url"]}
        item = InputFile(
            path=Path("b.md"),
            prompt="p",
            references={"end_image_url": ["https://x.com/last.jpg"]},
        )
        payload = self._build(profile, item)
        assert payload["end_image_url"] == "https://x.com/last.jpg"

    def test_named_slots_and_image_url_param(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {
                "video": {"url": "https://example.com/out.mp4"}
            }
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                with patch("urllib.request.urlretrieve"):
                    engine = Engine(
                        {
                            "endpoint": "bytedance/seedance-2.5/reference-to-video",
                            "media_type": "video",
                            "image_url_param": "video_urls",
                            "reference_param": "image_urls",
                            "reference_param_multi": True,
                            "parameters": {"task": "editing"},
                        },
                        tmp_path,
                    )
                    engine.run(
                        [
                            InputFile(
                                path=Path("b.md"),
                                prompt="Edit @Video 1",
                                reference_urls=["https://ref.com/clip.mp4"],
                                references={
                                    "image_urls": ["https://ref.com/plate.jpg"]
                                },
                            )
                        ]
                    )
                    arguments = mock_fal_client.subscribe.call_args[1]["arguments"]
                    assert arguments["video_urls"] == ["https://ref.com/clip.mp4"]
                    assert arguments["image_urls"] == ["https://ref.com/plate.jpg"]
                    assert arguments["task"] == "editing"

    def test_fps_stripped_from_payload(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {
                "video": {"url": "https://example.com/out.mp4"}
            }
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                with patch("urllib.request.urlretrieve"):
                    engine = Engine(
                        {
                            "endpoint": "test/model",
                            "media_type": "video",
                            "parameters": {"fps": 24, "duration": "5"},
                        },
                        tmp_path,
                    )
                    engine.run(
                        [
                            InputFile(
                                path=Path("b.md"),
                                prompt="test",
                                metadata={"duration": "5", "fps": 24},
                            )
                        ]
                    )
                    arguments = mock_fal_client.subscribe.call_args[1]["arguments"]
                    assert "fps" not in arguments
                    assert arguments["duration"] == "5"

    def test_slot_scalar_from_profile(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {
                "video": {"url": "https://example.com/out.mp4"}
            }
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                with patch("urllib.request.urlretrieve"):
                    engine = Engine(
                        {
                            "endpoint": "bytedance/seedance-2.5/image-to-video",
                            "media_type": "video",
                            "image_url_param": "image_url",
                            "slots": ["end_image_url"],
                            "slot_scalar": ["end_image_url"],
                        },
                        tmp_path,
                    )
                    engine.run(
                        [
                            InputFile(
                                path=Path("b.md"),
                                prompt="test",
                                reference_urls=["https://ref.com/first.jpg"],
                                references={
                                    "end_image_url": ["https://ref.com/last.jpg"]
                                },
                            )
                        ]
                    )
                    arguments = mock_fal_client.subscribe.call_args[1]["arguments"]
                    assert arguments["image_url"] == "https://ref.com/first.jpg"
                    assert arguments["end_image_url"] == "https://ref.com/last.jpg"

    def test_schema_string_param_gets_single_url(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(
            path=Path("b.md"), prompt="p", reference_urls=["https://x.com/a.jpg"]
        )
        payload = self._build(profile, item, schema={"image_url": {"type": "string"}})
        assert payload["image_url"] == "https://x.com/a.jpg"

    def test_schema_array_param_keeps_list_with_one_url(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(
            path=Path("b.md"),
            prompt="p",
            references={"reference_images": ["https://x.com/i.jpg"]},
        )
        payload = self._build(
            profile, item, schema={"reference_images": {"type": "array"}}
        )
        assert payload["reference_images"] == ["https://x.com/i.jpg"]

    def test_multiple_urls_fallback_keeps_list(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(
            path=Path("b.md"),
            prompt="p",
            reference_urls=["https://x.com/a.jpg", "https://x.com/b.jpg"],
        )
        payload = self._build(profile, item)
        assert payload["image_url"] == ["https://x.com/a.jpg", "https://x.com/b.jpg"]

    def test_multiple_urls_on_string_param_raises_loudly(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(
            path=Path("b.md"),
            prompt="p",
            reference_urls=["https://x.com/a.jpg", "https://x.com/b.jpg"],
        )
        with pytest.raises(EngineError, match="single media URL"):
            self._build(profile, item, schema={"image_url": {"type": "string"}})

    def test_empty_named_list_omitted_by_default(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(
            path=Path("b.md"), prompt="p", references={"reference_images": []}
        )
        payload = self._build(profile, item)
        assert "reference_images" not in payload

    def test_empty_named_list_emitted_when_required(self):
        profile = {"endpoint": "test/model", "required_slots": ["reference_images"]}
        item = InputFile(
            path=Path("b.md"), prompt="p", references={"reference_images": []}
        )
        payload = self._build(profile, item)
        assert payload["reference_images"] == []

    def test_duration_override_verbatim_int(self):
        profile = {"endpoint": "test/model", "duration_param_name": "duration"}
        item = InputFile(path=Path("b.md"), prompt="p", metadata={"duration": 7})
        payload = self._build(profile, item)
        assert payload["duration"] == 7

    def test_duration_token_stays_string(self):
        profile = {"endpoint": "test/model", "duration_param_name": "duration"}
        item = InputFile(path=Path("b.md"), prompt="p", metadata={"duration": "auto"})
        payload = self._build(profile, item)
        assert payload["duration"] == "auto"

    def test_no_metadata_duration_leaves_params_untouched(self):
        profile = {"endpoint": "test/model", "duration_param_name": "duration"}
        item = InputFile(path=Path("b.md"), prompt="p", metadata={})
        payload = self._build(profile, item, params={"aspect_ratio": "16:9"})
        assert "duration" not in payload
        assert payload["aspect_ratio"] == "16:9"

    PVIDEO_SCHEMA = {
        "aspect_ratio": {"type": "string"},
        "duration": {"type": "integer"},
        "resolution": {"type": "string"},
        "num_inference_steps": {"type": "integer"},
        "draft": {"type": "boolean"},
    }

    def test_numeric_string_params_coerced_against_schema(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(path=Path("b.md"), prompt="p")
        params = {
            "aspect_ratio": "16:9",
            "duration": "5",
            "resolution": "720p",
            "num_inference_steps": "30",
            "draft": False,
            "prompt_upsampling": True,
        }
        payload = self._build(profile, item, params=params, schema=self.PVIDEO_SCHEMA)
        assert payload["duration"] == 5
        assert payload["num_inference_steps"] == 30
        assert payload["aspect_ratio"] == "16:9"
        assert payload["resolution"] == "720p"
        assert payload["draft"] is False
        assert payload["prompt_upsampling"] is True

    def test_quoted_boolean_param_coerced_against_schema(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(path=Path("b.md"), prompt="p")
        payload = self._build(
            profile, item, params={"draft": "false"}, schema=self.PVIDEO_SCHEMA
        )
        assert payload["draft"] is False

    def test_non_numeric_string_on_integer_param_passes_verbatim(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(path=Path("b.md"), prompt="p")
        payload = self._build(
            profile, item, params={"duration": "auto"}, schema=self.PVIDEO_SCHEMA
        )
        assert payload["duration"] == "auto"

    def test_duration_override_coerced_against_schema(self):
        profile = {"endpoint": "test/model", "duration_param_name": "duration"}
        item = InputFile(path=Path("b.md"), prompt="p", metadata={"duration": "10"})
        payload = self._build(profile, item, schema=self.PVIDEO_SCHEMA)
        assert payload["duration"] == 10

    def test_missing_schema_passes_params_verbatim(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(path=Path("b.md"), prompt="p")
        payload = self._build(profile, item, params={"duration": "5"}, schema={})
        assert payload["duration"] == "5"

    FPS_REF_SCHEMA = {
        "components": {"schemas": {"fps": {"type": "integer", "enum": [24, 48]}}},
        "duration": {"type": "integer"},
        "fps": {"allOf": [{"$ref": "#/components/schemas/fps"}], "default": 24},
    }

    def test_allof_ref_param_coerced(self):
        engine = Engine({"endpoint": "test/model"}, "/tmp/out")
        resolved = engine._resolve_schema_refs(
            self.FPS_REF_SCHEMA["fps"], self.FPS_REF_SCHEMA
        )
        assert engine._coerce_param_value("fps", "24", {"fps": resolved}) == 24
        item = InputFile(path=Path("b.md"), prompt="p")
        payload = self._build(
            {"endpoint": "test/model"},
            item,
            params={"duration": "5"},
            schema=self.FPS_REF_SCHEMA,
        )
        assert payload["duration"] == 5
        # fps is generator metadata and is stripped before the provider call
        assert "fps" not in payload

    def test_resolve_schema_refs_merges_ref_and_own_keys(self):
        engine = Engine({"endpoint": "test/model"}, "/tmp/out")
        resolved = engine._resolve_schema_refs(
            {"allOf": [{"$ref": "#/components/schemas/fps"}], "default": 24},
            self.FPS_REF_SCHEMA,
        )
        assert resolved == {"type": "integer", "enum": [24, 48], "default": 24}


class TestSchemaFetch:
    """Parsing the fal models API openapi payload into Input properties."""

    OPENAPI = {
        "paths": {
            "/fal-ai/flux/schnell": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/FluxSchnellInput"}
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "FluxSchnellInput": {
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {"type": "string"},
                        "num_images": {"type": "integer", "default": 1},
                        "output_format": {"type": "string", "enum": ["jpeg", "png"]},
                    },
                }
            }
        },
    }

    def test_request_body_ref_resolved(self):
        engine = Engine({"endpoint": "fal-ai/flux/schnell"}, "/tmp/out")
        assert engine._request_body_ref(self.OPENAPI) == "FluxSchnellInput"

    def test_parse_input_props_yields_types(self):
        engine = Engine({"endpoint": "fal-ai/flux/schnell"}, "/tmp/out")
        props = engine._parse_input_props(self.OPENAPI)
        assert props["num_images"]["type"] == "integer"
        assert props["output_format"]["enum"] == ["jpeg", "png"]
        assert props["prompt"]["type"] == "string"

    def test_parse_input_props_falls_back_to_input_suffix(self):
        doc = {
            "paths": {},
            "components": {
                "schemas": {
                    "WeirdInput": {"properties": {"fps": {"type": "integer"}}}
                }
            },
        }
        engine = Engine({"endpoint": "fal-ai/weird"}, "/tmp/out")
        props = engine._parse_input_props(doc)
        assert props["fps"]["type"] == "integer"

    def test_input_props_returns_empty_on_fetch_error(self):
        engine = Engine({"endpoint": "test/model"}, "/tmp/out")
        with patch.object(
            Engine, "_fetch_input_props", side_effect=OSError("offline")
        ):
            assert engine._input_props() == {}


class TestListStandbyProfiles:
    def test_img_shelf_returns_only_image_yamls(self):
        from engine_fal import list_standby_profiles

        imgs = list_standby_profiles("IMG")
        assert imgs
        assert all("IMG" in p.parts for p in imgs)

    def test_vid_shelf_returns_only_video_yamls(self):
        from engine_fal import list_standby_profiles

        vids = list_standby_profiles("VID")
        assert vids
        assert all("VID" in p.parts for p in vids)

    def test_unknown_media_type_returns_empty(self):
        from engine_fal import list_standby_profiles

        assert list_standby_profiles("TXT") == []

    def test_no_filter_returns_every_shelf(self):
        from engine_fal import list_standby_profiles

        all_profiles = list_standby_profiles()
        imgs = list_standby_profiles("IMG")
        assert set(all_profiles) >= set(imgs)


class _FakeError(Exception):
    def __init__(self, status=None, detail="", status_code=None):
        self.status = status
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail or str(status or status_code))


class TestErrorClassification:
    def _engine(self):
        return Engine({"endpoint": "test/model"}, "/tmp/out")

    def test_high_demand_is_retryable(self):
        err = _FakeError(
            detail=(
                "Service is currently unavailable due to high demand. "
                "Please try again later."
            )
        )
        eng = self._engine()
        assert eng._is_rate_limited(err)
        assert Engine._classify_error(err) == "retryable"
        assert eng._is_high_demand(err)

    def test_http_429_is_retryable(self):
        eng = self._engine()
        assert eng._is_rate_limited(_FakeError(status=429, detail="rate limited"))

    def test_status_code_attribute_is_honoured(self):
        eng = self._engine()
        assert eng._is_rate_limited(_FakeError(status_code=429, detail="slow down"))

    def test_ratelimit_class_string_is_retryable(self):
        eng = self._engine()
        assert eng._is_rate_limited(_FakeError(detail="ModelRateLimitError: boom"))

    def test_insufficient_payment_is_fatal(self):
        eng = self._engine()
        assert eng._is_fatal(_FakeError(detail="Insufficient balance to run model"))

    def test_http_402_is_fatal(self):
        eng = self._engine()
        assert eng._is_fatal(_FakeError(status=402, detail="Payment required"))

    def test_http_401_is_fatal(self):
        eng = self._engine()
        assert eng._is_fatal(_FakeError(status=401, detail="Unauthorized"))

    def test_unknown_5xx_is_neither_retryable_nor_fatal(self):
        eng = self._engine()
        err = _FakeError(status=500, detail="internal error")
        assert not eng._is_rate_limited(err)
        assert not eng._is_fatal(err)


class TestRetry:
    def test_transient_then_success_is_retried(self, tmp_path):
        line = "Service is currently unavailable due to high demand (E003)"
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.side_effect = [
                _FakeError(detail=line),
                {"images": [{"url": "https://example.com/out.png"}]},
            ]
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                with patch("urllib.request.urlretrieve", side_effect=_write_temp):
                    engine = Engine(
                        {
                            "endpoint": "test/model",
                            "media_type": "image",
                            "retry_on_429": 2,
                            "retry_base_delay": 0.0,
                        },
                        tmp_path,
                    )
                    results = engine.run(
                        [InputFile(path=Path("b.md"), prompt="hello")]
                    )
                    assert results[0].status == "ok"
                    assert mock_fal_client.subscribe.call_count == 2

    def test_other_error_is_not_retried(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.side_effect = RuntimeError("API timeout")
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {
                        "endpoint": "test/model",
                        "media_type": "image",
                        "retry_on_429": 3,
                        "retry_base_delay": 0.0,
                    },
                    tmp_path,
                )
                results = engine.run([InputFile(path=Path("b.md"), prompt="test")])
                assert results[0].status == "error"
                assert mock_fal_client.subscribe.call_count == 1


class TestCircuitBreakerAndFatalAbort:
    def test_circuit_breaker_aborts_after_n_consecutive(self, tmp_path):
        line = "AvailabilityError: high demand (E003)"
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.side_effect = _FakeError(detail=line)
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {
                        "endpoint": "test/model",
                        "media_type": "image",
                        "retry_on_429": 0,
                        "circuit_breaker_max": 2,
                    },
                    tmp_path,
                )
                inputs = [
                    InputFile(path=Path(f"{i}.md"), prompt=f"p{i}") for i in range(6)
                ]
                results = engine.run(inputs)
                # 2 errors recorded (circuit_max), then batch aborted.
                assert len(results) == 2
                assert all(r.status == "error" for r in results)
                # Did not attempt the remaining 4 items.
                assert mock_fal_client.subscribe.call_count == 2

    def test_fatal_error_aborts_batch_immediately(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.side_effect = _FakeError(
                status=402, detail="Payment required"
            )
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"},
                    tmp_path,
                )
                inputs = [
                    InputFile(path=Path(f"{i}.md"), prompt=f"p{i}") for i in range(5)
                ]
                results = engine.run(inputs)
                assert len(results) == 1
                assert results[0].status == "error"
                assert mock_fal_client.subscribe.call_count == 1


class TestImports:
    def test_init_exports_all_names(self):
        from engine_fal import (
            Engine,
            EngineError,
            InputFile,
            OutputFile,
            ProgressEvent,
            list_standby_profiles,
        )
        assert Engine is not None
        assert InputFile is not None
        assert OutputFile is not None
        assert ProgressEvent is not None
        assert EngineError is not None
        assert callable(list_standby_profiles)
