from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from transcripteur_whisper.services.history_service import HistoryArtifact, HistoryEntry
from transcripteur_whisper.ui.widgets.history_page import HistoryPage
from transcripteur_whisper.ui.workers import TaskRunner


class FakeHistoryService:
    def __init__(self, transcript: Path):
        self.transcript = transcript
        self.queries = []

    def week_start(self, _value=None):
        return date(2026, 9, 14)

    def week(self, start, query=""):
        self.queries.append((start, query))
        when = datetime(2026, 9, 15, 10, 15, tzinfo=timezone.utc)
        return [HistoryEntry(
            identifier="job:test:0",
            start=when,
            end=when + timedelta(minutes=30),
            title="Daily équipe",
            kind="recording_transcript",
            status="done",
            source_name="native_recording_test.wav",
            job_id="test",
            duration_seconds=1800,
            artifacts=(HistoryArtifact("transcription", self.transcript.name, self.transcript),),
        )]

    def read_text(self, path):
        return "Décision de la réunion"


def test_history_page_renders_week_and_preview(qtbot, tmp_path):
    transcript = tmp_path / "transcription.txt"
    transcript.write_text("ignored", encoding="utf-8")
    runner = TaskRunner()
    service = FakeHistoryService(transcript)
    page = HistoryPage(
        runner,
        jobs=object(),
        paths=SimpleNamespace(results=tmp_path),
        service=service,
    )
    qtbot.addWidget(page)
    page.show()
    page.activate()
    qtbot.waitUntil(lambda: runner.active_count == 0)

    cell = page.table.cellWidget(10, 1)
    assert cell is not None
    event = cell.findChild(QPushButton)
    assert event is not None
    assert "Daily équipe" in event.text()

    qtbot.mouseClick(event, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: runner.active_count == 0)
    assert page.detail_title.text() == "Daily équipe"
    assert "Décision de la réunion" in page.preview.toPlainText()
