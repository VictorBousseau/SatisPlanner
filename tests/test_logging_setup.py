"""The log, and what the user is told when something goes wrong.

The rule these tests hold to is the one from the specification: never a raw traceback
in front of a person, and never a failure that leaves no trace behind.
"""

import logging
from pathlib import Path

import pytest

from satisplanner import logging_setup


@pytest.fixture(autouse=True)
def restore_logging() -> "object":
    """Put the root logger and the exception hook back after each test."""
    import sys

    handlers = list(logging.getLogger().handlers)
    level = logging.getLogger().level
    hook = sys.excepthook
    yield None
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)
    sys.excepthook = hook
    logging_setup._log_path = None


def test_configure_opens_a_file_and_writes_to_it(tmp_path: Path) -> None:
    path = logging_setup.configure(directory=tmp_path / "journaux")
    assert path is not None
    assert logging_setup.current_log_path() == path

    logging.getLogger("satisplanner.test").warning("quelque chose s'est mal passe")
    logging.shutdown()
    assert "quelque chose s'est mal passe" in path.read_text(encoding="utf-8")


def test_the_log_is_bounded(tmp_path: Path) -> None:
    """A forgotten installation must not end up filling a disk."""
    logging_setup.configure(directory=tmp_path)
    rotating = next(
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.handlers.RotatingFileHandler)
    )
    assert rotating.maxBytes == logging_setup.MAX_LOG_BYTES
    assert rotating.backupCount == logging_setup.LOG_BACKUP_COUNT


def test_an_unwritable_directory_degrades_instead_of_failing(tmp_path: Path) -> None:
    blocked = tmp_path / "fichier"
    blocked.write_text("", encoding="utf-8")
    assert logging_setup.configure(directory=blocked / "journaux") is None
    # And logging still works, which is the point of degrading rather than raising.
    logging.getLogger("satisplanner.test").info("toujours vivant")


def test_the_visible_part_of_the_report_stays_short(tmp_path: Path) -> None:
    """What is on screen before anything is unfolded: the sentence, the line, the path.

    Not the traceback. A wall of frames in a message box is a wall of frames, and the
    reader has to get past it to find the two things they can act on.
    """
    logging_setup.configure(directory=tmp_path)
    report = logging_setup.build_report(ValueError("un tampon inconnu"))

    assert "Traceback" not in report.full_message
    assert "ValueError : un tampon inconnu" in report.full_message, (
        "la ligne de l'exception doit etre lisible sans deplier quoi que ce soit"
    )
    assert str(logging_setup.current_log_path()) in report.full_message
    assert "enregistrez-la sous un autre nom" in report.message


def test_the_details_hold_the_whole_traceback(tmp_path: Path) -> None:
    """« Montrer les details » must show details.

    The regression test for a button that promised a traceback and delivered the one
    line already printed above it.
    """
    logging_setup.configure(directory=tmp_path)
    try:
        raise ValueError("un tampon inconnu")
    except ValueError as exc:
        report = logging_setup.build_report(exc)

    assert "Traceback" in report.details
    assert "test_the_details_hold_the_whole_traceback" in report.details, (
        "la trace doit nommer la fonction ou l'erreur est nee"
    )
    assert "test_logging_setup.py" in report.details
    assert "ValueError: un tampon inconnu" in report.details


def test_what_the_copy_button_puts_on_the_clipboard(tmp_path: Path) -> None:
    """One paste has to be enough to describe the problem to somebody else."""
    logging_setup.configure(directory=tmp_path)
    try:
        raise ValueError("un tampon inconnu")
    except ValueError as exc:
        report = logging_setup.build_report(exc)

    pasted = report.clipboard_text
    assert "ValueError : un tampon inconnu" in pasted
    assert "Traceback" in pasted
    assert str(logging_setup.current_log_path()) in pasted


def test_an_exception_with_no_traceback_still_has_details() -> None:
    """Built by hand rather than raised: its own line is all there is, and it shows.

    The degraded case has to stay a sentence rather than an empty pane, which is what
    a naive "take the traceback" would leave behind.
    """
    report = logging_setup.build_report(ValueError("jamais levee"))

    assert report.details == "ValueError: jamais levee"


def test_an_uncaught_exception_is_logged_and_shown(tmp_path: Path) -> None:
    path = logging_setup.configure(directory=tmp_path)
    assert path is not None
    shown: list[logging_setup.CrashReport] = []
    logging_setup.install_excepthook(shown.append)

    import sys

    try:
        raise RuntimeError("le port n'existe pas")
    except RuntimeError as exc:
        sys.excepthook(type(exc), exc, exc.__traceback__)

    logging.shutdown()
    written = path.read_text(encoding="utf-8")
    assert "exception non rattrapee" in written
    # The traceback belongs in the file, so that it survives the box being closed...
    assert "Traceback" in written
    # ...and behind the button, so that it can be read without going to look for it.
    assert len(shown) == 1
    assert "le port n'existe pas" in shown[0].details
    assert "Traceback" in shown[0].details
    assert "Traceback" not in shown[0].full_message


def test_ctrl_c_is_not_reported_as_a_crash(tmp_path: Path) -> None:
    logging_setup.configure(directory=tmp_path)
    shown: list[logging_setup.CrashReport] = []
    logging_setup.install_excepthook(shown.append)

    import sys

    exc = KeyboardInterrupt()
    sys.excepthook(KeyboardInterrupt, exc, None)
    assert shown == [], "interrompre n'est pas planter"


def test_a_reporter_that_fails_does_not_take_the_process_with_it(tmp_path: Path) -> None:
    path = logging_setup.configure(directory=tmp_path)
    assert path is not None

    def broken(_report: logging_setup.CrashReport) -> None:
        msg = "la fenetre est deja detruite"
        raise RuntimeError(msg)

    logging_setup.install_excepthook(broken)

    import sys

    exc = ValueError("erreur d'origine")
    sys.excepthook(ValueError, exc, None)  # must not raise

    logging.shutdown()
    written = path.read_text(encoding="utf-8")
    assert "erreur d'origine" in written
    assert "impossible d'afficher l'erreur" in written


def test_the_exit_code_is_always_recorded(tmp_path: Path) -> None:
    """A log that simply stops must never be ambiguous."""
    path = logging_setup.configure(directory=tmp_path)
    assert path is not None
    logging_setup.log_exit(0)
    logging_setup.log_exit(3)
    logging.shutdown()
    written = path.read_text(encoding="utf-8")
    assert "arret normal" in written
    assert "arret avec le code 3" in written
