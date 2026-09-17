"""AI summary preferences/provider parsing and forced recording fallback."""
from __future__ import annotations

import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from transcripteur_whisper.core.paths import AppPaths
from transcripteur_whisper.services import ai_summary
from transcripteur_whisper.services.ai_summary import SummaryPreferences, generate_daily_summary
from transcripteur_whisper.services.library_recording import LibraryRecordingService
from transcripteur_whisper.services.library_storage import AudioCatalog, StoragePreferences


def test_summary_preferences_keep_provider_keys_separate(tmp_path, monkeypatch):
    paths = AppPaths.create(tmp_path / "user")
    monkeypatch.setattr(ai_summary, "_protect", lambda value: "protected:" + value)
    monkeypatch.setattr(ai_summary, "_unprotect", lambda value: value.removeprefix("protected:"))
    preferences = SummaryPreferences.load(paths)
    preferences = preferences.save(
        paths, provider="openai", prompt="Mon prompt", replacement_key="sk-openai"
    )
    assert preferences.provider == "openai"
    assert preferences.key_for("openai") == "sk-openai"
    preferences = preferences.save(
        paths, provider="gemini", prompt="Mon prompt", replacement_key="gemini-key"
    )
    assert preferences.key_for("openai") == "sk-openai"
    assert preferences.key_for("gemini") == "gemini-key"
    assert SummaryPreferences.load(paths).provider == "gemini"


@pytest.mark.parametrize(
    ("provider", "response", "expected"),
    [
        ("openai", {"output_text": "openai summary"}, "openai summary"),
        ("anthropic", {"content": [{"type": "text", "text": "anthropic summary"}]}, "anthropic summary"),
        ("gemini", {"candidates": [{"content": {"parts": [{"text": "gemini summary"}]}}]}, "gemini summary"),
        ("deepseek", {"choices": [{"message": {"content": "deepseek summary"}}]}, "deepseek summary"),
    ],
)
def test_daily_summary_provider_response_parsing(monkeypatch, provider, response, expected):
    monkeypatch.setattr(ai_summary, "_request_json", lambda *args, **kwargs: response)
    assert generate_daily_summary(provider, "secret", "Prompt", "Transcript") == expected


def test_force_stop_releases_backend_and_keeps_managed_wav(tmp_path):
    paths = AppPaths.create(tmp_path / "user")
    preferences = StoragePreferences.load(paths)
    preferences.audio_work_dir.mkdir(parents=True, exist_ok=True)
    audio_paths = replace(paths, results=preferences.audio_work_dir)
    catalog = AudioCatalog(paths, preferences)

    journal = paths.temp / "recordings"
    journal.mkdir(parents=True, exist_ok=True)
    output = journal / ("native_recording_" + "a" * 32 + ".wav")

    class Source:
        def __init__(self, key):
            self.key = key
            self.reader_done = threading.Event()
            self.writer_thread = None

    class Session:
        recording_id = "a" * 32
        output_path = output
        speaker_name = "Speakers"
        microphone_name = "Microphone"
        _system_reader_thread = None
        _system_context = None
        _stop_ts = None

        def __init__(self):
            self._stop_event = threading.Event()
            self._sources = {"microphone": Source("microphone"), "system": Source("system")}

        def _close_microphone_stream(self):
            return None

        def _close_system_loopback(self):
            self._system_context = None

        def _mark_source_failure(self, *_args):
            return None

        def _mix_sources(self):
            self.output_path.write_bytes(b"valid wav placeholder")
            return 2.0

        def _salvage_best_source(self):
            raise AssertionError("mix should succeed")

        def _verify_final_output(self):
            assert self.output_path.exists()

        def has_live_workers(self):
            return False

        def _discard_source_files(self):
            return None

    class Devices:
        def __init__(self):
            self.gate = threading.RLock()
            self.active = True

        def set_capture_active(self, value):
            self.active = bool(value)

    session = Session()
    recorder = SimpleNamespace(_active=session, _completed={}, _lock=threading.RLock())
    service = object.__new__(LibraryRecordingService)
    service.paths = audio_paths
    service.catalog = catalog
    service.devices = Devices()
    service._recorder = recorder
    service._active_result = SimpleNamespace(recording_id=session.recording_id)
    service._last_completed = None
    service._pending_final = None
    service._resources_active = True
    service._selected = {"system": "speaker"}
    service._capture_name = "Daily"
    service._capture_start = None
    service.last_publish_error = None
    service._orphan_sessions = []

    result = service._force_stop()
    assert recorder._active is None
    assert not service.devices.active
    assert result.path.exists()
    assert result.path.parent == preferences.audio_work_dir
    assert catalog.find(result.path) is not None
