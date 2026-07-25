# Mentions légales et attributions

Trois choses différentes se trouvent au même endroit dans ce dépôt. Elles n'ont pas le
même propriétaire et pas la même licence.

## 1. Le code de SatisPlanner

Tout ce qui est versionné dans `satisplanner/`, `tests/`, `tools/`, `main.py`,
`build_exe.ps1` et la documentation : écrit pour ce projet, sous **licence MIT** (voir
`LICENSE`). L'icône de l'application, `satisplanner/resources/satisplanner.ico`, en fait
partie : elle est dessinée par `tools/make_app_icon.py` et ne reprend aucun élément
graphique du jeu.

## 2. Les données et les assets de Satisfactory

**Satisfactory est la propriété de Coffee Stain Studios.** Le jeu, son nom, ses logos,
ses données et ses icônes ne sont pas couverts par la licence ci-dessus et
n'appartiennent pas à ce projet.

- **Les icônes** (`satisplanner/resources/icons/`) sont des assets extraits du jeu par
  l'utilisateur. Elles sont **ignorées par git, jamais versionnées, jamais
  redistribuées**. La variante d'exe produite par `build_exe.ps1 -NoAssets` n'en contient
  aucune ; c'est cette variante-là qui peut être partagée. L'application est **entièrement
  fonctionnelle sans elles** : les classes sans fichier sont dessinées par
  `ui/icon_provider.py`, ce qui est le fonctionnement nominal et non un mode dégradé.
  La procédure d'extraction est décrite dans le README.
- **La base de données** (`satisplanner/resources/game_1.2.sqlite`) est dérivée des
  fichiers de documentation livrés avec le jeu (`CommunityResources/Docs`). Elle est
  versionnée parce qu'elle est nécessaire au fonctionnement de l'outil, mais les valeurs
  qu'elle contient — recettes, débits, libellés français — restent la propriété de Coffee
  Stain Studios. Elle est régénérable par quiconque possède le jeu, avec
  `python -m satisplanner.data.build`.

Ce projet n'est **ni un mod, ni un lecteur de sauvegarde, ni un produit officiel**, et
n'est affilié à Coffee Stain Studios d'aucune manière.

## 3. Les composants tiers embarqués dans l'exe

| Composant   | Licence  | Rôle                        |
|-------------|----------|-----------------------------|
| Qt / PySide6 | **LGPL v3** | Interface graphique       |
| pydantic     | MIT      | Modèles et validation       |
| CPython      | PSF      | Interpréteur                |
| PyInstaller  | GPL v2 avec exception d'exécution | Empaquetage |

**Sur la LGPL de Qt** : distribuer un exécutable qui embarque les bibliothèques Qt
implique des obligations — mentionner Qt et sa licence, ne pas en modifier les
bibliothèques, et permettre au destinataire de les remplacer par une autre version. Le
build `--onedir` les satisfait naturellement, puisque les DLL de Qt sont des fichiers
distincts, remplaçables, à côté de l'exécutable ; un build `--onefile` rendrait ce point
nettement moins net. C'est une raison de plus, en sus du temps de démarrage, de s'en
tenir à `--onedir`.

L'exception d'exécution de PyInstaller couvre explicitement les exécutables produits :
empaqueter avec PyInstaller ne contamine pas la licence de l'application.
