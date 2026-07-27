"""``SatisPlanner.exe --self-check``: the packaging checklist, run by the exe itself.

The test suite proves the code works. It does not prove that *this executable* works,
because the two differ in exactly the way that breaks: a database that was not
embedded, a Qt plugin that was pruned, a path that resolved to somewhere inside an
archive. Those defects only ever show up on a machine that has nothing installed --
which is, by construction, a machine that cannot run the test suite.

So the checklist ships inside the application. It drives the real window: it places
nodes through the palette's own entries, connects them through the scene, saves a real
``.sfp``, reopens it, round-trips a share code and writes a PNG and a PDF. Whoever
receives the executable can run it and send the result back.

Everything it writes goes into ``%LOCALAPPDATA%\\SatisPlanner\\verification`` and is
left there on purpose: a report saying the PDF was written is worth less than the PDF.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QMessageBox

from satisplanner import __version__, paths
from satisplanner.data import db, factory_file
from satisplanner.ui.catalogue import EntryKind
from satisplanner.ui.main_window import MainWindow

logger = logging.getLogger(__name__)

VERIFICATION_SUBPATH: Final = "verification"

# A chain small enough to be instant and real enough to be worth solving: an iron
# deposit, a smelter, an exit.
DEPOSIT: Final = "Desc_OreIron_C"
RECIPE: Final = "Recipe_IngotIron_C"
PRODUCT: Final = "Desc_IronIngot_C"


@dataclass
class Check:
    """One line of the checklist."""

    title: str
    passed: bool
    detail: str

    def __str__(self) -> str:
        return f"[{'OK   ' if self.passed else 'ÉCHEC'}] {self.title} — {self.detail}"


class SelfCheck:
    """Runs the checklist against a real window and collects what happened."""

    def __init__(self, window: MainWindow, directory: Path | None = None) -> None:
        self.window = window
        self.directory = directory or (paths.app_data_directory() / VERIFICATION_SUBPATH)
        self.checks: list[Check] = []

    # ------------------------------------------------------------------ runner

    def run(self) -> list[Check]:
        paths.ensure_directory(self.directory)
        self._step("Base de recettes chargée", self._catalogue)
        self._step("Palette peuplée", self._palette)
        self._step("Icônes", self._icons)
        self._step("Interface en français", self._french)
        self._step("Conception, enregistrement, réouverture", self._round_trip)
        self._step("Code de partage", self._share_code)
        self._step("Export PNG", self._png)
        self._step("Export PDF", self._pdf)
        return self.checks

    def _step(self, title: str, action: Callable[[], str]) -> None:
        """Run one check, turning any failure into a line rather than a traceback."""
        try:
            self.checks.append(Check(title, passed=True, detail=action()))
        except Exception as exc:
            logger.exception("vérification en échec : %s", title)
            self.checks.append(Check(title, passed=False, detail=f"{type(exc).__name__} : {exc}"))

    # ------------------------------------------------------------------ checks

    def _catalogue(self) -> str:
        game_data = self.window.game_data
        assert game_data.recipes, "aucune recette : la base n'a pas été embarquée"
        assert game_data.items, "aucun item"
        return (
            f"{len(game_data.recipes)} recettes, {len(game_data.items)} items, "
            f"données de jeu {db.GAME_VERSION}"
        )

    def _palette(self) -> str:
        entries = self.window.palette_widget.visible_entries()
        assert entries, "la palette est vide"
        return f"{len(entries)} entrée(s) affichée(s) sur {len(self.window.entries)}"

    def _icons(self) -> str:
        """Both backends: files if there are any, and the drawing that never fails."""
        provider = self.window.icons
        drawn = provider.generate("Desc_Verification_C", "Vérification")
        assert not drawn.isNull(), "le repli généré ne dessine rien"
        indexed = len(provider.index)
        if indexed:
            return f"{indexed} fichier(s) indexé(s), repli généré opérationnel"
        return "aucun fichier d'icône (variante publiable) : tout est dessiné, ce qui est normal"

    def _french(self) -> str:
        """Qt's own strings, which live in a file the packaging can quietly drop.

        Checked on a real message box rather than on the loader's return value: what
        matters is the word on the button, not whether a catalogue was found.
        """
        box = QMessageBox()
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
        labels = {button.text().replace("&", "") for button in box.buttons()}
        assert "Annuler" in labels, f"boutons standard non traduits : {sorted(labels)}"
        return f"boutons standard traduits ({', '.join(sorted(labels))})"

    def _round_trip(self) -> str:
        chain = self._build_chain()
        report = self.window.document.solve_now()
        assert report.final_outputs, "l'usine ne produit rien"

        path = self.directory / "verification.sfp"
        assert self.window._write(path), "l'enregistrement a échoué"
        assert path.is_file()

        self.window.document.reset()
        assert not self.window.document.graph.nodes
        assert self.window.open_file(path), "la réouverture a échoué"
        reopened = {node.id for node in self.window.document.graph.nodes}
        assert reopened == chain, f"nœuds perdus : {chain - reopened}"

        outputs = self.window.document.solve_now().final_outputs
        assert outputs == report.final_outputs, "les débits ont changé en passant par le disque"
        return f"{len(chain)} nœuds, {path.name} ({path.stat().st_size} octets), débits identiques"

    def _share_code(self) -> str:
        code = self.window.document.share_code()
        assert code.startswith("SFP1:"), code[:16]
        loaded = factory_file.decode_share_code(code)
        assert {node.id for node in loaded.graph.nodes} == {
            node.id for node in self.window.document.graph.nodes
        }
        (self.directory / "code_de_partage.txt").write_text(code, encoding="utf-8")
        return f"{len(code)} caractères, relu sans perte"

    def _png(self) -> str:
        path = self.directory / "verification.png"
        assert self.window.export_png(path), "l'export PNG a échoué"
        return f"{path.name} ({path.stat().st_size} octets)"

    def _pdf(self) -> str:
        path = self.directory / "verification.pdf"
        assert self.window.export_pdf(path, include_totals=True), "l'export PDF a échoué"
        return f"{path.name} ({path.stat().st_size} octets)"

    # ------------------------------------------------------------------ helper

    def _build_chain(self) -> set[str]:
        """Place and wire three nodes the way the palette and the canvas do it."""
        self.window.document.reset()
        mine = self._place(EntryKind.EXTRACTOR, DEPOSIT, 0.0)
        smelter = self._place(EntryKind.RECIPE, RECIPE, 400.0)
        exit_node = self._place(EntryKind.OUTPUT, PRODUCT, 800.0)
        for source, target, item in ((mine, smelter, DEPOSIT), (smelter, exit_node, PRODUCT)):
            problem = self.window.scene.connect_nodes(source, target, item)
            assert problem is None, problem
        return {mine, smelter, exit_node}

    def _place(self, kind: EntryKind, class_name: str, x: float) -> str:
        entry = next(
            item
            for item in self.window.entries
            if item.kind is kind and item.class_name == class_name
        )
        before = {node.id for node in self.window.document.graph.nodes}
        self.window.scene.add_entry(entry, QPointF(x, 0.0))
        (created,) = {node.id for node in self.window.document.graph.nodes} - before
        return created


def report_text(checks: list[Check], directory: Path, elapsed: float) -> str:
    """The whole checklist as one block of text, for the log and for the box."""
    lines = [
        f"SatisPlanner {__version__} — vérification de l'exécutable",
        f"Données de jeu : Satisfactory {db.GAME_VERSION}",
        f"Exécution {'depuis les sources' if not paths.is_frozen() else 'empaquetée'}"
        f" — ressources : {paths.resource_directory()}",
        "",
        *[str(check) for check in checks],
        "",
        f"{sum(check.passed for check in checks)} / {len(checks)} vérifications réussies"
        f" en {elapsed:.2f} s",
        f"Fichiers produits : {directory}",
    ]
    return "\n".join(lines)


REPORT_FILENAME: Final = "rapport.txt"


def run_self_check(window: MainWindow, directory: Path | None = None) -> tuple[bool, str]:
    """Run everything and return ``(all passed, the report)``.

    The report is also written next to the files it produced, so it can be sent back
    from a machine where reading a log is not something anyone wants to be asked.
    """
    started = time.perf_counter()
    checker = SelfCheck(window, directory)
    checks = checker.run()
    text = report_text(checks, checker.directory, time.perf_counter() - started)
    logger.info("vérification de l'exécutable :\n%s", text)
    try:
        (checker.directory / REPORT_FILENAME).write_text(text, encoding="utf-8")
    except OSError:
        logger.warning("rapport de vérification non écrit dans %s", checker.directory)
    return all(check.passed for check in checks), text
