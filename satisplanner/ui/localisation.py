"""French for the parts of the interface this project did not write.

Every string in this application is French, except the ones Qt supplies itself: the
buttons of a standard message box, the two headers of a file dialog, the menu of a
text field. Qt ships translations but loads none of them on its own, so without this
a French window asks "Enregistrer avant de continuer ?" over buttons reading *Save*,
*Discard* and *Cancel*.

Installed from the application's entry point and again from the main window. That is
deliberate: the window is what every test builds, and a toolkit that answers in
English under test while answering in French in production is a difference that hides
exactly the kind of defect these tests exist to catch.
"""

import logging
from typing import Final

from PySide6.QtCore import QLibraryInfo, QTranslator
from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)

# The catalogue holding the standard dialog buttons and the file dialog.
TRANSLATION_NAME: Final = "qtbase_fr"

# Kept alive at module level: Qt does not take ownership of a translator, and one
# that is garbage-collected takes its translations with it.
_translator: QTranslator | None = None


def install_french_translations() -> bool:
    """Load Qt's own French strings. Idempotent, and harmless without a QApplication.

    Returns whether the catalogue is in place -- false when Qt was built without it,
    in which case the standard buttons stay English and everything else still works.
    """
    global _translator
    if _translator is not None:
        return True
    app = QApplication.instance()
    if app is None:
        return False

    translator = QTranslator(app)
    directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if not translator.load(TRANSLATION_NAME, directory):
        logger.warning("traductions Qt françaises absentes de %s", directory)
        return False
    app.installTranslator(translator)
    _translator = translator
    logger.debug("traductions Qt françaises installees")
    return True
