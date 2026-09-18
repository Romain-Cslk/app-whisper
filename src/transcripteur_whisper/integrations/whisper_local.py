"""The existing CPU/int8 faster-whisper backend, loaded on demand."""
from __future__ import annotations

from pathlib import Path
from typing import Callable


def load_model(model_path: Path):
    from faster_whisper import WhisperModel

    return WhisperModel(str(model_path), device="cpu", compute_type="int8",
                        local_files_only=True)


def transcribe_segments(model, path: Path, language: str | None,
                        cancel_check: Callable[[], None],
                        on_segment: Callable[[float, list[dict]], None] | None = None):
    """Return text plus Whisper timestamps without changing model behaviour."""
    segments, info = model.transcribe(str(path), language=language, beam_size=5, vad_filter=True)
    duration = info.duration or 1.0
    timed: list[dict] = []
    lines: list[str] = []
    for segment in segments:
        cancel_check()
        text = (segment.text or "").strip()
        if text:
            # Some backends/tests expose only ``end``. Missing timing fields must
            # never discard text that Whisper has already yielded.
            start = float(getattr(segment, "start", 0) or 0)
            end = float(getattr(segment, "end", start) or start)
            timed.append({"start": start, "end": max(start, end), "text": text})
            lines.append(text)
        if on_segment is not None:
            on_segment(min(max(0.0, float(segment.end or 0)) / duration, 1.0), list(timed))
    cancel_check()
    return "\n".join(lines).strip(), timed


def transcribe(model, path: Path, language: str | None,
               cancel_check: Callable[[], None], on_segment: Callable[[float, str], None]) -> str:
    def progress(value: float, segments: list[dict]) -> None:
        on_segment(value, "\n".join(item["text"] for item in segments).strip())

    text, _segments = transcribe_segments(model, path, language, cancel_check, progress)
    return text
