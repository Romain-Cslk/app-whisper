# Bibliotheque desktop : stockage, suppression et suivi

## Cible et integration

Evolution de `desktop-native` apres la PR #44. L'application reste native PySide6,
sans page web ni serveur ajoute. Le point d'entree `app.main` assemble les nouveaux
services et widgets dans `ui/library_window.py`. Le controleur de transcription,
les executors, la capture WASAPI et les widgets de base sont reutilises par heritage
et injection de dependances, sans monkey-patch. Les autres branches ne sont pas modifiees.

## Exigences et criteres d'acceptation

| ID | Besoin | Critere d'acceptation |
| --- | --- | --- |
| B01 | Supprimer un audio ou un transcript | Une action dans Historique et une action TXT dans Resultats demandent confirmation et ne suppriment que le fichier selectionne. |
| B02 | Respecter le nom du resultat | Les exports TXT/ZIP proposent le nom personnalise. Le nom est partage entre Transcrire et Enregistrer ; un WAV nomme contient aussi un identifiant unique. |
| B03 | Choisir les dossiers | Parametres distingue quatre emplacements : audio intermediaire, transcript intermediaire, audio final et transcript/document final, plus les dossiers consultes par l'historique. |
| B04 | Ouvrir les dossiers | Chaque dossier de stockage et chaque dossier d'historique peut etre ouvert dans l'explorateur. |
| B05 | Conservation audio | Choix : apres traitement reussi, 1 jour, 3 jours, 1 semaine, 1 mois, 3 mois, jamais. |
| B06 | Abandonner une recuperation | Le WAV de recuperation selectionne peut etre abandonne, avec confirmation et sans supprimer les autres canaux. |
| B07 | Rendre Enregistrer visible | Bouton colore, hauteur minimale 46 px, libelle explicite et etats demarrage/arret/finalisation. |
| B08 | Journal en cours de traitement | Phases, pourcentage et segments sont visibles pendant le job ; les logs sont horodates et le suivi automatique peut etre decoche. |
| B09 | Filtrer l'historique | Deux cases Audios / Transcripts-documents : chaque type seul, union des deux, ou aucun. Les fichiers associes suivent aussi le filtre. |

## Suppression : regles de securite

La confirmation indique le chemin du seul fichier vise. **La suppression est
definitive et ne passe pas par la corbeille**. Le choix par defaut est Non.
Supprimer un TXT ne supprime ni l'audio ni le document associe ; supprimer l'audio
ne supprime pas son transcript. Les references des manifests sont actualisees,
et les dates d'enregistrement restent disponibles apres suppression du WAV.
Les resultats et l'historique sont rafraichis apres l'action.

Un fichier utilise par un job actif ou en attente ne peut pas etre supprime.
Un audio importe ou externe peut etre consulte dans l'historique mais ne peut pas
etre supprime par l'application. Seuls les WAV appartenant au catalogue de capture
peuvent etre supprimes. Les liens symboliques et les chemins hors dossiers autorises
sont refuses. Le catalogue verifie aussi taille et date de modification avant suppression.
Aucune suppression recursive de dossier n'est utilisee.

Les anciens WAV portant le nom UUID produit par l'application sont reconnus
uniquement dans l'ancien dossier utilisateur `results`. Les fichiers d'un dossier
personnalise ne sont jamais declares appartenir a l'application sur leur seul nom.
Les nouveaux WAV sont catalogues explicitement a la fin de la capture/recuperation.

## Noms et exports

Le nom saisi est repris dans `transcription_<nom>.txt` et `resultats_<nom>.zip`.
Les fichiers generiques anciens sont proposes sous le nom de leur resultat lors
 d'un Enregistrer sous. Pour les lots, les suffixes individuels existants sont conserves.

Un nouvel enregistrement nomme produit `audio_<nom>_<identifiant>.wav` ; une collision
ne remplace jamais un autre WAV. Le nom audio est fige au demarrage de la capture.
Le nom du job est fige au lancement de la transcription. Sans nom personnalise,
le comportement de nommage automatique existant reste disponible.

## Parametres et compatibilite des donnees

La configuration est dans `<dossier utilisateur>/config/storage.json` et le catalogue
audio dans `config/audio-library.json`. Les TXT et WAV ne sont pas dupliques dans le
catalogue et aucun contenu de transcript ni cle API n'y est ajoute.

