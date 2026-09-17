"""No Qt, audio hardware, model downloads, network or real user files."""
import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from transcripteur_whisper.services.library_storage import (
    RETENTION,
    AudioCatalog,
    LibraryError,
    StoragePreferences,
    checked_file,
    export_filename,
)


@pytest.fixture
def paths(tmp_path):
    root = tmp_path / "user"
    value = SimpleNamespace(root=root, config=root / "config", temp=root / "temp", results=root / "results")
    for directory in (value.config, value.temp, value.results):
        directory.mkdir(parents=True)
    return value


@pytest.fixture
def catalog(paths):
    return AudioCatalog(paths, StoragePreferences.load(paths))


def audio(catalog, name="native_recording_" + "a" * 32 + ".wav", *, days=0):
    path = catalog.preferences.audio_dir / name
    path.write_bytes(b"synthetic wav fixture")
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))
    record = catalog.register(path, name, duration=60)
    return path, record


def test_default_uses_readable_session_paths_and_no_final_audio_archive(paths):
    pref = StoragePreferences.load(paths)
    assert pref.retention == "never"
    assert not pref.keep_final_audio
    assert pref.audio_dir == pref.transcript_dir == paths.results
    assert pref.audio_work_dir == paths.root / "Sessions" / "Enregistrements"
    assert pref.transcript_work_dir == paths.root / "Sessions" / "Transcriptions"
    assert pref.audio_work_dir != pref.audio_dir
    assert pref.transcript_work_dir != pref.transcript_dir
    roots = pref.search_roots(paths)
    assert paths.results in roots
    assert pref.audio_dir not in roots or pref.audio_dir == paths.results
    assert pref.audio_work_dir in roots and pref.transcript_work_dir in roots
    assert paths.root / "work" / "audio" in roots
    assert paths.root / "work" / "transcripts" in roots
    assert set(RETENTION) == {"never", "processed", "1", "3", "7", "30", "90"}


def test_settings_persist_separate_roots_without_moving_old_files(paths, tmp_path):
    old = paths.results / "existing.txt"
    old.write_text("Keep me")
    pref = replace(
        StoragePreferences.load(paths),
        audio_dir=tmp_path / "Audio-final",
        transcript_dir=tmp_path / "Text-final",
        audio_work_dir=tmp_path / "Audio-work",
        transcript_work_dir=tmp_path / "Text-work",
        keep_final_audio=True,
        retention="7",
    ).save(paths)
    loaded = StoragePreferences.load(paths)
    assert loaded == pref
    assert old.read_text() == "Keep me"
    assert paths.results in loaded.search_roots(paths)
    assert not loaded.keep_final_audio
    assert loaded.retention == "7"
    # There is no final audio destination anymore; audio_dir is legacy compatibility only.
    assert loaded.transcript_dir.is_dir()
    assert loaded.audio_work_dir.is_dir() and loaded.transcript_work_dir.is_dir()


def test_v1_final_paths_are_preserved_but_work_moves_local(paths, tmp_path):
    final_audio = tmp_path / "OneDrive-Audio"
    final_text = tmp_path / "OneDrive-Text"
    (paths.config / "storage.json").write_text(
        '{"version": 1, "audio_dir": "' + str(final_audio).replace("\\", "\\\\") +
        '", "transcript_dir": "' + str(final_text).replace("\\", "\\\\") + '"}',
        encoding="utf-8",
    )
    pref = StoragePreferences.load(paths)
    assert pref.audio_dir == final_audio.resolve()
    assert pref.transcript_dir == final_text.resolve()
    assert pref.audio_work_dir == paths.root / "Sessions" / "Enregistrements"
    assert pref.transcript_work_dir == paths.root / "Sessions" / "Transcriptions"
    assert not pref.keep_final_audio




def test_v2_default_work_paths_migrate_to_readable_session_names(paths):
    old_audio = paths.root / "work" / "audio"
    old_text = paths.root / "work" / "transcripts"
    (paths.config / "storage.json").write_text(
        json.dumps({
            "version": 2,
            "audio_dir": str(paths.results),
            "transcript_dir": str(paths.results),
            "audio_work_dir": str(old_audio),
            "transcript_work_dir": str(old_text),
        }),
        encoding="utf-8",
    )
    pref = StoragePreferences.load(paths)
    assert pref.audio_work_dir == paths.root / "Sessions" / "Enregistrements"
    assert pref.transcript_work_dir == paths.root / "Sessions" / "Transcriptions"
    assert old_audio in pref.search_roots(paths)
    assert old_text in pref.search_roots(paths)

def test_explicit_history_replaces_automatic_roots(paths, tmp_path):
    folder = tmp_path / "Archive"
    folder.mkdir()
    pref = replace(StoragePreferences.load(paths), history_dirs=(folder, folder)).save(paths)
    assert pref.search_roots(paths) == (folder,)


@pytest.mark.parametrize("field", ["audio_dir", "transcript_dir", "audio_work_dir", "transcript_work_dir"])
def test_reject_relative_directory_without_changing_preferences(paths, field):
    original = StoragePreferences.load(paths).save(paths)
    with pytest.raises(LibraryError):
        replace(original, **{field: Path("relative")}).save(paths)
    assert StoragePreferences.load(paths) == original


