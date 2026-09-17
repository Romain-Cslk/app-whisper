"""Qt regression checks for the actual desktop composition (fake I/O backends)."""
import threading
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QFileDialog

from tests.test_desktop_ui import FakeJobs, FakeMedia, FakeModels, FakeRecording
from transcripteur_whisper.core.paths import AppPaths
from transcripteur_whisper.services.library_storage import StoragePreferences
from transcripteur_whisper.ui.library_window import MainWindow
from transcripteur_whisper.ui.widgets import library_controls
from transcripteur_whisper.ui.widgets.history_page import install_history_tab


@pytest.fixture
def window(qtbot, tmp_path):
    paths = AppPaths.create(tmp_path / "user")
    jobs, media = FakeJobs(), FakeMedia()
    jobs.cleanup_audio = lambda: {"removed": [], "errors": []}
    jobs.load_history = jobs.history
    recording = FakeRecording(paths)
    recording.discarded = []
    recording.discard_recovery = recording.discarded.append
    widget = MainWindow(paths, jobs=jobs, media=media, models=FakeModels(),
                        recording=recording, devices=object(), monitor=False)
    def close(w):
        w.close()
        qtbot.waitUntil(lambda: w._close_ready, timeout=6000)
    qtbot.addWidget(widget, before_close_func=close)
    widget.show()
    qtbot.waitUntil(lambda: not widget._initializing and widget.runner.active_count == 0)
    return widget


def test_all_tabs_and_no_duplicate_history(window):
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == [
        "Transcrire des fichiers", "Enregistrer le son", "Résultats", "Historique", "Journal", "Paramètres"]
    assert install_history_tab(window, window.paths) is window.history_page
    assert window.tabs.count() == 6


def test_storage_page_exposes_work_and_final_text_paths_without_audio_archive(window):
    page = window.storage_page
    assert page.transcripts_work.text() != page.transcripts_final.text()
    assert "sessions" in page.audio_work.text().lower()
    assert "sessions" in page.transcripts_work.text().lower()
    assert not hasattr(page, "keep_final_audio")
    assert not hasattr(page, "audio_final")
    assert hasattr(page, "policy")


def test_record_name_is_shared_and_button_is_visible(window):
    window.output_name.setText("Daily produit")
    assert window.recording_panel.name.text() == "Daily produit"
    window.recording_panel.name.setText("Autre réunion")
    assert window.output_name.text() == "Autre réunion"
    assert window.recording_panel.record_button.minimumHeight() >= 46
    assert "Démarrer" in window.recording_panel.record_button.text()


def test_named_export_default_for_txt_and_zip(window, monkeypatch):
    calls = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (calls.append(a[2]) or "", ""))
    window.results.load("job", {"output_name": "Mon daily", "files": []})
    window.results.export_batch("txt")
    window.results.export_batch("zip")
    assert calls == ["transcription_Mon daily.txt", "resultats_Mon daily.zip"]


def test_abandonment_requires_confirmation(window, monkeypatch, tmp_path):
    path = tmp_path / "recovery.wav"
    path.write_bytes(b"journal")
    window.recording_panel._show_recovery((path,))
    monkeypatch.setattr(library_controls, "confirm_removal", lambda *a, **k: False)
    window.recording_panel.discard_selected()
    assert window.recording.discarded == []
    assert path.exists()


def test_settings_saved_off_gui_and_paths_not_redirected_mid_session(window, qtbot, tmp_path, monkeypatch):
    seen = []
    original = StoragePreferences.save
    def save(preferences, paths):
        seen.append(QThread.currentThread())
        return original(preferences, paths)
    monkeypatch.setattr(StoragePreferences, "save", save)
    old = window.paths.results
    window.storage_page.transcripts_final.setText(str(tmp_path / "Transcripts-final"))
    window.storage_page.audio_work.setText(str(tmp_path / "Audio-work"))
    window.storage_page.transcripts_work.setText(str(tmp_path / "Transcripts-work"))
    window.storage_page.save()
    qtbot.waitUntil(lambda: window.runner.active_count == 0)
    assert seen and seen[0] != QApplication.instance().thread()
    assert window.paths.results == old
    saved = StoragePreferences.load(window.storage_paths)
    assert saved.transcript_dir == tmp_path / "Transcripts-final"
    assert saved.transcript_work_dir == tmp_path / "Transcripts-work"
    assert saved.audio_work_dir == tmp_path / "Audio-work"
    assert not saved.keep_final_audio
    assert saved.retention == window.storage_page.policy.currentData()


def test_history_filters_apply_to_associated_files(window, qtbot):
    audio = window.catalog.preferences.audio_dir / "managed.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"fake wav")
    record = window.catalog.register(audio, "record", duration=60)
    transcript = window.catalog.preferences.transcript_dir / "transcription_Daily.txt"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("Selected transcript")
    window.jobs.state.update(id="a" * 32, output_name="Daily", created_at=datetime.now(timezone.utc).isoformat(),
        files=[{"name": audio.name, "status": "done", "transcription_path": str(transcript),
                "document_path": None, "out_path": str(transcript), "recording_id": record["id"]}])
    window.jobs.submitted.append(True)
    page = window.history_page
    window.tabs.setCurrentWidget(page)
    qtbot.waitUntil(lambda: not page._loading and bool(page.entries))
    assert len(page.entries) == 1
    assert next(iter(page.entries.values())).has_audio
    page.audios.setChecked(False)
    qtbot.waitUntil(lambda: not page._loading and not next(iter(page.entries.values())).has_audio)
    qtbot.waitUntil(lambda: page.preview.toPlainText() == "Selected transcript")
    page.transcripts.setChecked(False)
    qtbot.waitUntil(lambda: not page._loading and not page.entries)
    assert not page.delete_button.isEnabled()


def test_journal_shows_phase_and_percentage(window):
    window._render_job({"status": "running", "progress": .42, "logs": ["segment recu"],
                       "files": [{"name": "meeting.wav", "status": "running", "stage": "transcription_api",
                                  "progress": .42, "segment_index": 2, "segment_count": 5}]})
    assert "42 %" in window.live_status.text()
    assert "2/5" in window.live_status.text()
    assert "segment recu" in window.logs.toPlainText()
    assert window.follow_logs.isChecked()


def test_filter_changed_during_loading_is_not_dropped(window, qtbot, monkeypatch):
    page = window.history_page
    gate, started = threading.Event(), threading.Event()
    calls = []
    def delayed(week, query, *, audio, transcripts):
        calls.append((audio, transcripts))
        if len(calls) == 1:
            started.set()
            assert gate.wait(5)
        return []
    monkeypatch.setattr(page.service, "week", delayed)
    page.refresh()
    qtbot.waitUntil(started.is_set)
    page.audios.setChecked(False)
    gate.set()
    qtbot.waitUntil(lambda: len(calls) >= 2 and not page._loading)
    assert calls[-1] == (False, True)
