"""Service-level tests; no audio device, real inference or external API call."""
import json
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from transcripteur_whisper.core.paths import AppPaths
from transcripteur_whisper.models.jobs import JobError
from transcripteur_whisper.services.library_history import LibraryHistoryService, filter_artifacts
from transcripteur_whisper.services.library_jobs import LibraryJobService
from transcripteur_whisper.services.library_recording import LibraryRecordingService
from transcripteur_whisper.services.library_storage import AudioCatalog, LibraryError, StoragePreferences


@pytest.fixture
def library(tmp_path):
    paths = AppPaths.create(tmp_path / "data")
    preferences = StoragePreferences.load(paths)
    preferences.audio_work_dir.mkdir(parents=True, exist_ok=True)
    preferences.transcript_work_dir.mkdir(parents=True, exist_ok=True)
    catalog = AudioCatalog(paths, preferences)
    # Exercise storage methods without starting the inference executors.
    jobs = object.__new__(LibraryJobService)
    jobs.paths = replace(paths, results=preferences.transcript_work_dir)
    jobs.catalog, jobs.preferences = catalog, preferences
    jobs.retention = "processed"
    jobs._lock = threading.RLock()
    jobs._jobs, jobs._manifest_paths = {}, {}
    jobs._futures = {}
    yield paths, catalog, jobs


def job_fixture(library, *, status="done", content="hello", identifier="b" * 32):
    paths, catalog, jobs = library
    audio = catalog.preferences.audio_dir / ("native_recording_" + "a" * 32 + ".wav")
    audio.parent.mkdir(parents=True, exist_ok=True)
    if not audio.exists():
        audio.write_bytes(b"synthetic audio")
    record = catalog.find(audio) or catalog.register(audio, "audio-one", duration=60)
    manifest_folder = jobs.paths.results / identifier
    manifest_folder.mkdir(parents=True, exist_ok=True)
    transcript = catalog.preferences.transcript_dir / "transcription_Daily.txt"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(content, encoding="utf-8")
    metadata = {"name": audio.name, "path": str(audio), "out_path": str(transcript),
                "transcription_path": str(transcript), "document_path": None, "status": status,
                "transcription_available": True, "document_available": False,
                "recording_id": record["id"], "recording_started_at": record["started_at"],
                "recording_duration": record["duration"], "stage": status, "progress": 1.0}
    job = {"id": identifier, "job_id": identifier, "created_at": datetime.now(timezone.utc).isoformat(),
           "status": status, "output_name": "Daily", "files": [metadata], "logs": [],
           "cancel_requested": False, "storage_state": "published",
           "storage_transcript_dir": str(catalog.preferences.transcript_dir),
           "storage_transcript_work_dir": str(jobs.paths.results)}
    jobs._jobs[identifier] = job
    jobs._manifest_paths[identifier] = manifest_folder / "job.json"
    jobs._persist(identifier)
    return audio, transcript, job


@pytest.mark.parametrize("status", ["partial", "error", "cancelled", "pending", "running"])
def test_processed_policy_keeps_incomplete_and_active_jobs(library, status):
    audio, _, _ = job_fixture(library, status=status)
    assert jobs_cleanup(library)["removed"] == []
    assert audio.exists()


def jobs_cleanup(library):
    return library[2].cleanup_audio()


def test_processed_requires_nonempty_existing_transcript(library):
    audio, text, _ = job_fixture(library, content="")
    jobs_cleanup(library)
    assert audio.exists()
    text.write_text("Complete transcription")
    assert jobs_cleanup(library)["removed"] == [audio.name]
    assert not audio.exists() and text.exists()


def test_another_active_job_protects_same_audio(library):
    audio, text, _ = job_fixture(library)
    job_fixture(library, status="running", identifier="c" * 32)
    jobs_cleanup(library)
    assert audio.exists() and text.exists()