Les quatre dossiers sont validates dans un worker : chemins absolus, creation si
necessaire et verification d'ecriture. Le dossier temporaire est interdit comme
stockage configure. Un dossier intermediaire ne peut pas etre identique a son dossier
final. La sauvegarde de configuration est atomique ; une configuration illisible
n'autorise pas de nettoyage destructif.

Le **stockage intermediaire** recoit les ecritures pendant la capture et la transcription.
Par defaut il est local, sous `<dossier utilisateur>/work/audio` et
`<dossier utilisateur>/work/transcripts`. Le **stockage final** ne recoit qu'un fichier
termine : un WAV apres finalisation de la capture, ou un TXT/document apres la fin du
job. Un dossier OneDrive/SharePoint peut donc etre utilise comme destination finale sans
recevoir les remplacements atomiques repetes des transcriptions partielles.

Si la publication finale echoue, le job reste termine et le resultat est conserve dans
le stockage intermediaire avec un message explicite. La copie finale ne transforme pas
un traitement Whisper reussi en echec.

**Les changements de dossiers prennent effet au prochain lancement.** Aucun fichier
existant n'est deplace automatiquement. Les anciens dossiers finaux et intermediaires
sont memorises pour que les resultats precedents restent consultables et exportables.
La configuration, les logs et le temporaire conservent leur emplacement utilisateur.
La conservation audio, elle, s'applique des la sauvegarde.

L'historique propose deux modes :

- automatique : dossiers finaux et intermediaires actuels, anciens dossiers et ancien `results` ;
- explicite : uniquement les dossiers choisis, parcourus sans suivre les liens symboliques.

Les TXT autonomes des dossiers choisis sont aussi visibles. Faute de metadonnees de
capture, leur date de fichier est indiquee comme estimee : elle n'est pas presentee
comme une date d'enregistrement certaine. La recherche lit les textes par blocs,
sans les charger tous en memoire ni les envoyer a un service externe.

## Conservation audio

Le reglage initial est **Jamais (conserver les audios)**. Activer une autre regle
affiche un avertissement : des WAV deja presents peuvent etre eligibles immediatement.

| Choix | Regle |
| --- | --- |
| Des qu'ils sont traites | Job entier termine avec succes, fichier traite avec succes et TXT non vide encore present. |
| 1 / 3 jours | Age depuis la fin de l'enregistrement >= 1 / 3 jours. |
| 1 semaine | 7 jours. |
| 1 mois / 3 mois | 30 / 90 jours, indique explicitement dans l'interface. |
| Jamais | Aucune suppression automatique. |

Les politiques d'age s'appliquent meme aux audios non encore transcrits : ce choix
est indique dans Parametres. Le mode apres traitement, lui, conserve les audios en
cas d'echec, de resultat partiel ou d'annulation. Dans tous les modes, un autre job
actif utilisant le meme audio le protege.

Le controle s'execute apres un job, au chargement initial et toutes les 60 secondes
pendant que l'application est ouverte. Il ne s'execute pas lorsque l'application est
fermee ; les fichiers devenus eligibles sont controles au lancement suivant.
La soumission, la suppression et le nettoyage utilisent le meme ordre de verrous.
Les journaux WAV de recuperation sont exclus de toute retention automatique.

## Recuperation, journal et filtres

Abandonner agit sur le journal WAV selectionne, pas sur tous les canaux d'une session.
Un WAV utilise par une capture ou une finalisation est refuse. La capture et la
transcription ne peuvent pas etre lancees pendant une operation de recuperation/abandon.

Le Journal utilise le suivi de job existant (cycle cible de 400 ms) ; les messages
additionnels de progression sont limites pour ne pas saturer la vue. Les changements
de phase et la fin de fichier sont toujours journalises. Le contenu du transcript
n'est pas journalise. Il ne s'agit pas d'une reconnaissance vocale mot-a-mot en direct.

Les cases Historique filtrent les fichiers, pas uniquement la couleur des cartes.
Une entree associee audio + transcript apparait une seule fois pour ce resultat.
Une recherche ou un changement de filtre pendant un chargement est rejoue ; une
reponse obsolete ne remplace pas la selection courante. Plusieurs cartes dans une
heure restent accessibles, au lieu d'etre coupees dans la cellule.

## Validation

Voir `docs/validation/bibliotheque-desktop.md`, `tests/test_library_storage.py`,
`tests/test_library_lifecycle.py` et `tests/test_library_ui.py`.
Une validation sur le poste Windows avec une capture reelle et le build EXE reste
necessaire avant diffusion, meme si les tests avec backends factices sont verts.
