"""Read-only chronological history built from persistent jobs and native recordings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class HistoryArtifact:
    kind: str
    label: str
    path: Path


@dataclass(frozen=True)
class HistoryEntry:
    identifier: str
    start: datetime
    end: datetime
    title: str
    kind: str
    status: str
    source_name: str
    job_id: str | None
    duration_seconds: float | None
    artifacts: tuple[HistoryArtifact, ...]

    @property
    def has_transcript(self) -> bool:
        return any(item.kind in {"transcription", "document"} for item in self.artifacts)

    @property
    def has_audio(self) -> bool:
        return any(item.kind == "audio" for item in self.artifacts)


class HistoryService:
    """Aggregate existing durable desktop outputs without creating a second store."""

    def __init__(
        self,
        paths: Any,
        jobs: Any,
        *,
        duration_probe: Callable[[Path], float | None] | None = None,
    ) -> None:
        self.paths = paths
        self.jobs = jobs
        self._duration_probe = duration_probe
        self._text_cache: dict[Path, tuple[int, int, str]] = {}

    @staticmethod
    def week_start(value: date | datetime | None = None) -> date:
        current = value.date() if isinstance(value, datetime) else value or datetime.now().astimezone().date()
        return current - timedelta(days=current.weekday())

    def week(self, start: date | datetime, query: str = "") -> list[HistoryEntry]:
        first_day = self.week_start(start)
        tz = datetime.now().astimezone().tzinfo
        range_start = datetime.combine(first_day, time.min, tzinfo=tz)
        range_end = range_start + timedelta(days=7)
        normalized_query = " ".join(query.casefold().split())
        entries = [
            entry
            for entry in self.all_entries()
            if entry.start < range_end and entry.end >= range_start
        ]
        if normalized_query:
            entries = [entry for entry in entries if self._matches(entry, normalized_query)]
        return sorted(entries, key=lambda item: (item.start, item.title.casefold(), item.identifier))

    def all_entries(self) -> list[HistoryEntry]:
        recordings = self._recording_entries()
        recording_by_name = {entry.source_name.casefold(): entry for entry in recordings}
        consumed_recordings: set[str] = set()
        entries: list[HistoryEntry] = []
        for job in self.jobs.history():
            job_id = str(job.get("id") or job.get("job_id") or "")
            if not job_id:
                continue
            created_at = self._parse_datetime(job.get("created_at"))
            if created_at is None:
                continue
            files = job.get("files") or []
            for index, metadata in enumerate(files):
                artifacts = self._job_artifacts(job_id, metadata)
                if not artifacts:
                    continue
                source_name = str(metadata.get("name") or f"Résultat {index + 1}")
                recording = recording_by_name.get(source_name.casefold())
                if recording is not None:
                    consumed_recordings.add(recording.identifier)
                    start = recording.start
                    end = recording.end
                    duration = recording.duration_seconds
                    artifacts = recording.artifacts + artifacts
                    kind = "recording_transcript"
                else:
                    start = created_at
                    end = start + timedelta(minutes=45)
                    duration = None
                    kind = "transcript"
                title = str(job.get("output_name") or "").strip() or self._display_name(source_name)
                entries.append(
                    HistoryEntry(
                        identifier=f"job:{job_id}:{index}",
                        start=start,
                        end=end,
                        title=title,
                        kind=kind,
                        status=str(metadata.get("status") or job.get("status") or "done"),
                        source_name=source_name,
                        job_id=job_id,
                        duration_seconds=duration,
                        artifacts=self._deduplicate_artifacts(artifacts),
                    )
                )
        entries.extend(entry for entry in recordings if entry.identifier not in consumed_recordings)
        return entries

    def read_text(self, path: Path) -> str:
        safe = self._safe_result_path(path)
        if safe.suffix.casefold() != ".txt":
            raise ValueError("Seuls les fichiers texte de l'historique peuvent être lus.")
        return safe.read_text(encoding="utf-8")

    def _recording_entries(self) -> list[HistoryEntry]:
        entries: list[HistoryEntry] = []
        root = self.paths.results.resolve()
        for raw_path in sorted(root.glob("*.wav")):
            try:
                path = self._safe_result_path(raw_path)
                stat = path.stat()
            except (OSError, ValueError):
                continue
            end = datetime.fromtimestamp(stat.st_mtime).astimezone()
            duration = self._probe_duration(path)
            start = end - timedelta(seconds=duration) if duration and duration > 0 else end
            recovered = path.name.casefold().startswith("enregistrement_recupere_")
            title = "Enregistrement récupéré" if recovered else "Enregistrement"
            entries.append(
                HistoryEntry(
                    identifier=f"recording:{path.name}",
                    start=start,
                    end=end,
                    title=title,
                    kind="recording",
                    status="recovered" if recovered else "done",
                    source_name=path.name,
                    job_id=None,
                    duration_seconds=duration,
                    artifacts=(HistoryArtifact("audio", path.name, path),),
                )
            )
        return entries

    def _job_artifacts(self, job_id: str, metadata: dict) -> tuple[HistoryArtifact, ...]:
        job_root = (self.paths.results / job_id).resolve()
        candidates = (
            ("transcription", metadata.get("transcription_path")),
            ("document", metadata.get("document_path")),
        )
        artifacts: list[HistoryArtifact] = []
        for kind, raw in candidates:
            if not raw:
                continue
            try:
                path = self._safe_result_path(Path(raw))
            except ValueError:
                continue
            if not path.is_relative_to(job_root) or not path.is_file():
                continue
            artifacts.append(HistoryArtifact(kind, path.name, path))
        return tuple(artifacts)

    def _matches(self, entry: HistoryEntry, normalized_query: str) -> bool:
        metadata = " ".join(
            [entry.title, entry.source_name, entry.status, entry.kind]
            + [artifact.label for artifact in entry.artifacts]
        ).casefold()
        terms = normalized_query.split()
        searchable_text = []
        for artifact in entry.artifacts:
            if artifact.kind not in {"transcription", "document"}:
                continue
            text = self._cached_text(artifact.path)
            if text:
                searchable_text.append(text)
        haystack = metadata + "\n" + "\n".join(searchable_text)
        return all(term in haystack for term in terms)

    def _cached_text(self, path: Path) -> str:
        try:
            safe = self._safe_result_path(path)
            stat = safe.stat()
        except (OSError, ValueError):
            return ""
        cached = self._text_cache.get(safe)
        marker = (stat.st_mtime_ns, stat.st_size)
        if cached and cached[:2] == marker:
            return cached[2]
        try:
            text = safe.read_text(encoding="utf-8", errors="replace").casefold()
        except OSError:
            return ""
        self._text_cache[safe] = (marker[0], marker[1], text)
        return text

    def _safe_result_path(self, path: Path) -> Path:
        root = self.paths.results.resolve()
        candidate = Path(path).expanduser().resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("Le fichier demandé n'appartient pas à l'historique de l'application.")
        return candidate

    def _probe_duration(self, path: Path) -> float | None:
        probe = self._duration_probe
        if probe is None:
            try:
                from .media_service import probe_audio_duration
            except Exception:
                return None
            probe = probe_audio_duration
        try:
            value = probe(path)
        except Exception:
            return None
        return float(value) if value is not None and value >= 0 else None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        else:
            parsed = parsed.astimezone()
        return parsed

    @staticmethod
    def _display_name(filename: str) -> str:
        stem = Path(filename).stem
        for prefix in ("native_recording_", "enregistrement_recupere_"):
            if stem.casefold().startswith(prefix):
                return "Enregistrement"
        return stem.replace("_", " ").strip() or "Transcription"

    @staticmethod
    def _deduplicate_artifacts(artifacts: Iterable[HistoryArtifact]) -> tuple[HistoryArtifact, ...]:
        seen: set[Path] = set()
        output: list[HistoryArtifact] = []
        for artifact in artifacts:
            if artifact.path in seen:
                continue
            seen.add(artifact.path)
            output.append(artifact)
        return tuple(output)
