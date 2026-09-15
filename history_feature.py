from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

_METADATA_FILENAME = "metadata.json"
_HISTORY_SCHEMA_VERSION = 1
_JOB_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_DOCUMENT_SUFFIXES = {
    "resume",
    "compte_rendu",
    "note_de_cadrage",
    "cahier_des_charges",
    "procedure_technique",
    "rapport_analyse",
    "support_formation",
}
_INSTALL_LOCK = threading.Lock()


def _history_dir(server_module: Any) -> Path:
    path = Path(server_module.DATA_DIR) / "history"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_metadata(path: Path) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _job_history_dir(server_module: Any, job_id: str) -> Path:
    if not _JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="Historique introuvable")
    return _history_dir(server_module) / job_id


def _display_title(job: Dict[str, Any]) -> str:
    output_name = str(job.get("output_name") or "").strip()
    if output_name:
        return output_name
    files = job.get("files") or []
    if files:
        source_name = str(files[0].get("name") or "").strip()
        if source_name:
            return Path(source_name).stem or source_name
    created_at = _parse_datetime(job.get("created_at")) or datetime.now(timezone.utc)
    return f"Transcription {created_at.astimezone().strftime('%d/%m/%Y %H:%M')}"


def _kind_for_artifact(filename: str, output_type: Optional[str]) -> str:
    stem = Path(filename).stem.casefold()
    normalized_type = (output_type or "").casefold()
    if normalized_type and normalized_type != "transcription":
        if stem.startswith(f"{normalized_type}_") or stem.endswith(f"_{normalized_type}"):
            return "document"
    if any(stem.startswith(f"{suffix}_") or stem.endswith(f"_{suffix}") for suffix in _DOCUMENT_SUFFIXES):
        return "document"
    return "transcription"


def _copy_artifacts(source_dir: Path, destination_dir: Path, output_type: Optional[str]) -> List[Dict[str, Any]]:
    artifacts: List[Dict[str, Any]] = []
    destination_dir.mkdir(parents=True, exist_ok=True)
    if not source_dir.exists():
        return artifacts

    for source in sorted(source_dir.glob("*.txt")):
        if not source.is_file():
            continue
        destination = destination_dir / source.name
        shutil.copy2(source, destination)
        artifacts.append(
            {
                "name": source.name,
                "kind": _kind_for_artifact(source.name, output_type),
                "size": destination.stat().st_size,
            }
        )
    return artifacts


def _public_snapshot(server_module: Any, job: Dict[str, Any]) -> Dict[str, Any]:
    snapshotter = getattr(server_module, "_public_job_snapshot", None)
    if callable(snapshotter):
        return snapshotter(job)
    snapshot = deepcopy(job)
    snapshot.pop("cancel_requested", None)
    return snapshot


def _archive_job(server_module: Any, job_id: str) -> Optional[Dict[str, Any]]:
    if not _JOB_ID_RE.fullmatch(job_id):
        return None

    jobs_lock = getattr(server_module, "JOBS_LOCK", None)
    if jobs_lock is None:
        job = (getattr(server_module, "JOBS", {}) or {}).get(job_id)
        snapshot = _public_snapshot(server_module, job) if job else None
    else:
        with jobs_lock:
            job = (getattr(server_module, "JOBS", {}) or {}).get(job_id)
            snapshot = _public_snapshot(server_module, job) if job else None

    source_dir = Path(server_module.TRANS_DIR) / job_id
    history_dir = _history_dir(server_module) / job_id
    existing = _load_metadata(history_dir / _METADATA_FILENAME)

    if snapshot is None:
        if existing is not None:
            return existing
        return _backfill_one(server_module, source_dir)

    artifacts = _copy_artifacts(source_dir, history_dir, snapshot.get("output_type"))
    if not artifacts and existing:
        artifacts = list(existing.get("artifacts") or [])

    source_files = []
    for file_meta in snapshot.get("files") or []:
        source_files.append(
            {
                "name": file_meta.get("name"),
                "size": file_meta.get("size"),
                "status": file_meta.get("status"),
                "error": file_meta.get("error"),
                "transcription_available": bool(file_meta.get("transcription_available")),
                "document_available": bool(file_meta.get("document_available")),
            }
        )

    metadata: Dict[str, Any] = {
        "schema_version": _HISTORY_SCHEMA_VERSION,
        "id": job_id,
        "title": _display_title(snapshot),
        "created_at": snapshot.get("created_at") or (existing or {}).get("created_at") or _utc_now_iso(),
        "finished_at": snapshot.get("finished_at") or (existing or {}).get("finished_at") or _utc_now_iso(),
        "status": snapshot.get("status") or (existing or {}).get("status") or "done",
        "use_api": bool(snapshot.get("use_api")),
        "model": snapshot.get("model"),
        "lang": snapshot.get("lang"),
        "output_type": snapshot.get("output_type") or "transcription",
        "source_files": source_files,
        "artifacts": artifacts,
        "recovered": False,
        "updated_at": _utc_now_iso(),
    }
    _write_json_atomic(history_dir / _METADATA_FILENAME, metadata)
    return metadata


