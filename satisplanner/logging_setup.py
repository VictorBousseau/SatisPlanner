"""Logging, crash reporting, and the promise that nothing disappears silently.

Three different ways an application can die, and each needs its own net:

1. a Python exception nobody caught -- :func:`install_excepthook` writes it to the log
   with its traceback and hands a readable summary to the user interface;
2. a hard crash in the C++ half -- an access violation in Qt leaves no Python
   traceback at all, so :mod:`faulthandler` is pointed at a file of its own and
   writes the native stack before the process goes;
3. an ordinary exit -- logged, with its code, so a log that simply stops tells you
   something the log itself could not.

The traceback goes to the log. The user gets a sentence and the path to that log:
a raw traceback in a message box tells the person reading it nothing they can act on,
and the one thing they *can* do -- send the file -- needs them to know where it is.

This module is free of Qt on purpose. The log has to be open before there is a
``QApplication``, and a crash handler that needs the toolkit to be running is a crash
handler that fails exactly when it is called for.
"""

import faulthandler
import logging
import logging.handlers
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, TextIO

from satisplanner import paths

logger = logging.getLogger(__name__)

LOG_FILENAME: Final = "satisplanner.log"
# Native crashes go to their own file: faulthandler holds its handle open for the
# whole run, and on Windows an open handle is enough to make log rotation fail.
CRASH_FILENAME: Final = "crash.log"

# One megabyte, three generations: enough to hold the session that went wrong and the
# two before it, bounded enough that a forgotten installation never fills a disk.
MAX_LOG_BYTES: Final = 1_000_000
LOG_BACKUP_COUNT: Final = 3

LOG_FORMAT: Final = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

_log_path: Path | None = None
# Kept alive deliberately: faulthandler writes to this handle at crash time, and a
# closed file would make the last net the one with a hole in it.
_crash_file: TextIO | None = None


def current_log_path() -> Path | None:
    """The log file in use, or ``None`` when only the console is being written to."""
    return _log_path


def configure(*, level: int = logging.INFO, directory: Path | None = None) -> Path | None:
    """Set up console and file logging, and return the log file's path.

    Returns ``None`` when no file could be opened -- a read-only or missing
    ``%LOCALAPPDATA%``. That is a degraded run, not a failed one: the console handler
    is installed either way and the application starts.
    """
    global _log_path

    root = logging.getLogger()
    root.setLevel(level)
    for existing in list(root.handlers):
        root.removeHandler(existing)

    # A windowed PyInstaller build has no standard error at all -- it is ``None``, not
    # a stream that goes nowhere -- and a StreamHandler built on it raises on the
    # first record. The packaged application therefore logs to its file only.
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(console)

    target = paths.ensure_directory(directory if directory is not None else paths.log_directory())
    if target is None:
        _log_path = None
        logger.warning("aucun dossier de journal accessible : journalisation console seulement")
        return None

    path = target / LOG_FILENAME
    try:
        rotating = logging.handlers.RotatingFileHandler(
            path,
            maxBytes=MAX_LOG_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as exc:
        _log_path = None
        logger.warning("journal illisible (%s) : journalisation console seulement", exc)
        return None

    rotating.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(rotating)
    _log_path = path
    _enable_faulthandler(target)
    return path


def _enable_faulthandler(directory: Path) -> None:
    """Point the native crash handler at its own file under the log directory."""
    global _crash_file
    try:
        _crash_file = (directory / CRASH_FILENAME).open("a", encoding="utf-8")
        faulthandler.enable(file=_crash_file)
    except (OSError, ValueError):
        # ValueError: a stream without a real file descriptor, which happens under
        # some test runners. Losing the native net is not worth losing the run.
        logger.debug("faulthandler indisponible")


@dataclass(frozen=True)
class CrashReport:
    """What the user is told when something went wrong, and where the rest is."""

    title: str
    message: str
    details: str
    log_path: Path | None

    @property
    def full_message(self) -> str:
        """The message with the log's location appended, when there is one."""
        if self.log_path is None:
            return self.message
        return f"{self.message}\n\nJournal : {self.log_path}"


def build_report(exception: BaseException) -> CrashReport:
    """Turn an exception into something worth showing a person.

    ``details`` holds the exception's own line -- type and message -- and not the
    traceback: the traceback is in the log, which is where it is useful.
    """
    name = type(exception).__name__
    text = str(exception).strip()
    return CrashReport(
        title="Erreur inattendue",
        message=(
            "SatisPlanner a rencontre une erreur inattendue.\n\n"
            "L'usine en cours n'a pas ete modifiee par cette erreur ; si elle contient "
            "du travail non enregistre, enregistrez-la sous un autre nom avant de "
            "continuer."
        ),
        details=f"{name} : {text}" if text else name,
        log_path=current_log_path(),
    )


def install_excepthook(show: Callable[[CrashReport], None] | None = None) -> None:
    """Route uncaught exceptions to the log, then to ``show``.

    ``show`` is injected rather than imported so this module stays free of Qt, and so
    a test can watch what the user would have been told without a window existing.
    """

    def hook(
        exc_type: type[BaseException],
        exception: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            # Ctrl+C is a decision, not a defect: let the default handler end the run
            # quietly instead of reporting it as a crash.
            sys.__excepthook__(exc_type, exception, traceback)
            return
        logging.getLogger("satisplanner").critical(
            "exception non rattrapee", exc_info=(exc_type, exception, traceback)
        )
        if show is None:
            return
        try:
            show(build_report(exception))
        except Exception:  # the reporter itself must never be what kills the process
            logging.getLogger("satisplanner").exception("impossible d'afficher l'erreur")

    sys.excepthook = hook


def log_exit(code: int) -> None:
    """Record how the run ended, so a log that stops is never ambiguous."""
    if code == 0:
        logger.info("arret normal")
    else:
        logger.warning("arret avec le code %d", code)
