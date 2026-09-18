"""Compact desktop workspace over existing transcription/recording services.

The header and job strip have bounded height; the current page takes all remaining
space. No widget is made visible by walking an entire hidden tab's descendants.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..services.ai_summary import SummaryPreferences
from ..services.library_jobs import PHASES, LibraryJobService
from ..services.library_storage import AudioCatalog, StoragePreferences
from ..services.session_recording import SessionRecordingService
from ..services.summary_profiles import WritingStore
from .main_window import LANGS, OUTPUTS
from .main_window import MainWindow as BaseWindow
from .widgets.file_list import FileList
from .widgets.library_controls import LibraryResultsPanel
from .widgets.workspace_panels import (
    WorkspaceHistoryPage,
    WorkspaceRecordingPanel,
    WorkspaceStoragePage,
    readable_combo,
)
from .widgets.workspace_prompts import WritingSettingsPage
from .workspace_theme import apply_workspace_theme

TERMINAL = {"done", "partial", "error", "cancelled"}


def page_widget():
    page = QWidget()
    page.setProperty("workspacePage", True)
    page.setAutoFillBackground(True)
    return page


def scrolling_page(content):
    scroll = QScrollArea()
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setWidget(content)
    scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    return scroll


class MainWindow(BaseWindow):
    def __init__(self, paths, **kwargs):
        from ..services.audio_device_service import AudioDeviceService
        self.storage_paths = paths
        self.storage_preferences = StoragePreferences.load(paths)
        self.summary_preferences = SummaryPreferences.load(paths)
        self.writing_store = WritingStore(paths)
        self.writing_preferences = self.writing_store.load()
        self.catalog = AudioCatalog(paths, self.storage_preferences)
        job_paths = replace(paths, results=self.storage_preferences.transcript_work_dir)
        audio_paths = replace(paths, results=self.storage_preferences.audio_work_dir)
        job_paths.results.mkdir(parents=True, exist_ok=True)
        audio_paths.results.mkdir(parents=True, exist_ok=True)
        devices = kwargs.pop("devices", None) or AudioDeviceService()
        jobs = kwargs.pop("jobs", None) or LibraryJobService(job_paths, self.catalog, load_history=False)
        recording = kwargs.pop("recording", None) or SessionRecordingService(audio_paths, devices, self.catalog)
        self._maintenance_pending = False
        self._library_initial_load = False
        self._queued_recordings = []
        self._previous_job_completed = False
        self._writing_snapshot = None
        self._layout_ready = False
        super().__init__(job_paths, jobs=jobs, recording=recording, devices=devices, **kwargs)
        self.setMinimumSize(600, 460)
        self.resize(1120, 760)
        self._layout_ready = True
        self.maintenance_timer = QTimer(self)
        self.maintenance_timer.setInterval(60000)
        self.maintenance_timer.timeout.connect(self._cleanup_audio)
        self.maintenance_timer.start()
        self.badge_timer = QTimer(self)
        self.badge_timer.setInterval(1000)
        self.badge_timer.timeout.connect(self._update_capture_badge)
        self.badge_timer.start()
        self._compact_tables()

    def _build_ui(self):
        self.logo = QLabel()
        self.logo.setFixedSize(30, 28)
        self.title = QLabel("Transcripteur Whisper")
        self.title.setObjectName("workspaceTitle")
        self.capture_badge = QLabel("")
        self.capture_badge.setStyleSheet("color: #e45b72; font-weight: 600")
        self.theme_button = QPushButton("Mode clair")
        self.theme_button.clicked.connect(self.toggle_theme)
        self.header = QWidget()
        self.header.setObjectName("workspaceHeader")
        self.header.setFixedHeight(42)
        header = QHBoxLayout(self.header)
        header.setContentsMargins(3, 0, 0, 0)
        header.addWidget(self.logo)
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.capture_badge)
        header.addWidget(self.theme_button)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)

        self.mode = QComboBox()
        self.mode.addItem("Local (CPU int8)", "local")
        self.mode.addItem("OpenAI API", "api")
        self.model = QComboBox()
        self.language = QComboBox()
        self.language.addItem("Détection automatique", None)
        for label, code in LANGS.items():
            self.language.addItem(label, code)
        self.output_type = QComboBox()
        for value, label in OUTPUTS.items():
            self.output_type.addItem(label, value)
        self.output_name = QLineEdit()
        self.output_name.setMaxLength(80)
        self.output_name.setPlaceholderText("Nom de cette transcription")
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Clé OpenAI de transcription — non enregistrée")
        self.api_label = QLabel("Clé de transcription")
        self.api_hint = QLabel("Mode API : l'audio sera envoyé à OpenAI ; des frais peuvent s'appliquer.")
        self.api_hint.setWordWrap(True)
        self.api_hint.setProperty("secondary", True)
        for combo in (self.mode, self.model, self.language, self.output_type):
            readable_combo(combo)
        self.options = page_widget()
        form = QFormLayout(self.options)
        form.setContentsMargins(0, 0, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setVerticalSpacing(7)
        form.addRow("Mode", self.mode)
        form.addRow("Modèle", self.model)
        form.addRow("Langue", self.language)
        form.addRow("Sortie", self.output_type)
        form.addRow(self.api_label, self.api_key)
        form.addRow(self.api_hint)
        self.options_toggle = QToolButton()
        self.options_toggle.setText("Options de transcription")
        self.options_toggle.setCheckable(True)
        self.options_toggle.setChecked(False)
        self.options_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.options_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.options_toggle.toggled.connect(self._toggle_options)
        self.options.hide()
        self.option_summary = QLabel("Transcription locale")
        self.option_summary.setProperty("secondary", True)
        option_row = QHBoxLayout()
        option_row.addWidget(self.options_toggle)
        option_row.addWidget(self.option_summary, 1)
        self.summary_requested = QCheckBox("Rédaction IA")
        self.summary_requested.setToolTip("Envoie la transcription et le contexte au fournisseur configuré, uniquement si cette case est cochée.")
        self.profile_combo = QComboBox()
        readable_combo(self.profile_combo)
        self.profile_combo.setAccessibleName("Modèle de rédaction IA")
        self.profile_combo.setMaximumWidth(280)
        self.edit_prompts = QPushButton("Modèles et contexte…")
        self.edit_prompts.clicked.connect(self._show_writing_settings)
        ai_row = QHBoxLayout()
        ai_row.addWidget(self.summary_requested)
        ai_row.addWidget(self.profile_combo, 1)
        ai_row.addWidget(self.edit_prompts)
        ai_row.addStretch(1)
        self.summary_requested.toggled.connect(lambda checked: self.profile_combo.setEnabled(checked and not self._active))
        self.profile_combo.setEnabled(False)
        self.files = FileList(self.runner, self.media)
        self.files.notice.connect(self.show_notice)
        self.files.changed.connect(self._update_start_enabled)
        self.files.table.setMinimumHeight(130)
        self.files.table.setWordWrap(False)
        self.files.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.files.table.horizontalHeader().setMinimumSectionSize(48)
        self.files.layout().setStretchFactor(self.files.table, 1)
        self.files.remove_button.setText("Retirer")
        self.start_button = QPushButton("Démarrer la transcription")
        self.start_button.setObjectName("startButton")
        self.start_button.clicked.connect(self.start_transcription)
        self.last_result = QLabel("")
        self.last_result.setWordWrap(True)
        actions = QHBoxLayout()
        actions.addWidget(self.start_button)
        actions.addWidget(self.last_result, 1)
        transcribe = page_widget()
        layout = QVBoxLayout(transcribe)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(9)
        layout.addWidget(self.output_name)
        layout.addLayout(option_row)
        layout.addWidget(self.options)
        layout.addLayout(ai_row)
        layout.addWidget(self.files, 1)
        layout.addLayout(actions)
        self.transcribe_page = scrolling_page(transcribe)
        self.tabs.addTab(self.transcribe_page, "Transcrire")

        self.recording_panel = WorkspaceRecordingPanel(self.runner, self.recording, self.settings)
        self.recording_panel.recording_saved.connect(self._recording_saved)
        self.recording_panel.notice.connect(self.show_notice)
        self.recording_panel.state_changed.connect(self._update_start_enabled)
        self.recording_panel.shutdown_failed.connect(self._shutdown_error)
        self.output_name.textChanged.connect(self._name_from_transcript)
        self.recording_panel.name.textChanged.connect(self._name_from_recording)
        record_content = page_widget()
        record_layout = QVBoxLayout(record_content)
        record_layout.setContentsMargins(10, 8, 10, 8)
        record_layout.addWidget(self.recording_panel)
        record_layout.addStretch(1)
        self.record_page = scrolling_page(record_content)
        self.tabs.addTab(self.record_page, "Enregistrer")

        self.results = LibraryResultsPanel(self.runner, self.jobs, self.storage_preferences.transcript_dir)
        self.results.notice.connect(self.show_notice)
        self.results.files_changed.connect(self._refresh_library)
        self.results.txt_button.setText("Exporter TXT")
        self.results.zip_button.setText("Exporter ZIP")
        self.results.folder_button.setText("Dossier")
        self.results.copy_button.setText("Copier")
        self.results.save_button.setText("Enregistrer sous…")
        for label in self.results.findChildren(QLabel):
            label.setWordWrap(True)
        self.results.layout().setStretchFactor(self.results.files, 1)
        self.history = QComboBox()
        readable_combo(self.history)
        self.history.setAccessibleName("Historique des traitements")
        self.history.currentIndexChanged.connect(self._history_selected)
        results_page = page_widget()
        results_layout = QVBoxLayout(results_page)
        results_layout.setContentsMargins(10, 8, 10, 8)
        results_layout.addWidget(self.history)
        results_layout.addWidget(self.results, 1)
        self.tabs.addTab(results_page, "Résultats")

        self.history_page = WorkspaceHistoryPage(self.runner, self.jobs, self.paths, self.catalog, parent=self)
        self.history_page.notice.connect(self.show_notice)
        self.history_page.files_changed.connect(self._refresh_library)
        self.tabs.addTab(self.history_page, "Historique")

        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1000)
        self.logs.setMinimumHeight(100)
        self.logs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.logs.setPlaceholderText("Le journal du traitement s'affichera ici.")
        self.live_status = QLabel("Aucun traitement en cours")
        self.live_status.setWordWrap(True)
        self.follow_logs = QCheckBox("Suivre les nouvelles lignes")
        self.follow_logs.setChecked(True)
        self.journal_page = page_widget()
        journal = QVBoxLayout(self.journal_page)
        journal.setContentsMargins(10, 8, 10, 8)
        journal.addWidget(self.live_status)
        journal.addWidget(self.logs, 1)
        journal.addWidget(self.follow_logs)
        self.tabs.addTab(self.journal_page, "Journal")

        self.storage_page = WorkspaceStoragePage(self.runner, self.storage_paths, self.storage_preferences,
                                                busy=lambda: self._active or self.recording_panel.busy or self._closing or self._initializing)
        self.storage_page.notice.connect(self.show_notice)
        self.storage_page.saved.connect(self._storage_saved)
        self.writing_page = WritingSettingsPage(self.runner, self.storage_paths)
        self.writing_page.notice.connect(self.show_notice)
        self.writing_page.saved.connect(self._writing_saved)
        self.settings_tabs = QTabWidget()
        self.settings_tabs.setDocumentMode(True)
        self.settings_tabs.addTab(self.storage_page, "Stockage")
        self.settings_tabs.addTab(self.writing_page, "Rédaction IA et contexte")
        self.tabs.addTab(self.settings_tabs, "Paramètres")
        self.tabs.currentChanged.connect(self._tab_changed)
        self._fill_profiles()
        self.profile_combo.currentIndexChanged.connect(self._profile_selected)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFixedHeight(8)
        self.progress.setTextVisible(False)
        self.progress.setMaximumWidth(180)
        self.state = QLabel("Prêt")
        self.state.setWordWrap(False)
        self.state.setMinimumWidth(0)
        self.state.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.cancel_button = QPushButton("Annuler le traitement")
        self.cancel_button.clicked.connect(self.cancel_transcription)
        self.task_strip = QWidget()
        strip = QHBoxLayout(self.task_strip)
        strip.setContentsMargins(2, 0, 2, 0)
        strip.addWidget(self.state, 1)
        strip.addWidget(self.progress)
        strip.addWidget(self.cancel_button)
        self.task_strip.setFixedHeight(34)
        self.task_strip.hide()
        self.notice = QLabel("")
        self.notice.setMinimumWidth(0)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.notice.setFixedHeight(24)
        self.notice.hide()
        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.setInterval(9000)
        self.notice_timer.timeout.connect(self.notice.hide)
        central = QWidget()
        central.setObjectName("workspaceRoot")
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 4, 10, 8)
        root.setSpacing(5)
        root.addWidget(self.header)
        root.addWidget(self.task_strip)
        root.addWidget(self.tabs, 1)
        root.addWidget(self.notice)
        self.setCentralWidget(central)
        self.statusBar().hide()
        self.menuBar().hide()
        self._update_start_enabled()

    def _apply_theme(self):
        dark = self.settings.get("theme", "dark") == "dark"
        apply_workspace_theme(dark)
        self.theme_button.setText("Mode clair" if dark else "Mode sombre")
        pixmap = QPixmap(str(self.paths.asset_path("logo_white.png" if dark else "logo.png")))
        self.logo.setPixmap(pixmap.scaled(28, 26, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def show_notice(self, message):
        key = self.api_key.text() if hasattr(self, "api_key") else ""
        message = str(message).replace(key, "[clé masquée]") if key else str(message)
        self.notice.setText(message.replace("\n", " · "))
        self.notice.setToolTip(message)
        self.notice.setVisible(bool(message))
        self.notice_timer.start()

    def _toggle_options(self, expanded):
        self.options.setVisible(expanded)
        self.options_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def _mode_changed(self):
        super()._mode_changed()
        if hasattr(self, "option_summary"):
            self.option_summary.setText(self.mode.currentText() + " · " + self.model.currentText())

    def _name_from_transcript(self, text):
        if not self.recording_panel.busy and not self._active:
            self.recording_panel.name.setText(text)

    def _name_from_recording(self, text):
        if not self._active:
            self.output_name.setText(text)

    def _initialized(self):
        if not self._library_initial_load:
            self._library_initial_load = True
            self.runner.submit(getattr(self.jobs, "load_history", self.jobs.history), self._load_history,
                               self.show_notice, lambda: super(MainWindow, self)._initialized())
            return
        super()._initialized()

    def _tab_changed(self, index):
        # Visibility is owned only by QTabWidget. In particular never call show()
        # on descendants of inactive pages, or attach an opacity effect to them.
        if self.tabs.widget(index) is self.history_page and not self._initializing:
            self.history_page.activate()

    def _fill_profiles(self):
        self.profile_combo.blockSignals(True)
        selected = self.profile_combo.currentData() or self.writing_preferences.selected_id
        self.profile_combo.clear()
        for profile in self.writing_preferences.profiles:
            self.profile_combo.addItem(profile.name, profile.id)
        self.profile_combo.setCurrentIndex(max(0, self.profile_combo.findData(selected)))
        self.profile_combo.blockSignals(False)

    def _profile_selected(self):
        identifier = self.profile_combo.currentData()
        if not identifier or self._active:
            return
        # Keep selection in memory until submission; do not invalidate the editor
        # revision when someone is working on an unsaved prompt in another tab.
        self.writing_preferences = replace(self.writing_preferences, selected_id=identifier)

    def _show_writing_settings(self):
        self.tabs.setCurrentWidget(self.settings_tabs)
        self.settings_tabs.setCurrentWidget(self.writing_page)

    def _writing_saved(self, preferences):
        self.writing_preferences = preferences
        self.summary_preferences = SummaryPreferences.load(self.storage_paths)
        self._fill_profiles()
        self.show_notice("Contexte et modèles disponibles pour la prochaine rédaction IA.")

    def start_transcription(self):
        if self._active or self._closing or self._initializing or self.recording_panel.busy or not self.files.paths:
            return
        if self.summary_requested.isChecked() and self.writing_page.dirty:
            self._show_writing_settings()
            self.show_notice("Enregistrez les modifications de contexte / prompt avant de lancer la rédaction IA.")
            return
        self._writing_snapshot = None
        if self.summary_requested.isChecked():
            try:
                preferences = self.writing_store.load()
                selected = self.profile_combo.currentData()
                preferences.instructions(selected)
                provider = SummaryPreferences.load(self.storage_paths).provider
                self._writing_snapshot = (preferences, selected, provider)
            except Exception as exc:
                self.show_notice(str(exc))
                return
        super().start_transcription()

    def _submit_job(self, options):
        if self._cancel_requested or self._closing:
            self._set_active(False)
            return
        if self._writing_snapshot is not None:
            preferences, selected, provider = self._writing_snapshot
            options = replace(options, generate_summary=True, summary_provider=provider,
                              summary_prompt=preferences.instructions(selected))
        files = list(self.files.paths)
        key = self.api_key.text().strip() if options.mode == "api" else ""
        self.runner.submit(lambda: self.jobs.submit(files, options, api_key=key), self._job_submitted, self._launch_failed)

    def _set_active(self, active):
        self._active = active
        self.options.setEnabled(not active)
        self.output_name.setEnabled(not active)
        self.files.setEnabled(not active)
        self.recording_panel.setEnabled(not self._closing)
        self.summary_requested.setEnabled(not active)
        self.profile_combo.setEnabled(not active and self.summary_requested.isChecked())
        self.task_strip.setVisible(active)
        self.cancel_button.setEnabled(active and not self._cancel_requested)
        self._update_start_enabled()

    def _job_submitted(self, identifier):
        super()._job_submitted(identifier)
        self.last_result.setText("Traitement lancé. Vous pouvez enregistrer une autre réunion.")
        # Keep the user's current tab; the slim task strip is visible everywhere.

    def _render_job(self, snapshot):
        selected_page = self.tabs.currentWidget()
        bar = self.logs.verticalScrollBar()
        scroll_position = bar.value()
        rendered = snapshot
        if snapshot.get("status") in TERMINAL and (snapshot.get("storage_state") == "pending" or snapshot.get("summary_state") == "running"):
            rendered = dict(snapshot, status="running")
        super()._render_job(rendered)
        self.tabs.setCurrentWidget(selected_page)
        if not self.follow_logs.isChecked():
            bar.setValue(scroll_position)
        details = []
        for metadata in snapshot.get("files", []):
            if metadata.get("status") == "running":
                phase = PHASES.get(metadata.get("stage"), "Traitement")
                percent = round(float(metadata.get("progress") or 0) * 100)
                segment = metadata.get("segment_index", 0)
                count = metadata.get("segment_count", 0)
                suffix = f" · segment {segment}/{count}" if count else ""
                details.append(f"{metadata.get('name', '')} · {phase} · {percent} %{suffix}")
        if snapshot.get("summary_state") == "running":
            self.state.setText("Rédaction IA en cours…")
        elif rendered is not snapshot:
            self.state.setText("Publication du résultat…")
        self.state.setToolTip(self.state.text())
        self.live_status.setText(" | ".join(details[:2]) or self.state.text())
        if snapshot.get("status") in TERMINAL and rendered is snapshot:
            self._previous_job_completed = True
            self.last_result.setText(self.state.text() + " — consultez Résultats.")
            if self._queued_recordings:
                queued = list(dict.fromkeys(self._queued_recordings))
                self._queued_recordings.clear()
                self.files.table.setRowCount(0)
                self.files.add_paths(queued)
                self.output_name.setText(self.recording_panel.name.text())
                self._previous_job_completed = False
                self.show_notice(f"{len(queued)} nouvel enregistrement prêt à être transcrit.")

    def _recording_saved(self, path):
        path = Path(path)
        if self._active:
            self._queued_recordings.append(path)
            self.show_notice(f"Enregistrement conservé : {path.name}. Il sera disponible après le traitement courant.")
        else:
            if self._previous_job_completed:
                self.files.table.setRowCount(0)
                self._previous_job_completed = False
            self.files.add_paths([path])
            self.show_notice("Enregistrement ajouté aux fichiers à transcrire.")
        self._refresh_library()

    def _refresh_library(self):
        if self._closing:
            return
        self.runner.submit(self.jobs.history, self._load_history, self.show_notice)
        if self.tabs.currentWidget() is self.history_page:
            self.history_page.refresh()

    def _storage_saved(self, preferences):
        self.jobs.retention = preferences.retention
        self._cleanup_audio()
        self.show_notice("Conservation mise à jour. Redémarrage nécessaire pour les nouveaux dossiers.")

    def _cleanup_audio(self):
        if self._maintenance_pending or self._closing or self._initializing:
            return
        self._maintenance_pending = True
        def done(result):
            if result.get("removed"):
                self._refresh_library()
            if result.get("errors"):
                self.show_notice(result["errors"][0])
        self.runner.submit(self.jobs.cleanup_audio, done, self.show_notice,
                           lambda: setattr(self, "_maintenance_pending", False))

    def _update_capture_badge(self):
        active = bool(self.recording.is_active)
        self.capture_badge.setText("● " + self.recording_panel.timer_label.text() if active else "")
        if active:
            self.capture_badge.setToolTip("Enregistrement en cours ; il reste indépendant de la transcription.")

    def _compact_tables(self):
        compact = self.width() < 850
        self.files.table.verticalHeader().setDefaultSectionSize(30 if compact else 36)
        self.files.table.setColumnHidden(1, compact)
        self.option_summary.setVisible(not compact)
        self.title.setText("Whisper" if self.width() < 710 else "Transcripteur Whisper")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._layout_ready:
            self._compact_tables()

    def closeEvent(self, event):
        if hasattr(self, "maintenance_timer"):
            self.maintenance_timer.stop()
        super().closeEvent(event)

    def _shutdown_error(self, message):
        super()._shutdown_error(message)
        if hasattr(self, "maintenance_timer"):
            self.maintenance_timer.start()