def test_temporary_storage_is_rejected(paths):
    with pytest.raises(LibraryError):
        replace(StoragePreferences.load(paths), audio_work_dir=paths.temp / "audio").save(paths)


def test_intermediate_and_final_storage_must_be_distinct(paths):
    pref = StoragePreferences.load(paths)
    with pytest.raises(LibraryError):
        replace(pref, transcript_work_dir=pref.transcript_dir).save(paths)


def test_invalid_configuration_fails_closed(paths):
    (paths.config / "storage.json").write_text('{"retention": "anything"}')
    with pytest.raises(LibraryError):
        StoragePreferences.load(paths)


def test_corrupt_audio_registry_fails_closed(paths):
    (paths.config / "audio-library.json").write_text('{broken')
    with pytest.raises(LibraryError):
        AudioCatalog(paths, StoragePreferences.load(paths))


@pytest.mark.parametrize("policy,age,deleted", [
    ("never", 365, False), ("1", 0, False), ("1", 2, True), ("3", 2, False),
    ("3", 4, True), ("7", 6, False), ("7", 8, True), ("30", 29, False),
    ("30", 31, True), ("90", 89, False), ("90", 91, True),
])
def test_retention_thresholds(catalog, policy, age, deleted):
    path, _ = audio(catalog, days=age)
    removed, errors = catalog.cleanup(policy, active=set(), completed=set())
    assert not errors
    assert path.exists() != deleted
    assert bool(removed) == deleted


def test_processed_requires_completion_and_active_lease_wins(catalog):
    path, record = audio(catalog)
    assert catalog.cleanup("processed", active=set(), completed=set()) == ([], [])
    assert path.exists()
    assert catalog.cleanup("processed", active={record["id"]}, completed={record["id"]}) == ([], [])
    removed, errors = catalog.cleanup("processed", active=set(), completed={record["id"]})
    assert not errors and removed == [path.name] and not path.exists()


def test_age_retention_does_not_delete_active_file(catalog):
    path, record = audio(catalog, days=100)
    catalog.cleanup("1", active={record["id"]}, completed=set())
    assert path.exists()


def test_never_enrolls_arbitrary_imported_wav(catalog):
    imported = catalog.preferences.audio_dir / "holiday.wav"
    imported.write_bytes(b"user source")
    catalog.migrate_legacy(lambda p: 60)
    assert not catalog.find(imported)
    catalog.cleanup("1", active=set(), completed=set(), now=datetime.now(timezone.utc) + timedelta(days=1000))
    assert imported.exists()
    with pytest.raises(LibraryError):
        catalog.delete(imported)


def test_custom_directory_uuid_wav_is_not_automatically_owned(paths, tmp_path):
    folder = tmp_path / "custom"
    folder.mkdir()
    pref = replace(StoragePreferences.load(paths), audio_dir=folder)
    catalog = AudioCatalog(paths, pref)
    path = folder / ("native_recording_" + "b" * 32 + ".wav")
    path.write_bytes(b"not created by app")
    catalog.migrate_legacy(lambda p: 1)
    assert catalog.records == {}


def test_deletion_refuses_replaced_content(catalog):
    path, _ = audio(catalog)
    path.write_bytes(b"new unrelated contents")
    with pytest.raises(LibraryError):
        catalog.delete(path)
    assert path.exists()


def test_single_deletion_preserves_associated_text_and_timing(catalog):
    path, record = audio(catalog)
    text = path.with_suffix(".txt")
    text.write_text("transcript")
    catalog.delete(path)
    assert text.read_text() == "transcript"
    reloaded = AudioCatalog(catalog.paths, catalog.preferences)
    assert reloaded.records[record["id"]]["deleted"]
    assert reloaded.records[record["id"]]["started_at"] == record["started_at"]


def test_repeated_stop_does_not_reset_retention(catalog):
    path, first = audio(catalog, days=8)
    second = catalog.register(path, first["id"], duration=500)
    assert second == first


def test_external_path_and_symlink_refused(catalog, tmp_path):
    outside = tmp_path / "private.txt"
    outside.write_text("do not read")
    with pytest.raises(LibraryError):
        checked_file(outside, catalog.roots)
    link = catalog.preferences.audio_dir / "alias.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink permission unavailable")
    with pytest.raises(LibraryError):
        checked_file(link, catalog.roots)
    assert outside.read_text() == "do not read"


@pytest.mark.parametrize("title,expected", [
    ("Mon daily", "transcription_Mon daily.txt"),
    ("Meeting.txt", "transcription_Meeting.txt"),
    (r"C:\folder\Daily", "transcription_Daily.txt"),
    ('A:B?C*', 'transcription_A_B_C_.txt'),
    ('CON', 'transcription_CON.txt'),
])
def test_export_preserves_user_name_safely(title, expected):
    assert export_filename(title) == expected


def test_zip_and_audio_names_use_same_title():
    assert export_filename("Daily", "resultats", ".zip") == "resultats_Daily.zip"
    assert export_filename("Daily", "audio", ".wav") == "audio_Daily.wav"
