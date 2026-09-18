"""Recovery inbox: one row per capture, no scan-time writes or recursive deletion."""
from __future__ import annotations

import re
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from .library_storage import LibraryError, atomic_json

JOURNAL_NAME = re.compile(r"\.?native_recording_([0-9a-f]{32})(?:_(microphone|system))?\.wav\Z", re.I)


@dataclass(frozen=True)
class JournalFile:
    path: Path
    role: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class RecoverySession:
    id: str
    files: tuple[JournalFile, ...]

    @property
    def size(self) -> int:
        return sum(file.size for file in self.files)

    @property
    def date_label(self) -> str:
        timestamp = max(file.mtime_ns for file in self.files) / 1_000_000_000
        return datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M")

    @property
    def sources_label(self) -> str:
        labels = {"microphone": "Microphone", "system": "Son du PC", "mixed": "Piste mixée"}
        return " + ".join(labels[role] for role in dict.fromkeys(file.role for file in self.files))


@dataclass(frozen=True)
class RecoveryBatchResult:
    completed: tuple[str, ...] = ()
    recovered_paths: tuple[Path, ...] = ()
    errors: tuple[str, ...] = ()
    quarantine: Path | None = None


class RecoveryInbox:
    """Operations take immutable scan snapshots and revalidate every file.

    Abandonment moves the selected journals to a private quarantine, not the
    system recycle bin. Interrupted moves cannot erase the only audio copy.
    Active and orphaned capture IDs are excluded and rechecked before writes.
    """

    def __init__(self, journal_root: Path, quarantine: Path, *,
                 blocked_ids: Callable[[], set[str]] = lambda: set(),
                 completed_ids: Callable[[], set[str]] = lambda: set(),
                 busy: Callable[[], bool] = lambda: False):
        self.root = Path(journal_root).resolve()
        self.quarantine = Path(quarantine).resolve()
        self.blocked_ids, self.completed_ids, self.busy = blocked_ids, completed_ids, busy
        self.lock = threading.RLock()
        if self.quarantine == self.root or self.quarantine.is_relative_to(self.root):
            raise LibraryError("La zone de mise de côté doit être extérieure au dossier de récupération.")

    def list(self) -> tuple[RecoverySession, ...]:
        with self.lock:
            blocked = self.blocked_ids() | self.completed_ids()
            grouped: dict[str, list[JournalFile]] = {}
            if not self.root.is_dir():
                return ()
            # Deliberately top-level only: never read Sources/, imported WAVs or
            # the quarantine, and never generate a recovered file during a scan.
            for path in self.root.iterdir():
                match = JOURNAL_NAME.fullmatch(path.name)
                if not match or match[1].lower() in blocked:
                    continue
                try:
                    if path.is_symlink() or path.absolute() != path.resolve() or not path.is_file():
                        continue
                    stat = path.stat()
                    grouped.setdefault(match[1].lower(), []).append(
                        JournalFile(path, match[2].lower() if match[2] else "mixed", stat.st_size, stat.st_mtime_ns)
                    )
                except OSError:
                    continue
            sessions = [RecoverySession(key, tuple(sorted(items, key=lambda f: f.role)))
                        for key, items in grouped.items()]
            return tuple(sorted(sessions, key=lambda s: max(f.mtime_ns for f in s.files), reverse=True))

    def validate(self, session: RecoverySession) -> None:
        if self.busy():
            raise LibraryError("Arrêtez la capture avant de gérer les sessions de récupération.")
        if session.id in self.blocked_ids():
            raise LibraryError("Cette session est encore utilisée par le moteur audio.")
        if not re.fullmatch(r"[0-9a-f]{32}", session.id):
            raise LibraryError("Session de récupération invalide.")
        expected = {file.path for file in session.files}
        actual = set()
        for path in self.root.iterdir():
            match = JOURNAL_NAME.fullmatch(path.name)
            if match and match[1].lower() == session.id:
                actual.add(path)
        if not expected or actual != expected:
            raise LibraryError("La session a changé depuis son affichage. Actualisez la liste.")
        for file in session.files:
            path = file.path
            match = JOURNAL_NAME.fullmatch(path.name)
            if (path.parent != self.root or not match or match[1].lower() != session.id
                    or path.is_symlink() or path.absolute() != path.resolve() or not path.is_file()):
                raise LibraryError("Un fichier ne fait pas partie de cette session.")
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (file.size, file.mtime_ns):
                raise LibraryError("Un fichier est encore modifié. Il reste conservé.")

    def _quarantine(self, session: RecoverySession, reason: str) -> Path:
        self.validate(session)
        target = self.quarantine / f"{datetime.now():%Y-%m-%d_%Hh%M%S}_{session.id[:8]}_{uuid4().hex[:6]}"
        target.mkdir(parents=True, exist_ok=False)
        moved: list[tuple[Path, Path]] = []
        try:
            atomic_json(target / "session.json", {
                "session": session.id, "reason": reason,
                "files": [{"name": file.path.name, "size": file.size, "mtime_ns": file.mtime_ns}
                          for file in session.files],
            })
            for file in session.files:
                # A coordinator serializes start/recovery; the stat comparison
                # also guards files which an external writer changed meanwhile.
                stat = file.path.stat()
                if (stat.st_size, stat.st_mtime_ns) != (file.size, file.mtime_ns):
                    raise LibraryError("Un fichier a changé pendant l'opération.")
                destination = target / file.path.name
                shutil.move(str(file.path), str(destination))
                moved.append((file.path, destination))
        except Exception:
            for original, destination in reversed(moved):
                try:
                    if not original.exists():
                        shutil.move(str(destination), str(original))
                except OSError:
                    pass  # The preserved file and receipt remain in quarantine.
            raise
        return target

    def discard(self, sessions: tuple[RecoverySession, ...]) -> RecoveryBatchResult:
        completed, errors = [], []
        with self.lock:
            for session in dict.fromkeys(sessions):
                try:
                    self._quarantine(session, "abandon explicite")
                    completed.append(session.id)
                except (OSError, LibraryError) as exc:
                    errors.append(f"Session {session.id[:8]} : {exc}")
        return RecoveryBatchResult(tuple(completed), errors=tuple(errors), quarantine=self.quarantine)

    def recover(self, sessions: tuple[RecoverySession, ...],
                recover_one: Callable[[RecoverySession], Path]) -> RecoveryBatchResult:
        completed, paths, errors = [], [], []
        with self.lock:
            for session in dict.fromkeys(sessions):
                try:
                    self.validate(session)
                    path = Path(recover_one(session))
                    if not path.is_file() or path.stat().st_size <= 44 or path.parent == self.root:
                        raise LibraryError("Aucun WAV récupéré n'a été validé. Les originaux restent conservés.")
                    completed.append(session.id)
                    paths.append(path)
                    try:
                        self._quarantine(session, "récupération réussie")
                    except (OSError, LibraryError) as exc:
                        errors.append(f"Session {session.id[:8]} récupérée ; originaux non déplacés : {exc}")
                except (OSError, LibraryError, RuntimeError, ValueError) as exc:
                    errors.append(f"Session {session.id[:8]} : {exc}")
        return RecoveryBatchResult(tuple(completed), tuple(paths), tuple(errors), self.quarantine)
