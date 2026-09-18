"""Recovery grouping and destructive-path guards, using real temporary files."""
import shutil
from pathlib import Path

import pytest

from transcripteur_whisper.services.library_storage import LibraryError
from transcripteur_whisper.services.recovery_sessions import JournalFile, RecoveryInbox, RecoverySession

A = 'a' * 32
B = 'b' * 32


@pytest.fixture
def inbox(tmp_path):
    root = tmp_path / 'temp' / 'recordings'
    root.mkdir(parents=True)
    return RecoveryInbox(root, tmp_path / 'ignorees')


def journal(inbox, identifier=A, role='microphone', data=b'original journal'):
    name = f'.native_recording_{identifier}_{role}.wav' if role != 'mixed' else f'native_recording_{identifier}.wav'
    path = inbox.root / name
    path.write_bytes(data)
    return path


def test_two_channels_and_mix_are_one_session(inbox):
    for role in ('microphone', 'system', 'mixed'):
        journal(inbox, role=role)
    sessions = inbox.list()
    assert len(sessions) == 1 and len(sessions[0].files) == 3
    assert 'Microphone' in sessions[0].sources_label
    assert 'Son du PC' in sessions[0].sources_label


def test_hundreds_of_files_do_not_become_hundreds_of_popups(inbox):
    for index in range(200):
        for role in ('microphone', 'system'):
            journal(inbox, identifier=f'{index:032x}', role=role)
    assert len(inbox.list()) == 200
    assert sum(len(s.files) for s in inbox.list()) == 400


def test_scan_does_not_write_or_create_files(inbox):
    journal(inbox)
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in inbox.root.iterdir()}
    for _ in range(10):
        inbox.list()
    assert before == {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in inbox.root.iterdir()}
    assert not inbox.quarantine.exists()


def test_scan_excludes_active_orphan_and_completed_sessions(inbox):
    journal(inbox, A)
    journal(inbox, B)
    inbox.blocked_ids = lambda: {A}
    assert [s.id for s in inbox.list()] == [B]
    inbox.completed_ids = lambda: {B}
    assert inbox.list() == ()


def test_no_recursive_scan_or_external_wav_enrollment(inbox):
    (inbox.root / 'Sources').mkdir()
    (inbox.root / 'Sources' / f'native_recording_{A}.wav').write_bytes(b'archive')
    (inbox.root / 'imported.wav').write_bytes(b'external')
    (inbox.root / 'anything.txt').write_text('not audio')
    assert inbox.list() == ()


def test_scan_handles_missing_folder_without_creating_it(tmp_path):
    inbox = RecoveryInbox(tmp_path / 'missing', tmp_path / 'quarantine')
    assert inbox.list() == ()
    assert not inbox.root.exists()


def test_abandonment_removes_selected_session_from_inbox_but_preserves_audio(inbox):
    selected = journal(inbox, A)
    journal(inbox, A, role='system')
    other = journal(inbox, B)
    session = next(s for s in inbox.list() if s.id == A)
    result = inbox.discard((session,))
    assert result.completed == (A,) and not result.errors
    assert not selected.exists() and other.exists()
    assert len(list(inbox.quarantine.rglob('*.wav'))) == 2
    assert [s.id for s in inbox.list()] == [B]


def test_abandoned_session_does_not_return_after_restart(inbox):
    journal(inbox)
    inbox.discard(inbox.list())
    reopened = RecoveryInbox(inbox.root, inbox.quarantine)
    assert not reopened.list()
    assert len(list(inbox.quarantine.rglob('*.wav'))) == 1


def test_changed_file_is_never_discarded(inbox):
    path = journal(inbox)
    selection = inbox.list()
    path.write_bytes(b'changed new recording data')
    result = inbox.discard(selection)
    assert result.errors and not result.completed
    assert path.read_bytes() == b'changed new recording data'


