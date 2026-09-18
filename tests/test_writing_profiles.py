"""Writing settings tests: real files, no Qt or external API calls."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from transcripteur_whisper.services.library_storage import LibraryError
from transcripteur_whisper.services.summary_profiles import WritingStore, prompt_fingerprint


@pytest.fixture
def store(tmp_path):
    return WritingStore(SimpleNamespace(config=tmp_path))


def test_three_default_profiles_are_named_and_distinct(store):
    preferences = store.load()
    assert [p.name for p in preferences.profiles] == ['Résumé', 'Daily', 'Compte rendu']
    assert len({p.prompt for p in preferences.profiles}) == 3
    assert preferences.selected.name == 'Daily'


def test_previous_daily_prompt_migrates_but_credentials_never_do(store):
    store.legacy_path.write_text(json.dumps({'prompt': 'Mon ancien daily', 'keys': {'openai': 'SECRET'}}))
    preferences = store.save(store.load())
    assert preferences.selected.prompt == 'Mon ancien daily'
    assert 'SECRET' not in store.path.read_text()
    assert 'keys' not in json.loads(store.path.read_text())


def test_context_people_vocabulary_survive_restart(store):
    saved = store.save(replace(store.load(), context='Projet de test', people='Alice : testeur', vocabulary='ABC : application'))
    loaded = WritingStore(SimpleNamespace(config=store.path.parent)).load()
    assert loaded == saved


def test_selected_profile_is_persisted(store):
    store.save(store.load())
    assert store.select('resume').selected.name == 'Résumé'
    assert store.load().selected_id == 'resume'


def test_custom_model_can_be_named_and_added(store):
    preferences = store.load().add_profile('Analyse technique', 'Explique les décisions techniques sans invention.')
    saved = store.save(preferences)
    assert saved.selected.name == 'Analyse technique'
    assert len(store.load().profiles) == 4


def test_instructions_include_context_without_treating_it_as_meeting_evidence(store):
    preferences = replace(store.load(), context='Contexte projet', people='Alice : référente', vocabulary='ERP : application')
    text = preferences.instructions('compte-rendu')
    assert text.startswith(preferences.profiles[2].prompt)
    for value in ('Contexte projet', 'Alice : référente', 'ERP : application', 'seule source des faits', 'sans prouver'):
        assert value in text


def test_job_instructions_are_immutable_snapshot(store):
    original = store.load()
    frozen = original.instructions()
    modified = replace(original, context='Autre contexte')
    store.save(modified)
    assert original.instructions() == frozen
    assert store.load().instructions() != frozen


def test_concurrent_edit_refuses_overwrite(store):
    first = store.load()
    second = store.load()
    store.save(replace(first, people='Alice'))
    with pytest.raises(LibraryError, match='autre fenêtre'):
        store.save(replace(second, people='Bob'))
    assert store.load().people == 'Alice'


@pytest.mark.parametrize('bad', ['', ' ' * 5, 'X' * 61])
def test_invalid_names_rejected_without_creating_file(store, bad):
    preferences = store.load()
    candidate = replace(preferences, profiles=(replace(preferences.profiles[0], name=bad), *preferences.profiles[1:]))
    with pytest.raises(LibraryError):
        store.save(candidate)
    assert not store.path.exists()


@pytest.mark.parametrize('prompt', ['', ' ', 'x' * 20001])
def test_invalid_prompt_rejected(store, prompt):
    preferences = store.load()
    with pytest.raises(LibraryError):
        store.save(replace(preferences, profiles=(replace(preferences.profiles[0], prompt=prompt), *preferences.profiles[1:])))


def test_duplicate_case_insensitive_name_refused(store):
    with pytest.raises(LibraryError):
        store.load().add_profile('DAILY', 'new instruction')


def test_duplicate_ids_refused(store):
    preferences = store.load()
    with pytest.raises(LibraryError):
        store.save(replace(preferences, profiles=(preferences.profiles[0], preferences.profiles[0])))


def test_missing_selection_refused(store):
    with pytest.raises(LibraryError):
        store.save(replace(store.load(), selected_id='unknown'))


@pytest.mark.parametrize('field', ['context', 'people', 'vocabulary'])
def test_context_size_limit(store, field):
    with pytest.raises(LibraryError):
        store.save(replace(store.load(), **{field: 'x' * 12001}))


def test_malformed_file_not_silently_reset(store):
    store.path.write_text('{broken')
    with pytest.raises(LibraryError):
        store.load()
    assert store.path.read_text() == '{broken'


def test_revision_not_written_and_no_hidden_key_fields(store):
    store.save(store.load())
    assert set(json.loads(store.path.read_text())) == {'version', 'profiles', 'selected_id', 'context', 'people', 'vocabulary'}


def test_fingerprint_changes_for_context_or_profile(store):
    preferences = store.load()
    assert prompt_fingerprint(preferences, 'daily') != prompt_fingerprint(preferences, 'resume')
    assert prompt_fingerprint(preferences, 'daily') != prompt_fingerprint(replace(preferences, context='new'), 'daily')
