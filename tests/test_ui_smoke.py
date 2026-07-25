"""The window must actually build. Uses pytest-qt's `qtbot` for the event loop."""

from pytestqt.qtbot import QtBot

from satisplanner.ui.main_window import MainWindow


def test_main_window_builds_and_shows(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    with qtbot.waitExposed(window):
        window.show()

    assert window.isVisible()
    assert "SatisPlanner" in window.windowTitle()
    assert window.centralWidget() is not None
