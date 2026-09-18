"""Readable recording metadata and source-based speaker formatting."""
from __future__ import annotations

from datetime import datetime, timedelta

SPEAKER_LABELS = {
    "microphone": "Moi",
    "system": "Autres interlocuteurs",
}


def _parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except (TypeError, ValueError):
        return None


def format_duration(seconds) -> str:
    try:
        value = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return "Durée inconnue"
    hours, rest = divmod(value, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_clock(seconds) -> str:
    try:
        value = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        value = 0
    hours, rest = divmod(value, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def recording_fingerprint(metadata: dict) -> str:
    if not metadata.get("recording_id"):
        return ""
    started = _parse_datetime(metadata.get("recording_started_at"))
    duration = metadata.get("recording_duration")
    completed = None
    try:
        seconds = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        seconds = None
    if started is not None and seconds is not None and seconds >= 0:
        completed = started + timedelta(seconds=seconds)
    if completed is None:
        completed = _parse_datetime(metadata.get("recording_completed_at"))
    source_names = metadata.get("recording_source_names") or {}
    lines = ["╭─ Empreinte de l’enregistrement"]
    if started is not None:
        lines.append(f"│ Début : {started:%d/%m/%Y %H:%M:%S}")
    if completed is not None:
        lines.append(f"│ Fin   : {completed:%d/%m/%Y %H:%M:%S}")
    if seconds is not None:
        lines.append(f"│ Durée : {format_duration(seconds)}")
    if source_names.get("microphone"):
        lines.append(f"│ Moi : {source_names['microphone']}")
    if source_names.get("system"):
        lines.append(f"│ Autres interlocuteurs : {source_names['system']}")
    lines.append("╰────────────────────────────────────────")
    return "\n".join(lines)


def format_recording_transcript(metadata: dict, body: str) -> str:
    body = str(body or "").strip()
    fingerprint = recording_fingerprint(metadata)
    if fingerprint and body:
        return fingerprint + "\n\n" + body + "\n"
    if fingerprint:
        return fingerprint + "\n"
    return body + ("\n" if body else "")


def merge_speaker_segments(segments_by_source: dict[str, list[dict]]) -> str:
    entries = []
    order = {"microphone": 0, "system": 1}
    for role, segments in segments_by_source.items():
        label = SPEAKER_LABELS.get(role, role)
        for segment in segments or []:
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            try:
                start = max(0.0, float(segment.get("start") or 0))
                end = max(start, float(segment.get("end") or start))
            except (TypeError, ValueError):
                start = end = 0.0
            entries.append({"role": role, "label": label, "start": start, "end": end, "text": text})
    entries.sort(key=lambda item: (item["start"], order.get(item["role"], 99), item["end"]))
    merged = []
    for item in entries:
        if (merged and merged[-1]["role"] == item["role"]
                and item["start"] - merged[-1]["end"] <= 1.0):
            merged[-1]["end"] = max(merged[-1]["end"], item["end"])
            merged[-1]["text"] += " " + item["text"]
        else:
            merged.append(dict(item))
    return "\n\n".join(
        f"[{format_clock(item['start'])}–{format_clock(item['end'])}] {item['label']} — {item['text']}"
        for item in merged
    )


def speaker_sections(text_by_source: dict[str, str]) -> str:
    blocks = []
    for role in ("microphone", "system"):
        text = str(text_by_source.get(role) or "").strip()
        if text:
            blocks.append(f"## {SPEAKER_LABELS[role]}\n{text}")
    return "\n\n".join(blocks)
