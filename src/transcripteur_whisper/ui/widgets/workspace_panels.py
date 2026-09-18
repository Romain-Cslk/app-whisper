"""Compact native layouts: recording, history and storage keep independent space."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from .library_controls import LibraryRecordingPanel
from .library_page import LibraryPage
from .recovery_dialog import RecoveryDialog
from .storage_page import StoragePage


def readable_combo(combo):
    combo.setMinimumWidth(0)
    combo.setMinimumContentsLength(12)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


def _empty_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.layout():
            _empty_layout(item.layout())
        if item.widget():
            item.widget().hide()


class WorkspaceRecordingPanel(LibraryRecordingPanel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setTitle("")
        self.setObjectName("recordingWorkspace")
        self._scan_pending = False
        self._recovery_dialog = None
        self._source_columns = None
        root = self.layout()
        _empty_layout(root)
        root.setContentsMargins(12, 8, 12, 12)
        root.setSpacing(12)
        root.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.heading = QLabel("Nouvel enregistrement")
        self.heading.setStyleSheet("font-size: 14pt; font-weight: 600")
        root.addWidget(self.heading)
        root.addWidget(QLabel("Nom de l'enregistrement"))
        root.addWidget(self.name)
        self.sources_grid = QGridLayout()
        self.source_cards = []
        for checkbox, combo, meter, label in (
            (self.microphone_enabled, self.microphone, self.mic_meter, "Votre microphone"),
            (self.speaker_enabled, self.speaker, self.system_meter, "Son du PC / interlocuteurs"),
        ):
            checkbox.setText(label)
            readable_combo(combo)
            meter.setMinimumHeight(16)
            meter.setMaximumHeight(16)
            meter.setTextVisible(False)
            card = QFrame()
            card.setProperty("workspacePage", True)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(6, 6, 6, 6)
            card_layout.setSpacing(9)
            card_layout.addWidget(checkbox)
            card_layout.addWidget(combo)
            card_layout.addWidget(QLabel("Niveau sonore"))
            card_layout.addWidget(meter)
            self.source_cards.append(card)
        root.addLayout(self.sources_grid)
        self.record_button.setStyleSheet("")
        self.record_button.setMinimumWidth(0)
        self.record_button.setMinimumHeight(44)
        self.timer_label.setStyleSheet("font-size: 17pt; font-weight: 600")
        actions = QHBoxLayout()
        actions.addWidget(self.record_button)
        actions.addWidget(self.timer_label)
        actions.addStretch(1)
        root.addLayout(actions)
        root.addWidget(self.status)
        self.recovery_count = QLabel("Recherche des sessions récupérables…")
        self.recovery_count.setWordWrap(True)
        self.manage_recovery = QPushButton("Gérer la récupération…")
        self.manage_recovery.clicked.connect(self.open_recovery)
        self.recovery_row = QHBoxLayout()
        self.recovery_row.addWidget(self.recovery_count, 1)
        self.recovery_row.addWidget(self.manage_recovery)
        root.addLayout(self.recovery_row)
        for widget in (self.name, self.record_button, self.timer_label, self.status,
                       self.microphone_enabled, self.microphone, self.mic_meter,
                       self.speaker_enabled, self.speaker, self.system_meter):
            widget.show()
        # Legacy file-by-file controls stay hidden and never drive the inbox.
        self.recovery_choices.hide()
        self.recover_button.hide()
        self.discard_button.hide()
        self.state_changed.connect(self._sync_capture_controls)
        self._reflow_sources()
        self._sync_capture_controls()

    def _reflow_sources(self):
        columns = 1 if self.width() < 690 else 2
        if columns == self._source_columns:
            return
        self._source_columns = columns
        for card in self.source_cards:
            self.sources_grid.removeWidget(card)
        for index, card in enumerate(self.source_cards):
            self.sources_grid.addWidget(card, index // columns, index % columns)
        self.sources_grid.setColumnStretch(0, 1)
        self.sources_grid.setColumnStretch(1, 1 if columns == 2 else 0)

    def _sync_capture_controls(self):
        busy = self.busy
        for widget in (self.microphone, self.speaker, self.microphone_enabled, self.speaker_enabled):
            widget.setEnabled(not busy and not self._closing)
        if hasattr(self, "manage_recovery"):
            self.manage_recovery.setEnabled(not busy and not self._closing)
            self.manage_recovery.setToolTip("Arrêtez l'enregistrement pour gérer la récupération." if busy else "")

    def scan_recovery(self):
        if self._scan_pending or self._closing:
            return
        if not hasattr(self.service, "recovery_sessions"):
            self.recovery_count.setText("Récupération indisponible pour ce service.")
            self.manage_recovery.setEnabled(False)
            return
        self._scan_pending = True
        self.runner.submit(self.service.recovery_sessions, self._show_sessions, self.notice.emit,
                           lambda: setattr(self, "_scan_pending", False))

    def _show_sessions(self, sessions):
        self.recovery_count.setText(f"{len(sessions)} session(s) à récupérer." if sessions else "Aucune session interrompue à récupérer.")
        self._sync_capture_controls()

    def _show_recovery(self, _paths):
        # Old asynchronous scan results must not resurrect the legacy controls.
        self.recovery_choices.hide()
        self.recover_button.hide()
        self.discard_button.hide()

    def open_recovery(self):
        if self.busy or self._closing or not hasattr(self.service, "recovery_sessions"):
            self.notice.emit("Arrêtez l'enregistrement avant de gérer la récupération.")
            return
        if self._recovery_dialog is not None and self._recovery_dialog.isVisible():
            self._recovery_dialog.raise_()
            return
        dialog = RecoveryDialog(self.runner, self.service, self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.recovered.connect(self.recording_saved.emit)
        dialog.changed.connect(self.scan_recovery)
        dialog.finished.connect(lambda _value: self.scan_recovery())
        self._recovery_dialog = dialog
        dialog.open()

    def _stopped(self, result):
        super()._stopped(result)
        self.scan_recovery()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "sources_grid"):
            self._reflow_sources()


class WorkspaceHistoryPage(LibraryPage):
    def _build_ui(self):
        self.setProperty("workspacePage", True)
        self.previous_button = QPushButton("‹")
        self.next_button = QPushButton("›")
        self.today_button = QPushButton("Aujourd'hui")
        self.refresh_button = QPushButton("Actualiser")
        self.previous_button.setAccessibleName("Semaine précédente")
        self.next_button.setAccessibleName("Semaine suivante")
        self.week_caption = QLabel()
        self.week_caption.setWordWrap(True)
        self.week_caption.setMinimumWidth(0)
        self.week_caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.previous_button.clicked.connect(lambda: self._move_week(-7))
        self.next_button.clicked.connect(lambda: self._move_week(7))
        self.today_button.clicked.connect(self._go_today)
        self.refresh_button.clicked.connect(self.refresh)
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setMaxLength(200)
        self.search.setPlaceholderText("Rechercher dans les titres et les textes…")
        self.search.setAccessibleName("Rechercher dans l'historique")
        tools = QHBoxLayout()
        for widget in (self.previous_button, self.today_button, self.next_button):
            tools.addWidget(widget)
        tools.addWidget(self.week_caption, 1)
        tools.addWidget(self.refresh_button)
        filters = QHBoxLayout()
        self.audios = QCheckBox("Audios")
        self.transcripts = QCheckBox("Transcripts")
        self.summaries = QCheckBox("Résumés IA")
        for box in (self.audios, self.transcripts, self.summaries):
            box.setChecked(True)
            box.toggled.connect(lambda _value: self.refresh())
            filters.addWidget(box)
        filters.addStretch()
        self.compact_views = QWidget()
        view_row = QHBoxLayout(self.compact_views)
        view_row.setContentsMargins(0, 0, 0, 0)
        self.agenda_button = QPushButton("Agenda")
        self.reading_button = QPushButton("Lecture")
        self._view_group = QButtonGroup(self)
        for button in (self.agenda_button, self.reading_button):
            button.setCheckable(True)
            self._view_group.addButton(button)
            view_row.addWidget(button)
        self.agenda_button.setChecked(True)
        self.agenda_button.clicked.connect(lambda: self._choose_compact_view(False))
        self.reading_button.clicked.connect(lambda: self._choose_compact_view(True))
        filters.addWidget(self.compact_views)
        self._reading_compact = False
        self._rendering_entries = False
        self.table = QTableWidget(24, 7)
        self.table.setMinimumWidth(0)
        self.table.setMinimumHeight(130)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setVerticalHeaderLabels([f"{hour:02d}:00" for hour in range(24)])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setMinimumSectionSize(42)
        self.table.verticalHeader().setMinimumWidth(42)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.detail_title = QLabel("Sélectionnez un élément")
        self.detail_title.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_title.setWordWrap(True)
        self.detail_meta = QLabel("Audio, transcription et résumé restent regroupés.")
        self.detail_meta.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_meta.setWordWrap(True)
        self.artifacts = QListWidget()
        self.artifacts.setMinimumHeight(36)
        self.artifacts.setMaximumHeight(74)
        self.artifacts.currentRowChanged.connect(self._artifact_selected)
        self.artifacts.itemDoubleClicked.connect(lambda _item: self.open_selected())
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(72)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.open_button = QPushButton("Ouvrir")
        self.copy_button = QPushButton("Copier")
        self.save_button = QPushButton("Enregistrer sous…")
        self.folder_button = QPushButton("Dossier")
        self.delete_button = QPushButton("Supprimer…")
        for button, callback in ((self.open_button, self.open_selected), (self.copy_button, self.copy_text),
                                 (self.save_button, self.save_selected), (self.folder_button, self.open_folder),
                                 (self.delete_button, self.delete_selected)):
            button.clicked.connect(callback)
        actions = QGridLayout()
        for i, button in enumerate((self.open_button, self.copy_button, self.save_button, self.folder_button, self.delete_button)):
            actions.addWidget(button, i // 3, i % 3)
        detail = QWidget()
        self.detail = detail
        detail.setMinimumWidth(0)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(8, 0, 0, 0)
        detail_layout.setSpacing(5)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_meta)
        detail_layout.addWidget(self.artifacts)
        detail_layout.addWidget(self.preview, 1)
        detail_layout.addLayout(actions)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(detail)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([650, 420])
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)
        root.addLayout(tools)
        root.addWidget(self.search)
        root.addLayout(filters)
        root.addWidget(self.splitter, 1)
        self._compact_history = None
        self._set_detail_buttons(False)

    def _resize_history_rows(self, entries=None):
        entries = list(self.entries.values() if entries is None else entries)
        compact = self.width() < 900
        height = 27 if compact else 34
        for row in range(24):
            counts = [sum(e.start.hour == row and max(0, min(6, (e.start.date() - self.current_week).days)) == day
                          for e in entries) for day in range(7)]
            self.table.setRowHeight(row, max(40 if compact else 52, max(counts, default=0) * (height + 3) + 6))
        for button in self.table.findChildren(QPushButton):
            button.setMinimumSize(0, height)
            button.setMaximumHeight(height)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def resizeEvent(self, event):
        # Do not invoke the former layout animation or resize entire page text.
        QWidget.resizeEvent(self, event)
        if not hasattr(self, "splitter"):
            return
        self._apply_history_layout()
        self._resize_history_rows()

    def _apply_history_layout(self):
        compact = self.width() < 920
        self.compact_views.setVisible(compact)
        self.table.setVisible(not compact or not self._reading_compact)
        self.detail.setVisible(not compact or self._reading_compact)
        if not compact:
            self.splitter.setSizes([650, 420])

    def _choose_compact_view(self, reading):
        self._reading_compact = bool(reading)
        self.agenda_button.setChecked(not reading)
        self.reading_button.setChecked(reading)
        self._apply_history_layout()

    def _render(self, entries):
        self._rendering_entries = True
        try:
            super()._render(entries)
        finally:
            self._rendering_entries = False
        self._apply_history_layout()

    def select_entry(self, identifier):
        super().select_entry(identifier)
        if not self._rendering_entries and self.width() < 920:
            self._choose_compact_view(True)

    @staticmethod
    def _event_style(entry):
        background = "#146473" if entry.has_transcript else "#24596b"
        return (f"QPushButton {{background:{background};color:white;border:1px solid #467d89;"
                "border-radius:5px;padding:2px 5px;text-align:left;font-size:9pt;}"
                "QPushButton:hover {background:#1d7b88;}")


class WorkspaceStoragePage(StoragePage):
    """Reuse storage validation, but remove the duplicate provider/prompt editor."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setProperty("workspacePage", True)
        for group in self.findChildren(QGroupBox):
            if group.title().startswith("Résumé IA"):
                group.hide()
        for form in self.findChildren(QFormLayout):
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setVerticalSpacing(9)
        for combo in self.findChildren(QComboBox):
            readable_combo(combo)
        self._saving = False

    def save(self):
        if self._saving:
            return
        if self.busy():
            self.notice.emit("Attendez la fin de la capture et du traitement pour changer les dossiers.")
            return
        history = () if self.automatic.isChecked() else tuple(Path(self.history.item(i).text()) for i in range(self.history.count()))
        if not self.automatic.isChecked() and not history:
            self.notice.emit("Ajoutez un dossier d'historique ou activez la recherche automatique.")
            return
        candidate = replace(self.preferences, audio_work_dir=Path(self.audio_work.text()),
                            transcript_work_dir=Path(self.transcripts_work.text()),
                            transcript_dir=Path(self.transcripts_final.text()), retention=self.policy.currentData(),
                            history_dirs=history, keep_final_audio=False)
        self._saving = True
        self.save_button.setEnabled(False)
        self.message.setText("Enregistrement des dossiers…")
        def success(preferences):
            self.preferences = preferences
            self.message.setText("Dossiers enregistrés. Redémarrage nécessaire pour leurs nouveaux emplacements.")
            self.saved.emit(preferences)
        def finish():
            self._saving = False
            self.save_button.setEnabled(True)
        self.runner.submit(lambda: candidate.save(self.paths), success, self.notice.emit, finish)
