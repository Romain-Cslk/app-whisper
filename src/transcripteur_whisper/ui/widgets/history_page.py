"""Teams-inspired weekly history for recordings and persistent transcripts."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...services.history_service import HistoryArtifact, HistoryEntry, HistoryService
from ..workers import TaskRunner

DAY_LABELS = ("Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim")
STATUS_LABELS = {
    "done": "Terminé",
    "partial": "Partiel",
    "error": "Échec",
    "cancelled": "Annulé",
    "recovered": "Récupéré",
}


class HistoryPage(QWidget):
    notice = Signal(str)

    def __init__(
        self,
        runner: TaskRunner,
        jobs: Any,
        paths: Any,
        parent: QWidget | None = None,
        *,
        service: HistoryService | None = None,
    ) -> None:
        super().__init__(parent)
        self.runner = runner
        self.paths = paths
        self.service = service or HistoryService(paths, jobs)
        self.current_week = self.service.week_start()
        self.entries: dict[str, HistoryEntry] = {}
        self._selected_id: str | None = None
        self._loading = False
        self._read_request = 0
        self._build_ui()
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(280)
        self.search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _text: self.search_timer.start())
        self._update_week_caption()

    def _build_ui(self) -> None:
        self.previous_button = QPushButton("‹")
        self.previous_button.setAccessibleName("Semaine précédente")
        self.today_button = QPushButton("Aujourd’hui")
        self.next_button = QPushButton("›")
        self.next_button.setAccessibleName("Semaine suivante")
        self.refresh_button = QPushButton("Actualiser")
        self.week_caption = QLabel()
        self.week_caption.setStyleSheet("font-size: 13pt; font-weight: 600;")
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("Rechercher dans les titres et transcriptions…")
        self.search.setAccessibleName("Rechercher dans l’historique")
        self.previous_button.clicked.connect(lambda: self._move_week(-7))
        self.today_button.clicked.connect(self._go_today)
        self.next_button.clicked.connect(lambda: self._move_week(7))
        self.refresh_button.clicked.connect(self.refresh)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.previous_button)
        toolbar.addWidget(self.today_button)
        toolbar.addWidget(self.next_button)
        toolbar.addWidget(self.week_caption)
        toolbar.addStretch()
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.refresh_button)

        self.table = QTableWidget(24, 7)
        self.table.setAccessibleName("Agenda hebdomadaire des enregistrements et transcriptions")
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.table.setShowGrid(True)
        self.table.verticalHeader().setDefaultSectionSize(62)
        self.table.verticalHeader().setMinimumWidth(58)
        self.table.setVerticalHeaderLabels([f"{hour:02d}:00" for hour in range(24)])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setMinimumSectionSize(125)
        self.table.setMinimumWidth(760)

        self.detail_title = QLabel("Sélectionnez un élément de l’agenda")
        self.detail_title.setWordWrap(True)
        self.detail_title.setStyleSheet("font-size: 14pt; font-weight: 600;")
        self.detail_meta = QLabel(
            "Cliquez sur une carte pour afficher la transcription ou l’enregistrement associé."
        )
        self.detail_meta.setWordWrap(True)
        self.detail_meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.artifacts = QListWidget()
        self.artifacts.setAccessibleName("Fichiers associés à l’élément sélectionné")
        self.artifacts.currentRowChanged.connect(self._artifact_selected)
        self.artifacts.itemDoubleClicked.connect(lambda _item: self.open_selected())
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Le contenu du transcript apparaîtra ici.")
        self.open_button = QPushButton("Ouvrir")
        self.copy_button = QPushButton("Copier le texte")
        self.save_button = QPushButton("Enregistrer sous…")
        self.folder_button = QPushButton("Ouvrir le dossier")
        self.open_button.clicked.connect(self.open_selected)
        self.copy_button.clicked.connect(self.copy_text)
        self.save_button.clicked.connect(self.save_selected)
        self.folder_button.clicked.connect(self.open_folder)
        detail_actions = QHBoxLayout()
        detail_actions.addWidget(self.open_button)
        detail_actions.addWidget(self.copy_button)
        detail_actions.addWidget(self.save_button)
        detail_actions.addWidget(self.folder_button)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 8, 8, 8)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_meta)
        detail_layout.addWidget(self.artifacts, 1)
        detail_layout.addWidget(self.preview, 3)
        detail_layout.addLayout(detail_actions)

        calendar = QWidget()
        calendar_layout = QVBoxLayout(calendar)
        calendar_layout.setContentsMargins(0, 0, 4, 0)
        helper = QLabel(
            "Vue semaine · 24 h. Les enregistrements natifs sont positionnés à leur heure réelle ; "
            "les fichiers importés utilisent l’heure de création de leur transcription."
        )
        helper.setWordWrap(True)
        calendar_layout.addWidget(helper)
        calendar_layout.addWidget(self.table, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(calendar)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([760, 360])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(toolbar)
        layout.addWidget(splitter, 1)
        self._set_detail_buttons(False)

    def activate(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.refresh_button.setEnabled(False)
        week = self.current_week
        query = self.search.text().strip()
        self.runner.submit(
            lambda: self.service.week(week, query),
            self._render,
            self._load_failed,
            self._load_finished,
        )

    def _load_failed(self, message: str) -> None:
        self.notice.emit(f"Impossible de charger l’historique : {message}")

    def _load_finished(self) -> None:
        self._loading = False
        self.refresh_button.setEnabled(True)

    def _render(self, entries: list[HistoryEntry]) -> None:
        previous = self._selected_id
        self.entries = {entry.identifier: entry for entry in entries}
        self.table.clearContents()
        self._update_headers()
        grouped: dict[tuple[int, int], list[HistoryEntry]] = {}
        for entry in entries:
            day = (entry.start.date() - self.current_week).days
            if day < 0:
                day = 0
            elif day > 6:
                day = 6
            grouped.setdefault((entry.start.hour, day), []).append(entry)
        for (hour, day), cell_entries in grouped.items():
            container = QWidget()
            cell_layout = QVBoxLayout(container)
            cell_layout.setContentsMargins(3, 3, 3, 3)
            cell_layout.setSpacing(3)
            for entry in cell_entries:
                button = QPushButton(self._event_label(entry))
                button.setToolTip(self._event_tooltip(entry))
                button.setStyleSheet(self._event_style(entry))
                button.setMinimumHeight(38)
                button.clicked.connect(lambda _checked=False, key=entry.identifier: self.select_entry(key))
                cell_layout.addWidget(button)
            cell_layout.addStretch()
            self.table.setCellWidget(hour, day, container)
        if previous in self.entries:
            self.select_entry(previous)
        elif entries:
            self.select_entry(entries[0].identifier)
        else:
            self._clear_detail("Aucun élément pour cette semaine et cette recherche.")
        self._scroll_to_useful_hour(entries)

    def _update_headers(self) -> None:
        today = datetime.now().astimezone().date()
        for index in range(7):
            current = self.current_week + timedelta(days=index)
            label = f"{DAY_LABELS[index]}\n{current.strftime('%d/%m')}"
            if current == today:
                label += " · Aujourd’hui"
            item = QTableWidgetItem(label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setHorizontalHeaderItem(index, item)

    def _scroll_to_useful_hour(self, entries: list[HistoryEntry]) -> None:
        current_week = self.service.week_start()
        if self.current_week == current_week:
            target = max(0, datetime.now().astimezone().hour - 1)
        elif entries:
            target = max(0, min(entry.start.hour for entry in entries) - 1)
        else:
            target = 8
        item = self.table.item(target, 0)
        if item is None:
            item = QTableWidgetItem("")
            self.table.setItem(target, 0, item)
        self.table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtTop)

    def select_entry(self, identifier: str) -> None:
        entry = self.entries.get(identifier)
        if entry is None:
            return
        self._selected_id = identifier
        self.detail_title.setText(entry.title)
        duration = self._format_duration(entry.duration_seconds)
        status = STATUS_LABELS.get(entry.status, entry.status)
        meta = [entry.start.strftime("%A %d/%m/%Y · %H:%M"), status]
        if duration:
            meta.append(duration)
        if entry.source_name:
            meta.append(entry.source_name)
        self.detail_meta.setText(" · ".join(meta))
        self.artifacts.blockSignals(True)
        self.artifacts.clear()
        for artifact in entry.artifacts:
            prefix = {"audio": "Audio", "transcription": "Transcript", "document": "Document"}.get(
                artifact.kind, artifact.kind
            )
            item = QListWidgetItem(f"{prefix} · {artifact.label}")
            item.setData(Qt.ItemDataRole.UserRole, artifact)
            item.setToolTip(str(artifact.path))
            self.artifacts.addItem(item)
        self.artifacts.blockSignals(False)
        preferred = next(
            (index for index, artifact in enumerate(entry.artifacts) if artifact.kind == "transcription"),
            0,
        )
        if entry.artifacts:
            self.artifacts.setCurrentRow(preferred)
        else:
            self.preview.setPlainText("Aucun fichier associé n’est disponible.")
            self._set_detail_buttons(False)

    @property
    def selected_artifact(self) -> HistoryArtifact | None:
        item = self.artifacts.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return value if isinstance(value, HistoryArtifact) else None

    def _artifact_selected(self, row: int) -> None:
        artifact = self.selected_artifact
        self._set_detail_buttons(artifact is not None)
        if artifact is None:
            self.preview.clear()
            return
        self._read_request += 1
        request = self._read_request
        if artifact.kind in {"transcription", "document"}:
            self.preview.setPlainText("Chargement…")
            self.runner.submit(
                lambda: self.service.read_text(artifact.path),
                lambda text: self._show_text(request, text),
                lambda message: self._show_text(request, f"Lecture impossible : {message}"),
            )
        else:
            self.preview.setPlainText(
                "Enregistrement audio conservé localement. Utilisez « Ouvrir » pour l’écouter "
                "avec l’application Windows associée."
            )

    def _show_text(self, request: int, text: str) -> None:
        if request == self._read_request:
            self.preview.setPlainText(text)
            self.preview.verticalScrollBar().setValue(0)

    def open_selected(self) -> None:
        artifact = self.selected_artifact
        if artifact and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(artifact.path))):
            self.notice.emit("Windows ne peut pas ouvrir ce fichier.")

    def open_folder(self) -> None:
        artifact = self.selected_artifact
        folder = artifact.path.parent if artifact else self.paths.results
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def copy_text(self) -> None:
        artifact = self.selected_artifact
        if artifact is None or artifact.kind not in {"transcription", "document"}:
            return
        text = self.preview.toPlainText()
        if text and text != "Chargement…":
            QApplication.clipboard().setText(text)
            self.notice.emit("Texte copié dans le presse-papiers.")

    def save_selected(self) -> None:
        artifact = self.selected_artifact
        if artifact is None:
            return
        file_filter = "Audio WAV (*.wav)" if artifact.kind == "audio" else "Texte (*.txt)"
        destination, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le fichier", artifact.label, file_filter
        )
        if destination:
            self.runner.submit(
                lambda: shutil.copyfile(artifact.path, Path(destination)),
                lambda _result: self.notice.emit(f"Fichier enregistré : {destination}"),
                self.notice.emit,
            )

    def _move_week(self, days: int) -> None:
        self.current_week += timedelta(days=days)
        self._update_week_caption()
        self.refresh()

    def _go_today(self) -> None:
        self.current_week = self.service.week_start()
        self._update_week_caption()
        self.refresh()

    def _update_week_caption(self) -> None:
        end = self.current_week + timedelta(days=6)
        self.week_caption.setText(
            f"Semaine du {self.current_week.strftime('%d/%m/%Y')} au {end.strftime('%d/%m/%Y')}"
        )

    def _clear_detail(self, message: str) -> None:
        self._selected_id = None
        self.detail_title.setText("Historique")
        self.detail_meta.setText(message)
        self.artifacts.clear()
        self.preview.clear()
        self._set_detail_buttons(False)

    def _set_detail_buttons(self, available: bool) -> None:
        artifact = self.selected_artifact if available else None
        self.open_button.setEnabled(available)
        self.save_button.setEnabled(available)
        self.folder_button.setEnabled(available)
        self.copy_button.setEnabled(bool(artifact and artifact.kind in {"transcription", "document"}))

    @staticmethod
    def _event_label(entry: HistoryEntry) -> str:
        marker = "🎙" if entry.has_audio else "📝"
        transcript = " + transcript" if entry.has_audio and entry.has_transcript else ""
        return f"{marker} {entry.start.strftime('%H:%M')} · {entry.title}{transcript}"

    @staticmethod
    def _event_tooltip(entry: HistoryEntry) -> str:
        duration = HistoryPage._format_duration(entry.duration_seconds)
        details = [entry.title, f"Début : {entry.start.strftime('%d/%m/%Y %H:%M')}"]
        if duration:
            details.append(f"Durée : {duration}")
        details.append(f"Source : {entry.source_name}")
        return "\n".join(details)

    @staticmethod
    def _event_style(entry: HistoryEntry) -> str:
        if entry.status in {"error", "cancelled"}:
            background = "#8f4b55"
        elif entry.status == "partial":
            background = "#8a6b2f"
        elif entry.has_audio and not entry.has_transcript:
            background = "#39715f"
        else:
            background = "#5369d6"
        return (
            "QPushButton {"
            f"background: {background}; color: white; border: 0; border-radius: 5px; "
            "padding: 5px 7px; text-align: left; font-weight: 600;"
            "} QPushButton:hover { border: 1px solid rgba(255,255,255,150); }"
        )

    @staticmethod
    def _format_duration(seconds: float | None) -> str:
        if seconds is None or seconds <= 0:
            return ""
        total = int(round(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, _seconds = divmod(remainder, 60)
        if hours:
            return f"{hours} h {minutes:02d} min"
        return f"{max(1, minutes)} min"


def install_history_tab(window: Any, paths: Any) -> HistoryPage:
    """Add the desktop-native history without coupling MainWindow to the service."""

    existing = getattr(window, "history_page", None)
    if isinstance(existing, HistoryPage):
        return existing
    page = HistoryPage(window.runner, window.jobs, paths, parent=window)
    page.notice.connect(window.show_notice)
    index = min(3, window.tabs.count())
    window.tabs.insertTab(index, page, "Historique")

    def tab_changed(current: int) -> None:
        if window.tabs.widget(current) is page:
            page.activate()

    window.tabs.currentChanged.connect(tab_changed)
    window.history_page = page
    return page
