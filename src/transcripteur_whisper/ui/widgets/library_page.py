"""History filters and explicit deletion, reusing the native week view."""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPushButton

from ...services.library_history import LibraryHistoryService
from ...services.library_storage import export_filename
from .history_page import STATUS_LABELS, HistoryPage
from .library_controls import confirm_removal


class LibraryPage(HistoryPage):
    files_changed = Signal()

    def __init__(self, runner, jobs, paths, catalog, parent=None):
        self._generation = 0
        self._reload_pending = False
        self._last_key = None
        self._deleting = False
        self._text_ready = False
        super().__init__(runner, jobs, paths, parent=parent,
                         service=LibraryHistoryService(paths, jobs, catalog))

    def _build_ui(self):
        super()._build_ui()
        self.detail_title.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_meta.setTextFormat(Qt.TextFormat.PlainText)
        for label in self.findChildren(QLabel):
            if label.text().startswith("Vue semaine"):
                label.setText("Vue semaine : date de capture connue, date du traitement ou date de fichier estimée. Voir le détail.")
        self.search.setMaxLength(200)
        self.audios = QCheckBox("Audios")
        self.transcripts = QCheckBox("Transcripts / documents")
        self.audios.setChecked(True)
        self.transcripts.setChecked(True)
        filters = QHBoxLayout()
        filters.addWidget(self.audios)
        filters.addWidget(self.transcripts)
        filters.addStretch()
        self.layout().insertLayout(1, filters)
        self.audios.toggled.connect(lambda _: self.refresh())
        self.transcripts.toggled.connect(lambda _: self.refresh())
        self.delete_button = QPushButton("Supprimer le fichier sélectionné…")
        self.delete_button.clicked.connect(self.delete_selected)
        # Keep actions within the detail pane, not across the calendar.
        self.preview.parentWidget().layout().addWidget(self.delete_button)
        self.delete_button.setEnabled(False)

    def _query_key(self):
        return (self.current_week, self.search.text().strip(), self.audios.isChecked(), self.transcripts.isChecked())

    def refresh(self):
        self._generation += 1
        if self._loading:
            self._reload_pending = True
            return
        generation = self._generation
        key = self._query_key()
        if self._last_key is not None and self._last_key != key:
            self.entries.clear()
            self.table.clearContents()
            self._clear_detail("Chargement de la sélection…")
        self._last_key = key
        self._loading = True
        self.refresh_button.setEnabled(False)
        self.runner.submit(
            lambda: self.service.week(key[0], key[1], audio=key[2], transcripts=key[3]),
            lambda entries: self._render(entries) if generation == self._generation and key == self._query_key() else None,
            lambda message: self._load_failed(message) if generation == self._generation else None,
            self._load_finished,
        )

    def _load_finished(self):
        super()._load_finished()
        if self._reload_pending:
            self._reload_pending = False
            QTimer.singleShot(0, self.refresh)

    def _render(self, entries):
        super()._render(entries)
        # Several recordings in one hour must never hide each other.
        for row in range(24):
            counts = [sum(e.start.hour == row and max(0, min(6, (e.start.date() - self.current_week).days)) == day
                          for e in entries) for day in range(7)]
            self.table.setRowHeight(row, max(62, max(counts, default=0) * 44 + 6))
        if not entries and not self.audios.isChecked() and not self.transcripts.isChecked():
            self._clear_detail("Cochez Audios, Transcripts ou les deux pour afficher les fichiers.")

    def select_entry(self, identifier):
        super().select_entry(identifier)
        entry = self.entries.get(identifier)
        if entry:
            days = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")
            date = f"{days[entry.start.weekday()]} {entry.start:%d/%m/%Y %H:%M}"
            details = [date, STATUS_LABELS.get(entry.status, entry.status)]
            duration = self._format_duration(entry.duration_seconds)
            if duration:
                details.append(duration)
            details.extend((getattr(entry, "date_note", ""), entry.source_name))
            self.detail_meta.setText(" \u00b7 ".join(value for value in details if value))

    def _clear_detail(self, message):
        self._read_request += 1
        self._text_ready = False
        super()._clear_detail(message)

    def _artifact_selected(self, row):
        self._read_request += 1
        request = self._read_request
        artifact = self.selected_artifact
        self._text_ready = False
        self._set_detail_buttons(artifact is not None)
        if artifact is None:
            self.preview.clear()
        elif artifact.kind == "audio":
            self.preview.setPlainText("Utilisez Ouvrir pour écouter cet audio. La suppression des audios importés ou externes est interdite.")
        else:
            self.preview.setPlainText("Chargement…")
            self.runner.submit(lambda: self.service.read_text(artifact.path),
                lambda text: self._loaded(request, text),
                lambda message: self._read_failed(request, message))

    def _loaded(self, request, text):
        if request == self._read_request:
            self.preview.setPlainText(text)
            self._text_ready = True
            self.copy_button.setEnabled(True)

    def _read_failed(self, request, message):
        if request == self._read_request:
            self.preview.setPlainText(f"Lecture impossible : {message}")
            self.copy_button.setEnabled(False)

    def _set_detail_buttons(self, available):
        super()._set_detail_buttons(available)
        self.copy_button.setEnabled(bool(available and self._text_ready))
        if hasattr(self, "delete_button"):
            artifact = self.selected_artifact
            managed = bool(artifact and (artifact.kind != "audio" or self.service.catalog.find(artifact.path)))
            self.delete_button.setEnabled(available and managed and not self._deleting)

    def copy_text(self):
        if self._text_ready:
            QApplication.clipboard().setText(self.preview.toPlainText())
            self.notice.emit("Texte copié.")

    def delete_selected(self):
        artifact, identifier = self.selected_artifact, self._selected_id
        if not artifact or not identifier or self._deleting or not confirm_removal(self, artifact.path):
            return
        self._deleting = True
        self._set_detail_buttons(True)

        def success(_):
            self._clear_detail("Fichier supprimé. Les autres fichiers associés sont conservés.")
            self.files_changed.emit()
            self.refresh()

        def finish():
            self._deleting = False
            self._set_detail_buttons(self.selected_artifact is not None)
        self.runner.submit(lambda: self.service.delete_artifact(identifier, artifact.path), success, self.notice.emit, finish)

    def open_selected(self):
        artifact = self.selected_artifact
        if artifact:
            def open_file(path):
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                    self.notice.emit("Le système ne peut pas ouvrir ce fichier.")
            self.runner.submit(lambda: self.service._safe_result_path(artifact.path), open_file, self.notice.emit)

    def save_selected(self):
        artifact = self.selected_artifact
        entry = self.entries.get(self._selected_id)
        if not artifact or not entry:
            return
        filename = artifact.label
        if filename in {"transcription.txt", "transcriptions.txt", "document.txt", "resume.txt"}:
            filename = export_filename(entry.title, artifact.kind)
        extension = ".wav" if artifact.kind == "audio" else ".txt"
        destination, _ = QFileDialog.getSaveFileName(self, "Enregistrer le fichier", filename,
                             "Audio WAV (*.wav)" if extension == ".wav" else "Texte (*.txt)")
        if destination:
            target = Path(destination)
            if not target.suffix:
                target = target.with_suffix(extension)
            def save():
                source = self.service._safe_result_path(artifact.path)
                if target.resolve() != source:
                    shutil.copyfile(source, target)
            self.runner.submit(save, lambda _: self.notice.emit(f"Fichier enregistré : {target}"), self.notice.emit)
