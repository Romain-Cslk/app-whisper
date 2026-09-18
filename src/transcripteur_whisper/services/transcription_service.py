"""Batch transcription orchestration, partial persistence and source separation."""
from __future__ import annotations

import shutil
from pathlib import Path

from ..integrations import openai_service, whisper_local
from ..models.jobs import JobCancelled, ProgressReporter
from ..models.transcription import TranscriptionOptions
from .document_service import generate_document
from .files import output_filename, write_text_atomic
from .media_service import MediaService
from .model_service import ModelService
from .transcript_format import format_recording_transcript, merge_speaker_segments, speaker_sections


class TranscriptionService:
    def __init__(self, paths, *, media=None, models=None):
        self.paths = paths
        self.media = media or MediaService(paths)
        self.models = models or ModelService(paths)

    def run(self, job: dict, options: TranscriptionOptions, api_key: str,
            reporter: ProgressReporter) -> None:
        model = None
        client = None
        try:
            if not options.use_api:
                model_path = self.models.download(options.model, reporter.cancel_check,
                    lambda value: reporter.job(model_download_progress=value), reporter.log)
                reporter.cancel_check()
                reporter.log("Chargement du modèle local (CPU int8)…")
                model = whisper_local.load_model(model_path)
            if options.use_api or options.output_type != "transcription":
                client = openai_service.make_openai_client(api_key)
            for index, metadata in enumerate(job["files"]):
                reporter.cancel_check()
                self._run_file(job, index, metadata, options, model, client, reporter)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            del model

    @staticmethod
    def _speaker_sources(metadata: dict):
        raw = metadata.get("recording_sources") or {}
        result = []
        for role in ("microphone", "system"):
            value = raw.get(role)
            if value:
                path = Path(value)
                if path.is_file():
                    result.append((role, path))
        return result

    def _local_source_transcript(self, sources, model, language, reporter, partial):
        collected: dict[str, list[dict]] = {}
        total = max(1, len(sources))
        for source_index, (role, path) in enumerate(sources):
            label = "Moi / microphone" if role == "microphone" else "Autres interlocuteurs / son du PC"
            reporter.log(f"Séparation par source : {label}.")

            def update(progress, segments, role=role, source_index=source_index):
                collected[role] = segments
                partial((source_index + progress) / total, merge_speaker_segments(collected),
                        speaker_separated=True)

            _text, segments = whisper_local.transcribe_segments(
                model, path, language, reporter.cancel_check, update
            )
            collected[role] = segments
        return merge_speaker_segments(collected)

    def _api_source_transcript(self, job, index, sources, options, client, reporter, partial):
        collected: dict[str, str] = {}
        cleanups = []
        total = max(1, len(sources))
        try:
            for source_index, (role, path) in enumerate(sources):
                label = "Moi / microphone" if role == "microphone" else "Autres interlocuteurs / son du PC"
                reporter.log(f"Séparation par source API : {label}.")
                chunks, cleanup, _duration = self.media.prepare_api_chunks(
                    job["id"], index * 10 + source_index + 1, path,
                    f"{job['files'][index]['output_stem']}_{role}", reporter.cancel_check,
                )
                cleanups.append(cleanup)

                def on_chunk(position, text, role=role, source_index=source_index, count=len(chunks)):
                    collected[role] = text
                    partial((source_index + position / max(1, count)) / total,
                            speaker_sections(collected), speaker_separated=True,
                            segment_index=position, segment_count=count)

                collected[role] = openai_service.transcribe_chunks(
                    client, chunks, options.model, options.language, reporter.cancel_check, on_chunk
                )
            return speaker_sections(collected)
        finally:
            for cleanup in cleanups:
                shutil.rmtree(cleanup, ignore_errors=True)

    def _run_file(self, job, index, metadata, options, model, client, reporter):
        cleanup = None
        result_dir = self.paths.results / job["id"]
        transcript = result_dir / output_filename(job, "transcription", index)
        document_requested = options.output_type != "transcription"
        weight = 0.85 if document_requested else 1.0

        def partial(progress: float, text: str, **changes) -> None:
            if text:
                write_text_atomic(transcript, format_recording_transcript(metadata, text))
                changes.update(out_path=str(transcript), transcription_path=str(transcript),
                               transcription_available=True)
            reporter.file(index, progress=progress * weight, **changes)
            reporter.job(progress=(index + progress * weight) / len(job["files"]))

        reporter.file(index, status="running", stage="conversion", error=None)
        reporter.log(f"Traitement : {metadata['name']}")
        try:
            source = Path(metadata["path"])
            speaker_sources = self._speaker_sources(metadata)
            if speaker_sources:
                reporter.file(index, stage="transcription_api" if options.use_api else "transcription_local")
                reporter.log("Transcription séparée par source audio : microphone = Moi ; son du PC = Autres interlocuteurs.")
                if options.use_api:
                    text = self._api_source_transcript(
                        job, index, speaker_sources, options, client, reporter, partial
                    )
                else:
                    reporter.log("VAD Silero · beam_size=5 · séparation source audio")
                    text = self._local_source_transcript(
                        speaker_sources, model, options.language, reporter, partial
                    )
            elif options.use_api:
                chunks, cleanup, duration = self.media.prepare_api_chunks(
                    job["id"], index, source, metadata["output_stem"], reporter.cancel_check)
                reporter.file(index, stage="transcription_api", duration=duration, segment_count=len(chunks))
                reporter.log(f"Transcription OpenAI : {len(chunks)} segment(s) de dix minutes au maximum.")

                def on_chunk(position, text):
                    partial(position / max(1, len(chunks)), text, segment_index=position)
                    reporter.log(f"Segment {position}/{len(chunks)} reçu.")

                text = openai_service.transcribe_chunks(client, chunks, options.model,
                    options.language, reporter.cancel_check, on_chunk)
            else:
                processing, cleanup = self.media.prepare_local_media(
                    job["id"], index, source, metadata["output_stem"], reporter.cancel_check)
                reporter.file(index, stage="transcription_local")
                reporter.log("VAD Silero · beam_size=5")
                text = whisper_local.transcribe(model, processing, options.language,
                                               reporter.cancel_check, partial)

            rendered = format_recording_transcript(metadata, text)
            write_text_atomic(transcript, rendered)
            reporter.file(index, out_path=str(transcript), transcription_path=str(transcript),
                          transcription_available=True, stage="recomposition")
            reporter.cancel_check()
            if document_requested and text:
                reporter.file(index, stage="document")
                reporter.log(f"Génération du document : {options.output_type}")
                processed = generate_document(client, options.output_type, text,
                    cancel_check=reporter.cancel_check, log=reporter.log)
                document = result_dir / output_filename(job, options.output_type, index)
                write_text_atomic(document, processed)
                reporter.file(index, out_path=str(document), document_path=str(document), document_available=True)
            reporter.file(index, status="done", stage="done", progress=1.0)
            reporter.job(progress=(index + 1) / len(job["files"]))
        except JobCancelled:
            reporter.file(index, status="cancelled", stage="cancelled")
            raise
        except Exception as exc:
            available = transcript.is_file() and transcript.stat().st_size > 0
            status = "partial" if available else "error"
            reporter.file(index, status=status, stage=status, error=str(exc))
            reporter.log(f"{'Résultat partiel conservé' if available else 'Échec'} pour {metadata['name']} : {exc}")
        finally:
            if cleanup is not None:
                shutil.rmtree(cleanup, ignore_errors=True)
