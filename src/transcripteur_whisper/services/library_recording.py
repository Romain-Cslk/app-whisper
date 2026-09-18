"""Recording ownership, readable work files and reliable forced finalization."""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from transcripteur_whisper.audio.native_recorder import NativeRecordingResult

from .library_storage import AudioCatalog, LibraryError, atomic_copy, export_filename
from .recording_service import RecordingService


class LibraryRecordingService(RecordingService):
    """Record locally and keep managed WAVs in the recording work directory.

    A normal stop remains the preferred path. If a Windows loopback driver stays
    blocked during shutdown, ``force_stop`` detaches that session, finalizes the
    already flushed journals when possible and always releases the application
    state. The blocked SoundCard reader is never destroyed concurrently: it is
    retained as a daemon orphan and reaped later if it returns.
    """

    def __init__(self, paths, devices, catalog: AudioCatalog):
        super().__init__(paths, devices, preserve_sources=True)
        self.catalog = catalog
        self.output_name = ""
        self._capture_name = ""
        self._capture_start = None
        self.last_publish_error: str | None = None
        self._orphan_sessions: list[tuple[object, bool]] = []

    def _start(self, *args):
        self._reap_orphans()
        result = super()._start(*args)
        if not result.resumed:
            self._capture_name = self.output_name.strip()
            self._capture_start = datetime.now(timezone.utc).isoformat()
            self.last_publish_error = None
        return result

    def _named_work_result(self, result):
        started = self._capture_start
        try:
            stamp = datetime.fromisoformat(started).astimezone().strftime("%Y-%m-%d_%Hh%M") if started else ""
        except ValueError:
            stamp = ""
        title = self._capture_name.strip()
        parts = [part for part in (title, stamp) if part]
        filename = export_filename("_".join(parts) or "enregistrement", "audio", ".wav")
        destination = result.path.with_name(f"{Path(filename).stem}_{result.recording_id[:8]}.wav")
        if destination == result.path:
            return result
        if destination.exists():
            destination = destination.with_name(f"{destination.stem}_{uuid4().hex[:8]}.wav")
        result.path.rename(destination)
        renamed = replace(result, path=destination)
        self._last_completed = renamed
        return renamed

    def _publish_path(self, path: Path, identifier: str, *, started_at: str | None,
                      duration: float | None, sources: dict[str, str] | None = None,
                      source_names: dict[str, str] | None = None) -> Path:
        work = Path(path).resolve()
        if not self.catalog.preferences.keep_final_audio:
            self.last_publish_error = None
            self.catalog.register(work, identifier, started_at=started_at, duration=duration,
                                  sources=sources, source_names=source_names)
            return work

        final_dir = self.catalog.preferences.audio_dir.resolve()
        final_dir.mkdir(parents=True, exist_ok=True)
        destination = final_dir / work.name
        if destination.exists() and destination.resolve() != work:
            destination = destination.with_name(f"{destination.stem}_{uuid4().hex[:8]}{destination.suffix}")

        self.last_publish_error = None
        try:
            atomic_copy(work, destination)
            self.catalog.register(destination, identifier, started_at=started_at, duration=duration,
                                  sources=sources, source_names=source_names)
        except (OSError, LibraryError) as exc:
            self.last_publish_error = str(exc)
            self.catalog.register(work, identifier, started_at=started_at, duration=duration,
                                  sources=sources, source_names=source_names)
            return work

        try:
            work.unlink(missing_ok=True)
        except OSError:
            pass
        return destination

    def _archive_source_tracks(self, result):
        """Move private source journals into a readable, app-owned sidecar folder."""
        journal_dir = getattr(self, "_journal_dir", None)
        if journal_dir is None:
            return {}, {}
        destination_dir = Path(self.paths.results) / "Sources" / result.recording_id
        source_names = {
            "microphone": str(getattr(result, "microphone_device_name", "") or "Microphone"),
            "system": str(getattr(result, "system_device_name", "") or "Son du PC"),
        }
        destinations = {
            "microphone": destination_dir / "moi_microphone.wav",
            "system": destination_dir / "autres_son_du_pc.wav",
        }
        archived: dict[str, str] = {}
        for role, destination in destinations.items():
            source = Path(journal_dir) / f".native_recording_{result.recording_id}_{role}.wav"
            try:
                if not source.is_file() or source.stat().st_size <= 44:
                    continue
                destination_dir.mkdir(parents=True, exist_ok=True)
                atomic_copy(source, destination)
                source.unlink(missing_ok=True)
                archived[role] = str(destination.resolve())
            except OSError:
                # The mixed WAV remains usable. A locked source journal is kept
                # for recovery rather than making the recording fail.
                continue
        if not archived and destination_dir.exists():
            try:
                destination_dir.rmdir()
            except OSError:
                pass
        return archived, {key: source_names[key] for key in archived}

    def _finalize_library_result(self, result, *, sources=None, source_names=None):
        existing = self.catalog.find(result.path)
        if existing:
            return result
        result = self._named_work_result(result)
        published = self._publish_path(
            result.path,
            result.recording_id,
            started_at=self._capture_start,
            duration=result.duration,
            sources=sources,
            source_names=source_names,
        )
        result = replace(result, path=published)
        self._last_completed = result
        return result

    def _stop(self):
        result = super()._stop()
        sources, source_names = self._archive_source_tracks(result)
        return self._finalize_library_result(result, sources=sources, source_names=source_names)

    def force_stop(self):
        """Bounded fallback used after a normal stop timed out.

        The application is released even if a driver-owned system reader never
        returns. Any valid audio already flushed to disk is mixed/salvaged first.
        """
        return self._call(self._force_stop)

    def _force_stop(self):
        with self.devices.gate:
            session = getattr(self._recorder, "_active", None)
            if session is None:
                if self._last_completed is not None:
                    return self._last_completed
                raise LibraryError("Aucun enregistrement actif à finaliser.")

            finalized = False
            native_result = None
            failure: Exception | None = None
            session._stop_event.set()
            session._stop_ts = time.monotonic()

            try:
                try:
                    session._close_microphone_stream()
                except Exception as exc:
                    session._mark_source_failure("microphone", "arrêt forcé microphone", exc)
                for source in session._sources.values():
                    source.reader_done.set()

                deadline = time.monotonic() + 3.0
                for source in session._sources.values():
                    writer = source.writer_thread
                    if writer is not None and writer.is_alive():
                        writer.join(timeout=max(0.0, deadline - time.monotonic()))
                stuck_writers = [
                    source.key for source in session._sources.values()
                    if source.writer_thread is not None and source.writer_thread.is_alive()
                ]
                reader = session._system_reader_thread
                if reader is None or not reader.is_alive():
                    try:
                        session._close_system_loopback()
                    except Exception:
                        pass

                if stuck_writers:
                    raise LibraryError(
                        "Les fichiers audio sont encore en cours de fermeture ("
                        + ", ".join(stuck_writers)
                        + "). Ils restent conservés pour récupération."
                    )
                try:
                    duration = session._mix_sources()
                except Exception:
                    duration = session._salvage_best_source()
                session._verify_final_output()
                native_result = NativeRecordingResult(
                    recording_id=session.recording_id,
                    path=session.output_path,
                    duration=duration,
                    system_device_name=session.speaker_name,
                    microphone_device_name=session.microphone_name,
                )
                finalized = True
            except Exception as exc:
                failure = exc
            finally:
                recorder_lock = getattr(self._recorder, "_lock", None)
                if recorder_lock is not None:
                    with recorder_lock:
                        if getattr(self._recorder, "_active", None) is session:
                            self._recorder._active = None
                        if native_result is not None:
                            self._recorder._completed[native_result.recording_id] = native_result
                if session.has_live_workers():
                    self._orphan_sessions.append((session, finalized))
                elif finalized and not getattr(session, "preserve_sources", False):
                    session._discard_source_files()
                self._active_result = None
                self._pending_final = None
                self._resources_active = False
                self._selected = {}
                self.devices.set_capture_active(False)

            if native_result is None:
                raise LibraryError(
                    "L'enregistrement a été libéré pour que l'application reste utilisable. "
                    "Les pistes disponibles sont conservées pour récupération. " + str(failure or "")
                )

            destination = self.paths.results / native_result.path.name
            atomic_copy(native_result.path, destination)
            persisted = replace(native_result, path=destination)
            try:
                native_result.path.unlink(missing_ok=True)
            except OSError:
                pass
            self._last_completed = persisted
            sources, source_names = self._archive_source_tracks(persisted)
            result = self._finalize_library_result(
                persisted, sources=sources, source_names=source_names
            )
            return result

    def _reap_orphans(self):
        """Release old SoundCard contexts only after their reader has returned."""
        # Some recovery paths (and focused lifecycle tests) can operate on a
        # minimally reconstructed service. Treat an absent orphan registry as
        # an empty registry instead of breaking recovery/abandonment.
        orphan_sessions = getattr(self, "_orphan_sessions", None)
        if not orphan_sessions:
            self._orphan_sessions = []
            return
        remaining: list[tuple[object, bool]] = []
        for session, finalized in orphan_sessions:
            reader = session._system_reader_thread
            if reader is not None and reader.is_alive():
                remaining.append((session, finalized))
                continue
            try:
                session._close_system_loopback()
            except Exception:
                pass
            for source in session._sources.values():
                writer = source.writer_thread
                if writer is not None and writer.is_alive():
                    writer.join(timeout=0.5)
            if session.has_live_workers():
                remaining.append((session, finalized))
                continue
            if finalized:
                if not getattr(session, "preserve_sources", False):
                    session._discard_source_files()
                continue
            # If forced finalization initially failed only because a writer was
            # still draining, make a recoverable final WAV once it is safe.
            try:
                try:
                    session._mix_sources()
                except Exception:
                    session._salvage_best_source()
                session._verify_final_output()
                session._discard_source_files()
            except Exception:
                # Keep source journals; they remain the last copy of the audio.
                remaining.append((session, False))
        self._orphan_sessions = remaining

    def recoverable_recordings(self):
        # Recovery is normally queried from a generic UI worker, so route the
        # orphan reaper through the dedicated COM/coordinator thread. Calls made
        # from recover()/discard_recovery() are already on that thread.
        worker = getattr(self, "_worker", None)
        if worker is None:
            # A minimally reconstructed service has no coordinator thread; the
            # base implementation only needs the journal/state fields.
            self._reap_orphans()
            return RecordingService.recoverable_recordings(self)
        if threading.current_thread() is worker:
            self._reap_orphans()
            return RecordingService.recoverable_recordings(self)
        if getattr(self, "_closed", False):
            return RecordingService.recoverable_recordings(self)
        return self._call(lambda: (self._reap_orphans(), RecordingService.recoverable_recordings(self))[1])

    def recover(self, path):
        def work():
            self._reap_orphans()
            if self.is_active:
                raise LibraryError("Arrêtez l'enregistrement avant de récupérer un WAV.")
            intermediate = Path(super(LibraryRecordingService, self).recover(path))
            from .media_service import probe_audio_duration

            identifier = "recovered:" + uuid4().hex
            duration = probe_audio_duration(intermediate)
            published = self._publish_path(
                intermediate,
                identifier,
                started_at=None,
                duration=duration,
            )
            return published

        return self._call(work)

    def discard_recovery(self, path: Path):
        return self._call(lambda: self._discard_recovery(path))

    def _discard_recovery(self, path):
        self._reap_orphans()
        source = Path(path).absolute()
        if (self.is_active or source != source.resolve() or source.is_symlink()
                or source.parent != self._journal_dir.resolve()
                or source not in self.recoverable_recordings()
                or (self._pending_final and source == self._pending_final.path)):
            raise LibraryError("Ce WAV ne peut pas être abandonné : capture active ou fichier non récupérable.")
        source.unlink()
