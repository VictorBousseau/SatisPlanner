"""The totals panel: the whole report as one scrollable document.

A read-only rendering of :mod:`satisplanner.ui.report_html`, which is also what the
PDF export prints. Keeping the two on the same generator means the exported page and
the panel can never disagree about a number.
"""

import logging

from PySide6.QtWidgets import QTextBrowser, QVBoxLayout, QWidget

from satisplanner.core.results import FactoryReport
from satisplanner.ui import report_html
from satisplanner.ui.binding import DocumentBinding
from satisplanner.ui.document import FactoryDocument

logger = logging.getLogger(__name__)


class TotalsPanel(QWidget):
    """Raw materials, byproducts, power and the shopping list, in that order."""

    def __init__(self, document: FactoryDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.browser)

        self._binding = DocumentBinding()
        self.set_document(document)

    def set_document(self, document: FactoryDocument) -> None:
        """Show another factory, and stop listening to the previous one.

        The report is **read**, never asked for: the document being shown has
        already been solved, and switching tabs is not an edit.
        """
        self._binding.unbind()
        self.document = document
        self._binding.bind(document.reportChanged, self.show_report)
        self.show_report(document.report)

    def show_report(self, report: FactoryReport | None) -> None:
        """Redraw, keeping the scroll position so a live recompute is not jarring."""
        scroll = self.browser.verticalScrollBar()
        position = scroll.value()
        self.browser.setHtml(report_html.document(report, self.document.game_data))
        scroll.setValue(min(position, scroll.maximum()))

    def html(self) -> str:
        """What is currently displayed, for the tests and for the export."""
        return report_html.document(self.document.report, self.document.game_data)
