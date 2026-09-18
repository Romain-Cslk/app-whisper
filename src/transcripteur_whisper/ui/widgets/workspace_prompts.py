"""Independent settings for provider credentials, context and named writing models."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...services.ai_summary import PROVIDERS, SummaryPreferences
from ...services.library_storage import LibraryError
from ...services.summary_profiles import PromptProfile, WritingStore


class WritingSettingsPage(QWidget):
    saved = Signal(object)
    notice = Signal(str)

    def __init__(self, runner, paths, *, parent=None):
        super().__init__(parent)
        self.setProperty("workspacePage", True)
        self.runner, self.paths = runner, paths
        self.store = WritingStore(paths)
        self.preferences = self.store.load()
        self._dirty = False
        self._loading = False
        self._current_id = None
        self._saving = False
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.profiles_list = QListWidget()
        self.profiles_list.setMinimumWidth(120)
        self.profiles_list.setMaximumWidth(220)
        self.profiles_list.setAccessibleName("Modèles de rédaction")
        self.name = QLineEdit()
        self.name.setMaxLength(60)
        self.name.setPlaceholderText("Nom affiché sur l'écran de transcription")
        self.prompt = QPlainTextEdit()
        self.prompt.setAccessibleName("Instructions du modèle sélectionné")
        self.prompt.setPlaceholderText("Décrivez la structure, le ton et les règles de ce document…")
        self.prompt.setMinimumHeight(150)
        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.addWidget(QLabel("Nom du modèle"))
        editor_layout.addWidget(self.name)
        editor_layout.addWidget(QLabel("Prompt — instructions de rédaction"))
        editor_layout.addWidget(self.prompt, 1)
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        side.addWidget(self.profiles_list, 1)
        self.add_button = QPushButton("Ajouter")
        self.duplicate_button = QPushButton("Dupliquer")
        self.remove_button = QPushButton("Supprimer")
        for button in (self.add_button, self.duplicate_button, self.remove_button):
            side.addWidget(button)
        self.profile_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.profile_splitter.addWidget(sidebar)
        self.profile_splitter.addWidget(editor)
        self.profile_splitter.setStretchFactor(1, 1)
        self.profile_splitter.setSizes([160, 600])
        self.tabs.addTab(self.profile_splitter, "Modèles")

        self.context = QPlainTextEdit(self.preferences.context)
        self.people = QPlainTextEdit(self.preferences.people)
        self.vocabulary = QPlainTextEdit(self.preferences.vocabulary)
        self.context.setPlaceholderText("Projet, rôle, objectifs, organisation… Ne collez pas de clé API ici.")
        self.people.setPlaceholderText("Prénom / nom, fonction, alias, orthographe exacte…")
        self.vocabulary.setPlaceholderText("Applications, sigles, noms techniques et leur signification…")
        context_page = QWidget()
        context_layout = QVBoxLayout(context_page)
        explanation = QLabel("Ces informations aident à comprendre les termes. Elles ne remplacent pas les faits de la transcription.")
        explanation.setWordWrap(True)
        context_layout.addWidget(explanation)
        self.context_tabs = QTabWidget()
        for label, field in (("Contexte", self.context), ("Personnes", self.people), ("Vocabulaire", self.vocabulary)):
            field.setAccessibleName(label + " pour l'IA")
            self.context_tabs.addTab(field, label)
        context_layout.addWidget(self.context_tabs, 1)
        privacy = QLabel("Enregistré localement. Envoyé au fournisseur choisi uniquement pour une rédaction IA demandée. N'y mettez pas de secret.")
        privacy.setWordWrap(True)
        context_layout.addWidget(privacy)
        self.tabs.addTab(context_page, "Contexte")

        self.credentials = SummaryPreferences.load(paths)
        provider_page = QWidget()
        provider_layout = QVBoxLayout(provider_page)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.provider = QComboBox()
        for key, value in PROVIDERS.items():
            self.provider.addItem(value["label"], key)
        self.provider.setCurrentIndex(max(0, self.provider.findData(self.credentials.provider)))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.clear_key = QCheckBox("Supprimer la clé enregistrée de ce fournisseur")
        self.provider_state = QLabel()
        self.provider_state.setWordWrap(True)
        form.addRow("Fournisseur", self.provider)
        form.addRow("Clé API", self.key)
        form.addRow(self.clear_key)
        form.addRow(self.provider_state)
        provider_layout.addLayout(form)
        info = QLabel("La clé de rédaction IA est distincte de la transcription. Sous Windows, elle reste protégée par DPAPI. Champ vide : la clé existante est conservée.")
        info.setWordWrap(True)
        provider_layout.addWidget(info)
        provider_layout.addStretch(1)
        self.tabs.addTab(provider_page, "Fournisseur IA")

        self.status = QLabel("Trois modèles prêts à personnaliser : Résumé, Daily et Compte rendu.")
        self.status.setWordWrap(True)
        self.save_button = QPushButton("Enregistrer")
        self.save_button.setProperty("primary", True)
        self.reload_button = QPushButton("Recharger")
        footer = QHBoxLayout()
        footer.addWidget(self.status, 1)
        footer.addWidget(self.reload_button)
        footer.addWidget(self.save_button)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.addWidget(self.tabs, 1)
        root.addLayout(footer)

        self.profiles_list.currentRowChanged.connect(self._select)
        self.add_button.clicked.connect(lambda: self._add(False))
        self.duplicate_button.clicked.connect(lambda: self._add(True))
        self.remove_button.clicked.connect(self._remove)
        self.save_button.clicked.connect(self.save)
        self.reload_button.clicked.connect(self.reload)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        for field in (self.name, self.key):
            field.textChanged.connect(self._changed)
        for field in (self.prompt, self.context, self.people, self.vocabulary):
            field.textChanged.connect(self._changed)
        self.clear_key.toggled.connect(self._changed)
        self._fill_list()
        self._provider_changed()
        self._dirty = False
        self.status.setText("Modèles prêts : sélectionnez-en un pour modifier son nom et ses instructions.")

    @property
    def dirty(self):
        return self._dirty

    def _changed(self, *_args):
        if not self._loading:
            self._dirty = True
            self.status.setText("Modifications non enregistrées.")

    def _capture_profile(self):
        if self._current_id:
            profiles = tuple(replace(p, name=self.name.text().strip(), prompt=self.prompt.toPlainText().strip())
                             if p.id == self._current_id else p for p in self.preferences.profiles)
            self.preferences = replace(self.preferences, profiles=profiles)

    def _fill_list(self, selected=None):
        self._loading = True
        self.profiles_list.blockSignals(True)
        self.profiles_list.clear()
        for profile in self.preferences.profiles:
            self.profiles_list.addItem(profile.name)
        index = next((i for i, p in enumerate(self.preferences.profiles)
                      if p.id == (selected or self.preferences.selected_id)), 0)
        self.profiles_list.setCurrentRow(index)
        self.profiles_list.blockSignals(False)
        self._current_id = None
        self._select(index)
        self._loading = False

    def _select(self, index):
        self._capture_profile()
        if not 0 <= index < len(self.preferences.profiles):
            return
        self._loading = True
        profile = self.preferences.profiles[index]
        self._current_id = profile.id
        self.name.setText(profile.name)
        self.prompt.setPlainText(profile.prompt)
        self.remove_button.setEnabled(len(self.preferences.profiles) > 1)
        self._loading = False

    def _add(self, duplicate):
        self._capture_profile()
        from uuid import uuid4
        prompt = self.prompt.toPlainText() if duplicate else "Rédige un document fidèle à la transcription. N'invente aucun fait."
        stem = (self.name.text().strip() + " (copie)") if duplicate else "Nouveau modèle"
        names = {p.name.casefold() for p in self.preferences.profiles}
        name, number = stem, 2
        while name.casefold() in names:
            name, number = f"{stem} {number}", number + 1
        if len(self.preferences.profiles) >= 40:
            self.notice.emit("Limite de 40 modèles atteinte.")
            return
        profile = PromptProfile(uuid4().hex, name, prompt)
        self.preferences = replace(self.preferences, profiles=(*self.preferences.profiles, profile))
        self._fill_list(profile.id)
        self._changed()

    def _remove(self):
        if len(self.preferences.profiles) <= 1:
            return
        answer = QMessageBox.question(self, "Supprimer le modèle", "Supprimer ce modèle de rédaction ? Aucun document ne sera supprimé.",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        kept = tuple(p for p in self.preferences.profiles if p.id != self._current_id)
        self.preferences = replace(self.preferences, profiles=kept,
                                   selected_id=self.preferences.selected_id if self.preferences.selected_id != self._current_id else kept[0].id)
        self._current_id = None
        self._fill_list()
        self._changed()

    def _provider_changed(self, *_args):
        self._loading = True
        self.key.clear()
        self.clear_key.setChecked(False)
        has_key = self.credentials.has_key(self.provider.currentData())
        self.key.setPlaceholderText("Nouvelle clé pour remplacer l'existante" if has_key else "Clé API")
        self.provider_state.setText("Une clé est enregistrée pour ce fournisseur." if has_key else "Aucune clé enregistrée pour ce fournisseur.")
        self._loading = False
        self._changed()

    def save(self):
        if self._saving:
            return
        self._capture_profile()
        candidate = replace(self.preferences, context=self.context.toPlainText().strip(),
                            people=self.people.toPlainText().strip(), vocabulary=self.vocabulary.toPlainText().strip())
        provider, key, clear = self.provider.currentData(), self.key.text().strip(), self.clear_key.isChecked()
        try:
            candidate.validate()
        except (LibraryError, TypeError, AttributeError) as exc:
            self.status.setText(str(exc))
            return
        self._saving = True
        self.tabs.setEnabled(False)
        self.save_button.setEnabled(False)
        self.reload_button.setEnabled(False)
        self.status.setText("Enregistrement…")

        def save_all():
            # Detect an outdated editor before touching provider credentials.
            if self.store.load().revision != candidate.revision:
                raise LibraryError("Les modèles ont changé dans une autre fenêtre. Rechargez-les avant d'enregistrer.")
            # Reload keys immediately before writing, preserving other providers.
            current = SummaryPreferences.load(self.paths)
            current.save(self.paths, provider=provider, prompt=current.prompt,
                         replacement_key=key, clear_key=clear)
            return self.store.save(candidate)

        def success(preferences):
            self.preferences = preferences
            self.credentials = SummaryPreferences.load(self.paths)
            self._provider_changed()
            self._fill_list(self._current_id)
            self._dirty = False
            self.status.setText("Contexte et modèles enregistrés. Utilisés pour les prochains traitements.")
            self.saved.emit(preferences)

        def finish():
            self._saving = False
            self.tabs.setEnabled(True)
            self.save_button.setEnabled(True)
            self.reload_button.setEnabled(True)
        self.runner.submit(save_all, success, lambda message: self.status.setText(message), finish)

    def reload(self):
        if self._dirty:
            answer = QMessageBox.question(self, "Recharger", "Abandonner les modifications non enregistrées ?",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            self.preferences = self.store.load()
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self._current_id = None
        self._loading = True
        self.context.setPlainText(self.preferences.context)
        self.people.setPlainText(self.preferences.people)
        self.vocabulary.setPlainText(self.preferences.vocabulary)
        self._fill_list()
        self.credentials = SummaryPreferences.load(self.paths)
        self.provider.blockSignals(True)
        self.provider.setCurrentIndex(max(0, self.provider.findData(self.credentials.provider)))
        self.provider.blockSignals(False)
        self._provider_changed()
        self._loading = False
        self._dirty = False
        self.status.setText("Dernière version rechargée.")

    def show_context(self):
        self.tabs.setCurrentIndex(1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep editor usable, not crushed beneath long explanatory labels.
        self.profiles_list.setMaximumWidth(155 if self.width() < 760 else 220)
