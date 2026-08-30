"""The parts of the interface this project did not write.

Every sentence in this application is its own, except the ones Qt supplies: the
buttons of a standard message box, the two headers of a file dialog, the menu of a
text field. Qt ships translations but loads none of them on its own, so without this
a French window asks "Enregistrer avant de continuer ?" over buttons reading *Save*,
*Discard* and *Cancel*.

Both catalogues exist -- ``qtbase_fr.qm`` and ``qtbase_en.qm`` -- and the English one
is loaded as deliberately as the French. It would mostly be redundant, English being
Qt's source language, but loading it is what makes switching back **symmetric**:
without it, leaving French would leave the French buttons in place, because a
translator that is never removed is never undone.

Installed from the application's entry point and again from the main window. That is
deliberate: the window is what every test builds, and a toolkit that answers in
English under test while answering in French in production is a difference that hides
exactly the kind of defect these tests exist to catch.
"""

import logging
from typing import Final

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtWidgets import QApplication

from satisplanner.core.i18n import Language

logger = logging.getLogger(__name__)

# The catalogue holding the standard dialog buttons and the file dialog, one per
# language. The name is the Qt convention and not a path: Qt finds the file itself.
TRANSLATION_NAMES: Final[dict[Language, str]] = {
    Language.FRENCH: "qtbase_fr",
    Language.ENGLISH: "qtbase_en",
}

# Kept alive at module level: Qt does not take ownership of a translator, and one
# that is garbage-collected takes its translations with it.
_translator: QTranslator | None = None
_installed: Language | None = None


def install_translations(language: Language) -> bool:
    """Load Qt's own strings for this language, removing whatever was there.

    Idempotent, and harmless without a ``QApplication``. Returns whether the
    catalogue is in place -- false when Qt was built without it, in which case the
    standard buttons stay in Qt's own English and everything else still works.
    """
    global _translator, _installed
    if _installed is language and _translator is not None:
        return True
    app = QApplication.instance()
    if app is None:
        return False

    if _translator is not None:
        app.removeTranslator(_translator)
        _translator = None
        _installed = None

    translator = QTranslator(app)
    directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    name = TRANSLATION_NAMES[language]
    if not translator.load(name, directory):
        logger.warning("traductions Qt « %s » absentes de %s", name, directory)
        return False
    app.installTranslator(translator)
    _translator = translator
    _installed = language
    logger.debug("traductions Qt « %s » installées", name)
    return True


def install_stored_language() -> Language:
    """Set the language from the stored preference, before anything is built.

    Called from the entry point, so the very first dialog -- a crash box while the
    catalogue is loading, for instance -- already speaks the right language. The
    window does it again from the same preference; the second call is a comparison.
    """
    from satisplanner.core import i18n
    from satisplanner.ui.preferences import Preferences

    language = Preferences().language
    i18n.set_language(language)
    install_translations(language)
    return language


def system_language() -> Language:
    """What the machine's own locale suggests, for a first launch and nothing else.

    French when the system is French, English otherwise -- and English rather than
    "the closest of the two" because there is no closest: someone on a German
    Windows reads English far more often than French.

    Only ever consulted once, when no preference has been stored. After that the
    stored choice wins, because a user who picked a language meant it.
    """
    for name in QLocale.system().uiLanguages():
        if name.split("-")[0].split("_")[0].casefold() == "fr":
            return Language.FRENCH
    return Language.ENGLISH
