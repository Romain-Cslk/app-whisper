"""Storage preferences for work files, text publication and audio retention."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...services.library_storage import RETENTION, StoragePreferences


class StoragePage(QWidget):
    saved = Signal(object)
    notice = Signal(str)

    def __init__(self, runner, paths, preferences: StoragePreferences, *, busy=lambda: False, parent=None):
        super().__init__(parent)
        self.runner, self.paths, self.preferences, self.busy = runner, paths, preferences, busy

        self.audio_work = QLineEdit(str(preferences.audio_work_dir))
        self.transcripts_work = QLineEdit(str(preferences.transcript_work_dir))
        self.transcripts_final = QLineEdit(str(preferences.transcript_dir))
        self.transcripts = self.transcripts_final  # compatibility alias

        self.audio_work.setAccessibleName("Dossier de travail des enregistrements")
        self.transcripts_work.setAccessibleName("Dossier de travail des transcriptions")
        self.transcripts_final.setAccessibleName("Dossier final des transcripts")

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(12)

        heading = QLabel("Stockage")
        heading.setStyleSheet("font-size: 16pt; font-weight: bold;")
        content_layout.addWidget(heading)

        work_group = QGroupBox("Fichiers de travail")
        work_layout = QVBoxLayout(work_group)
        work_note = QLabel(
            "Les enregistrements et les transcriptions en cours restent dans ces dossiers locaux. "
            "Choisissez de préférence un emplacement non synchronisé."
        )
        work_note.setWordWrap(True)
        work_layout.addWidget(work_note)

        work_form = QFormLayout()
        work_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        work_form.addRow("Enregistrements", self._folder_row(self.audio_work))
        work_form.addRow("Transcriptions", self._folder_row(self.transcripts_work))

        self.policy = QComboBox()
        for value, label in RETENTION.items():
            self.policy.addItem(label, value)
        self.policy.setCurrentIndex(max(0, self.policy.findData(preferences.retention)))
        self.policy.setAccessibleName("Durée de conservation des enregistrements")
        work_form.addRow("Conserver les enregistrements", self.policy)
        work_layout.addLayout(work_form)

        retention_note = QLabel(
            "Les WAV créés ou récupérés par l'application restent dans le dossier Enregistrements "
            "pendant la durée choisie. Les fichiers audio importés ne sont jamais supprimés automatiquement."
        )
        retention_note.setWordWrap(True)
        work_layout.addWidget(retention_note)
        content_layout.addWidget(work_group)

        final_group = QGroupBox("Résultats terminés")
        final_layout = QVBoxLayout(final_group)
        final_note = QLabel(
            "Les transcripts et documents terminés sont copiés ici une seule fois après la fin du traitement."
        )
        final_note.setWordWrap(True)
        final_layout.addWidget(final_note)
        final_form = QFormLayout()
        final_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        final_form.addRow("Transcripts et documents", self._folder_row(self.transcripts_final))
        final_layout.addLayout(final_form)
        content_layout.addWidget(final_group)

        history_group = QGroupBox("Historique")
        history_layout = QVBoxLayout(history_group)
        self.automatic = QCheckBox("Utiliser automatiquement les dossiers actuels et précédents")
        self.automatic.setChecked(not preferences.history_dirs)
        history_layout.addWidget(self.automatic)

        self.history = QListWidget()
        self.history.setAccessibleName("Dossiers consultés par l'historique")
        self.history.addItems([str(path) for path in preferences.search_roots(paths)])
        self.history.setMinimumHeight(120)
        history_layout.addWidget(self.history)

        history_actions = QHBoxLayout()
        self.add_folder = QPushButton("Ajouter un dossier…")
        self.remove_folder = QPushButton("Retirer")
        self.open_folder = QPushButton("Ouvrir")
        self.add_folder.clicked.connect(self._add_history)
        self.remove_folder.clicked.connect(self._remove_history)
        self.open_folder.clicked.connect(self._open_history)
        history_actions.addWidget(self.add_folder)
        history_actions.addWidget(self.remove_folder)
        history_actions.addWidget(self.open_folder)
        history_actions.addStretch(1)
        history_layout.addLayout(history_actions)
        content_layout.addWidget(history_group)

        self.message = QLabel(
            "Les nouveaux dossiers sont utilisés après redémarrage. Aucun fichier existant n'est déplacé automatiquement."
        )
        self.message.setWordWrap(True)
        content_layout.addWidget(self.message)

        self.save_button = QPushButton("Enregistrer les paramètres")
        self.save_button.clicked.connect(self.save)
        content_layout.addWidget(self.save_button)
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        self.automatic.toggled.connect(self._automatic_changed)
        self._automatic_changed(self.automatic.isChecked())

    def _folder_row(self, field):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(lambda: self._browse(field))
        open_button = QPushButton("Ouvrir")
        open_button.clicked.connect(lambda: self._open(field.text()))
        layout.addWidget(field, 1)
        layout.addWidget(browse)
        layout.addWidget(open_button)
        return row

    def _browse(self, field):
        path = QFileDialog.getExistingDirectory(self, "Choisir un dossier", field.text())
        if path:
            field.setText(path)

    def _open(self, value):
        path = Path(value).expanduser()
        if not path.is_absolute() or not path.is_dir():
            self.notice.emit("Ce dossier n'existe pas encore. Enregistrez les paramètres pour le créer.")
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
            self.notice.emit("Le système ne peut pas ouvrir ce dossier.")

    def _open_history(self):
        if self.history.currentItem():
            self._open(self.history.currentItem().text())

    def _add_history(self):
        path = QFileDialog.getExistingDirectory(self, "Ajouter un dossier à l'historique")
        if path and path not in [self.history.item(i).text() for i in range(self.history.count())]:
            self.automatic.setChecked(False)
            self.history.addItem(path)

    def _remove_history(self):
        row = self.history.currentRow()
        if row >= 0:
            self.history.takeItem(row)

    def _automatic_changed(self, automatic):
        self.remove_folder.setEnabled(not automatic)
        self.history.setToolTip(
            "Liste indicative ; les dossiers sont calculés au prochain lancement."
            if automatic else "Seuls ces dossiers seront consultés."
        )

    def save(self):
        if self.busy():
            self.notice.emit("Attendez la fin du traitement ou de l'enregistrement avant de modifier le stockage.")
            return
        history = () if self.automatic.isChecked() else tuple(
            Path(self.history.item(i).text()) for i in range(self.history.count())
        )
        if not self.automatic.isChecked() and not history:
            self.notice.emit("Ajoutez au moins un dossier ou activez la recherche automatique.")
            return
        candidate = replace(
            self.preferences,
            transcript_dir=Path(self.transcripts_final.text()),
            audio_work_dir=Path(self.audio_work.text()),
            transcript_work_dir=Path(self.transcripts_work.text()),
            keep_final_audio=False,
            retention=self.policy.currentData(),
            history_dirs=history,
        )
        self.save_button.setEnabled(False)
        self.runner.submit(
            lambda: candidate.save(self.paths),
            self._saved,
            self.notice.emit,
            lambda: self.save_button.setEnabled(True),
        )

    def _saved(self, preferences):
        self.preferences = preferences
        self.message.setText(
            "Paramètres enregistrés. Redémarrez l'application pour utiliser les nouveaux dossiers."
        )
        self.saved.emit(preferences)