def test_delete_text_updates_manifest_and_preserves_audio_and_document(library):
    audio, text, job = job_fixture(library)
    document = text.with_name("document_Daily.txt")
    document.write_text("Summary")
    job["files"][0].update(document_path=str(document), document_available=True)
    jobs = library[2]
    jobs.delete_file(text)
    stored = json.loads(jobs._manifest_paths[job["id"]].read_text())
    assert stored["files"][0]["transcription_path"] is None
    assert not stored["files"][0]["transcription_available"]
    assert audio.exists() and document.exists() and not text.exists()
    assert jobs.snapshot(job["id"])["files"][0]["out_path"] == str(document)


def test_deletion_refuses_active_job_and_external_audio(library, tmp_path):
    audio, text, _ = job_fixture(library, status="running")
    for path in (audio, text):
        with pytest.raises(JobError):
            library[2].delete_file(path)
        assert path.exists()
    external = tmp_path / "source.wav"
    external.write_bytes(b"external source")
    with pytest.raises(LibraryError):
        library[2].delete_file(external)
    assert external.exists()


def test_imported_source_paths_not_written_to_manifest(library):
    _, _, job = job_fixture(library)
    manifest = json.loads(library[2]._manifest_paths[job["id"]].read_text())
    assert "path" not in manifest["files"][0]
    assert manifest["files"][0]["recording_id"] == "audio-one"


def test_history_audio_and_transcript_are_one_entry_and_filter_union(library):
    paths, catalog, jobs = library
    audio, transcript, _ = job_fixture(library)
    history = LibraryHistoryService(paths, jobs, catalog)
    all_entries = history.all_entries()
    assert len(all_entries) == 1
    entry = all_entries[0]
    assert entry.has_audio and entry.has_transcript
    assert [a.kind for a in filter_artifacts(entry, True, False).artifacts] == ["audio"]
    assert [a.kind for a in filter_artifacts(entry, False, True).artifacts] == ["transcription"]
    assert len(filter_artifacts(entry, True, True).artifacts) == 2
    assert filter_artifacts(entry, False, False) is None
    assert history.week(entry.start, audio=False, transcripts=False) == []


def test_deleting_audio_keeps_transcript_recording_date(library):
    paths, catalog, jobs = library
    audio, transcript, _ = job_fixture(library)
    history = LibraryHistoryService(paths, jobs, catalog)
    before = history.all_entries()[0]
    history.delete_artifact(before.identifier, audio)
    after = history.all_entries()[0]
    assert after.start == before.start
    assert after.has_transcript and not after.has_audio and transcript.exists()


def test_history_rejects_a_file_not_in_selected_entry(library):
    paths, catalog, jobs = library
    _, text, _ = job_fixture(library)
    history = LibraryHistoryService(paths, jobs, catalog)
    with pytest.raises(LibraryError):
        history.delete_artifact("wrong-entry", text)
    assert text.exists()


def test_history_reads_custom_folders_without_enrolling_external_audio(library, tmp_path):
    paths, _, jobs = library
    folder = tmp_path / "archive"
    folder.mkdir()
    text = folder / "External transcript.txt"
    text.write_text("unique research result")
    wav = folder / "source.wav"
    wav.write_bytes(b"not app-owned")
    pref = replace(StoragePreferences.load(paths), history_dirs=(folder,))
    catalog = AudioCatalog(paths, pref)
    history = LibraryHistoryService(paths, jobs, catalog)
    entries = history.all_entries()
    assert len(entries) == 2
    assert catalog.records == {}
    assert len(history.week(entries[0].start, "unique research")) == 1


def test_fulltext_search_finds_term_across_stream_chunk_boundary(library):
    paths, catalog, jobs = library
    _, _, _job = job_fixture(library, content="x" * 65533 + "TARGET" + "y" * 70000)
    history = LibraryHistoryService(paths, jobs, catalog)
    entry = history.all_entries()[0]
    assert len(history.week(entry.start, "target")) == 1
    assert history.week(entry.start, "absent") == []


