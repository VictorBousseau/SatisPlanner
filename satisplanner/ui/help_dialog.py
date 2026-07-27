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
)


# Rules a user cannot deduce from the interface and will otherwise assume wrongly.
# Each one is a modelling choice, not a limitation to be worked around.
MODELLING_NOTES: Final[tuple[str, ...]] = (
    "La pureté d'un gisement s'applique à <b>tous</b> les extracteurs de ce nœud : "
    "un nœud est un gisement. Deux gisements de puretés différentes, ce sont deux "
    "nœuds, et c'est la seule façon de les représenter.",
    "La cadence multiplie le débit à l'identique et l'électricité en loi de "
    "puissance : à 250 %, une machine produit 2,5 fois plus et consomme environ "
    "3,36 fois plus.",
    "Les répartiteurs, groupeurs et jonctions ne sont jamais des nœuds. Ils sont "
    "déduits des lignes qui partagent un nœud et comptés dans la liste de courses.",
    "Les tampons sont des puits et des sources infinis. L'application dit si les "
    "débits sont tenables et en combien de temps un stock se vide, mais ne simule "
    "pas le temps qui passe.",
    "Rien ici n'est de la géométrie : ni distance, ni élévation, ni hauteur de "
    "refoulement des pompes.",
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

    Ampersands are stripped: they are menu mnemonics, not part of the name, and
    "&Fichier" in a help page looks like a typo.
    """
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for action in actions:
        keys = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
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