def test_new_channel_invalidates_old_session_selection(inbox):
    first = journal(inbox)
    selection = inbox.list()
    second = journal(inbox, role='system')
    result = inbox.discard(selection)
    assert result.errors and first.exists() and second.exists()


def test_busy_capture_refuses_all_operations(inbox):
    path = journal(inbox)
    selection = inbox.list()
    inbox.busy = lambda: True
    result = inbox.discard(selection)
    assert result.errors and path.exists()


def test_newly_blocked_session_is_rechecked(inbox):
    path = journal(inbox)
    selection = inbox.list()
    inbox.blocked_ids = lambda: {A}
    assert inbox.discard(selection).errors
    assert path.exists()


def test_symlink_is_not_followed_or_deleted(inbox, tmp_path):
    outside = tmp_path / 'external.wav'
    outside.write_bytes(b'external')
    link = inbox.root / f'native_recording_{A}.wav'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('No symlink permission')
    assert inbox.list() == ()
    assert outside.read_bytes() == b'external'


def test_forged_outside_path_refused(inbox, tmp_path):
    outside = tmp_path / f'native_recording_{A}.wav'
    outside.write_bytes(b'private')
    stat = outside.stat()
    fake = RecoverySession(A, (JournalFile(outside, 'mixed', stat.st_size, stat.st_mtime_ns),))
    assert inbox.discard((fake,)).errors
    assert outside.exists()


def test_batch_partial_failure_does_not_hide_failed_session(inbox, monkeypatch):
    a = journal(inbox, A)
    b = journal(inbox, B)
    original = shutil.move
    def move(source, destination):
        if Path(source) == a:
            raise PermissionError('locked')
        return original(source, destination)
    monkeypatch.setattr(shutil, 'move', move)
    result = inbox.discard(inbox.list())
    assert result.completed == (B,) and result.errors
    assert a.exists() and not b.exists()
    assert [s.id for s in inbox.list()] == [A]


def test_failed_second_move_rolls_back_first_without_losing_bytes(inbox, monkeypatch):
    first = journal(inbox, role='microphone', data=b'mic')
    second = journal(inbox, role='system', data=b'system')
    original = shutil.move
    def move(source, destination):
        if Path(source) == second:
            raise PermissionError('locked system')
        return original(source, destination)
    monkeypatch.setattr(shutil, 'move', move)
    result = inbox.discard(inbox.list())
    assert result.errors and not result.completed
    assert first.read_bytes() == b'mic' and second.read_bytes() == b'system'


def test_successful_recovery_outputs_one_file_and_consumes_session_inbox(inbox, tmp_path):
    original = journal(inbox)
    journal(inbox, role='system')
    destination = tmp_path / 'audio_recupere.wav'
    calls = []
    def recover(session):
        calls.append(session)
        destination.write_bytes(b'x' * 100)
        return destination
    result = inbox.recover(inbox.list(), recover)
    assert len(calls) == 1 and len(calls[0].files) == 2
    assert result.recovered_paths == (destination,)
    assert not original.exists() and destination.exists() and not inbox.list()


def test_failed_recovery_keeps_originals(inbox):
    original = journal(inbox)
    def recover(_session):
        raise LibraryError('Unreadable')
    result = inbox.recover(inbox.list(), recover)
    assert result.errors and not result.completed and original.exists()
    assert len(inbox.list()) == 1


def test_empty_recovered_result_does_not_consume_original(inbox, tmp_path):
    original = journal(inbox)
    destination = tmp_path / 'empty.wav'
    destination.write_bytes(b'')
    assert inbox.recover(inbox.list(), lambda _s: destination).errors
    assert original.exists()


def test_duplicate_selected_session_is_processed_once(inbox):
    journal(inbox)
    session = inbox.list()[0]
    result = inbox.discard((session, session))
    assert result.completed == (A,) and not result.errors


def test_quarantine_inside_scan_root_is_rejected(tmp_path):
    with pytest.raises(LibraryError):
        RecoveryInbox(tmp_path, tmp_path / 'unsafe')
