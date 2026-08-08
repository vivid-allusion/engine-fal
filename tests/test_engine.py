import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine_fal import Engine, EngineError, InputFile, OutputFile, ProgressEvent  # noqa: E402
import engine_fal.metadata as meta  # noqa: E402


class TestDatatypes:
    def test_inputfile_defaults(self):
        f = InputFile(path=Path("test.md"), prompt="hello")
        assert f.path == Path("test.md")
        assert f.prompt == "hello"
        assert f.reference_urls == []
        assert f.metadata == {}

    def test_outputfile_defaults(self):
        o = OutputFile(source_path=Path("test.md"))
        assert o.source_path == Path("test.md")
        assert o.path is None
        assert o.status == "ok"
        assert o.error_msg == ""
        assert o.media_type == ""
        assert o.metadata == {}

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
        with pytest.raises(EngineError, match="Missing 'endpoint'"):
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
            mock_fal_client.subscribe.return_value = {"images": [{"url": "https://example.com/out.png"}]}

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

    def test_per_bullet_error_returns_error_outputfile(self, tmp_path):
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
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"},
                    tmp_path,
                )
                bullets = [
                    InputFile(path=Path(f"b{i}.md"), prompt="test")
                    for i in range(3)
                ]
                results = engine.run(bullets)
                statuses = [r.status for r in results]
                assert statuses.count("ok") == 2
                assert statuses.count("error") == 1

    def test_save_results_downloads_urls(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {"images": [{"url": "https://example.com/img.png"}]}
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                with patch("urllib.request.urlretrieve") as mock_retrieve:
                    mock_retrieve.return_value = (None, None)
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
                    mock_retrieve.assert_called_once()

    def test_video_result_normalization(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {"video": {"url": "https://example.com/out.mp4"}}
            with patch.dict("sys.modules", {"fal_client": mock_fal_client}):
                with patch("urllib.request.urlretrieve") as mock_retrieve:
                    mock_retrieve.return_value = (None, None)
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
            mock_fal_client.subscribe.return_value = {"video": {"url": "https://example.com/out.mp4"}}
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
                    assert fal_input["fps"] == 24

    def test_reference_param_from_profile(self, tmp_path):
        with patch.dict("os.environ", {"FAL_KEY": "test_key"}):
            mock_fal_client = MagicMock()
            mock_fal_client.subscribe.return_value = {"images": [{"url": "https://a.com/ok.png"}]}
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
