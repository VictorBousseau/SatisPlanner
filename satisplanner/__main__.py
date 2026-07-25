"""Application entry point: ``python -m satisplanner``."""

import logging
import sys

logger = logging.getLogger(__name__)


def main() -> int:
    """Start the Qt application and return its exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    # Qt is imported lazily so that `--help`-style tooling and the architecture
    # test never pay for it.
    from PySide6.QtWidgets import QApplication

    from satisplanner.ui.main_window import MainWindow
    from satisplanner.ui.theme import apply_theme

    app = QApplication(sys.argv)
    app.setApplicationName("SatisPlanner")
    apply_theme(app)

    window = MainWindow()
    window.show()

    logger.info("Interface demarree")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
