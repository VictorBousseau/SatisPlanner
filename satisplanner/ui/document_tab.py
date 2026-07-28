"""One open factory, and everything that belongs to it alone.

A tab owns three things: the document, the scene mirroring it, and the view onto
that scene. They are created together and thrown away together, which is what makes
"open six factories" mean six independent editing sessions rather than one document
the window keeps swapping underneath.

The view is per tab rather than shared, and that is the whole point rather than an
implementation detail: zoom, framing and selection live in the view and in the
scene, so coming back to a tab finds the factory exactly where it was left. A single
shared view would have to remember and restore all three by hand, and would get it
wrong the first time somebody switched tabs mid-drag.

The three **panels** are not here. They are shared by the window and rebound on
every tab change -- see :meth:`satisplanner.ui.main_window.MainWindow._activate` --
because three panels per open factory is three times the work on every edit, for two
of which nobody is looking.
"""

import logging
from collections.abc import Sequence
from typing import Final

from PySide6.QtWidgets import QVBoxLayout, QWidget

from satisplanner.core.models import GameData
from satisplanner.ui.canvas import FactoryScene, FactoryView
from satisplanner.ui.catalogue import PaletteEntry
from satisplanner.ui.document import FactoryDocument
from satisplanner.ui.icon_provider import IconProvider

logger = logging.getLogger(__name__)

# Same mark as the window title, for the same reason: a tab bar of file names says
# nothing about which of them would lose work if the window closed now.
MODIFIED_MARK: Final = " •"
# A tab is too narrow for the window title's spelled-out "OUVERTURE PARTIELLE", so
# the sentence moves to the tooltip and the tab keeps a sign that something is off.
PARTIAL_MARK: Final = "⚠ "


class DocumentTab(QWidget):
    """One factory being edited: its document, its scene and its view."""

    def __init__(
        self,
        game_data: GameData,
        icons: IconProvider,
        entries: Sequence[PaletteEntry] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = FactoryDocument(game_data, self)
        self.scene = FactoryScene(self.document, icons, entries, self)
        self.view = FactoryView(self.scene, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

    # -------------------------------------------------------------------- label

    def title(self, name: str | None = None) -> str:
        """What the tab bar shows: the file name, and what state it is in.

        ``name`` replaces the file name for a tab that has none to show -- a module
        opened from the library is worth naming after the module rather than
        leaving three of them reading "Usine sans titre".
        """
        mark = MODIFIED_MARK if self.document.is_modified else ""
        partial = PARTIAL_MARK if self.document.is_partial else ""
        shown = (
            name if name is not None and self.document.path is None else self.document.display_name
        )
        return f"{partial}{shown}{mark}"

    def tooltip(self) -> str:
        """Where the file is, and the warning the tab has no room for."""
        lines = [str(self.document.path) if self.document.path else "Jamais enregistrée"]
        if self.document.is_partial:
            lines.append(self.document.partial_description())
        return "\n\n".join(lines)

    # -------------------------------------------------------------------- state

    @property
    def is_pristine(self) -> bool:
        """True for a blank tab nobody has touched.

        Opening a file into such a tab rather than beside it is what stops the
        window filling up with the empty document it started with.
        """
        return (
            self.document.path is None
            and not self.document.is_modified
            and not self.document.graph.nodes
        )

    def dispose(self) -> None:
        """Let go of the graphics items while the scene is still alive.

        A window keeps its tabs until it closes and never needs this; closing one
        tab out of six does, and so does a test that opens a dozen.
        """
        self.scene.dispose()
