"""Desktop composition for storage, retention, recovery and library actions.

The existing MainWindow remains the transcription/recording controller. This
subclass uses its dependency-injection API and replaces only specialized widgets.
No service is monkey-patched and no operation touches imported media.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..services.ai_summary import PROVIDERS, SummaryPreferences
from ..services.library_jobs import PHASES, LibraryJobService
from ..services.library_recording import LibraryRecordingService
from ..services.library_storage import AudioCatalog, StoragePreferences
from .main_window import MainWindow as BaseWindow
from .widgets.library_controls import LibraryRecordingPanel, LibraryResultsPanel
from .widgets.library_page import LibraryPage
from .widgets.storage_page import StoragePage


class MainWindow(BaseWindow):
    def __init__(self, paths, **kwargs):
        from ..services.audio_device_service import AudioDeviceService
        self.storage_paths = paths
        self.storage_preferences = StoragePreferences.load(paths)
        self.summary_preferences = SummaryPreferences.load(paths)
        self.catalog = AudioCatalog(paths, self.storage_preferences)
        # Live writes use local session folders. Final folders are only publication
        # targets after recording/transcription ends.
        job_paths = replace(paths, results=self.storage_preferences.transcript_work_dir)
        audio_paths = replace(paths, results=self.storage_preferences.audio_work_dir)
        job_paths.results.mkdir(parents=True, exist_ok=True)
        audio_paths.results.mkdir(parents=True, exist_ok=True)
        devices = kwargs.pop("devices", None)
        if devices is None:
            devices = AudioDeviceService()
        jobs = kwargs.pop("jobs", None)
        if jobs is None:
            jobs = LibraryJobService(job_paths, self.catalog, load_history=False)
            # BaseWindow invokes jobs.history for an injected service. Do the
            # one-time disk load using the explicit initializer below instead.
        recording = kwargs.pop("recording", None)
        if recording is None:
            recording = LibraryRecordingService(audio_paths, devices, self.catalog)
        self._maintenance_pending = False
        self._library_initial_load = False
        super().__init__(job_paths, jobs=jobs, recording=recording, devices=devices, **kwargs)
        # Most pages can scroll; allow genuinely compact windows instead of
        # pinning the desktop application to the original 800 px minimum.
        self.setMinimumSize(640, 520)
        self.maintenance_timer = QTimer(self)
        self.maintenance_timer.setInterval(60_000)
        self.maintenance_timer.timeout.connect(self._cleanup_audio)
        self.maintenance_timer.start()

    def _build_ui(self):
        super()._build_ui()
        self.summary_requested = QCheckBox("Générer un CR IA après la transcription")
        self.summary_requested.setChecked(False)
        self.summary_requested.setToolTip(
            "Utilise le fournisseur et la clé configurés dans Paramètres > Résumé IA. "
            "La transcription elle-même garde son mode et son modèle actuels."
        )
        transcribe_scroll = self.tabs.widget(0)
        transcribe = transcribe_scroll.widget() if hasattr(transcribe_scroll, "widget") else None
        if transcribe is not None and transcribe.layout() is not None:
            transcribe.layout().insertWidget(1, self.summary_requested)

        old = self.recording_panel
        self.recording_panel = LibraryRecordingPanel(self.runner, self.recording, self.settings)
        self.tabs.widget(1).layout().replaceWidget(old, self.recording_panel)
        old.deleteLater()
        self.recording_panel.recording_saved.connect(self._recording_saved)
        self.recording_panel.notice.connect(self.show_notice)
        self.recording_panel.state_changed.connect(self._update_start_enabled)
        self.recording_panel.shutdown_failed.connect(self._shutdown_error)
        self.output_name.textChanged.connect(self.recording_panel.name.setText)
        self.recording_panel.name.textChanged.connect(self.output_name.setText)

        old = self.results
        self.results = LibraryResultsPanel(self.runner, self.jobs, self.storage_preferences.transcript_dir)
        self.tabs.widget(2).layout().replaceWidget(old, self.results)
        old.deleteLater()
        self.results.notice.connect(self.show_notice)
        self.results.files_changed.connect(self._refresh_library)

        journal_index = self.tabs.indexOf(self.logs)
        self.tabs.removeTab(journal_index)
        self.journal_page = QWidget()
        journal = QVBoxLayout(self.journal_page)
        self.live_status = QLabel("Aucun traitement en cours")
        self.live_status.setWordWrap(True)
        self.follow_logs = QCheckBox("Suivre les nouvelles lignes")
        self.follow_logs.setChecked(True)
        row = QHBoxLayout()
        row.addWidget(self.live_status, 1)
        row.addWidget(self.follow_logs)
        journal.addLayout(row)
        journal.addWidget(self.logs, 1)
        self.tabs.insertTab(journal_index, self.journal_page, "Journal")

        self.history_page = LibraryPage(self.runner, self.jobs, self.paths, self.catalog, parent=self)
        self.history_page.notice.connect(self.show_notice)
        self.history_page.files_changed.connect(self._refresh_library)
        self.tabs.insertTab(3, self.history_page, "Historique")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.storage_page = StoragePage(
            self.runner,
            self.storage_paths,
            self.storage_preferences,
            summary_preferences=self.summary_preferences,
            busy=lambda: self._active or self.recording_panel.busy or self._initializing or self._closing,
            parent=self,
        )
        self.storage_page.notice.connect(self.show_notice)
        self.storage_page.saved.connect(self._storage_saved)
        self.tabs.addTab(self.storage_page, "Paramètres")

    def _initialized(self):
        # Called by the base async initializer. Keep Start disabled until real
        # manifest loading + safe startup retention have finished.
        if not self._library_initial_load:
            self._library_initial_load = True
            self.runner.submit(getattr(self.jobs, "load_history", self.jobs.history), self._load_history, self.show_notice,
                               lambda: super(MainWindow, self)._initialized())
            return
        super()._initialized()

    def _tab_changed(self, index):
        if self.tabs.widget(index) is self.history_page and not self._initializing:
            self.history_page.activate()

    def _submit_job(self, options):
        if self._cancel_requested or self._closing:
            self._set_active(False)
            return
        preferences = SummaryPreferences.load(self.storage_paths)
        if self.summary_requested.isChecked():
            options = replace(
                options,
                generate_summary=True,
                summary_provider=preferences.provider,
                summary_prompt=preferences.prompt,
            )
        files = self.files.paths
        key = self.api_key.text().strip() if options.mode == "api" else ""
        self.runner.submit(
            lambda: self.jobs.submit(files, options, api_key=key),
            self._job_submitted,
            self._launch_failed,
        )

    def _job_submitted(self, identifier):
        super()._job_submitted(identifier)
        self.live_status.setText("Traitement démarré ; mise à jour toutes les 400 ms.")
        if not self._closing:
            self.tabs.setCurrentWidget(self.journal_page)

    def _render_job(self, snapshot):
        bar = self.logs.verticalScrollBar()
        previous = bar.value()
        follow = self.follow_logs.isChecked()
        # JobService reaches a terminal transcription state just before the
        # optional publication to the configured transcript directory. Keep the
        # poller alive during that short finalization window.
        rendered = snapshot
        if snapshot.get("storage_state") == "pending" and snapshot.get("status") in {
            "done", "partial", "error", "cancelled"
        }:
            rendered = dict(snapshot)
            rendered["status"] = "running"
            rendered["progress"] = min(float(snapshot.get("progress") or 0), 0.999)
        super()._render_job(rendered)
        if snapshot.get("summary_state") == "running":
            provider = snapshot.get("summary_provider") or ""
            label = PROVIDERS.get(provider, {}).get("label", provider or "IA")
            self.state.setText(f"Génération du CR IA avec {label}…")
        elif rendered is not snapshot:
            self.state.setText("Finalisation du stockage…")
        if not follow:
            bar.setValue(previous)
        details = []
        for metadata in snapshot.get("files", []):
            if metadata.get("status") == "running":
                phase = PHASES.get(metadata.get("stage"), "En attente")
                percent = round(float(metadata.get("progress") or 0) * 100)
                segment = metadata.get("segment_index", 0)
                total = metadata.get("segment_count", 0)
                suffix = f" · segment {segment}/{total}" if total else ""
                details.append(f"{metadata.get('name', '')} · {phase} · {percent} %{suffix}")
        self.live_status.setText(" | ".join(details[:2]) or self.state.text())
        self.live_status.setToolTip(f"Dernier état reçu : {datetime.now():%H:%M:%S}")

    def _recording_saved(self, path):
        super()._recording_saved(path)
        self._refresh_library()

    def _refresh_library(self):
        if self._closing:
            return
        self.runner.submit(self.jobs.history, self._load_history, self.show_notice)
        if self.tabs.currentWidget() is self.history_page:
            self.history_page.refresh()

    def _storage_saved(self, preferences):
        # Directories require restart to avoid redirecting a running engine. AI
        # provider/key/prompt settings are reloaded at each new job and therefore
        # take effect immediately.
        self.jobs.retention = preferences.retention
        self.summary_preferences = SummaryPreferences.load(self.storage_paths)
        label = PROVIDERS[self.summary_preferences.provider]["label"]
        self.summary_requested.setToolTip(
            f"CR IA configuré avec {label}. Modifiez le fournisseur, la clé ou le prompt dans Paramètres."
        )
        self.show_notice(
            "Paramètres enregistrés. Les réglages IA sont actifs immédiatement ; "
            "redémarrez l'application pour appliquer les dossiers de stockage."
        )
        self._cleanup_audio()

    def _cleanup_audio(self):
        if self._maintenance_pending or self._closing or self._initializing:
            return
        self._maintenance_pending = True
        def done(result):
            if result["removed"]:
                self.show_notice(f"Conservation audio : {len(result['removed'])} WAV supprimé(s).")
                self._refresh_library()
            if result["errors"]:
                self.show_notice(result["errors"][0])
        self.runner.submit(self.jobs.cleanup_audio, done, self.show_notice,
                           lambda: setattr(self, "_maintenance_pending", False))

    def closeEvent(self, event):
        if hasattr(self, "maintenance_timer"):
            self.maintenance_timer.stop()
        super().closeEvent(event)
