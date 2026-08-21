"""The "how do I do that" screen: canvas gestures, then every keyboard shortcut.

The gestures are written out by hand because nothing in the code knows that holding
the middle button pans the view. The shortcuts are **not**: they are read off the
window's own actions, so a shortcut that is changed, added or removed changes this
page with it. A help page maintained by hand is a help page that is wrong within a
month, and it is worse than no page at all -- it is trusted.
"""

import logging
from collections.abc import Iterable, Sequence
from typing import Final

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from satisplanner.ui import theme

logger = logging.getLogger(__name__)

# (gesture, what it does). The canvas is the part of this application nobody can
# guess, so it comes first and gets the room.
GESTURES: Final[tuple[tuple[str, str], ...]] = (
    (
        "Glisser une entrée de la palette sur le canvas",
        "pose le nœud à l'endroit lâché, aligné sur la grille",
    ),
    (
        "Double-clic dans la palette",
        "ouvre la fiche de l'objet : recettes, machines, débits, coût en minerai",
    ),
    (
        "Dans une fiche, clic sur un ingrédient",
        "ouvre sa fiche. Alt+Gauche et Alt+Droite reviennent en arrière et en avant",
    ),
    (
        "Dans une fiche, [poser sur le canvas]",
        "pose cette recette au centre de la vue",
    ),
    (
        "Glisser d'un port de sortie vers un port d'entrée",
        "tire une ligne. Le trait est vert si elle peut exister, rouge sinon, "
        "avec la raison en infobulle pendant le tirage",
    ),
    (
        "Lâcher n'importe où sur un tampon vide",
        "le raccorde : un tampon sans contenu accepte le premier item qui arrive",
    ),
    ("Glisser un nœud", "le déplace. Un glissement continu vaut une seule annulation"),
    ("Glisser sur le fond", "sélection rectangulaire"),
    ("Clic milieu maintenu", "déplace la vue"),
    ("Molette", "zoom avant et arrière autour du curseur"),
    (
        "Clic droit sur un nœud",
        "fiche de l'objet, ajuster aux intrants, nombre de machines, cadence, "
        "pureté du gisement, extracteur, carburant du générateur, contenu du "
        "tampon, supprimer",
    ),
    (
        "Entrée dans la palette",
        "pose l'entrée surlignée au centre de la vue",
    ),
    (
        "Double-clic sur une valeur d'un nœud",
        "l'édite sur place : nombre de machines, cadence, pureté, extracteur, "
        "carburant, débit d'un apport, stock d'un tampon. Entrée valide, Échap "
        "annule, une valeur hors domaine est refusée sans être effacée",
    ),
    (
        "Double-clic sur une ligne",
        "change son tier, dans la même liste que le menu contextuel",
    ),
    (
        "Copier une sélection",
        "emporte les nœuds et les lignes qui les relient entre eux ; une ligne qui "
        "sortait de la sélection n'a plus de quoi s'accrocher et n'est pas copiée",
    ),
    (
        "Affichage ▸ Machines déployées",
        "dessine une vignette par machine bâtie sur chaque nœud. Clic droit sur un "
        "nœud pour y déroger. Purement visuel : aucun chiffre ne change",
    ),
    (
        "Clic droit sur une ligne",
        "changer de tier, passer au tier suffisant quand elle sature, supprimer",
    ),
    (
        "Clic sur une ligne du tableau",
        "sélectionne le nœud sur le canvas, et réciproquement",
    ),
    (
        "Clic sur un diagnostic",
        "sélectionne et centre le nœud ou la ligne concernée",
    ),
    (
        "Raccords ▸ Mode simple / Mode fidèle",
        "bascule toute l'usine, en une seule annulation. Vers le fidèle, les arbres "
        "de raccords sont posés ; vers le simple, ils sont dissous. Ce qui change "
        "de débit est dit port par port",
    ),
    (
        "Générer ▸ Générer une usine depuis un objectif",
        "développe « tant de cet objet par minute » en une usine entière, dans un "
        "onglet neuf. Une recette imposée par objet est retenue d'une fois sur "
        "l'autre ; le rapport dit ce qu'il reste à régler",
    ),
    (
        "Palette, section Raccords",
        "un répartiteur et un groupeur se posent comme n'importe quel autre nœud, "
        "et leur mode se règle par clic droit ou par la colonne du tableau",
    ),
    (
        "Modules ▸ Enregistrer la sélection comme module",
        "range le morceau sélectionné dans la bibliothèque, avec ses débits d'entrée "
        "et de sortie calculés sur le module seul. Depuis un onglet ouvert sur un "
        "module, aucune sélection n'est nécessaire : c'est le module entier",
    ),
    (
        "Modules ▸ Bibliothèque",
        "insérer au centre de la vue, ouvrir dans un onglet pour modifier, démarrer "
        "une usine depuis un module, renommer, décrire, supprimer",
    ),
)


