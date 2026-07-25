"""The assembled window: it builds, it reads the shipped database, and it responds.

This is the only test that goes through the real ``satisplanner/resources/*.sqlite``
rather than the fixture, which makes it the guard on the application being autonomous:
if the packaged database ever stops being loadable, this fails.
"""

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QWheelEvent
from pytestqt.qtbot import QtBot

from satisplanner.core.models import GameData
from satisplanner.ui.canvas import MAX_SCALE, MIN_SCALE
from satisplanner.ui.catalogue import EntryKind, PaletteEntry
from satisplanner.ui.main_window import MainWindow
from satisplanner.ui.palette import ENTRY_MIME_TYPE, encode_entry


@pytest.fixture
def window(qtbot: QtBot, game_data: GameData) -> Iterator[MainWindow]:
    built = MainWindow(game_data)
    qtbot.addWidget(built)
    yield built
    built.scene.dispose()


def entry_of(window: MainWindow, kind: EntryKind, class_name: str) -> PaletteEntry:
    return next(e for e in window.entries if e.kind is kind and e.class_name == class_name)


def _drop(window: MainWindow, payload: QMimeData, at: QPointF) -> None:
    window.view.dropEvent(
        QDropEvent(
            at,
            Qt.DropAction.CopyAction,
            payload,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def test_main_window_builds_and_shows(qtbot: QtBot) -> None:
    """No argument: the catalogue comes from the database inside the package."""
    built = MainWindow()
    qtbot.addWidget(built)
    with qtbot.waitExposed(built):
        built.show()

    assert built.isVisible()
    assert "SatisPlanner" in built.windowTitle()
    assert built.centralWidget() is built.view
    assert built.game_data.recipes, "le catalogue embarque doit etre lisible sans le jeu"
    built.scene.dispose()


def test_the_phase_4_docks_are_reserved_and_empty(window: MainWindow) -> None:
    titles = [dock.windowTitle() for dock in window.reserved_docks]
    assert titles == ["Tableau", "Totaux", "Diagnostics"]
    assert window.palette_dock.widget() is window.palette_widget


def test_double_clicking_the_palette_drops_a_node_in_the_view(window: MainWindow) -> None:
    window.palette_widget.entryActivated.emit(
        entry_of(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    )
    assert len(window.document.graph.nodes) == 1


def test_a_drop_on_the_canvas_creates_the_node_it_carries(window: MainWindow) -> None:
    payload = QMimeData()
    payload.setData(
        ENTRY_MIME_TYPE, encode_entry(entry_of(window, EntryKind.EXTRACTOR, "Desc_OreIron_C"))
    )
    window.view.dragEnterEvent(
        QDragEnterEvent(
            QPoint(60, 60),
            Qt.DropAction.CopyAction,
            payload,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    _drop(window, payload, QPointF(60, 60))
    assert [node.kind.value for node in window.document.graph.nodes] == ["resource"]


def test_a_drop_of_something_else_is_ignored(window: MainWindow) -> None:
    payload = QMimeData()
    payload.setText("du texte quelconque")
    _drop(window, payload, QPointF(10, 10))
    assert window.document.graph.nodes == []


def test_the_wheel_zooms_within_bounds(window: MainWindow) -> None:
    def wheel(delta: int) -> None:
        window.view.wheelEvent(
            QWheelEvent(
                QPointF(100, 100),
                QPointF(100, 100),
                QPoint(0, 0),
                QPoint(0, delta),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
        )

    for _ in range(40):
        wheel(120)
    assert window.view.transform().m11() <= MAX_SCALE
    for _ in range(80):
        wheel(-120)
    assert window.view.transform().m11() >= MIN_SCALE

    window.view.reset_zoom()
    assert window.view.transform().m11() == pytest.approx(1.0)


def test_fitting_an_empty_factory_does_nothing(window: MainWindow) -> None:
    before = window.view.transform()
    window.fit_to_factory()
    assert window.view.transform() == before


def test_the_status_bar_summarises_the_report(window: MainWindow) -> None:
    assert "palette" in window.statusBar().currentMessage().lower()

    window.palette_widget.entryActivated.emit(
        entry_of(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    )
    window.document.solve_now()
    message = window.statusBar().currentMessage()
    assert "1 noeud(s)" in message
    # A lone smelter is connected to nothing: that is a warning, not a silence.
    assert "avertissement" in message
