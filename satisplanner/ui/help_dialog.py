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
        "Glisser une entree de la palette sur le canvas",
        "pose le noeud a l'endroit lache, aligne sur la grille",
    ),
    ("Double-clic dans la palette", "pose le noeud au centre de la vue"),
    (
        "Glisser d'un port de sortie vers un port d'entree",
        "tire une ligne. Le trait est vert si elle peut exister, rouge sinon, "
        "avec la raison en infobulle pendant le tirage",
    ),
    (
        "Lacher n'importe ou sur un tampon vide",
        "le raccorde : un tampon sans contenu accepte le premier item qui arrive",
    ),
    ("Glisser un noeud", "le deplace. Un glissement continu vaut une seule annulation"),
    ("Glisser sur le fond", "selection rectangulaire"),
    ("Clic milieu maintenu", "deplace la vue"),
    ("Molette", "zoom avant et arriere autour du curseur"),
    (
        "Clic droit sur un noeud",
        "ajuster aux intrants, nombre de machines, contenu du tampon, supprimer",
    ),
    (
        "Clic droit sur une ligne",
        "changer de tier, passer au tier suffisant quand elle sature, supprimer",
    ),
    (
        "Clic sur une ligne du tableau",
        "selectionne le noeud sur le canvas, et reciproquement",
    ),
    (
        "Clic sur un diagnostic",
        "selectionne et centre le noeud ou la ligne concernee",
    ),
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
    return f"""
    <style>
      body {{ font-size: 10pt; }}
      h2 {{ margin-top: 14px; margin-bottom: 4px; }}
      td {{ padding: 3px 10px 3px 0; vertical-align: top; }}
      td.key {{ color: {theme.ACCENT}; white-space: nowrap; }}
      p.note {{ color: {theme.TEXT_MUTED}; }}
    </style>
    <h2>Gestes du canvas</h2>
    <table>{gestures}</table>
    <h2>Raccourcis</h2>
    <table>{keys}</table>
    <p class="note">La touche Suppr efface la selection, noeuds et lignes confondus.
    Tout passe par la pile d'annulation, deplacements compris.</p>
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
