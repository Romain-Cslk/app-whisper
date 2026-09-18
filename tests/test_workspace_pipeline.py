"""No live API calls: every writing phase receives the selected instructions."""
from transcripteur_whisper.services import ai_summary


def test_long_document_uses_selected_profile_and_context_for_every_part(monkeypatch):
    calls = []
    def provider(provider, key, instructions, text):
        calls.append((instructions, text))
        return 'Note factuelle.'
    monkeypatch.setattr(ai_summary, '_call_provider', provider)
    prompt = 'Format : analyse technique. Vocabulaire : QXY est une application.'
    result = ai_summary.generate_daily_summary('openai', 'test-only', prompt, 'x' * 56000)
    assert result == 'Note factuelle.'
    assert len(calls) >= 3
    assert all(prompt in instructions for instructions, _text in calls)
    assert 'Phase de préparation' in calls[0][0]
    assert 'Phase de préparation' not in calls[-1][0]


def test_short_document_also_receives_context(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_summary, '_call_provider', lambda provider, key, instructions, text: calls.append(instructions) or 'Résultat')
    ai_summary.generate_daily_summary('gemini', 'test-only', 'Contexte projet de test', 'La réunion commence.')
    assert len(calls) == 1 and 'Contexte projet de test' in calls[0]