# Rules a user cannot deduce from the interface and will otherwise assume wrongly.
# Each one is a modelling choice, not a limitation to be worked around.
MODELLING_NOTES: Final[tuple[str, ...]] = (
    "La pureté d'un gisement s'applique à <b>tous</b> les extracteurs de ce nœud : "
    "un nœud est un gisement. Deux gisements de puretés différentes, ce sont deux "
    "nœuds, et c'est la seule façon de les représenter. Un <b>geyser</b> se lit de "
    "la même façon, avec des générateurs géothermiques à la place des extracteurs. "
    "<b>La règle vaut pour un gisement, pas pour un puits de ressource</b> : un "
    "pressuriseur ouvre plusieurs satellites d'un coup et rien ne dit qu'ils se "
    "ressemblent, donc là, la pureté "
    "est <b>par satellite</b> et le nœud porte un décompte — « 1 impur · 2 normaux · "
    "3 purs » — au lieu d'une valeur. C'est pour garder la règle du gisement entière "
    "que le puits est un type de nœud à part.",
    "<b>Un puits de ressource est deux bâtiments</b>, et un seul des deux consomme. "
    "Le pressuriseur coûte <b>150 MW</b>, un seul quel que soit le nombre de "
    "satellites ; les satellites, eux, ne consomment rien. Un puits vide coûte donc "
    "déjà ses 150 MW, ce qui est signalé. Chaque satellite donne 30, 60 ou 120 m³/min "
    "selon sa pureté et <b>porte sa propre canalisation</b> : trois satellites font "
    "trois ports de sortie. Surcadencer agit sur le puits entier — le débit suit, et "
    "la facture suit l'exposant du pressuriseur.",
    "La cadence multiplie le débit à l'identique et l'électricité en loi de "
    "puissance : à 250 %, une machine produit 2,5 fois plus et consomme environ "
    "3,36 fois plus. L'exposant est le même pour toutes les machines de "
    "production, y compris les trois ci-dessous.",
    "<b>Trois machines consomment selon ce qu'elles fabriquent</b>, et non selon "
    "ce qu'elles sont : le <b>Convertisseur</b>, l'<b>Accélérateur de "
    "particules</b> et l'<b>Encodeur quantique</b>. Leur plaque est à zéro et le "
    "chiffre est porté par la <b>recette</b>. En jeu leur consommation oscille — "
    "l'Accélérateur va de 250 à 750 MW sur des diamants, de 500 à 1500 sur la "
    "matière noire ; l'Encodeur, de 0 à 2000 — et comme ce modèle est en régime "
    "permanent, c'est la <b>moyenne</b> qui est comptée, soit le milieu de "
    "l'oscillation. La fiche d'objet montre les deux bornes à côté d'elle, parce "
    "que dimensionner une centrale sur la moyenne d'un Encodeur sans savoir qu'il "
    "touche deux gigawatts serait une mauvaise surprise.",
    "<b>Le nombre de machines est ce que vous saisissez</b>, jamais ce que l'outil "
    "décide. En regard, il vous dit combien sont réellement utiles compte tenu de "
    "ce qui arrive, et l'écart entre les deux. Pour prendre le problème par "
    "l'autre bout — « je veux tant par minute » —, c'est <b>Générer une usine</b>.",
    "<b>La centrale nucléaire produit sur une ligne</b>, et c'est le seul générateur "
    "du jeu qui le fasse. Elle rend <b>50 déchets d'uranium par barre</b> — 10 par "
    "minute — et 10 déchets de plutonium par barre de plutonium ; une barre de "
    "ficsonium ne laisse rien. La règle du sous-produit s'y applique donc : "
    "<b>une centrale dont les déchets ne vont nulle part s'arrête</b>, exactement "
    "comme une raffinerie, et ne produit plus un mégawatt. Son eau d'appoint est "
    "considérable : <b>240 m³/min</b>, contre 45 pour un générateur à charbon.",
    "<b>Le générateur géothermique ne brûle rien</b> : il se pose sur un geyser, et "
    "c'est la pureté du geyser qui décide. Le jeu annonce lui-même 100, 300 ou "
    "600 MW au maximum selon le geyser ; comme ce modèle est en régime permanent, "
    "c'est la <b>moyenne</b> qui est comptée — <b>100, 200 ou 400 MW</b>. La pureté "
    "est une saisie, comme celle d'un gisement, parce que rien dans les données du "
    "jeu ne dit où sont les geysers de votre carte. Il ne se surcadence pas : le "
    "jeu le refuse, seul de tous les générateurs.",
    "<b>Un sous-produit sans issue arrête la machine entièrement.</b> Une recette "
    "qui produit deux choses doit voir partir les deux : sans quoi le nœud tombe à "
    "0 %, comme en jeu où la machine se bouche. Ce blocage se juge sur la "
    "topologie et non sur le débit — une sortie qui n'absorbe qu'une partie ne "
    "bloque pas, elle exerce une contre-pression et le taux descend.",
    "<b>La capacité d'une ligne est une contrainte, pas une remarque.</b> Un "
    "convoyeur Mk.1 alimenté à 480/min en transporte 60 et refoule le reste en "
    "amont. Le diagnostic donne les deux chiffres — porté et demandé — parce que "
    "c'est le second qui nomme le tier à installer, et il est calculé par une "
    "résolution jumelle sans plafond, pas estimé.",
    "<b>Chaque usine est en mode simple ou en mode fidèle</b>, et le réglage est "
    "dans le document, pas dans les préférences : une usine partagée s'ouvre chez "
    "le destinataire dans le mode où elle a été pensée, sinon les chiffres "
    "changeraient. Le <b>mode simple</b> — celui d'une usine neuve — laisse un port "
    "porter autant de lignes qu'on veut et partage également entre elles ; c'est le "
    "mode pour réfléchir aux débits. Le <b>mode fidèle</b> applique la règle du jeu, "
    "un port une ligne, et vous fait poser les raccords ; c'est le mode pour "
    "construire. Menu <b>Raccords</b>, et la bascule s'annule.",
    "<b>La liste de courses compte les raccords dans les deux modes, et pas de la "
    "même façon.</b> En simple ils sont <b>déduits</b> : quatre lignes sur un port "
    "en demandent deux, dans le chaînage le plus économe. En fidèle ils sont "
    "<b>comptés là où ils sont posés</b>, et une usine dessinée à la main en utilise "
    "souvent davantage — un arbre bâti pour la symétrie, un raccord resté en place "
    "après qu'un nombre de machines a baissé. Un total qui monte en basculant n'est "
    "donc pas une incohérence : c'est le dessin qui dit quelque chose que la "
    "déduction ne pouvait pas savoir.",
    "Un raccord <b>intelligent ou programmable interdit le retour au mode "
    "simple</b>, et l'application le refuse en le nommant plutôt que de l'effacer. "
    "Le filtrage et le surplus n'existent que parce que le raccord existe ; perdre "
    "un routage de sous-produit sans l'avoir voulu serait pire qu'un refus.",
    "<b>Un port porte une ligne</b> en mode fidèle, et un nœud a autant de ports qu'il a de "
    "bâtiments : huit fonderies ont huit sorties, une seule en a une. Pour en "
    "faire partir davantage, posez un répartiteur — c'est un nœud comme un autre, "
    "il partage également entre ses branches et il est compté dans la liste de "
    "courses. Une entrée et une sortie d'usine sont la frontière du modèle et pas "
    "des bâtiments : elles n'ont pas de limite.",
    "<b>Le partage est max-min, pas proportionnel.</b> Chaque branche reçoit une "
    "part égale, et ce qu'une branche saturée ne peut pas prendre est repartagé "
    "également entre les autres : 60 lingots pour deux consommateurs qui en "
    "demandent 30 et 60 donnent 30 et 30 — le petit est servi entièrement — et non "
    "20 et 40, qui feraient boiter les deux. Et un arbre réel ne donne des parts "
    "égales que lorsque le nombre de branches se ramène à des moitiés et des "
    "tiers : <b>2, 3, 4, 6 et 9 oui, 5 et 7 non</b>. C'est le jeu qui est ainsi.",
    "<b>Générer une usine</b> développe une cible — « deux cadres modulaires "
    "lourds par minute » — en machines, lignes et raccords, par la recette "
    "standard sauf là où vous en imposez une autre. Deux variantes : ratios "
    "exacts, avec des machines en nombre décimal, ou arrondi au bâtiment "
    "entier, constructible tel quel, avec un conteneur là où l'arrondi crée "
    "un surplus. Ce qui en sort est une usine ordinaire, modifiable "
    "immédiatement.",
    "Ce que le générateur ne peut pas savoir, il le dit : <b>les gisements sont "
    "posés en pureté normale avec le premier extracteur venu</b>, parce que ce "
    "qui se trouve sur votre carte n'est écrit nulle part dans les données du "
    "jeu. Réglez la pureté et l'extracteur, le nombre suivra.",
    "Un répartiteur se règle : <b>standard</b>, <b>intelligent</b> (une branche "
    "réglée) ou <b>programmable</b> (toutes). Le seul réglage qui déplace des "
    "chiffres est <b>« surplus »</b> : cette branche ne prend que ce dont les "
    "autres n'ont pas voulu, ce qui permet d'envoyer un sous-produit au "
    "recyclage jusqu'à saturation et le reste à la torchère.",
    "<b>Filtrer sur un objet ne change rien, ici.</b> Une ligne ne porte qu'un "
    "objet : filtrer une branche sur celui qu'elle transporte déjà revient à "
    "« n'importe lequel », et la filtrer sur un autre la ferme complètement — "
    "ce qui est signalé. Poser des filtres en attendant un effet sur les débits "
    "n'en produira aucun ; c'est le surplus qu'il faut. Et le jeu ne filtre que "
    "les convoyeurs : il n'existe pas de jonction de pipeline intelligente.",
    "<b>Le brûleur de biomasse n'a pas de chaîne d'alimentation, et ce n'est pas "
    "un oubli.</b> La biomasse se fabrique <b>à la main</b> dans le jeu, à partir de "
    "bois, de feuilles ou de mycélium qu'on ramasse : aucune machine ne la produit, "
    "donc aucune recette ne peut la produire ici. Alimentez-le par un <b>apport "
    "extérieur</b> au débit que vous vous engagez à tenir. La même remarque vaut "
    "pour tout ce qui descend de la biomasse — biocarburant solide, puis liquide. "
    "C'est, avec les restes extraterrestres, tout ce qui reste hors d'atteinte du "
    "catalogue : le ramassage, et rien d'autre.",
    "<b>L'azote ne sort du sol que par un puits de ressource</b> : aucune foreuse et "
    "aucune pompe ne le travaille, c'est une matière brute à l'état gazeux. Posez un "
    "puits, et l'acide nitrique, le système de refroidissement, le cadre modulaire "
    "fusionné et le carburant de fusée deviennent constructibles de bout en bout.",
    "<b>Toutes les machines du jeu sont au catalogue</b>, et les 291 recettes qu'elles "
    "fabriquent s'y posent. Ce qui reste dehors, ce sont les <b>26 recettes "
    "fabriquées à la main</b> à l'atelier d'équipement — parachute, tronçonneuse, "
    "fusil : un atelier ne sera jamais un nœud d'usine. La fiche d'un objet les "
    "montre grisées et sans bouton pour les poser, en disant pourquoi. Et un objet "
    "qu'<b>aucune recette du jeu</b> ne produit dit d'où il vient : ramassé dans le "
    "monde, ou <b>sous-produit</b> — les déchets d'uranium sortent de la centrale "
    "nucléaire, dix par minute, ils ne se ramassent pas. Une absence qui ne se nomme "
    "pas se lit comme un trou dans les données.",
    "<b>Une machine à l'arrêt consomme quand même.</b> La production suit le taux "
    "de fonctionnement, la consommation non : un nœud bloqué ou affamé paie sa "
    "facture entière, comme en jeu. Ça ne se remarquait pas à 4 MW ; ça se "
    "remarque avec un Encodeur quantique bloqué, qui est la façon la plus chère de "
    "ce catalogue de ne rien produire.",
    "Les tampons sont des puits et des sources infinis. L'application dit si les "
    "débits sont tenables et en combien de temps un stock se vide, mais ne simule "
    "pas le temps qui passe.",
    "Rien ici n'est de la géométrie : ni distance, ni élévation, ni hauteur de "
    "refoulement des pompes.",
    "Un <b>module inséré est une copie</b>. Il ne garde aucun lien avec sa "
    "définition : le modifier ensuite ne change pas le module, et modifier le module "
    "ne change pas les usines où il a déjà été inséré. Les débits affichés sur un "
    "module sont ceux du module <b>seul</b>, résolu à l'enregistrement avec ses "
    "entrées servies et ses sorties écoulées ; inséré dans une usine qui l'affame, "
    "il en fera moins. C'est une étiquette, pas une promesse.",
    "Les <b>matériaux de construction</b> donnent le coût exact des machines, "
    "extracteurs, générateurs, conteneurs et répartiteurs, lu dans les recettes du "
    "jeu et agrégé par objet. Les <b>convoyeurs et les tuyaux n'y sont pas "
    "chiffrés</b> : leur coût se paie à la longueur, et l'outil ne connaît aucune "
    "distance. Le blanc est volontaire — une estimation tirée d'une longueur moyenne "
    "serait un chiffre inventé posé au milieu de chiffres exacts, et un blanc qui se "
    "voit vaut mieux qu'un total qu'on croit complet.",
    "L'électricité est un <b>compteur, pas une contrainte</b> : consommation et "
    "production sont affichées côte à côte, et un déficit ne bride aucun débit. "
    "En jeu, manquer de courant ne ralentit pas l'usine, cela disjoncte tout le "
    "réseau jusqu'à intervention manuelle ; afficher tout à zéro n'apprendrait "
    "rien et un bridage partiel serait une invention. Les générateurs, eux, "
    "tournent à 100 % : leur surcadençage suit un exposant différent de celui des "
    "machines et fera l'objet d'un travail à part.",
    "La consommation compte les machines <b>à l'arrêt</b> : elles sont construites et "
    "branchées. C'est un dimensionnement au pire cas, pas une mesure du jeu — les "
    "fichiers ne déclarent aucune consommation de veille, seulement une consommation "
    "nominale, et inventer une valeur réduite serait pire que compter au maximum. La "
    "production, elle, ne compte que ce qui brûle réellement : un générateur sans "
    "carburant ne produit rien.",
)


