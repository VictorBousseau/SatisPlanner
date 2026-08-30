"""The box shown when something went wrong that nobody caught.

A sentence saying what happened, the exception's own line, the path to the log, and
the **whole traceback** behind "Montrer les details" -- with a button that copies it.

It used to fold away the exception's line and nothing else, on the reasoning that the
traceback lived in the log and that was where it was useful. In practice that left the
one person who could act on it -- the reader, about to describe the problem to somebody
-- with a button promising details and delivering a single line they had already been
shown. The file and the line number are the details.
"""

import logging

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from satisplanner.core.i18n import _
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

    # First, because it is the one that matters: what somebody has to paste in
    # order to be helped. The log path is in it too, so this button is a superset
    # of the other and the other is kept only for the reader who wants to open the
    # file rather than send its contents.
    copy_details = box.addButton(_("Copier les détails"), QMessageBox.ButtonRole.ActionRole)
    copy_details.clicked.connect(lambda: _copy(report.clipboard_text))
    _keep_open(box, copy_details)

    if report.log_path is not None:
        copy_path = box.addButton(
            _("Copier le chemin du journal"), QMessageBox.ButtonRole.ActionRole
        )
        copy_path.clicked.connect(lambda: _copy(str(report.log_path)))
        _keep_open(box, copy_path)
    box.exec()


def _copy(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


def _keep_open(box: QMessageBox, button: QPushButton) -> None:
    """A ``QMessageBox`` closes on any button; copying a path must not close it.

    Re-showing is the documented way round it, and it costs one line here against a
    custom dialog everywhere else.
    """
    button.clicked.connect(box.show)
