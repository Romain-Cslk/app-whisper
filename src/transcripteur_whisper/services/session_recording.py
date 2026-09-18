"""Grouped recovery over the existing native recorder; no scan-time audio calls."""
from __future__ import annotations

import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from .library_recording import LibraryRecordingService
from .library_storage import LibraryError, atomic_copy
from .recovery_sessions import RecoveryInbox, RecoverySession


class SessionRecordingService(LibraryRecordingService):
    def __init__(self, paths, devices, catalog):
        self._session_file_lock = threading.RLock()
        super().__init__(paths, devices, catalog)
        self.recovery_inbox = RecoveryInbox(
            self._journal_dir, paths.root / "RecuperationIgnoree",
            blocked_ids=self._blocked_recovery_ids,
            completed_ids=self._completed_recovery_ids,
            busy=lambda: self.is_active,
        )

    def _blocked_recovery_ids(self) -> set[str]:
        values = set()
        for result in (getattr(self, "_active_result", None), getattr(self, "_pending_final", None)):
            if result is not None:
                values.add(result.recording_id)
        for session, _finalized in getattr(self, "_orphan_sessions", ()):
            values.add(session.recording_id)
        session = getattr(self._recorder, "_active", None)
        if session is not None:
            values.add(session.recording_id)
        return values

    def _completed_recovery_ids(self) -> set[str]:
        with self.catalog.lock:
            values = set()
            for key, record in self.catalog.records.items():
                # Only catalogued results, or deliberately deleted results, count
                # as handled. A missing destination is not proof of recovery.
                if record.get("deleted") or (record.get("path") and Path(record["path"]).is_file()):
                    values.add(key.removeprefix("recovery:"))
                    if record.get("recovery_session_id"):
                        values.add(record["recovery_session_id"])
            return values

    def recovery_sessions(self):
        return self.recovery_inbox.list()

    def _start(self, *args):
        # Serialize start with file recovery, without routing file I/O through a
        # potentially blocked SoundCard/COM coordinator.
        with self._session_file_lock:
            return super()._start(*args)

    def discard_sessions(self, sessions):
        with self._session_file_lock:
            return self.recovery_inbox.discard(tuple(sessions))

    def recover_sessions(self, sessions):
        with self._session_file_lock:
            return self.recovery_inbox.recover(tuple(sessions), self._recover_session)

    def _recover_session(self, session: RecoverySession) -> Path:
        import soundfile as sf

        from ..audio.recovery import recover_wav

        identifier = "recovery:" + session.id
        with self.catalog.lock:
            existing = self.catalog.records.get(identifier)
            if existing and not existing.get("deleted") and Path(existing["path"]).is_file():
                return Path(existing["path"])
        target_root = self.catalog.preferences.audio_work_dir
        target_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(max(f.mtime_ns for f in session.files) / 1e9).strftime("%Y-%m-%d_%Hh%M")
        target = target_root / f"audio_Recuperation_{stamp}_{session.id[:8]}.wav"
        counter = 2
        while target.exists():
            target = target_root / f"audio_Recuperation_{stamp}_{session.id[:8]}_{counter}.wav"
            counter += 1
        sources_root = target_root / "Sources" / ("recuperation_" + session.id)
        with tempfile.TemporaryDirectory(prefix="whisper-recovery-") as temporary:
            staging = Path(temporary)
            repaired, failed = {}, []
            for item in session.files:
                try:
                    repaired[item.role] = recover_wav(item.path, staging)
                except Exception as exc:
                    failed.append(f"{item.role}: {exc}")
            # Never hide corrupt source material merely because another channel
            # is valid. The user can keep the complete session for investigation.
            if failed:
                raise LibraryError("Récupération incomplète ; originaux conservés. " + "; ".join(failed))
            if not repaired:
                raise LibraryError("Aucune piste WAV exploitable.")
            mixed = repaired.get("mixed")
            if mixed is None:
                mixed = staging / "mix.wav"
                self._mix_recovered_tracks(repaired, mixed)
            info = sf.info(mixed)
            if not info.frames or not info.samplerate:
                raise LibraryError("Le fichier récupéré est vide.")
            atomic_copy(mixed, target)
            # Preserve the journal's estimated end date, not the recovery time.
            # AudioCatalog marks inferred timing as estimated for the history.
            ended_ns = max(file.mtime_ns for file in session.files)
            os.utime(target, ns=(ended_ns, ended_ns))
            source_paths = {}
            try:
                for role, source in repaired.items():
                    if role == "mixed":
                        continue
                    destination = sources_root / ("moi_microphone.wav" if role == "microphone" else "autres_son_du_pc.wav")
                    atomic_copy(source, destination)
                    source_paths[role] = str(destination)
                # V7 preserves private tracks and owns their retention together
                # with the mixed recording. No final-audio archive is introduced.
                self.catalog.register(target, identifier, duration=info.frames / info.samplerate,
                                      sources=source_paths,
                                      source_names={"microphone": "Microphone récupéré", "system": "Son du PC récupéré"})
                with self.catalog.lock:
                    self.catalog.records[identifier]["recovery_session_id"] = session.id
                    self.catalog._save()
            except Exception:
                # Only files created by this operation; originals are untouched.
                target.unlink(missing_ok=True)
                for path in source_paths.values():
                    Path(path).unlink(missing_ok=True)
                raise
            return target

    @staticmethod
    def _mix_recovered_tracks(tracks: dict[str, Path], destination: Path) -> None:
        import numpy as np
        import soundfile as sf

        opened = []
        try:
            for role, path in tracks.items():
                opened.append((role, sf.SoundFile(path)))
            rates = {handle.samplerate for _role, handle in opened}
            if len(rates) != 1:
                raise LibraryError("Fréquences audio différentes : les pistes sont conservées séparément.")
            with sf.SoundFile(destination, "w", samplerate=next(iter(rates)), channels=2, subtype="PCM_16") as output:
                while True:
                    blocks = [(role, handle.read(65536, dtype="float32", always_2d=True)) for role, handle in opened]
                    length = max(len(block) for _role, block in blocks)
                    if not length:
                        break
                    combined = np.zeros((length, 2), dtype="float32")
                    for role, block in blocks:
                        if block.shape[1] != 2:
                            raise LibraryError("Piste source au format inattendu.")
                        combined[:len(block)] += block * (0.8 if role == "system" else 1.0)
                    output.write(np.clip(combined, -1.0, 1.0))
        finally:
            for _role, handle in opened:
                handle.close()
