# Whisper Desktop - Espace de travail v8

## Objectif
Donner la priorité au contenu et aux actions utiles : en-tête compact,
pas de grand bloc permanent de progression terminée, journal et lecture extensibles,
contrôles audio sans chevauchement. L'application reste native PySide6.

## Interface
- En-tête de 42 pixels logiques ; indicateur de capture discret, indépendant du traitement.
- Barre de traitement de 34 pixels uniquement pendant un travail actif ; annulation explicite.
- Action Transcrire dans sa page, pas sous tous les onglets.
- Options techniques repliables. Les noms longs sont tronqués dans les tableaux et restent disponibles en infobulle.
- Sur petite largeur, les sources audio passent sur une colonne.
- Historique : agenda et lecture côte à côte sur grande largeur ; bascule Agenda/Lecture sous 920 pixels.
- Le journal dispose d'un éditeur extensible. Les pages restent opaques ; aucune animation d'opacité d'onglet.
- La palette bleu/vert et les thèmes clair/sombre sont conservés. Les survols restent visuels ; aucun mouvement de page.

## Rédaction IA et contexte
Paramètres > Rédaction IA et contexte contient trois sections : Modèles, Contexte et Fournisseur IA.

Trois modèles initiaux : Résumé, Daily, Compte rendu. Les instructions sont visibles dans un
cadre de texte ; chaque modèle peut être renommé, dupliqué, ajouté ou supprimé. Il reste au moins
un modèle et la limite est de 40. Le prompt Daily personnalisé existant est repris lors du premier chargement.

Contexte comprend trois zones : projet/contexte, personnes/rôles, applications/sigles/vocabulaire.
La transcription reste la seule preuve des propos, présences et décisions : le contexte est un guide de
lecture, pas une source de faits supplémentaires. Les termes ambigus doivent être signalés.

Sur l'écran Transcrire, cocher Rédaction IA et choisir le modèle nommé. Le prompt et le contexte
sont figés au lancement, puis transmis dans les options du traitement ; une modification ultérieure
ne change pas le document en cours. Les phases de préparation des longs transcripts reçoivent aussi
les consignes choisies (plus de préparation limitée au daily).

Configuration locale : config/writing-profiles.json. Aucune clé API n'est stockée dans ce fichier.
L'instantané des instructions est aussi conservé dans les métadonnées locales du traitement existantes.
Le contexte et la transcription ne sont envoyés au fournisseur que si la rédaction IA est demandée.
Les clés gardent le service DPAPI Windows existant. Ne pas saisir de secrets dans les zones libres.

## Récupération des enregistrements
Une fenêtre dédiée remplace les anciens choix fichier par fichier. Chaque capture est regroupée
par son identifiant : microphone, son du PC, éventuel mix. La liste indique la date estimée,
les sources et la taille ; recherche, sélection multiple et sélection de toutes les lignes visibles.

L'analyse est en lecture seule, limitée aux journaux de capture connus. Elle ne parcourt ni
les audios importés ni les pistes privées du dossier Sources. Les sessions actives, orphelines
encore détenues par le moteur et celles dont le résultat est déjà catalogué sont exclues.

Récupérer valide d'abord des copies réparées des pistes. Un mix absent est reconstruit par blocs.
Si une piste est invalide, les originaux sont conservés et un message d'échec est affiché ; aucune
session en échec n'est masquée. Les nouveaux WAV vont dans Enregistrements et gardent la politique de conservation existante.
Les positions temporelles des anciens journaux sont estimées ; aucun horodatage exact n'est inventé.

Abandonner met les seules sessions confirmées de côté dans <dossier utilisateur>/RecuperationIgnoree,
avec un reçu par session. Les originaux d'une récupération réussie y sont également déplacés afin
qu'ils ne soient pas reproposés. Il ne s'agit pas de la corbeille Windows ni d'une suppression définitive.
Cette mise de côté ne libère pas d'espace disque et n'est pas purgée automatiquement. Le bouton
d'ouverture permet d'inspecter ces fichiers. La conservation automatique ordinaire ne concerne
pas cette zone de récupération.

Après chaque lot, la liste est relue et les réussites disparaissent avec un compteur visible.
Les échecs restent visibles et leurs erreurs sont accessibles. Pendant la capture, les actions sont
explicitement indisponibles : il faut arrêter la capture avant de déplacer ses journaux.

## Ce qui reste inchangé
- WAV conservé dans le stockage de travail selon la liste de durées choisie.
- Aucun dossier ni option de stockage final audio.
- Publication des textes/documents après traitement seulement, y compris vers OneDrive.
- Empreinte du transcript, sources Moi/Son du PC et enregistrement pendant une transcription, fournis par V7.
- Aucun changement de moteur Whisper, de format de clé ou de fournisseur HTTP.

## Installation et retour arrière
Le script cible le worktree V7 et accepte ses modifications locales. Il ajoute des composants
nouveaux, change l'import du point d'entrée et adapte uniquement l'extraction documentaire.
Aucun fichier utilisateur audio, transcript ou paramètre n'est modifié par l'installateur.
Aucun commit, ajout à l'index, push ou changement de branche n'est effectué.

Tous les fichiers sont prévalidés avant écriture. Sauvegarde hors dépôt dans le dossier temporaire.
En cas d'échec des contrôles, les fichiers appliqués par cette installation sont restaurés ; une
modification externe détectée entre-temps est préservée et signalée. La sauvegarde reste disponible.
