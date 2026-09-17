"""Recording ownership, readable work files and explicit recovery abandonment."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .library_storage import AudioCatalog, LibraryError, atomic_copy, export_filename
from .recording_service import RecordingService


class LibraryRecordingService(RecordingService):
    """Record locally and keep managed WAVs in the recording work directory.

    Retention is handled by ``AudioCatalog``/``LibraryJobService``. Imported audio
    is never enrolled automatically and therefore is never deleted by retention.
    """

    def __init__(self, paths, devices, catalog: AudioCatalog):
        super().__init__(paths, devices)
        self.catalog = catalog
        self.output_name = ""
        self._capture_name = ""
        self._capture_start = None
        self.last_publish_error: str | None = None

    def _start(self, *args):
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
        # Base RecordingService uses this for idempotent repeated stops.
        self._last_completed = renamed
        return renamed

    def _publish_path(self, path: Path, identifier: str, *, started_at: str | None,
                      duration: float | None) -> Path:
        """Publish one complete WAV; keep the work file when final storage fails."""
        work = Path(path).resolve()
        if not self.catalog.preferences.keep_final_audio:
            self.last_publish_error = None
            self.catalog.register(work, identifier, started_at=started_at, duration=duration)
            return work

        final_dir = self.catalog.preferences.audio_dir.resolve()
        final_dir.mkdir(parents=True, exist_ok=True)
        destination = final_dir / work.name
        if destination.exists() and destination.resolve() != work:
            destination = destination.with_name(f"{destination.stem}_{uuid4().hex[:8]}{destination.suffix}")

        self.last_publish_error = None
        try:
            atomic_copy(work, destination)
            self.catalog.register(destination, identifier, started_at=started_at, duration=duration)
        except (OSError, LibraryError) as exc:
            self.last_publish_error = str(exc)
            # The completed local WAV remains the source of truth when the final
            # destination (for example OneDrive) is temporarily unavailable.
            self.catalog.register(work, identifier, started_at=started_at, duration=duration)
            return work

        try:
            work.unlink(missing_ok=True)
        except OSError:
            # A duplicate work copy is harmless; the catalog points to final.
            pass
        return destination

    def _stop(self):
        result = super()._stop()
        existing = self.catalog.find(result.path)
        if existing:
            return result

        result = self._named_work_result(result)
        published = self._publish_path(
            result.path,
            result.recording_id,
            started_at=self._capture_start,
            duration=result.duration,
        )
        result = replace(result, path=published)
        self._last_completed = result
        return result

    def recover(self, path):
        def work():
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
        source = Path(path).absolute()
        if (self.is_active or source != source.resolve() or source.is_symlink()
                or source.parent != self._journal_dir.resolve()
                or source not in self.recoverable_recordings()
                or (self._pending_final and source == self._pending_final.path)):
            raise LibraryError("Ce WAV ne peut pas être abandonné : capture active ou fichier non récupérable.")
        source.unlink()  # Exactly the confirmed journal, not its sibling channels.
