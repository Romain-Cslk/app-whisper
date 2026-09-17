# Validation : bibliotheque desktop

## Resultats locaux effectivement obtenus

- 33 tests de `test_library_storage.py` : PASS, sans dependance Qt/audio ni doublure
  des nouveaux composants testes. Configuration, noms, 7 politiques de conservation,
  protection des fichiers externes/actifs/modifies, liens symboliques et persistance.
- 19 tests de `test_library_lifecycle.py` : PASS en isolation, avec des doubles des
  frontieres existantes AppPaths / JobService / RecordingService / HistoryService et
  du writer. Les nouvelles methodes de suppression, retention, projection/recherche,
  export et progression sont executees ; les parents et la capture ne sont pas valides
  par cette execution isolee.
- Compilation Python de tous les nouveaux modules/tests et du point d'entree : PASS.

L'environnement local utilise Python 3.13.5, sans PySide6/PyAV. L'installation de Qt
et le clonage direct ont echoue a cause de la resolution DNS. Aucun test graphique,
aucune transcription reelle, aucune capture WASAPI et aucun build EXE n'ont donc ete
executes localement. Ces limites ne constituent pas des tests reussis.

## Tests natifs a executer sur le depot complet

Les tests fournis importent les vraies classes du depot ; les doubles de frontiere
utilises pour l'execution locale isolee ne sont pas ajoutes au package.
Les 8 tests `test_library_ui.py` couvrent la vraie composition Qt avec des backends
factices : six onglets, nom partage, exports nommes, confirmation d'abandon, sauvegarde
hors thread GUI, filtres/preview, Journal et changement de filtre pendant chargement.

```powershell
python -m pip install -e . pytest==8.4.2 pytest-qt==4.5.0
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
python -m compileall -q src
python -m transcripteur_whisper --smoke-test --smoke-report desktop-smoke.json
```

Utiliser Python 3.11 ou 3.12, conformement au projet. Le workflow Windows
`Desktop library validation` est fourni pour executer la suite complete et le smoke.
La presence du workflow ne vaut pas preuve de son execution : verifier son resultat
sur la PR avant fusion. Le smoke doit aussi annoncer `storage_tab: true`.

## Recette Windows manuelle avant diffusion

1. Creer un audio nomme, puis le transcrire et exporter TXT/ZIP : verifier le nom,
   la presence du WAV et du TXT et la navigation dans le Journal pendant le job.
2. Dans Historique, verifier audio seul, transcript seul, les deux et aucune case.
   Supprimer uniquement le TXT, puis un autre WAV : verifier les fichiers restants
   et refaire le controle apres redemarrage.
3. Changer les dossiers, enregistrer, redemarrer : verifier les nouveaux fichiers,
   les anciens resultats conserves et les boutons Ouvrir.
4. Sur des fichiers de test uniquement, essayer les politiques de retention.
   Verifier qu'un job actif/annule/partiel et un audio importe restent proteges
   selon la regle documentee (les politiques d'age ne requierent pas de transcription).
5. Abandonner un journal de recuperation en annulant d'abord la confirmation, puis
   en acceptant : seul le fichier selectionne doit disparaitre.
6. Tester la capture micro/son PC reelle, la fermeture pendant une operation,
   le mode clair/sombre et le build PyInstaller existant.
