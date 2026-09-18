from transcripteur_whisper.services.transcript_format import (
    format_recording_transcript,
    merge_speaker_segments,
    recording_fingerprint,
    speaker_sections,
)


def test_recording_fingerprint_has_start_end_and_duration():
    metadata = {
        "recording_id": "abc",
        "recording_started_at": "2026-09-17T10:00:00+00:00",
        "recording_duration": 3661,
        "recording_source_names": {"microphone": "Micro USB", "system": "Teams"},
    }
    text = recording_fingerprint(metadata)
    assert "Durée : 01:01:01" in text
    assert "Moi : Micro USB" in text
    assert "Autres interlocuteurs : Teams" in text
    assert "Début" in text and "Fin" in text


def test_imported_file_gets_no_fake_recording_fingerprint():
    assert recording_fingerprint({"duration": 60}) == ""
    assert format_recording_transcript({}, "bonjour") == "bonjour\n"


def test_local_source_segments_are_interleaved_and_labeled():
    result = merge_speaker_segments({
        "system": [{"start": 2.0, "end": 4.0, "text": "Bonjour Romain"}],
        "microphone": [
            {"start": 0.2, "end": 1.0, "text": "Salut"},
            {"start": 5.0, "end": 6.0, "text": "Oui"},
        ],
    })
    lines = result.split("\n\n")
    assert "Moi — Salut" in lines[0]
    assert "Autres interlocuteurs — Bonjour Romain" in lines[1]
    assert "Moi — Oui" in lines[2]


def test_api_source_sections_keep_sources_explicit():
    text = speaker_sections({"microphone": "ma réponse", "system": "leur question"})
    assert "## Moi\nma réponse" in text
    assert "## Autres interlocuteurs\nleur question" in text
