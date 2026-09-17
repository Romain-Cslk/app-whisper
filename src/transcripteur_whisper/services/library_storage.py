"""Desktop library preferences and a small catalog of app-owned WAVs.

No imported audio is enrolled automatically. Deletion only accepts an unchanged,
registered file, never a directory or a recursive glob. Callers serialize job
submission and cleanup with ``lock`` before taking the JobService lock.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

RETENTION = {
    "never": "Jamais (conserver les audios)",
    "processed": "Dès qu'ils sont traités avec succès",
    "1": "1 jour", "3": "3 jours", "7": "1 semaine",
    "30": "1 mois (30 jours)", "90": "3 mois (90 jours)",
}
LEGACY_AUDIO = re.compile(r"(?:native_recording_[0-9a-f]{32}|enregistrement_recupere_[0-9a-f]{12})\.wav\Z")
JOB_ID = re.compile(r"[0-9a-f]{32}\Z")


class LibraryError(ValueError):
    pass


def replace_with_retry(source: Path, destination: Path) -> None:
    """Replace a file, tolerating short Windows/OneDrive sharing locks."""
    delays = (0.0, 0.05, 0.10, 0.20, 0.40, 0.80, 1.60, 2.00)
    last_error: OSError | None = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            source.replace(destination)
            return
        except OSError as exc:
            # ERROR_ACCESS_DENIED=5 and ERROR_SHARING_VIOLATION=32 are commonly
            # transient while OneDrive/antivirus indexes a newly-created file.
            if os.name != "nt" or getattr(exc, "winerror", None) not in {5, 32}:
                raise
            last_error = exc
    assert last_error is not None
    raise last_error


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".whisper-", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_copy(source: Path, destination: Path) -> Path:
    """Publish one completed artifact without exposing a half-written file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        shutil.copyfile(source, temporary_path)
        replace_with_retry(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("objet JSON attendu")
        return value
    except (OSError, ValueError) as exc:
        raise LibraryError(f"Configuration illisible : {path.name}. Aucun nettoyage effectué.") from exc


def absolute_directory(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise LibraryError("Choisissez un chemin de dossier absolu.")
    return path.resolve()


def unique_paths(values) -> tuple[Path, ...]:
    return tuple(dict.fromkeys(absolute_directory(value) for value in values))


@dataclass(frozen=True)
class StoragePreferences:
    # Final destinations are user-facing and may be synchronized (OneDrive, etc.).
    audio_dir: Path
    transcript_dir: Path
    # Work destinations receive live writes while recording/transcribing. They
    # default to local AppData and are deliberately distinct from final storage.
    audio_work_dir: Path
    transcript_work_dir: Path
    # Kept only for backward-compatible config loading. There is no separate
    # final-audio archive; managed WAVs remain in audio_work_dir until retention.
    keep_final_audio: bool = False
    history_dirs: tuple[Path, ...] = ()
    retention: str = "never"
    previous_audio_dirs: tuple[Path, ...] = ()
    previous_transcript_dirs: tuple[Path, ...] = ()
    previous_audio_work_dirs: tuple[Path, ...] = ()
    previous_transcript_work_dirs: tuple[Path, ...] = ()

    @classmethod
    def load(cls, paths) -> "StoragePreferences":
        raw = load_json(paths.config / "storage.json")
        policy = raw.get("retention", "never")
        if policy not in RETENTION:
            raise LibraryError("Durée de conservation inconnue. Aucun nettoyage effectué.")
        list_fields = (
            "history_dirs", "previous_audio_dirs", "previous_transcript_dirs",
            "previous_audio_work_dirs", "previous_transcript_work_dirs",
        )
        for key in list_fields:
            if not isinstance(raw.get(key, []), list):
                raise LibraryError(f"Configuration invalide : {key}.")

        # No separate final-audio archive exists. WAVs stay in the recording
        # work folder and are governed only by the retention policy.
        keep_final_audio = False

        legacy_results = paths.root / "results"
        old_work_root = paths.root / "work"
        sessions_root = paths.root / "Sessions"

        def work_path(key: str, old_name: str, new_name: str) -> Path:
            value = raw.get(key)
            if not value:
                return absolute_directory(sessions_root / new_name)
            configured = absolute_directory(value)
            # Automatically leave the opaque v2 default behind. Custom paths are
            # always respected exactly as chosen by the user.
            if configured == absolute_directory(old_work_root / old_name):
                return absolute_directory(sessions_root / new_name)
            return configured

        # audio_dir/transcript_dir are FINAL destinations. The live-write folders
        # use readable local names and never need to be synchronized.
        return cls(
            audio_dir=absolute_directory(raw.get("audio_dir") or legacy_results),
            transcript_dir=absolute_directory(raw.get("transcript_dir") or legacy_results),
            audio_work_dir=work_path("audio_work_dir", "audio", "Enregistrements"),
            transcript_work_dir=work_path("transcript_work_dir", "transcripts", "Transcriptions"),
            keep_final_audio=keep_final_audio,
            history_dirs=unique_paths(raw.get("history_dirs", [])),
            retention=policy,
            previous_audio_dirs=unique_paths(raw.get("previous_audio_dirs", [])),
            previous_transcript_dirs=unique_paths(raw.get("previous_transcript_dirs", [])),
            previous_audio_work_dirs=unique_paths(raw.get("previous_audio_work_dirs", [])),
            previous_transcript_work_dirs=unique_paths(raw.get("previous_transcript_work_dirs", [])),
        )

    def search_roots(self, paths) -> tuple[Path, ...]:
        # An empty list means automatic discovery, not an invisible history.
        return self.history_dirs or unique_paths((
            self.transcript_dir, self.audio_work_dir, self.transcript_work_dir,
            paths.root / "results",
            paths.root / "work" / "audio", paths.root / "work" / "transcripts",
            *self.previous_transcript_dirs,
            *self.previous_audio_work_dirs, *self.previous_transcript_work_dirs,
        ))

    def job_roots(self, paths) -> tuple[Path, ...]:
        # New manifests live in transcript_work_dir. Final/legacy roots are also
        # scanned so histories created by the first library implementation remain
        # readable after upgrading.
        return unique_paths((
            self.transcript_work_dir, paths.root / "results", paths.root / "work" / "transcripts",
            *self.previous_transcript_work_dirs,
            self.transcript_dir, *self.previous_transcript_dirs,
            *self.history_dirs,
        ))

    def save(self, paths) -> "StoragePreferences":
        if self.retention not in RETENTION:
            raise LibraryError("Durée de conservation inconnue.")
        old = self.load(paths)
        audio = absolute_directory(self.audio_dir)
        transcripts = absolute_directory(self.transcript_dir)
        audio_work = absolute_directory(self.audio_work_dir)
        transcript_work = absolute_directory(self.transcript_work_dir)

        if transcripts == transcript_work:
            raise LibraryError("Le dossier transcript de travail doit être différent du dossier transcript final.")

        configured_directories = [transcripts, audio_work, transcript_work]
        for directory in configured_directories:
            # Work directories are persistent staging/fallback locations and must
            # never sit in the disposable temp tree. Final directories must also
            # not be disposable.
            if directory.is_relative_to(paths.temp.resolve()):
                raise LibraryError("Le dossier temporaire de l'application ne peut pas servir de stockage configuré.")
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=directory):
                pass

        history = unique_paths(self.history_dirs)
        for directory in history:
            if not directory.is_dir():
                raise LibraryError(f"Dossier d'historique introuvable : {directory}")

        saved = replace(
            self,
            audio_dir=audio, transcript_dir=transcripts,
            audio_work_dir=audio_work, transcript_work_dir=transcript_work,
            keep_final_audio=False,
            history_dirs=history,
            previous_audio_dirs=unique_paths((*old.previous_audio_dirs, old.audio_dir)),
            previous_transcript_dirs=unique_paths((*old.previous_transcript_dirs, old.transcript_dir)),
            previous_audio_work_dirs=unique_paths((*old.previous_audio_work_dirs, old.audio_work_dir)),
            previous_transcript_work_dirs=unique_paths((
                *old.previous_transcript_work_dirs, old.transcript_work_dir
            )),
        )
        atomic_json(paths.config / "storage.json", {
            "version": 3,
            "audio_dir": str(audio),
            "transcript_dir": str(transcripts),
            "audio_work_dir": str(audio_work),
            "transcript_work_dir": str(transcript_work),
            "keep_final_audio": False,
            "history_dirs": list(map(str, history)),
            "retention": saved.retention,
            "previous_audio_dirs": list(map(str, saved.previous_audio_dirs)),
            "previous_transcript_dirs": list(map(str, saved.previous_transcript_dirs)),
            "previous_audio_work_dirs": list(map(str, saved.previous_audio_work_dirs)),
            "previous_transcript_work_dirs": list(map(str, saved.previous_transcript_work_dirs)),
        })
        return saved


def export_filename(title: str, kind: str = "transcription", extension: str = ".txt") -> str:
    """Always keep the user title, even for merged TXT and ZIP exports."""
    title = re.split(r"[\\/]", str(title or ""))[-1]
    title = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", title).strip().rstrip(". ")
    for suffix in (".txt", ".wav", ".zip"):
        if title.lower().endswith(suffix):
            title = title[:-len(suffix)]
    title = re.sub(r"\s+", " ", title)[:80].rstrip(". ")
    title = title or datetime.now().strftime("%d_%m_%Y_%Hh%M")
    if kind not in {"transcription", "document", "audio", "resultats", "summary"}:
        kind = "document"
    if kind == "summary":
        kind = "resume_ia"
    if extension not in {".txt", ".wav", ".zip"}:
        raise LibraryError("Extension d'export non prise en charge.")
    return f"{kind}_{title}{extension}"


def checked_file(path: Path, roots: tuple[Path, ...], suffixes=(".txt", ".wav")) -> Path:
    candidate = Path(path).absolute()
    resolved = candidate.resolve()
    if (candidate != resolved or candidate.is_symlink()
            or not any(resolved.is_relative_to(root.resolve()) for root in roots)
            or resolved.suffix.lower() not in suffixes or not resolved.is_file()):
        raise LibraryError("Fichier absent, lien symbolique ou fichier extérieur aux dossiers autorisés.")
    return resolved


class AudioCatalog:
    def __init__(self, paths, preferences: StoragePreferences):
        self.paths = paths
        self.preferences = preferences  # Running paths stay fixed until restart.
        self.lock = threading.RLock()
        self.file = paths.config / "audio-library.json"
        self.records = load_json(self.file).get("records", {})
        if not isinstance(self.records, dict) or not all(isinstance(r, dict) for r in self.records.values()):
            raise LibraryError("Catalogue audio invalide. Aucun nettoyage effectué.")

    @property
    def roots(self) -> tuple[Path, ...]:
        return unique_paths((
            self.preferences.audio_dir, self.preferences.audio_work_dir,
            self.paths.root / "results", self.paths.root / "work" / "audio",
            *self.preferences.previous_audio_dirs,
            *self.preferences.previous_audio_work_dirs,
        ))

    def _save(self) -> None:
        atomic_json(self.file, {"version": 1, "records": self.records})

    def register(self, path: Path, identifier: str, *, started_at: str | None = None,
                 duration: float | None = None) -> dict:
        with self.lock:
            path = checked_file(path, self.roots, (".wav",))
            old = self.records.get(identifier)
            if old:
                if old["path"] != str(path):
                    raise LibraryError("Identifiant audio déjà utilisé.")
                return dict(old)  # Idempotent stop does not reset retention.
            stat = path.stat()
            ended = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            valid_duration = (float(duration) if duration is not None and math.isfinite(float(duration))
                              and 0 <= float(duration) < 366 * 86400 else None)
            start = started_at or (ended - timedelta(seconds=valid_duration or 0)).isoformat()
            record = {"id": identifier, "path": str(path), "name": path.name,
                      "started_at": start, "completed_at": ended.isoformat(),
                      "duration": valid_duration, "size": stat.st_size,
                      "mtime_ns": stat.st_mtime_ns, "deleted": False,
                      "time_estimated": started_at is None}
            self.records[identifier] = record
            try:
                self._save()
            except OSError:
                self.records.pop(identifier, None)
                raise
            return dict(record)

    def find(self, path: Path) -> dict | None:
        resolved = str(Path(path).resolve())
        with self.lock:
            return next((dict(r) for r in self.records.values()
                         if r.get("path") == resolved), None)

    def migrate_legacy(self, probe: Callable[[Path], float | None]) -> None:
        # Only UUID WAVs in the original application-owned results folder.
        # A WAV in an arbitrary user-selected folder is NEVER enrolled by a scan.
        for path in (self.paths.root / "results").glob("*.wav"):
            if LEGACY_AUDIO.fullmatch(path.name) and not self.find(path):
                try:
                    self.register(path, "legacy:" + path.stem, duration=probe(path))
                except (OSError, LibraryError):
                    continue

    def delete(self, path: Path) -> None:
        with self.lock:
            record = self.find(path)
            if not record or record.get("deleted"):
                raise LibraryError("Audio importé ou non géré : suppression interdite.")
            safe = checked_file(path, self.roots, (".wav",))
            stat = safe.stat()
            if (stat.st_size, stat.st_mtime_ns) != (record["size"], record["mtime_ns"]):
                raise LibraryError("L'audio a changé depuis sa création. Suppression refusée.")
            safe.unlink()  # Keep timing/link metadata for the remaining transcript.
            self.records[record["id"]]["deleted"] = True
            self._save()

    def cleanup(self, policy: str, *, active: set[str], completed: set[str],
                now: datetime | None = None) -> tuple[list[str], list[str]]:
        if policy not in RETENTION:
            raise LibraryError("Durée de conservation inconnue.")
        removed, errors = [], []
        if policy == "never":
            return removed, errors
        now = now or datetime.now(timezone.utc)
        with self.lock:
            for key, record in list(self.records.items()):
                if key in active or record.get("deleted"):
                    continue
                try:
                    ended = datetime.fromisoformat(record["completed_at"])
                    eligible = (key in completed if policy == "processed"
                                else now - ended >= timedelta(days=int(policy)))
                    if eligible:
                        self.delete(Path(record["path"]))
                        removed.append(record["name"])
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    errors.append(f"Audio conservé ({record.get('name', key)}) : {exc}")
        return removed, errors
