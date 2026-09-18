"""Qt interaction/geometry checks for the actual workspace, with fake I/O only."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMessageBox

from tests.test_desktop_ui import FakeJobs, FakeMedia, FakeModels, FakeRecording
from transcripteur_whisper.core.paths import AppPaths
from transcripteur_whisper.models.transcription import TranscriptionOptions
from transcripteur_whisper.services.recovery_sessions import RecoveryInbox
from transcripteur_whisper.ui.widgets.recovery_dialog import RecoveryDialog
from transcripteur_whisper.ui.workspace_window import MainWindow


@pytest.fixture
def workspace(qtbot, tmp_path):
    paths = AppPaths.create(tmp_path / 'user')
    jobs = FakeJobs()
    jobs.cleanup_audio = lambda: {'removed': [], 'errors': []}
    jobs.load_history = jobs.history
    window = MainWindow(paths, jobs=jobs, media=FakeMedia(), models=FakeModels(),
                        recording=FakeRecording(paths), devices=object(), monitor=False)
    def close(w):
        w.close()
        qtbot.waitUntil(lambda: w._close_ready, timeout=6000)
    qtbot.addWidget(window, before_close_func=close)
    window.show()
    qtbot.waitUntil(lambda: not window._initializing and window.runner.active_count == 0)
    window.notice.hide()
    return window


@pytest.mark.parametrize('size', [(1280, 800), (1024, 700), (700, 560)])
def test_chrome_leaves_space_and_all_tabs_fit(workspace, qtbot, size):
    workspace.resize(*size)
    for index in range(workspace.tabs.count()):
        workspace.tabs.setCurrentIndex(index)
        qtbot.wait(25)
        assert workspace.width() <= size[0] + 2
        assert workspace.height() <= size[1] + 2
        assert workspace.tabs.currentWidget().height() >= workspace.height() * .65
        assert workspace.header.height() <= 44
    assert not workspace.task_strip.isVisible()


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_only_one_tab_visible_and_no_opacity_effects(workspace, qtbot, theme):
    workspace.settings.set('theme', theme)
    workspace._apply_theme()
    for _ in range(3):
        for index in range(workspace.tabs.count()):
            workspace.tabs.setCurrentIndex(index)
            qtbot.wait(10)
            assert [i for i in range(workspace.tabs.count()) if workspace.tabs.widget(i).isVisible()] == [index]
            assert all(workspace.tabs.widget(i).graphicsEffect() is None for i in range(workspace.tabs.count()))


@pytest.mark.parametrize('width', [680, 1120])
def test_audio_selectors_and_meters_never_overlap(workspace, qtbot, width):
    workspace.resize(width, 720)
    workspace.tabs.setCurrentWidget(workspace.record_page)
    qtbot.wait(30)
    panel = workspace.recording_panel
    for combo, meter in ((panel.microphone, panel.mic_meter), (panel.speaker, panel.system_meter)):
        bottom = combo.mapTo(panel, QPoint(0, combo.height())).y()
        top = meter.mapTo(panel, QPoint(0, 0)).y()
        assert top > bottom
        assert meter.height() == 16
        assert combo.height() >= 25
    assert not panel.recovery_choices.isVisible()
    assert not panel.discard_button.isVisible()


def test_journal_uses_available_vertical_space(workspace, qtbot):
    workspace.tabs.setCurrentWidget(workspace.journal_page)
    qtbot.wait(30)
    assert workspace.logs.isVisible()
    assert workspace.logs.height() > workspace.journal_page.height() * .6
    workspace.logs.setPlainText('Journal de test\nTraitement en cours')
    assert 'Journal de test' in workspace.logs.toPlainText()


def test_compact_history_has_full_width_reading_mode(workspace, qtbot):
    workspace.resize(700, 560)
    page = workspace.history_page
    workspace.tabs.setCurrentWidget(page)
    qtbot.wait(30)
    page._choose_compact_view(True)
    qtbot.wait(10)
    assert page.detail.isVisible() and not page.table.isVisible()
    assert page.preview.width() > 400
    assert page.preview.height() >= 72
    page._choose_compact_view(False)
    assert page.table.isVisible() and not page.detail.isVisible()


def test_recording_remains_available_during_transcription(workspace):
    workspace._set_active(True)
    assert workspace.recording_panel.isEnabled()
    assert not workspace.files.isEnabled()
    assert workspace.task_strip.isVisible()
    workspace._set_active(False)
    assert not workspace.task_strip.isVisible()


def test_main_screen_has_three_named_writing_profiles(workspace):
    assert [workspace.profile_combo.itemText(i) for i in range(workspace.profile_combo.count())] == ['Résumé', 'Daily', 'Compte rendu']
    assert not workspace.summary_requested.isChecked()
    workspace.summary_requested.setChecked(True)
    assert workspace.profile_combo.isEnabled()


def test_prompt_editor_saves_names_context_and_content(workspace, qtbot):
    page = workspace.writing_page
    page.name.setText('Daily équipe')
    page.prompt.setPlainText('Instructions de test sans invention.')
    page.context.setPlainText('Contexte de test')
    page.people.setPlainText('Alice : testeuse')
    page.vocabulary.setPlainText('ABC : application de test')
    page.save()
    qtbot.waitUntil(lambda: not page._saving)
    stored = workspace.writing_store.load()
    assert stored.selected.name == 'Daily équipe'
    assert stored.context == 'Contexte de test'
    assert stored.people == 'Alice : testeuse'
    assert 'Daily équipe' in [workspace.profile_combo.itemText(i) for i in range(workspace.profile_combo.count())]
    assert not page.dirty


def test_selected_prompt_is_frozen_in_submitted_options(workspace, qtbot, tmp_path):
    from transcripteur_whisper.services.summary_profiles import WritingStore
    store = WritingStore(workspace.storage_paths)
    preferences = replace(store.load(), context='Contexte gelé', people='Alice', vocabulary='ABC')
    selected = 'resume'
    workspace._writing_snapshot = (preferences, selected, 'anthropic')
    calls = []
    workspace.jobs.submit = lambda files, options, api_key='': calls.append(options) or 'job-one'
    workspace._submit_job(TranscriptionOptions())
    qtbot.waitUntil(lambda: bool(calls))
    assert calls[0].generate_summary and calls[0].summary_provider == 'anthropic'
    assert calls[0].summary_prompt.startswith(preferences.profiles[0].prompt)
    assert 'Contexte gelé' in calls[0].summary_prompt


def test_abandonment_refreshes_rows_and_reports_completion(workspace, qtbot, tmp_path, monkeypatch):
    root = tmp_path / 'journals'
    root.mkdir()
    for role in ('microphone', 'system'):
        (root / f'.native_recording_{"a" * 32}_{role}.wav').write_bytes(b'original')
    inbox = RecoveryInbox(root, tmp_path / 'quarantine')
    service = SimpleNamespace(is_active=False, recovery_sessions=inbox.list,
                              discard_sessions=inbox.discard, recovery_inbox=inbox)
    dialog = RecoveryDialog(workspace.runner, service, workspace)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: not dialog._busy)
    assert dialog.table.rowCount() == 1
    dialog._select_visible()
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: QMessageBox.StandardButton.Yes)
    dialog._operate(True)
    qtbot.waitUntil(lambda: not dialog._busy)
    assert dialog.table.rowCount() == 0
    assert list(inbox.quarantine.rglob('*.wav'))
    assert 'Aucune session' in dialog.status.text() or 'mise(s) de côté' in dialog.status.text()
