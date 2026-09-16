# Cahier des charges — Historique hebdomadaire des enregistrements et transcriptions

## 1. Contexte

La branche `desktop-native` est une application Windows native PySide6. Elle conserve déjà durablement :

- les jobs de transcription et leurs métadonnées dans `results/<job_id>/job.json` ;
- les transcriptions et documents TXT dans le dossier du job ;
- les enregistrements WAV natifs terminés directement dans `results/` ;
- les enregistrements récupérés après incident également dans `results/`.

L'onglet **Résultats** actuel permet d'ouvrir un traitement connu via une liste déroulante, mais il ne répond pas au besoin de retrouver rapidement « la réunion de mardi matin » ou « l'enregistrement de jeudi vers 15 h ».

## 2. Objectif

Ajouter une vue **Historique** inspirée de l'agenda hebdomadaire Microsoft Teams : une semaine complète, lundi à dimanche, structurée heure par heure, permettant de retrouver les enregistrements et transcriptions produits par l'application puis d'ouvrir immédiatement le fichier ou son contenu.

La fonctionnalité doit réutiliser la persistance existante. Aucune base de données, copie parallèle des transcriptions ou nouveau service réseau ne doit être introduit.

## 3. Périmètre fonctionnel

### 3.1 Vue semaine

L'utilisateur dispose d'un nouvel onglet `Historique` dans l'application desktop.

La vue affiche :

- 7 colonnes, du lundi au dimanche ;
- 24 lignes horaires, de `00:00` à `23:00` ;
- une carte par enregistrement/transcription au niveau de son heure ;
- l'heure de début et un titre exploitable ;
- un marqueur visuel distinguant un enregistrement seul d'un élément avec transcript ;
- le statut du traitement dans le détail.

La vue doit s'ouvrir autour de l'heure courante pour la semaine en cours, ou autour du premier événement lorsqu'une autre semaine est affichée.

### 3.2 Navigation temporelle

L'utilisateur peut :

- aller à la semaine précédente ;
- revenir à la semaine courante avec `Aujourd'hui` ;
- aller à la semaine suivante ;
- voir explicitement la plage de dates de la semaine affichée.

### 3.3 Sources affichées

Deux sources doivent être agrégées :

1. **Enregistrements natifs WAV** situés directement dans `results/` ;
2. **Jobs persistants** exposés par `JobService.history()` et leurs artefacts TXT.

Pour un WAV natif :

- la date de fin provient de la date de modification du fichier final ;
- si la durée audio peut être déterminée, l'heure de début est calculée par `fin - durée` ;
- sinon l'heure du fichier sert de repère.

Pour une transcription de fichier importé :

- l'heure de création du job (`created_at`) sert de repère ;
- une durée visuelle de référence peut être utilisée car la durée source n'est pas persistée dans le manifeste actuel.

### 3.4 Regroupement enregistrement + transcript

Lorsqu'un job a pour fichier source le nom exact d'un WAV natif conservé dans `results/`, l'agenda ne doit pas afficher deux cartes distinctes.

Il doit créer **un seul événement** contenant :

- le WAV ;
- la transcription TXT ;
- le document généré éventuel ;
- l'heure et la durée du WAV ;
- le titre personnalisé du job si présent.

### 3.5 Recherche

Un champ de recherche filtre la semaine courante sur :

- titre du résultat ;
- nom du fichier source ;
- nom des artefacts ;
- statut/type ;
- contenu des transcriptions et documents TXT.

La lecture des TXT pour la recherche doit être faite hors du thread GUI et peut être mise en cache tant que le fichier ne change pas.

### 3.6 Consultation d'un événement

Le clic sur une carte ouvre un panneau de détail présentant :

- titre ;
- date et heure ;
- durée lorsqu'elle est connue ;
- statut ;
- fichier source ;
- liste des artefacts disponibles.

Pour un transcript/document TXT, le contenu est lisible directement dans l'application.

Pour un WAV, l'utilisateur peut l'ouvrir avec l'application Windows associée.

Actions attendues :

- ouvrir le fichier ;
- copier le texte ;
- enregistrer sous ;
- ouvrir le dossier contenant le fichier.

## 4. Exigences techniques

### 4.1 Architecture

La fonctionnalité respecte l'architecture de `desktop-native` :

- `HistoryService` est indépendant de Qt ;
- `HistoryPage` est un widget PySide6 ;
- les scans de fichiers et recherches plein texte utilisent `TaskRunner` ;
- aucun accès disque potentiellement lent n'est exécuté directement dans le thread GUI ;
- le nouvel onglet est installé lors de la composition de l'application.

### 4.2 Persistance

Aucune nouvelle persistance n'est créée.

Le service lit uniquement :

- `AppPaths.results` ;
- les snapshots renvoyés par `JobService.history()`.

La fonctionnalité ne modifie ni `job.json`, ni les transcripts, ni les WAV.

### 4.3 Sécurité des chemins

Tout fichier lu par le service doit, après résolution du chemin, rester sous `AppPaths.results`.

Pour un artefact de job, il doit également rester sous `results/<job_id>/`.

Un chemin externe, un lien résolu hors du dossier de résultats ou un fichier TXT arbitraire ne doit pas pouvoir être lu via la fonction de prévisualisation.

### 4.4 Compatibilité packaging

Les nouveaux modules Python sont importés depuis l'entrée d'application et doivent donc être automatiquement détectés par PyInstaller.

Aucune nouvelle dépendance n'est ajoutée.

## 5. Critères d'acceptation

La fonctionnalité est acceptée si :

1. un WAV natif apparaît à la bonne journée et à l'heure calculée depuis sa durée ;
2. un job de transcription apparaît dans la semaine correspondant à `created_at` ;
3. un WAV transcrit est regroupé avec son transcript dans une seule carte ;
4. un WAV non transcrit reste visible ;
5. une recherche sur une phrase présente uniquement dans le transcript retrouve l'événement ;
6. la navigation semaine précédente / actuelle / suivante fonctionne ;
7. le clic sur une carte affiche ses artefacts et le contenu TXT ;
8. les actions ouvrir / copier / enregistrer / ouvrir le dossier sont disponibles selon l'artefact ;
9. un chemin hors de `results/` est rejeté ;
10. le démarrage normal et le smoke test de l'application construisent l'onglet sans modifier le pipeline de transcription ou d'enregistrement.

## 6. Hors périmètre v1

- synchronisation Microsoft Teams / Outlook / Google Calendar ;
- calendrier multi-utilisateur ;
- édition ou suppression depuis l'agenda ;
- renommage des anciens résultats ;
- lecture audio intégrée avec waveform ;
- transcription automatique d'un WAV simplement parce qu'il apparaît dans l'historique ;
- migration vers une base SQL.