def shortcut_rows(actions: Iterable[QAction]) -> list[tuple[str, str]]:
    """``(label, keys)`` for every action that carries a shortcut, in order.

    **All** of an action's shortcuts, not merely its first: "Nouvel onglet" answers
    to Ctrl+N and to Ctrl+T, and a help page that mentioned one of them would be
    read as saying the other does not work.

    Ampersands are stripped: they are menu mnemonics, not part of the name, and
    "&Fichier" in a help page looks like a typo.
    """
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for action in actions:
        keys = " ou ".join(
            sequence.toString(QKeySequence.SequenceFormat.NativeText)
            for sequence in action.shortcuts()
        )
        label = action.text().replace("&", "").removesuffix("...")
        if not keys or not label or keys in seen:
            continue
        seen.add(keys)
        rows.append((label, keys))
    return rows


def help_html(shortcuts: Sequence[tuple[str, str]]) -> str:
    """The page itself, as HTML, so it can be checked without opening a window."""
    gestures = "".join(
        f"<tr><td class='key'>{gesture}</td><td>{effect}</td></tr>" for gesture, effect in GESTURES
    )
    keys = "".join(
        f"<tr><td class='key'>{keystroke}</td><td>{label}</td></tr>"
        for label, keystroke in shortcuts
    )
    notes = "".join(f"<li>{note}</li>" for note in MODELLING_NOTES)
    return f"""
    <style>
      body {{ font-size: 10pt; }}
      h2 {{ margin-top: 14px; margin-bottom: 4px; }}
      td {{ padding: 3px 10px 3px 0; vertical-align: top; }}
      td.key {{ color: {theme.ACCENT}; white-space: nowrap; }}
      p.note {{ color: {theme.TEXT_MUTED}; }}
      li {{ margin-bottom: 6px; }}
    </style>
    <h2>Gestes du canvas</h2>
    <table>{gestures}</table>
    <h2>Raccourcis</h2>
    <table>{keys}</table>
    <p class="note">La touche Suppr efface la sélection, nœuds et lignes confondus.
    Tout passe par la pile d'annulation, déplacements compris.</p>
    <h2>Ce que l'outil modélise, et comment</h2>
    <ul>{notes}</ul>
    """


class HelpDialog(QDialog):
    """A read-only page. No settings, no state, nothing to accept."""

    def __init__(self, shortcuts: Sequence[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Gestes et raccourcis")
        self.resize(680, 640)

        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(False)
        self.browser.setHtml(help_html(shortcuts))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.browser)
        layout.addWidget(buttons)
