"""A single searchable recovery inbox, rather than a growing WAV combobox."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class RecoveryDialog(QDialog):
    recovered = Signal(object)
    changed = Signal()

    def __init__(self, runner, service, parent=None):
        super().__init__(parent)
        self.runner, self.service = runner, service
        self._busy = False
        self._sessions = ()
        self._result_message = ""
        self.setWindowTitle("Enregistrements à récupérer")
        self.resize(860, 510)
        self.setMinimumSize(550, 360)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher une date ou une session…")
        self.refresh_button = QPushButton("Actualiser")
        self.all_button = QPushButton("Tout sélectionner")
        self.recover_button = QPushButton("Récupérer la sélection")
        self.recover_button.setProperty("primary", True)
        self.discard_button = QPushButton("Abandonner la sélection…")
        self.close_button = QPushButton("Fermer")
        self.folder_button = QPushButton("Éléments mis de côté")
        note = QLabel("Une ligne = une session, même avec deux sources. Les captures actives et les sessions déjà traitées sont exclues.")
        note.setWordWrap(True)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Date du fichier (estimée)", "Sources", "Taille", "Session"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.count = QLabel()
        self.status = QLabel("Chargement…")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        tools = QHBoxLayout()
        tools.addWidget(self.search, 1)
        tools.addWidget(self.all_button)
        tools.addWidget(self.refresh_button)
        actions = QHBoxLayout()
        actions.addWidget(self.recover_button)
        actions.addWidget(self.discard_button)
        actions.addStretch()
        actions.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(tools)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.count)
        layout.addWidget(self.status)
        layout.addWidget(self.folder_button)
        layout.addLayout(actions)
        self.search.textChanged.connect(self._filter)
        self.refresh_button.clicked.connect(self.refresh)
        self.all_button.clicked.connect(self._select_visible)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.recover_button.clicked.connect(lambda: self._operate(False))
        self.discard_button.clicked.connect(lambda: self._operate(True))
        self.close_button.clicked.connect(self.reject)
        self.folder_button.clicked.connect(self._open_quarantine)
        self._selection_changed()
        self.refresh()

    def refresh(self):
        if self._busy:
            return
        self._result_message = ""
        self.status.setText("Chargement des sessions…")
        self._set_busy(True)
        self.runner.submit(self.service.recovery_sessions, self._render,
                           lambda message: self.status.setText(message), lambda: self._set_busy(False))

    def _render(self, sessions):
        self._sessions = tuple(sessions)
        self.table.setRowCount(0)
        for row, session in enumerate(sessions):
            self.table.insertRow(row)
            for col, text in enumerate((session.date_label, session.sources_label,
                                        f"{session.size / (1024 ** 2):.1f} Mo", session.id[:8])):
                item = QTableWidgetItem(text)
                item.setToolTip("\n".join(file.path.name for file in session.files))
                self.table.setItem(row, col, item)
        self._filter()
        if not sessions:
            empty = "Aucune session à récupérer. Les éléments mis de côté restent hors de cette liste."
            self.status.setText((self._result_message + "\n" if self._result_message else "") + empty)

    def _filter(self, *_args):
        query = self.search.text().casefold().strip()
        for row, session in enumerate(self._sessions):
            haystack = " ".join((session.date_label, session.sources_label, session.id)).casefold()
            hidden = query not in haystack
            self.table.setRowHidden(row, hidden)
            if hidden:
                for col in range(self.table.columnCount()):
                    self.table.item(row, col).setSelected(False)
        self._selection_changed()

    def _select_visible(self):
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                for col in range(self.table.columnCount()):
                    self.table.item(row, col).setSelected(True)

    def _selection(self):
        rows = sorted({item.row() for item in self.table.selectedItems() if not self.table.isRowHidden(item.row())})
        return tuple(self._sessions[row] for row in rows)

    def _selection_changed(self):
        sessions = self._selection()
        visible = sum(not self.table.isRowHidden(row) for row in range(self.table.rowCount()))
        self.count.setText(f"{visible} session(s) affichée(s) · {len(sessions)} sélectionnée(s)")
        available = bool(sessions) and not self._busy and not self.service.is_active
        self.recover_button.setEnabled(available)
        self.discard_button.setEnabled(available)

    def _set_busy(self, busy):
        self._busy = busy
        for widget in (self.table, self.search, self.refresh_button, self.all_button, self.close_button):
            widget.setEnabled(not busy)
        self._selection_changed()

    def _operate(self, abandon):
        sessions = self._selection()
        if not sessions or self._busy:
            return
        if self.service.is_active:
            self.status.setText("Arrêtez la capture avant cette opération.")
            return
        if abandon:
            text = (f"Mettre de côté {len(sessions)} session(s) sélectionnée(s) ?\n\n"
                    "Leurs pistes seront déplacées dans le dossier privé RecuperationIgnoree. "
                    "Elles sortiront de cette liste, sans suppression définitive. "
                    "Les transcripts et les autres enregistrements ne seront pas modifiés.")
            answer = QMessageBox.question(self, "Abandonner la sélection", text,
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._set_busy(True)
        self.status.setText(f"{'Mise de côté' if abandon else 'Récupération'} de {len(sessions)} session(s)…")
        operation = self.service.discard_sessions if abandon else self.service.recover_sessions

        def success(result):
            for path in result.recovered_paths:
                self.recovered.emit(path)
            verb = "mise(s) de côté" if abandon else "récupérée(s)"
            message = f"{len(result.completed)} session(s) {verb}."
            if result.errors:
                message += f" {len(result.errors)} avertissement(s) — les fichiers concernés restent conservés."
                message += "\n" + "\n".join(result.errors[:3])
            self._result_message = message
            self.status.setText(message)
            self.status.setToolTip("\n".join(result.errors))
            self.changed.emit()

        def finish():
            # Refresh after completed disk operations, including partial failure.
            # This removes successful rows immediately, not only after restart.
            self.runner.submit(self.service.recovery_sessions, self._render,
                               lambda message: self.status.setText(message), lambda: self._set_busy(False))
        self.runner.submit(lambda: operation(sessions), success,
                           lambda message: self.status.setText(message), finish)

    def _open_quarantine(self):
        folder = self.service.recovery_inbox.quarantine
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def reject(self):
        if not self._busy:
            super().reject()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
        else:
            super().closeEvent(event)
