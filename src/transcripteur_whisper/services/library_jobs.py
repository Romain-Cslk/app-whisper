"""Storage-aware JobService with local work files and explicit final publication."""
from __future__ import annotations

import logging
import os
import re
import tempfile
import zipfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from ..models.jobs import TERMINAL_STATUSES, JobError
from .files import write_text_atomic
from .job_service import JobService, timestamp
from .library_storage import (
    JOB_ID,
    AudioCatalog,
    LibraryError,
    atomic_copy,
    atomic_json,
    checked_file,
    load_json,
    unique_paths,
)

logger = logging.getLogger(__name__)
PHASES = {
    "conversion": "Préparation / conversion audio",
    "transcription_local": "Transcription locale",
    "transcription_api": "Transcription OpenAI",
    "recomposition": "Écriture du transcript",
    "document": "Génération du document",
    "done": "Terminé",
    "partial": "Résultat partiel",
    "error": "Échec",
    "cancelled": "Annulé",
}


class LibraryJobService(JobService):
    """Keep live transcription writes in work storage, publish only at the end."""

    def __init__(self, paths, catalog: AudioCatalog, *, transcription=None, load_history=True):
        self.catalog = catalog
        self.preferences = catalog.preferences
        self._manifest_paths: dict[str, Path] = {}
        self.retention = self.preferences.retention
        super().__init__(paths, transcription=transcription, load_history=False)
        if load_history:
            self.load_history()

    def submit(self, files, options, api_key=""):
        # Lock order is shared with deletion/retention. A queued job protects its
        # source before any cleanup can start, including concurrent submissions.
        with self.catalog.lock, self._lock:
            identifier = super().submit(files, options, api_key)
            job = self._jobs[identifier]
            for metadata in job["files"]:
                record = self.catalog.find(Path(metadata["path"]))
                if record and not record.get("deleted"):
                    metadata["recording_id"] = record["id"]
                    metadata["recording_started_at"] = record["started_at"]
                    metadata["recording_duration"] = record["duration"]
            job.setdefault("storage_transcript_dir", str(self.preferences.transcript_dir.resolve()))
            job.setdefault("storage_transcript_work_dir", str(self.paths.results.resolve()))
            job.setdefault("storage_state", "pending")
            self._persist(identifier)
            return identifier

    def _persist(self, job_id, required=False):
        # Manifests always remain in the local session storage. Final user folders
        # receive completed TXT/document artifacts, never live job.json writes.
        with self._lock:
            manifest = deepcopy(self._jobs[job_id])
            manifest.pop("cancel_requested", None)
            for metadata in manifest["files"]:
                metadata.pop("path", None)  # Never persist imported source paths.
            path = self._manifest_paths.get(job_id, self.paths.results / job_id / "job.json")
            try:
                atomic_json(path, manifest)
                self._manifest_paths[job_id] = path
            except OSError:
                if required:
                    raise
                logger.warning("Impossible de sauvegarder le traitement %s", job_id)

    def _artifact_roots_for(self, job: dict, manifest_parent: Path) -> tuple[Path, ...]:
        final = job.get("storage_transcript_dir")
        values = [
            manifest_parent,
            self.paths.results,
            self.paths.root / "results",
            self.preferences.transcript_dir,
            *self.preferences.previous_transcript_dirs,
            *self.preferences.previous_transcript_work_dirs,
        ]
        if final:
            values.append(Path(final))
        return unique_paths(values)

    def _artifact_roots(self, job_id: str) -> tuple[Path, ...]:
        manifest = self._manifest_paths.get(job_id, self.paths.results / job_id / "job.json")
        return self._artifact_roots_for(self._jobs[job_id], manifest.parent)

    def load_history(self):
        from .media_service import probe_audio_duration

        self.catalog.migrate_legacy(probe_audio_duration)
        for root in self.preferences.job_roots(self.paths):
            if not root.is_dir():
                continue
            for manifest in root.glob("*/job.json"):
                try:
                    checked_file(manifest, (root,), (".json",))
                    job = load_json(manifest)
                    if (not JOB_ID.fullmatch(str(job.get("id", ""))) or not isinstance(job.get("files"), list)
                            or not isinstance(job.get("logs"), list)):
                        continue
                    datetime.fromisoformat(job["created_at"])
                    with self._lock:
                        if job["id"] in self._jobs:
                            continue

                    roots = self._artifact_roots_for(job, manifest.parent)
                    for metadata in job["files"]:
                        metadata.setdefault("name", "Résultat")
                        metadata.setdefault("status", "cancelled")
                        metadata.setdefault("progress", 0.0)
                        metadata.setdefault("stage", metadata["status"])
                        metadata.pop("path", None)
                        for key, available in (("transcription_path", "transcription_available"),
                                               ("document_path", "document_available")):
                            raw = metadata.get(key)
                            safe = None
                            if raw:
                                candidates = [Path(raw)]
                                # Portable/legacy manifests may contain an old
                                # absolute path; its basename can still be local.
                                candidates.append(manifest.parent / Path(raw).name)
                                for candidate in candidates:
                                    try:
                                        safe = checked_file(candidate, roots, (".txt",))
                                        break
                                    except (OSError, ValueError):
                                        continue
                            metadata[key] = str(safe) if safe else None
                            metadata[available] = safe is not None
                        metadata["out_path"] = metadata.get("document_path") or metadata.get("transcription_path")
                        if not metadata.get("recording_id"):
                            from .library_storage import LEGACY_AUDIO

                            if LEGACY_AUDIO.fullmatch(metadata["name"]):
                                for audio_root in self.catalog.roots:
                                    record = self.catalog.find(audio_root / metadata["name"])
                                    if record:
                                        metadata["recording_id"] = record["id"]
                                        metadata["recording_started_at"] = record["started_at"]
                                        metadata["recording_duration"] = record["duration"]
                                        break
                    if job.get("status") not in TERMINAL_STATUSES:
                        job.update(status="cancelled", finished_at=timestamp())
                        self._mark_unfinished(job, "cancelled")
                        job["logs"].append("Traitement interrompu. Résultats disponibles conservés.")
                    job["cancel_requested"] = False
                    with self._lock:
                        if job["id"] not in self._jobs:
                            self._jobs[job["id"]] = job
                            self._manifest_paths[job["id"]] = manifest
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    logger.warning("Historique illisible : %s", manifest.parent.name)
        self.cleanup_audio()
        return self.history()

    def snapshot(self, job_id):
        snapshot = super().snapshot(job_id)
        roots = self._artifact_roots(job_id)
        for metadata in snapshot["files"]:
            for key, available in (("transcription_path", "transcription_available"),
                                   ("document_path", "document_available")):
                try:
                    path = checked_file(Path(metadata[key]), roots, (".txt",)) if metadata.get(key) else None
                except (OSError, ValueError):
                    path = None
                metadata[key] = str(path) if path else None
                metadata[available] = path is not None
            metadata["out_path"] = metadata.get("document_path") or metadata.get("transcription_path")
            metadata["output_name"] = Path(metadata["out_path"]).name if metadata["out_path"] else None
        snapshot["has_transcriptions"] = any(m["transcription_available"] for m in snapshot["files"])
        snapshot["has_documents"] = any(m["document_available"] for m in snapshot["files"])
        return snapshot

    def delete_file(self, path: Path):
        with self.catalog.lock, self._lock:
            roots = self.preferences.job_roots(self.paths) + self.preferences.search_roots(self.paths) + self.catalog.roots
            path = checked_file(Path(path), roots)
            affected = []
            for identifier, job in self._jobs.items():
                for metadata in job["files"]:
                    matches = any(metadata.get(key) and Path(metadata[key]).resolve() == path
                                  for key in ("path", "transcription_path", "document_path", "out_path"))
                    record = self.catalog.records.get(metadata.get("recording_id"), {})
                    matches = matches or record.get("path") == str(path)
                    if matches and job["status"] not in TERMINAL_STATUSES:
                        raise JobError("Fichier utilisé par un traitement en cours ou en attente.")
                    if matches:
                        affected.append(identifier)
            if path.suffix.lower() == ".wav":
                self.catalog.delete(path)
            else:
                path = checked_file(path, roots, (".txt",))
                path.unlink()
            for identifier in set(affected):
                for metadata in self._jobs[identifier]["files"]:
                    for key, available in (("transcription_path", "transcription_available"),
                                           ("document_path", "document_available")):
                        if metadata.get(key) and Path(metadata[key]).resolve() == path:
                            metadata[key] = None
                            metadata[available] = False
                    metadata["out_path"] = metadata.get("document_path") or metadata.get("transcription_path")
                self._persist(identifier)

    @staticmethod
    def _session_label(job: dict, job_id: str) -> str:
        try:
            stamp = datetime.fromisoformat(job.get("created_at", "")).astimezone().strftime("%Y-%m-%d_%Hh%M")
        except (TypeError, ValueError):
            stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%Hh%M")
        title = str(job.get("output_name") or "").strip()
        if not title:
            files = job.get("files") or []
            title = Path(files[0].get("name", "Transcription")).stem if files else "Transcription"
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", title)
        title = re.sub(r"\s+", " ", title).strip(" ._")[:60] or "Transcription"
        return f"{stamp} - {title} - {job_id[:8]}"

    def _rename_session_folder(self, job_id: str) -> None:
        """Rename a finished UUID work folder to a readable session name."""
        with self._lock:
            manifest = self._manifest_paths.get(job_id)
            if manifest is None or not manifest.is_file():
                return
            source = manifest.parent.resolve()
            base = self.paths.results.resolve()
            if source.parent != base:
                return
            label = self._session_label(self._jobs[job_id], job_id)
            destination = base / label
            if source == destination:
                return
            counter = 2
            while destination.exists():
                destination = base / f"{label} ({counter})"
                counter += 1
            old_paths = {}
            for index, metadata in enumerate(self._jobs[job_id]["files"]):
                for key in ("transcription_path", "document_path", "out_path"):
                    raw = metadata.get(key)
                    if raw:
                        candidate = Path(raw).resolve()
                        if candidate.parent == source:
                            old_paths[(index, key)] = candidate.name

        source.rename(destination)

        with self._lock:
            self._manifest_paths[job_id] = destination / "job.json"
            for (index, key), name in old_paths.items():
                self._jobs[job_id]["files"][index][key] = str(destination / name)
            self._persist(job_id, required=True)

    @staticmethod
    def _final_destination(target: Path, source: Path, job_id: str) -> Path:
        destination = target / source.name
        if not destination.exists():
            return destination
        alternate = target / f"{source.stem}_{job_id[:8]}{source.suffix}"
        if not alternate.exists():
            return alternate
        counter = 2
        while True:
            candidate = target / f"{source.stem}_{job_id[:8]}_{counter}{source.suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _publish_job(self, job_id: str) -> bool:
        """Copy completed text artifacts from work storage to final storage.

        The manifest remains local. This is deliberate: OneDrive/SharePoint only
        sees one atomic copy per final artifact and never receives segment-by-
        segment transcript writes or frequently-updated job metadata.
        """
        with self._lock:
            job = self._jobs[job_id]
            target_base = Path(job.get("storage_transcript_dir") or self.preferences.transcript_dir).resolve()
            job.setdefault("storage_transcript_dir", str(target_base))
            job.setdefault("storage_transcript_work_dir", str(self.paths.results.resolve()))
            job.setdefault("storage_state", "pending")
            source_manifest = self._manifest_paths.get(job_id, self.paths.results / job_id / "job.json")
            source_root = source_manifest.parent.resolve()
            transfers = []
            for index, metadata in enumerate(job["files"]):
                for key in ("transcription_path", "document_path"):
                    raw = metadata.get(key)
                    if not raw:
                        continue
                    source = checked_file(Path(raw), (source_root,), (".txt",))
                    destination = self._final_destination(target_base, source, job_id)
                    transfers.append((index, key, source, destination))
            if not transfers:
                job["storage_state"] = "published"
                self._persist(job_id)
                return False

        target_base.mkdir(parents=True, exist_ok=True)
        created: list[Path] = []
        try:
            for _index, _key, source, destination in transfers:
                atomic_copy(source, destination)
                created.append(destination)
        except Exception:
            for path in created:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

        with self._lock:
            old_values = {(index, key): self._jobs[job_id]["files"][index].get(key)
                          for index, key, _source, _destination in transfers}
            try:
                for index, key, _source, destination in transfers:
                    self._jobs[job_id]["files"][index][key] = str(destination)
                    if key == "transcription_path":
                        self._jobs[job_id]["files"][index]["transcription_available"] = True
                    elif key == "document_path":
                        self._jobs[job_id]["files"][index]["document_available"] = True
                for metadata in self._jobs[job_id]["files"]:
                    metadata["out_path"] = metadata.get("document_path") or metadata.get("transcription_path")
                self._jobs[job_id]["storage_state"] = "published"
                self._persist(job_id, required=True)
            except Exception:
                for (index, key), value in old_values.items():
                    self._jobs[job_id]["files"][index][key] = value
                for metadata in self._jobs[job_id]["files"]:
                    metadata["out_path"] = metadata.get("document_path") or metadata.get("transcription_path")
                self._jobs[job_id]["storage_state"] = "pending"
                for path in created:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise

        # The local manifest is kept, but successfully published text work files
        # can be removed. Failure only leaves a harmless duplicate.
        for _index, _key, source, destination in transfers:
            if source != destination:
                try:
                    source.unlink(missing_ok=True)
                except OSError:
                    pass
        return True

    def cleanup_audio(self):
        with self.catalog.lock, self._lock:
            active, complete = set(), set()
            for identifier, job in self._jobs.items():
                roots = self._artifact_roots(identifier)
                for metadata in job["files"]:
                    key = metadata.get("recording_id")
                    if not key and metadata.get("path"):
                        record = self.catalog.find(Path(metadata["path"]))
                        key = record.get("id") if record else None
                    if not key:
                        continue
                    if job["status"] not in TERMINAL_STATUSES:
                        active.add(key)
                    elif job["status"] == "done" and metadata.get("status") == "done":
                        try:
                            path = checked_file(Path(metadata["transcription_path"]), roots, (".txt",))
                            if job.get("storage_state") == "published" and path.stat().st_size > 0:
                                complete.add(key)
                        except (OSError, ValueError, KeyError, TypeError):
                            pass
            removed, errors = self.catalog.cleanup(self.retention, active=active, completed=complete)
            for message in errors:
                logger.warning(message)
            return {"removed": removed, "errors": errors}

    def _run(self, job_id, options, api_key):
        super()._run(job_id, options, api_key)
        try:
            published = self._publish_job(job_id)
            if published:
                with self._lock:
                    self._jobs[job_id]["logs"].append(
                        "Stockage final : résultat copié dans le dossier configuré."
                    )
                    self._persist(job_id)
        except (OSError, LibraryError) as exc:
            with self._lock:
                self._jobs[job_id]["storage_state"] = "local_fallback"
                self._jobs[job_id]["logs"].append(
                    "Stockage final indisponible : résultat conservé dans le dossier de travail local. " + str(exc)
                )
                self._persist(job_id)
            logger.warning("Publication du transcript différée : %s", exc)
        try:
            self._rename_session_folder(job_id)
        except OSError as exc:
            logger.warning("Renommage du dossier de session impossible : %s", exc)
        try:
            result = self.cleanup_audio()
            if result["removed"]:
                with self._lock:
                    self._jobs[job_id]["logs"].append(
                        "Conservation audio : suppression de " + ", ".join(result["removed"])
                    )
                    self._persist(job_id)
        except (OSError, LibraryError):
            logger.warning("Nettoyage audio suspendu ; les fichiers restants sont conservés.")

    def export(self, job_id, destination, kind="result"):
        snapshot = self.snapshot(job_id)
        destination = Path(destination).expanduser().resolve()
        keys = {
            "transcription": ("transcription_path",),
            "document": ("document_path",),
            "result": ("out_path",),
        }
        if kind not in keys:
            raise JobError("Type d'export inconnu.")
        wanted = ("transcription_path", "document_path") if destination.suffix.lower() == ".zip" else keys[kind]
        roots = self._artifact_roots(job_id)
        sources = list(dict.fromkeys(
            checked_file(Path(metadata[key]), roots, (".txt",))
            for metadata in snapshot["files"] for key in wanted if metadata.get(key)
        ))
        if not sources:
            raise JobError("Aucun résultat disponible.")
        if destination in sources:
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.suffix.lower() == ".zip":
            fd, name = tempfile.mkstemp(prefix=".whisper-", dir=destination.parent)
            os.close(fd)
            temporary = Path(name)
            try:
                with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                    for source in sources:
                        archive.write(source, source.name)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            text = sources[0].read_text(encoding="utf-8") if len(sources) == 1 else "\n\n".join(
                f"===== {path.name} =====\n{path.read_text(encoding='utf-8')}" for path in sources
            )
            write_text_atomic(destination, text)
        return destination
