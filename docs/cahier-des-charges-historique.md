# Cahier des charges — Historique hebdomadaire des transcriptions

## 1. Contexte

L'application permet actuellement d'enregistrer ou d'importer un média, de le transcrire et de télécharger les résultats TXT. Les jobs et leurs sorties sont toutefois gérés comme des résultats temporaires : après la durée de rétention, ils ne constituent plus un historique exploitable.

Le besoin est de pouvoir retrouver rapidement une transcription réalisée à une date donnée, depuis une interface proche de l'agenda hebdomadaire de Microsoft Teams.

## 2. Objectifs

1. Conserver durablement les transcriptions TXT terminées, sans conserver les médias audio/vidéo source.
2. Présenter les traitements dans un agenda hebdomadaire lundi-dimanche, heure par heure.
3. Permettre de naviguer entre les semaines et de revenir à la semaine courante.
4. Permettre de rechercher un traitement par nom, fichier source ou contenu textuel.
5. Permettre d'ouvrir puis télécharger le transcript depuis l'historique.
6. Reprendre automatiquement les TXT encore présents au moment de la première exécution de cette version.

## 3. Besoins fonctionnels

### 3.1 Agenda hebdomadaire

- Vue 7 jours, du lundi au dimanche.
- 24 créneaux horaires par jour.
- Positionnement de chaque traitement selon sa date et heure de création.
- Carte affichant au minimum l'heure, le nom du traitement et le ou les fichiers source.
- Distinction visuelle des statuts `done`, `partial`, `error` et `cancelled`.
- Navigation semaine précédente / aujourd'hui / semaine suivante.

### 3.2 Consultation d'une transcription

Au clic sur une carte :

- ouverture d'un panneau de détail sans quitter l'agenda ;
- affichage de la date/heure, du mode de transcription, du modèle, de la langue et du statut ;
- affichage des fichiers source lorsque l'information est disponible ;
- liste des TXT archivés ;
- lecture du contenu d'un TXT ;
- téléchargement individuel d'un TXT.

### 3.3 Recherche

La recherche doit porter sur :

- le nom du traitement ;
- le nom des fichiers source ;
- le nom des artefacts ;
- le contenu des TXT archivés.

La recherche reste appliquée à la semaine affichée.

### 3.4 Persistance

- Chaque job terminé est archivé dans le dossier de données de l'application sous `history/<job_id>/`.
- Les TXT sont copiés dans l'historique avant que la rétention standard des jobs ne puisse les supprimer.
- Les médias source et fichiers temporaires continuent d'être supprimés selon le fonctionnement actuel.
- Un `metadata.json` accompagne chaque historique.
- Au démarrage, les dossiers de `transcriptions/` encore présents mais non archivés sont repris automatiquement.

## 4. Besoins techniques

- Aucun nouveau service, serveur ou SGBD.
- Stockage local en fichiers JSON + TXT pour rester cohérent avec l'application mono-utilisateur locale.
- Routes HTTP en lecture seule pour l'historique.
- Protection stricte contre la traversée de répertoires lors de la lecture/téléchargement d'un artefact.
- Compatibilité FastAPI / pywebview existante.
- Fonctionnalité assemblée dans `application.py`, utilisée par les lanceurs bureau.

## 5. Hors périmètre de cette version

- conservation ou lecture de l'audio historique ;
- modification du texte depuis l'historique ;
- suppression manuelle d'une entrée ;
- synchronisation cloud ou multi-utilisateur ;
- synchronisation avec Microsoft Teams / Outlook / Google Calendar ;
- reconstitution exacte de l'heure de début d'un enregistrement réalisé avant le lancement de la transcription.

## 6. Critères d'acceptation

1. Une nouvelle transcription terminée apparaît dans l'agenda à sa date/heure de création.
2. Elle reste disponible après redémarrage de l'application et après expiration de la rétention des jobs.
3. Le média source n'est pas conservé par cette fonctionnalité.
4. La navigation entre semaines n'effectue aucune écriture.
5. Une recherche sur une phrase contenue uniquement dans le TXT retrouve l'entrée correspondante.
6. Le clic sur une entrée ouvre son contenu et permet le téléchargement du TXT.
7. Un nom d'artefact non enregistré dans les métadonnées ne peut pas être utilisé pour lire un autre fichier du disque.
8. Les TXT existants encore présents dans `transcriptions/` sont repris lors du premier démarrage.
9. La suite de tests existante continue de passer et les nouveaux tests de persistance/historique passent également.