def _title_from_artifact(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"^(?:transcription|resume|compte_rendu|note_de_cadrage|cahier_des_charges|procedure_technique|rapport_analyse|support_formation)_", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"_(?:transcription|resume|compte_rendu|note_de_cadrage|cahier_des_charges|procedure_technique|rapport_analyse|support_formation)$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^\d{3}_", "", stem)
    return stem.replace("_", " ").strip() or "Transcription récupérée"


def _backfill_one(server_module: Any, source_dir: Path) -> Optional[Dict[str, Any]]:
    job_id = source_dir.name
    if not source_dir.is_dir() or not _JOB_ID_RE.fullmatch(job_id):
        return None

    history_dir = _history_dir(server_module) / job_id
    metadata_path = history_dir / _METADATA_FILENAME
    existing = _load_metadata(metadata_path)
    if existing is not None:
        return existing

    txt_files = [path for path in sorted(source_dir.glob("*.txt")) if path.is_file()]
    if not txt_files:
        return None

    artifacts = _copy_artifacts(source_dir, history_dir, None)
    first_timestamp = min(path.stat().st_mtime for path in txt_files)
    last_timestamp = max(path.stat().st_mtime for path in txt_files)
    metadata: Dict[str, Any] = {
        "schema_version": _HISTORY_SCHEMA_VERSION,
        "id": job_id,
        "title": _title_from_artifact(txt_files[0].name),
        "created_at": datetime.fromtimestamp(first_timestamp, timezone.utc).isoformat(),
        "finished_at": datetime.fromtimestamp(last_timestamp, timezone.utc).isoformat(),
        "status": "done",
        "use_api": None,
        "model": None,
        "lang": None,
        "output_type": "transcription",
        "source_files": [],
        "artifacts": artifacts,
        "recovered": True,
        "updated_at": _utc_now_iso(),
    }
    _write_json_atomic(metadata_path, metadata)
    return metadata


def _backfill_existing_transcriptions(server_module: Any) -> int:
    trans_dir = Path(server_module.TRANS_DIR)
    if not trans_dir.exists():
        return 0
    recovered = 0
    for source_dir in trans_dir.iterdir():
        if not source_dir.is_dir():
            continue
        if _backfill_one(server_module, source_dir) is not None:
            recovered += 1
    return recovered


def _iter_metadata(server_module: Any) -> Iterable[Dict[str, Any]]:
    history_dir = _history_dir(server_module)
    for child in history_dir.iterdir():
        if not child.is_dir() or not _JOB_ID_RE.fullmatch(child.name):
            continue
        metadata = _load_metadata(child / _METADATA_FILENAME)
        if metadata is not None:
            yield metadata


def _query_in_artifacts(server_module: Any, record: Dict[str, Any], needle: str) -> bool:
    if not needle:
        return True
    history_dir = _history_dir(server_module) / str(record.get("id") or "")
    for artifact in record.get("artifacts") or []:
        path = history_dir / str(artifact.get("name") or "")
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if needle in line.casefold():
                        return True
        except OSError:
            continue
    return False


def _record_matches_query(server_module: Any, record: Dict[str, Any], query: str) -> bool:
    needle = query.strip().casefold()
    if not needle:
        return True
    metadata_parts = [
        record.get("title"),
        record.get("model"),
        record.get("lang"),
        record.get("output_type"),
        record.get("status"),
    ]
    metadata_parts.extend(item.get("name") for item in record.get("source_files") or [])
    metadata_parts.extend(item.get("name") for item in record.get("artifacts") or [])
    haystack = "\n".join(str(part) for part in metadata_parts if part).casefold()
    return needle in haystack or _query_in_artifacts(server_module, record, needle)