def test_abandonment_deletes_only_selected_journal_and_refuses_active_capture(library):
    paths, _, _ = library
    service = object.__new__(LibraryRecordingService)
    service._journal_dir = paths.temp / "recordings"
    service._journal_dir.mkdir()
    first = service._journal_dir / (".native_recording_" + "a" * 32 + "_microphone.wav")
    second = first.with_name(first.name.replace("microphone", "system"))
    first.write_bytes(b"mic")
    second.write_bytes(b"pc")
    service._active_result = None
    service._resources_active = False
    service._pending_final = None
    service._discard_recovery(first)
    assert not first.exists() and second.exists()
    service._resources_active = True
    with pytest.raises(LibraryError):
        service._discard_recovery(second)
    assert second.exists()


def test_export_reads_previous_library_after_storage_change(library, tmp_path):
    paths, catalog, jobs = library
    _, text, job = job_fixture(library)
    # Change the write root; existing manifests keep their original location.
    from dataclasses import replace
    jobs.paths = replace(paths, results=tmp_path / "new-results")
    result = jobs.export(job["id"], tmp_path / "export.txt", kind="transcription")
    assert result.read_text().rstrip("\n") == text.read_text().rstrip("\n")


def test_publish_moves_completed_text_from_work_to_final_without_moving_manifest(library):
    paths, catalog, jobs = library
    identifier = "e" * 32
    folder = jobs.paths.results / identifier
    folder.mkdir(parents=True)
    source = folder / "transcription_Mon daily.txt"
    source.write_text("texte final", encoding="utf-8")
    job = {
        "id": identifier, "job_id": identifier,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "done", "output_name": "Mon daily", "logs": [],
        "cancel_requested": False, "storage_state": "pending",
        "storage_transcript_dir": str(catalog.preferences.transcript_dir),
        "storage_transcript_work_dir": str(jobs.paths.results),
        "files": [{
            "name": "meeting.wav", "status": "done", "stage": "done", "progress": 1.0,
            "out_path": str(source), "transcription_path": str(source), "document_path": None,
            "transcription_available": True, "document_available": False,
        }],
    }
    jobs._jobs[identifier] = job
    jobs._manifest_paths[identifier] = folder / "job.json"
    jobs._persist(identifier)

    assert jobs._publish_job(identifier)
    snapshot = jobs.snapshot(identifier)
    final = Path(snapshot["files"][0]["transcription_path"])
    assert final.parent == catalog.preferences.transcript_dir
    assert final.name.startswith("transcription_Mon daily")
    assert final.read_text(encoding="utf-8") == "texte final"
    assert not source.exists()
    assert jobs._manifest_paths[identifier].parent == folder
    assert jobs._manifest_paths[identifier].is_file()
    assert jobs._jobs[identifier]["storage_state"] == "published"


def test_publish_failure_keeps_intermediate_transcript(library, monkeypatch):
    _paths, catalog, jobs = library
    identifier = "f" * 32
    folder = jobs.paths.results / identifier
    folder.mkdir(parents=True)
    source = folder / "transcription_Fallback.txt"
    source.write_text("safe local result", encoding="utf-8")
    jobs._jobs[identifier] = {
        "id": identifier, "job_id": identifier,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "done", "output_name": "Fallback", "logs": [],
        "cancel_requested": False, "storage_state": "pending",
        "storage_transcript_dir": str(catalog.preferences.transcript_dir),
        "files": [{
            "name": "meeting.wav", "status": "done", "stage": "done", "progress": 1.0,
            "out_path": str(source), "transcription_path": str(source), "document_path": None,
            "transcription_available": True, "document_available": False,
        }],
    }
    jobs._manifest_paths[identifier] = folder / "job.json"
    jobs._persist(identifier)

    monkeypatch.setattr("transcripteur_whisper.services.library_jobs.atomic_copy",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("OneDrive lock")))
    with pytest.raises(PermissionError):
        jobs._publish_job(identifier)
    assert source.read_text(encoding="utf-8") == "safe local result"
    assert jobs._jobs[identifier]["storage_state"] == "pending"


