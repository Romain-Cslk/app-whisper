"""Focused extensions of the existing native recording and result widgets."""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton

from ...services.library_storage import export_filename
from .recording_panel import RecordingPanel
from .results_panel import ResultsPanel


def confirm_removal(parent, path: Path, *, recovery=False) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle("Abandonner le WAV" if recovery else "Supprimer le fichier")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(f"Supprimer définitivement ce seul fichier ?\n\n{path}\n\n"
                "Cette action ne passe pas par la corbeille et ne peut pas être annulée. "
                "Les autres fichiers associés restent conservés.")
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


class LibraryRecordingPanel(RecordingPanel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._force_stopping = False
        self._normal_stop_error = ""
        self.name = QLineEdit()
        self.name.setMaxLength(80)
        self.name.setPlaceholderText("Ex. Réunion de lancement")
        self.name.setAccessibleName("Nom du résultat et de l'enregistrement")
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Nom du résultat"))
        name_row.addWidget(self.name, 1)
        self.layout().insertLayout(0, name_row)
        self.discard_button = QPushButton("Abandonner le WAV sélectionné…")
        self.discard_button.clicked.connect(self.discard_selected)
        self.discard_button.hide()
        self.layout().addWidget(self.discard_button)
        self.record_button.setMinimumHeight(46)
        self.record_button.setMinimumWidth(210)
        self.record_button.setStyleSheet('''
            QPushButton { background: #c63549; color: white; padding: 10px 18px;
                          border: 2px solid #f37082; border-radius: 7px; font-weight: bold; }
            QPushButton:hover { background: #df4158; }
            QPushButton:focus { border: 2px solid white; }
            QPushButton:disabled { background: #62424a; color: #c4c4c4; border-color: #6a5559; }
        ''')
        self.state_changed.connect(self._record_label)
        self._record_label()

    def _record_label(self):
        self.name.setEnabled(not self.busy)
        if self._force_stopping:
            text = "Sécurisation de l'enregistrement…"
        elif self._transition:
            text = "Finalisation…" if self._stopping else "Veuillez patienter…"
        elif self.service.is_active:
            text = "■ Arrêter l'enregistrement"
        else:
            text = "● Démarrer l'enregistrement"
        self.record_button.setText(text)

    def start_recording(self):
        self.service.output_name = self.name.text().strip()
        super().start_recording()

    def _failed(self, message: str) -> None:
        # The Windows/SoundCard loopback reader can exceptionally remain blocked
        # inside a driver call. A normal stop must not strand the whole UI in
        # that state: use the bounded salvage/detach path automatically.
        if self._stopping and hasattr(self.service, "force_stop"):
            if self.service.is_active and not self._force_stopping:
                self._normal_stop_error = message
                self._stopping = False
                self._force_stopping = True
                self.status.setText(
                    "Le flux Windows ne répond pas à l'arrêt standard. "
                    "Sécurisation des données déjà enregistrées…"
                )
                self.notice.emit(
                    "Arrêt audio lent détecté : récupération automatique du WAV en cours."
                )
                self._record_label()
                self.runner.submit(
                    self.service.force_stop,
                    self._force_stopped,
                    self._force_failed,
                    self._force_finished,
                )
                return
            if not self.service.is_active:
                # The backend already released its slot but could not produce a
                # final WAV. Keep recovery files and, importantly, leave the UI
                # operable (and allow an application close to finish).
                self._stopping = False
                self.level_timer.stop()
                self.status.setText(
                    "Arrêt effectué avec récupération nécessaire. Les données disponibles ont été conservées."
                )
                self.notice.emit(message)
                self.scan_recovery()
                return
        super()._failed(message)

    def _transition_finished(self):
        # The finish callback of the failed normal stop fires after _failed().
        # Keep the transition locked until the forced salvage worker is done.
        if self._force_stopping:
            return
        super()._transition_finished()

    def _force_stopped(self, result):
        self._normal_stop_error = ""
        self._stopping = False
        super()._stopped(result)
        self.status.setText(
            f"Enregistrement sécurisé : {Path(result.path).name}. Ajouté aux fichiers à transcrire."
        )
        self.notice.emit("Le flux audio bloqué a été libéré sans perdre le WAV récupérable.")

    def _force_failed(self, message: str):
        self._stopping = False
        self.level_timer.stop()
        self.status.setText(
            "Le flux audio a été libéré. Le WAV final n'a pas pu être recomposé immédiatement ; "
            "les pistes disponibles restent conservées pour récupération."
        )
        self.notice.emit(message)

    def _force_finished(self):
        self._force_stopping = False
        super()._transition_finished()
        self.scan_recovery()
        self._record_label()

    def _stopped(self, result):
        super()._stopped(result)
        warning = getattr(self.service, "last_publish_error", None)
        if warning:
            message = f"WAV conservé dans le dossier Enregistrements : {Path(result.path).name}. {warning}"
            self.status.setText(message)
            self.notice.emit(message)

    def _recovered(self, path):
        super()._recovered(path)
        warning = getattr(self.service, "last_publish_error", None)
        if warning:
            self.notice.emit("WAV récupéré localement : " + warning)

    def _show_recovery(self, paths):
        super()._show_recovery(paths)
        self.discard_button.setVisible(bool(paths))

    def _recovery_finished(self):
        self._set_transition(False)
        self.recover_button.setEnabled(True)
        self.discard_button.setEnabled(True)
        self.scan_recovery()

    def recover_selected(self):
        path = self.recovery_choices.currentData()
        if path is None or self.busy or self._closing:
            return
        self._set_transition(True)
        self.recover_button.setEnabled(False)
        self.discard_button.setEnabled(False)
        self.runner.submit(lambda: self.service.recover(path), self._recovered,
                           self.notice.emit, self._recovery_finished)

    def discard_selected(self):
        path = self.recovery_choices.currentData()
        if path is None or self.busy or self._closing or not confirm_removal(self, Path(path), recovery=True):
            return
        self._set_transition(True)
        self.recover_button.setEnabled(False)
        self.discard_button.setEnabled(False)
        self.runner.submit(lambda: self.service.discard_recovery(path),
                           lambda _: self.notice.emit("WAV abandonné. Aucun autre fichier supprimé."),
                           self.notice.emit, self._recovery_finished)


class LibraryResultsPanel(ResultsPanel):
    files_changed = Signal()

    def __init__(self, *args, **kwargs):
        self._snapshot = {}
        self._deleting = False
        super().__init__(*args, **kwargs)
        self.delete_button = QPushButton("Supprimer le fichier sélectionné…")
        self.delete_button.clicked.connect(self.delete_selected)
        self.layout().addWidget(self.delete_button)
        self._selection_changed()

    def load(self, job_id, snapshot):
        self._snapshot = snapshot
        super().load(job_id, snapshot)
        for metadata in snapshot.get("files", []):
            raw = metadata.get("summary_path")
            if not raw:
                continue
            path = Path(raw)
            if path in self.entries:
                continue
            self.entries.append(path)
            self.files.addItem(path.name)
            self.files.item(self.files.count() - 1).setToolTip(str(path))
        self._selection_changed()

    def _selection_changed(self, *args):
        super()._selection_changed(*args)
        if hasattr(self, "delete_button"):
            self.delete_button.setEnabled(bool(self.selected_path) and not self._deleting
                and self._snapshot.get("status") in {"done", "partial", "error", "cancelled"})

    def delete_selected(self):
        path, identifier = self.selected_path, self.job_id
        if not path or self._deleting or not confirm_removal(self, path):
            return
        self._deleting = True
        self._selection_changed()

        def deleted(_):
            self.files_changed.emit()
            if self.job_id == identifier:
                self.runner.submit(lambda: self.jobs.snapshot(identifier),
                    lambda snapshot: self.load(identifier, snapshot) if self.job_id == identifier else None,
                    self.notice.emit)

        def finished():
            self._deleting = False
            self._selection_changed()
        self.runner.submit(lambda: self.jobs.delete_file(path), deleted, self.notice.emit, finished)

    def export_batch(self, extension):
        identifier = self.job_id
        if identifier is None:
            return
        filename = export_filename(self._snapshot.get("output_name", ""),
                                   "resultats" if extension == "zip" else "transcription", "." + extension)
        destination, _ = QFileDialog.getSaveFileName(self, "Exporter le lot", filename,
                        "Archive ZIP (*.zip)" if extension == "zip" else "Texte (*.txt)")
        if destination:
            path = Path(destination)
            if not path.suffix:
                path = path.with_suffix("." + extension)
            self.runner.submit(lambda: self.jobs.export(identifier, path,
                kind="result" if extension == "zip" else "transcription"),
                lambda result: self.notice.emit(f"Export terminé : {result}"), self.notice.emit)

    def save_selected(self):
        source = self.selected_path
        if source is None:
            return
        kind = "summary" if any(m.get("summary_path") == str(source) for m in self._snapshot.get("files", [])) else (
            "transcription" if any(m.get("transcription_path") == str(source)
                                   for m in self._snapshot.get("files", [])) else "document"
        )
        filename = source.name
        if source.name in {"transcription.txt", "document.txt", "resume.txt", "summary.txt"}:
            filename = export_filename(self._snapshot.get("output_name", ""), kind)
        destination, _ = QFileDialog.getSaveFileName(self, "Enregistrer le résultat", filename, "Texte (*.txt)")
        if destination:
            target = Path(destination)
            if not target.suffix:
                target = target.with_suffix(".txt")
            if target.resolve() == source.resolve():
                return
            self.runner.submit(lambda: shutil.copyfile(source, target),
                               lambda _: self.notice.emit(f"Fichier enregistré : {target}"), self.notice.emit)
