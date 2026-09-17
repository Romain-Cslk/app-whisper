from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from transcripteur_whisper.services.history_service import HistoryService


class FakeJobs:
    def __init__(self, history):
        self._history = history

    def history(self):
        return self._history


def make_paths(tmp_path: Path):
    results = tmp_path / "results"
    results.mkdir()
    return SimpleNamespace(results=results)


def test_week_merges_native_recording_and_transcript(tmp_path: Path):
    paths = make_paths(tmp_path)
    audio = paths.results / "native_recording_abc.wav"
    audio.write_bytes(b"wav")
    finished = datetime(2026, 9, 15, 10, 30, tzinfo=timezone.utc)
    os.utime(audio, (finished.timestamp(), finished.timestamp()))
    job_id = "a" * 32
    job_dir = paths.results / job_id
    job_dir.mkdir()
    transcript = job_dir / "transcription.txt"
    transcript.write_text("Bonjour projet Atlas", encoding="utf-8")
    jobs = FakeJobs([
        {
            "id": job_id,
            "created_at": "2026-09-15T10:31:00+00:00",
            "status": "done",
            "output_name": "Daily Atlas",
            "files": [{
                "name": audio.name,
                "status": "done",
                "transcription_path": str(transcript),
                "document_path": None,
            }],
        }
    ])
    service = HistoryService(paths, jobs, duration_probe=lambda _: 1800)
    entries = service.week(datetime(2026, 9, 15).date())
    assert len(entries) == 1
    entry = entries[0]
    assert entry.kind == "recording_transcript"
    assert entry.title == "Daily Atlas"
    assert entry.duration_seconds == 1800
    assert {artifact.kind for artifact in entry.artifacts} == {"audio", "transcription"}
    expected_start = finished.astimezone() - timedelta(seconds=1800)
    assert entry.start == expected_start


def test_week_includes_untranscribed_recording(tmp_path: Path):
    paths = make_paths(tmp_path)
    audio = paths.results / "native_recording_deadbeef.wav"
    audio.write_bytes(b"wav")
    stamp = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc).timestamp()
    os.utime(audio, (stamp, stamp))
    service = HistoryService(paths, FakeJobs([]), duration_probe=lambda _: 600)
    entries = service.week(datetime(2026, 9, 14).date())
    assert [entry.kind for entry in entries] == ["recording"]
    assert entries[0].has_audio and not entries[0].has_transcript


def test_search_matches_transcript_content(tmp_path: Path):
    paths = make_paths(tmp_path)
    job_id = "b" * 32
    job_dir = paths.results / job_id
    job_dir.mkdir()
    transcript = job_dir / "transcription.txt"
    transcript.write_text("Décision : livraison jeudi matin.", encoding="utf-8")
    jobs = FakeJobs([{
        "id": job_id,
        "created_at": "2026-09-16T08:15:00+00:00",
        "status": "done",
        "output_name": "Point projet",
        "files": [{"name": "meeting.mp3", "status": "done", "transcription_path": str(transcript)}],
    }])
    service = HistoryService(paths, jobs, duration_probe=lambda _: None)
    assert len(service.week(datetime(2026, 9, 16).date(), "livraison jeudi")) == 1
    assert service.week(datetime(2026, 9, 16).date(), "introuvable") == []


def test_unsafe_paths_are_ignored_and_cannot_be_read(tmp_path: Path):
    paths = make_paths(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    job_id = "c" * 32
    (paths.results / job_id).mkdir()
    jobs = FakeJobs([{
        "id": job_id,
        "created_at": "2026-09-16T08:15:00+00:00",
        "status": "done",
        "files": [{"name": "meeting.mp3", "status": "done", "transcription_path": str(outside)}],
    }])
    service = HistoryService(paths, jobs, duration_probe=lambda _: None)
    assert service.all_entries() == []
    with pytest.raises(ValueError):
        service.read_text(outside)


def test_imported_file_uses_job_creation_hour(tmp_path: Path):
    paths = make_paths(tmp_path)
    job_id = "d" * 32
    job_dir = paths.results / job_id
    job_dir.mkdir()
    transcript = job_dir / "transcription.txt"
    transcript.write_text("texte", encoding="utf-8")
    jobs = FakeJobs([{
        "id": job_id,
        "created_at": "2026-09-16T13:42:00+00:00",
        "status": "done",
        "files": [{"name": "client.mp3", "status": "done", "transcription_path": str(transcript)}],
    }])
    service = HistoryService(paths, jobs, duration_probe=lambda _: None)
    entry = service.all_entries()[0]
    assert entry.kind == "transcript"
    assert entry.start.minute == 42
    assert entry.end - entry.start == timedelta(minutes=45)