def test_named_recording_is_published_when_audio_archive_is_enabled(library, monkeypatch):
    from dataclasses import dataclass

    from transcripteur_whisper.services.recording_service import RecordingService

    @dataclass(frozen=True)
    class Result:
        recording_id: str
        path: Path
        duration: float

    _paths, catalog, _ = library
    catalog.preferences = replace(catalog.preferences, keep_final_audio=True)
    catalog.preferences.audio_work_dir.mkdir(parents=True, exist_ok=True)
    raw = catalog.preferences.audio_work_dir / ("native_recording_" + "d" * 32 + ".wav")
    raw.write_bytes(b"audio")
    original = Result("d" * 32, raw, 60)
    service = object.__new__(LibraryRecordingService)
    service.catalog = catalog
    service._capture_name = "Daily produit"
    service._capture_start = datetime.now(timezone.utc).isoformat()
    service.last_publish_error = None
    monkeypatch.setattr(RecordingService, "_stop",
                        lambda self: getattr(self, "_last_completed", original), raising=False)
    result = service._stop()
    assert result.path.parent == catalog.preferences.audio_dir
    assert result.path.name.startswith("audio_Daily produit_")
    assert result.path.name.endswith("_dddddddd.wav")
    assert not raw.exists() and result.path.exists()
    first = catalog.find(result.path)
    assert service._stop() == result
    assert catalog.find(result.path) == first



def test_recording_without_audio_archive_stays_in_local_session(library, monkeypatch):
    from dataclasses import dataclass

    from transcripteur_whisper.services.recording_service import RecordingService

    @dataclass(frozen=True)
    class Result:
        recording_id: str
        path: Path
        duration: float

    _paths, catalog, _ = library
    assert not catalog.preferences.keep_final_audio
    catalog.preferences.audio_work_dir.mkdir(parents=True, exist_ok=True)
    raw = catalog.preferences.audio_work_dir / ("native_recording_" + "9" * 32 + ".wav")
    raw.write_bytes(b"audio")
    original = Result("9" * 32, raw, 60)
    service = object.__new__(LibraryRecordingService)
    service.catalog = catalog
    service._capture_name = "Daily équipe"
    service._capture_start = "2026-09-16T14:05:00+00:00"
    service.last_publish_error = None
    monkeypatch.setattr(RecordingService, "_stop",
                        lambda self: getattr(self, "_last_completed", original), raising=False)
    result = service._stop()
    assert result.path.parent == catalog.preferences.audio_work_dir
    assert result.path.name.startswith("audio_Daily équipe_")
    assert result.path.name.endswith("_99999999.wav")
    assert result.path.exists()
    assert catalog.find(result.path) is not None


def test_finished_job_folder_becomes_readable(library):
    _paths, _catalog, jobs = library
    _audio, _text, job = job_fixture(library, identifier="1" * 32)
    old_folder = jobs._manifest_paths[job["id"]].parent
    assert old_folder.name == "1" * 32
    jobs._rename_session_folder(job["id"])
    new_folder = jobs._manifest_paths[job["id"]].parent
    assert new_folder != old_folder
    assert "Daily" in new_folder.name
    assert "11111111" in new_folder.name
    assert not old_folder.exists()
    assert (new_folder / "job.json").is_file()

def test_catalog_removes_private_source_tracks_with_recording(library):
    _paths, catalog, _jobs = library
    root = catalog.preferences.audio_work_dir
    root.mkdir(parents=True, exist_ok=True)
    audio = root / "audio_test.wav"
    audio.write_bytes(b"mixed")
    source_dir = root / "Sources" / "source-test"
    source_dir.mkdir(parents=True)
    microphone = source_dir / "moi_microphone.wav"
    system = source_dir / "autres_son_du_pc.wav"
    microphone.write_bytes(b"mic")
    system.write_bytes(b"pc")
    catalog.register(
        audio,
        "source-test",
        duration=10,
        sources={"microphone": str(microphone), "system": str(system)},
        source_names={"microphone": "Micro", "system": "Teams"},
    )
    catalog.delete(audio)
    assert not audio.exists()
    assert not microphone.exists()
    assert not system.exists()
