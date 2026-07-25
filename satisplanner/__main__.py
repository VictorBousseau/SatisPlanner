"""Application entry point: ``python -m satisplanner``.

Kept thin on purpose, but not empty: this is the only place where the three things
that must happen before anything else can happen in the right order -- the log is
opened, the crash net is hung, and only then is Qt started.

Qt is imported inside :func:`main` rather than at module level so that ``--help`` and
``--startup-probe`` do not pay for a toolkit they are not going to use, and so that
an import error in the interface still reaches a log that is already open.
"""

import argparse
import logging
import sys
import time
from typing import Final

from satisplanner import __version__, logging_setup

logger = logging.getLogger(__name__)

# As early as this module can see. It misses the interpreter's own start-up, which is
# why the honest measurement is taken from outside; this one tells us which of *our*
# steps is the expensive one.
_IMPORTED_AT: Final = time.perf_counter()

# Drawn for this project by ``tools/make_app_icon.py``; nothing of the game's is in it.
ICON_FILENAME: Final = "satisplanner.ico"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="satisplanner",
        description="Planificateur d'usines theoriques pour Satisfactory.",
    )
    parser.add_argument("--version", action="version", version=f"SatisPlanner {__version__}")
    parser.add_argument("--verbose", "-v", action="store_true", help="journalisation detaillee")
    parser.add_argument(
        "--startup-probe",
        action="store_true",
        help="afficher la fenetre, mesurer le temps de demarrage, puis quitter",
    )
    parser.add_argument(
        "--no-splash", action="store_true", help="ne pas afficher l'ecran de demarrage"
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="derouler la liste de verification de l'executable, puis quitter",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="avec --self-check : n'afficher aucune fenetre, se contenter du rapport "
        "ecrit et du code de sortie",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Start the Qt application and return its exit code."""
    args = parse_args(argv)
    level = logging.DEBUG if args.verbose else logging.INFO
    log_path = logging_setup.configure(level=level)
    logger.info("SatisPlanner %s - demarrage (journal : %s)", __version__, log_path)

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from satisplanner import paths
    from satisplanner.ui.crash import show_crash_report
    from satisplanner.ui.localisation import install_french_translations
    from satisplanner.ui.main_window import MainWindow
    from satisplanner.ui.splash import finish_splash, show_splash
    from satisplanner.ui.theme import apply_theme

    # Qt is given only the program name: the arguments above are ours, and Qt
    # complains about the ones it does not recognise.
    app = QApplication(sys.argv[:1])
    app.setApplicationName("SatisPlanner")
    app.setApplicationVersion(__version__)
    icon_file = paths.resource_directory() / ICON_FILENAME
    if icon_file.is_file():
        app.setWindowIcon(QIcon(str(icon_file)))
    # Hung as soon as there is an application to show a box with, and before the
    # window is built: loading the catalogue is itself something that can fail.
    logging_setup.install_excepthook(show_crash_report)
    install_french_translations()
    apply_theme(app)

    # Before the catalogue is read, which is the expensive half of what is left.
    splash = None if args.no_splash else show_splash()

    window = MainWindow()
    window.show()
    finish_splash(splash, window)
    logger.info("interface prete en %.2f s", time.perf_counter() - _IMPORTED_AT)

    if args.self_check:
        from satisplanner.ui.self_check import run_self_check

        passed, text = run_self_check(window)
        if not args.quiet:
            _show_self_check(window, passed, text)
        return 0 if passed else 1

    if args.startup_probe:
        # Quit on the next turn of the loop, so the window is genuinely painted first
        # and the measurement covers what a user would have waited for.
        QTimer.singleShot(0, app.quit)

    code = app.exec()
    logging_setup.log_exit(code)
    return code


def _show_self_check(window: object, passed: bool, text: str) -> None:
    """Put the checklist on screen: a packaged run has no console to print to."""
    from PySide6.QtWidgets import QMessageBox, QWidget

    box = QMessageBox(window if isinstance(window, QWidget) else None)
    box.setIcon(QMessageBox.Icon.Information if passed else QMessageBox.Icon.Critical)
    box.setWindowTitle("Verification de l'executable")
    box.setText(
        "Toutes les verifications sont passees." if passed else "Une verification a echoue."
    )
    box.setDetailedText(text)
    box.exec()


if __name__ == "__main__":
    raise SystemExit(main())