def _list_history_records(
    server_module: Any,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    query: str = "",
    limit: int = 500,
) -> List[Dict[str, Any]]:
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    if start and start_dt is None:
        raise HTTPException(status_code=400, detail="Date de début invalide")
    if end and end_dt is None:
        raise HTTPException(status_code=400, detail="Date de fin invalide")
    if start_dt and end_dt and end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="La date de fin doit être postérieure à la date de début")

    records: List[Dict[str, Any]] = []
    for record in _iter_metadata(server_module):
        created_at = _parse_datetime(record.get("created_at"))
        if start_dt and (created_at is None or created_at < start_dt):
            continue
        if end_dt and (created_at is None or created_at >= end_dt):
            continue
        if not _record_matches_query(server_module, record, query):
            continue
        records.append(record)

    records.sort(
        key=lambda record: _parse_datetime(record.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return records[:limit]


def _load_job_record(server_module: Any, job_id: str) -> Dict[str, Any]:
    directory = _job_history_dir(server_module, job_id)
    metadata = _load_metadata(directory / _METADATA_FILENAME)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Historique introuvable")
    return metadata


def _artifact_path(server_module: Any, job_id: str, artifact_name: str) -> Path:
    record = _load_job_record(server_module, job_id)
    allowed = {str(item.get("name") or "") for item in record.get("artifacts") or []}
    if artifact_name not in allowed or not artifact_name or Path(artifact_name).name != artifact_name:
        raise HTTPException(status_code=404, detail="Fichier historique introuvable")
    path = _job_history_dir(server_module, job_id) / artifact_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Fichier historique introuvable")
    return path


def _wrap_queue_runner(server_module: Any) -> None:
    original = getattr(server_module, "_run_job_from_queue", None)
    if not callable(original) or getattr(original, "__history_archive_wrapped__", False):
        return

    def wrapped(job_id: str, api_key: Optional[str]) -> Any:
        try:
            return original(job_id, api_key)
        finally:
            try:
                _archive_job(server_module, job_id)
            except Exception:
                logger = getattr(server_module, "logger", logging.getLogger("whisper_app.history"))
                logger.exception("Unable to archive transcription history for job %s", job_id)

    wrapped.__history_archive_wrapped__ = True  # type: ignore[attr-defined]
    wrapped.__wrapped__ = original  # type: ignore[attr-defined]
    server_module._run_job_from_queue = wrapped


def install_history_feature(app: FastAPI, server_module: Any = None) -> None:
    if server_module is None:
        import server as server_module  # type: ignore[no-redef]

    with _INSTALL_LOCK:
        if getattr(app.state, "history_feature_installed", False):
            return

        _history_dir(server_module)
        _backfill_existing_transcriptions(server_module)
        _wrap_queue_runner(server_module)

        @app.get("/history", response_class=HTMLResponse, name="history_page")
        def history_page(request: Request):
            return server_module.templates.TemplateResponse(
                request=request,
                name="history.html",
                context={"request": request},
            )

        @app.get("/api/history")
        def history_list(
            start: Optional[str] = None,
            end: Optional[str] = None,
            q: str = "",
            limit: int = Query(500, ge=1, le=2000),
        ):
            records = _list_history_records(
                server_module,
                start=start,
                end=end,
                query=q,
                limit=limit,
            )
            return JSONResponse({"records": records, "count": len(records)})

        @app.get("/api/history/{job_id}")
        def history_detail(job_id: str):
            return JSONResponse(_load_job_record(server_module, job_id))

        @app.get("/api/history/{job_id}/content/{artifact_name}")
        def history_content(job_id: str, artifact_name: str):
            path = _artifact_path(server_module, job_id, artifact_name)
            return PlainTextResponse(
                path.read_text(encoding="utf-8", errors="replace"),
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/api/history/{job_id}/download/{artifact_name}")
        def history_download(job_id: str, artifact_name: str):
            path = _artifact_path(server_module, job_id, artifact_name)
            return FileResponse(
                path=str(path),
                filename=path.name,
                media_type="text/plain; charset=utf-8",
                headers={"Cache-Control": "no-store"},
            )

        app.state.history_feature_installed = True
