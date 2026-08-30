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
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, TextIO

from satisplanner import paths
from satisplanner.core.i18n import _

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
    """What the user is told when something went wrong, and where the rest is.

    ``summary`` is the exception's own line, short enough to be read at a glance and
    shown without unfolding anything. ``details`` is the whole traceback, which is
    the only part worth sending to somebody who can fix it.
    """

    title: str
    message: str
    summary: str
    details: str
    log_path: Path | None

    @property
    def full_message(self) -> str:
        """What the box shows before anything is unfolded.

        The exception's line belongs here rather than behind a button: "something
        went wrong" alone tells a reader nothing they can repeat back.
        """
        parts = [self.message, self.summary]
        if self.log_path is not None:
            parts.append(_("Journal : {path}").format(path=self.log_path))
        return "\n\n".join(parts)

    @property
    def clipboard_text(self) -> str:
        """Everything worth pasting into a bug report, in one go."""
        parts = [self.summary, "", self.details]
        if self.log_path is not None:
            parts.extend(["", _("Journal : {path}").format(path=self.log_path)])
        return "\n".join(parts)


def build_report(exception: BaseException) -> CrashReport:
    """Turn an exception into something worth showing a person.

    ``details`` holds the **complete traceback**. It used to hold the exception's
    one line, on the reasoning that the traceback was in the log and the log was
    where it was useful -- which was true for whoever knew to go and read the log,
    and false for everybody else. A button called "Montrer les details" that shows
    ``AttributeError : 'bool' object has no attribute 'is_file'`` and nothing more
    has no details behind it; the file, the line and the frames are the details, and
    they are what gets pasted into a report.
    """
    name = type(exception).__name__
    text = str(exception).strip()
    return CrashReport(
        title=_("Erreur inattendue"),
        message=_(
            "SatisPlanner a rencontré une erreur inattendue.\n\n"
            "Aucune des usines ouvertes n'a été modifiée par cette erreur ; si l'une "
            "d'elles contient du travail non enregistré, enregistrez-la sous un autre "
            "nom avant de continuer."
        ),
        summary=f"{name} : {text}" if text else name,
        # ``format_exception`` copes with an exception that was never raised and has
        # no traceback of its own: it falls back to the exception's line, which is
        # then genuinely all there is to say.
        details="".join(
            traceback.format_exception(type(exception), exception, exception.__traceback__)
        ).strip(),
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
            "exception non rattrapée", exc_info=(exc_type, exception, traceback)
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
        logger.info("arrêt normal")
    else:
        logger.warning("arrêt avec le code %d", code)
