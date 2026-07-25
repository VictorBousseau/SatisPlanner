"""The box shown when something went wrong that nobody caught.

Deliberately small: a sentence saying what happened, the path to the log, and the
exception's own line folded away behind "Details". The traceback is in the log --
showing it here would tell the reader nothing they can act on, and would bury the one
thing they can do, which is send the file whose path is right there.
"""

import logging

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from satisplanner.logging_setup import CrashReport

logger = logging.getLogger(__name__)


def show_crash_report(report: CrashReport) -> None:
    """Tell the user, if there is still a Qt application able to tell them.

    Called from the exception hook, which can fire at any moment -- including while
    the application is shutting down. When Qt has already gone, the log is the report
    and there is nothing to show.
    """
    if QApplication.instance() is None:
        logger.debug("pas d'application Qt : rapport d'erreur laisse au journal")
        return

    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(report.title)
    box.setText(report.full_message)
    box.setDetailedText(report.details)
    box.addButton(QMessageBox.StandardButton.Close)
    if report.log_path is not None:
        copy = box.addButton("Copier le chemin du journal", QMessageBox.ButtonRole.ActionRole)
        copy.clicked.connect(lambda: _copy(str(report.log_path)))
        _keep_open(box, copy)
    box.exec()


def _copy(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


def _keep_open(box: QMessageBox, button: QPushButton) -> None:
    """A ``QMessageBox`` closes on any button; copying a path must not close it.

    Re-showing is the documented way round it, and it costs one line here against a
    custom dialog everywhere else.
    """
    button.clicked.connect(box.show)
