"""History projection over configured folders; destructive actions stay in services."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from .history_service import HistoryArtifact, HistoryEntry, HistoryService
from .library_storage import AudioCatalog, LibraryError, checked_file


@dataclass(frozen=True)
class LibraryEntry(HistoryEntry):
    date_note: str = ""


def filter_artifacts(entry: HistoryEntry, audio: bool, transcripts: bool, summaries: bool = True):
    artifacts = tuple(a for a in entry.artifacts if (audio and a.kind == "audio")
                      or (transcripts and a.kind in {"transcription", "document"})
                      or (summaries and a.kind == "summary"))
    return replace(entry, artifacts=artifacts) if artifacts else None


class LibraryHistoryService(HistoryService):
    def __init__(self, paths, jobs, catalog: AudioCatalog):
        super().__init__(paths, jobs)
        self.catalog = catalog
        self.roots = catalog.preferences.search_roots(paths)

    def _safe_result_path(self, path):
        return checked_file(path, self.roots)

    def _files(self):
        if self.catalog.preferences.history_dirs:
            for root in self.roots:
                if not root.is_dir():
                    raise LibraryError(f"Dossier d'historique indisponible : {root}")
        found = set()
        for root in self.roots:
            for folder, directories, filenames in os.walk(root, followlinks=False):
                directories[:] = [name for name in directories
                    if name not in {".git", ".venv", "__pycache__", "Sources"}
                    and not (Path(folder) / name).is_symlink()
                    and (Path(folder) / name).absolute() == (Path(folder) / name).resolve()]
                for name in filenames:
                    if Path(name).suffix.lower() not in {".wav", ".txt"}:
                        continue
                    try:
                        found.add(self._safe_result_path(Path(folder) / name))
                    except (OSError, ValueError):
                        continue
        return found

    def all_entries(self):
        visible = self._files()
        with self.catalog.lock:
            records = {key: dict(value) for key, value in self.catalog.records.items()}
        used = set()
        entries = []
        for job in self.jobs.history():
            identifier = job.get("id") or job.get("job_id")
            created = self._parse_datetime(job.get("created_at"))
            if not identifier or created is None:
                continue
            for index, metadata in enumerate(job.get("files", [])):
                artifacts = []
                for kind, key in (("transcription", "transcription_path"), ("document", "document_path"),
                                  ("summary", "summary_path")):
                    raw = metadata.get(key)
                    if raw and Path(raw) in visible:
                        path = Path(raw)
                        artifacts.append(HistoryArtifact(kind, path.name, path))
                record = records.get(metadata.get("recording_id"), {})
                audio = Path(record["path"]) if record.get("path") else None
                if audio in visible and not record.get("deleted"):
                    artifacts.insert(0, HistoryArtifact("audio", audio.name, audio))
                if not artifacts:
                    continue
                source = metadata.get("name") or "Résultat"
                start = self._parse_datetime(metadata.get("recording_started_at") or record.get("started_at"))
                duration = metadata.get("recording_duration", record.get("duration"))
                note = "Date de l'enregistrement" if start else "Date de création du traitement"
                if record.get("time_estimated"):
                    note = "Date estimée de l'ancien WAV"
                start = start or created
                try:
                    duration = float(duration) if duration is not None and 0 <= float(duration) < 366 * 86400 else None
                except (TypeError, ValueError):
                    duration = None
                entries.append(LibraryEntry(
                    f"job:{identifier}:{index}", start, start + timedelta(seconds=duration or 0),
                    str(job.get("output_name") or self._display_name(source)),
                    "recording_transcript" if audio else "transcript", str(metadata.get("status") or job["status"]),
                    source, identifier, duration, tuple(artifacts), note))
                used.update(a.path for a in artifacts)
        by_path = {Path(r["path"]): r for r in records.values() if r.get("path")}
        for path in sorted(visible - used):
            try:
                stat = path.stat()
                record = by_path.get(path, {})
                if record.get("deleted"):
                    continue
                kind = ("audio" if path.suffix.lower() == ".wav" else
                        "summary" if path.name.casefold().startswith(("resume_ia_", "cr_ia_")) else "transcription")
                start = self._parse_datetime(record.get("started_at")) or datetime.fromtimestamp(stat.st_mtime).astimezone()
                duration = record.get("duration")
                note = "Date de l'enregistrement" if record and not record.get("time_estimated") else "Date du fichier (estimée)"
                entries.append(LibraryEntry(
                    f"file:{path}", start, start + timedelta(seconds=duration or 0), self._display_name(path.name),
                    "recording" if kind == "audio" else "transcript", "done" if record else "external",
                    path.name, None, duration, (HistoryArtifact(kind, path.name, path),), note))
            except (OSError, ValueError, TypeError, OverflowError):
                continue
        return entries

    def week(self, start, query="", *, audio=True, transcripts=True, summaries=True):
        first = self.week_start(start)
        end = first + timedelta(days=7)
        if not audio and not transcripts and not summaries:
            return []
        entries = []
        for entry in self.all_entries():
            # Compare local dates, not the current UTC offset reused on old weeks.
            if entry.start.date() >= end or entry.end.date() < first:
                continue
            if query.strip() and not self._matches(entry, query.casefold()):
                continue
            selected = filter_artifacts(entry, audio, transcripts, summaries)
            if selected:
                entries.append(selected)
        return sorted(entries, key=lambda e: (e.start, e.title.casefold(), e.identifier))

    def _matches(self, entry, query):
        metadata = " ".join((entry.title, entry.source_name, entry.status,
                             *(a.label for a in entry.artifacts))).casefold()
        missing = set(query.split()) - {term for term in query.split() if term in metadata}
        if not missing:
            return True
        # Read the complete text with bounded memory. No transcript content goes
        # into a settings file, database, log, or third-party service.
        overlap = max(map(len, missing))
        for artifact in entry.artifacts:
            if artifact.kind == "audio":
                continue
            try:
                with self._safe_result_path(artifact.path).open(encoding="utf-8", errors="replace") as stream:
                    tail = ""
                    while chunk := stream.read(65536):
                        text = tail + chunk.casefold()
                        missing = {term for term in missing if term not in text}
                        if not missing:
                            return True
                        tail = text[-overlap:]
            except (OSError, ValueError):
                continue
        return False

    def delete_artifact(self, identifier, path):
        path = checked_file(Path(path), self.roots)
        if not any(e.identifier == identifier and any(a.path == path for a in e.artifacts)
                   for e in self.all_entries()):
            raise LibraryError("La sélection a changé. Actualisez l'historique.")
        self.jobs.delete_file(path)
