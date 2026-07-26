"""The two pieces of interface that only ever appear at the edges of a run: the
start-up screen, and the box shown when something went wrong that nobody caught.

Neither is reached by using the application normally, which is exactly why they are
worth a test: the crash box is the one piece of interface guaranteed to be shown on a
bad day, and it will not be tried by hand on a good one.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from satisplanner import logging_setup
from satisplanner.ui import crash, splash


def capture(into: list[QMessageBox]) -> Callable[[QMessageBox], int]:
    """Stand in for ``QMessageBox.exec``: keep the box, close it, say nothing."""

    def intercept(box: QMessageBox) -> int:
        into.append(box)
        return 0

    return intercept


def test_the_splash_says_what_it_is(qtbot: QtBot) -> None:
    del qtbot
    pixmap = splash.splash_pixmap()
    assert not pixmap.isNull()
    assert pixmap.size().toTuple() == (splash.WIDTH, splash.HEIGHT)


def test_the_splash_shows_and_goes_away(qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QWidget

    window = QWidget()
    qtbot.addWidget(window)
    shown = splash.show_splash()
    try:
        assert shown.isVisible()
        splash.finish_splash(shown, window)
        assert not shown.isVisible()
    finally:
        shown.deleteLater()


def test_finishing_nothing_is_harmless(qtbot: QtBot) -> None:
    """The ``--no-splash`` path hands ``None`` through the same call."""
    from PySide6.QtWidgets import QWidget

    window = QWidget()
    qtbot.addWidget(window)
    splash.finish_splash(None, window)


def test_the_crash_box_shows_a_sentence_and_a_path(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del qtbot
    logging_setup.configure(directory=tmp_path)
    try:
        seen: list[QMessageBox] = []
        monkeypatch.setattr(QMessageBox, "exec", capture(seen))

        try:
            raise ValueError("port inconnu")
        except ValueError as exc:
            crash.show_crash_report(logging_setup.build_report(exc))

        assert len(seen) == 1
        box = seen[0]
        assert "Traceback" not in box.text(), "jamais de trace brute avant qu'on la demande"
        assert "ValueError : port inconnu" in box.text(), "la ligne doit se lire tout de suite"
        assert str(tmp_path) in box.text(), "le chemin du journal doit etre donne"
        assert "Traceback" in box.detailedText(), "« Montrer les details » doit montrer la trace"
        assert "test_ui_startup.py" in box.detailedText()
    finally:
        logging_setup._log_path = None


def test_the_crash_box_can_hand_over_the_details(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The button that matters: one click, and the whole thing is ready to be pasted."""
    del qtbot
    logging_setup.configure(directory=tmp_path)
    try:
        QGuiApplication.clipboard().setText("")
        boxes: list[QMessageBox] = []
        monkeypatch.setattr(QMessageBox, "exec", capture(boxes))
        try:
            raise RuntimeError("bruit")
        except RuntimeError as exc:
            crash.show_crash_report(logging_setup.build_report(exc))

        copy = next(
            button for button in boxes[0].buttons() if "details" in button.text().replace("&", "")
        )
        copy.click()

        pasted = QGuiApplication.clipboard().text()
        assert "RuntimeError : bruit" in pasted
        assert "Traceback" in pasted
        assert str(logging_setup.current_log_path()) in pasted
    finally:
        logging_setup._log_path = None


def test_the_crash_box_can_hand_over_the_log_path(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the reader is actually going to do with it is send the file."""
    del qtbot
    logging_setup.configure(directory=tmp_path)
    try:
        QGuiApplication.clipboard().setText("")
        boxes: list[QMessageBox] = []
        monkeypatch.setattr(QMessageBox, "exec", capture(boxes))
        crash.show_crash_report(logging_setup.build_report(RuntimeError("bruit")))

        copy = next(
            button for button in boxes[0].buttons() if "journal" in button.text().replace("&", "")
        )
        copy.click()
        assert str(logging_setup.current_log_path()) == QGuiApplication.clipboard().text()
    finally:
        logging_setup._log_path = None


def test_a_report_without_a_log_still_says_something(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Degraded run: no log file, and still a box rather than a silent death."""
    del qtbot
    monkeypatch.setattr(logging_setup, "_log_path", None)
    seen: list[QMessageBox] = []
    monkeypatch.setattr(QMessageBox, "exec", capture(seen))

    report = logging_setup.build_report(ValueError("rien"))
    assert report.log_path is None
    crash.show_crash_report(report)
    assert seen and "Journal" not in seen[0].text()
