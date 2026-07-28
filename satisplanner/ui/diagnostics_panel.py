"""The diagnostics panel: every finding, sorted, filtered, and actionable.

Two things separate a useful diagnostics list from a wall of text. Clicking a line
must take you to what it is talking about -- so a row selects and centres its node or
its line on the canvas. And a finding that names a fix must offer it: "passer au tier
suffisant" and "ajuster ce noeud" are one click from the row that reported them,
rather than something to go and find in a context menu somewhere else.
"""

import logging
from typing import Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from satisplanner.core.results import Diagnostic, DiagnosticCode, FactoryReport, Severity
from satisplanner.ui import theme
from satisplanner.ui.binding import DocumentBinding
from satisplanner.ui.document import FactoryDocument
from satisplanner.ui.report_html import SEVERITY_COLOURS, SEVERITY_LABELS

logger = logging.getLogger(__name__)

# Findings whose fix the panel can carry out itself, and the button's wording.
ACTIONABLE: Final[dict[DiagnosticCode, str]] = {
    DiagnosticCode.LINE_SATURATION: "Passer au tier suffisant",
    DiagnosticCode.DEFICIT: "Ajuster ce nœud",
    DiagnosticCode.BACKPRESSURE: "Ajuster ce nœud",
}

_ROLE_DIAGNOSTIC: Final = int(Qt.ItemDataRole.UserRole)


class DiagnosticsPanel(QWidget):
    """The findings list, with a severity filter and the one-click fixes."""

    # Emitted with the node id and the edge id of the selected finding; either may be
    # empty, and the canvas decides what to do with them.
    targetPicked = Signal(str, str)
    fixRequested = Signal(str, str)

    def __init__(self, document: FactoryDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._report: FactoryReport | None = None

        self.errors = QCheckBox("Erreurs", self)
        self.warnings = QCheckBox("Avertissements", self)
        self.infos = QCheckBox("Informations", self)
        for box in (self.errors, self.warnings, self.infos):
            box.setChecked(True)
            box.toggled.connect(self.refresh)

        self.list = QListWidget(self)
        self.list.setWordWrap(True)
        self.list.currentItemChanged.connect(self._announce_target)
        self.list.itemActivated.connect(self._announce_target)

        self.fix_button = QPushButton("Corriger", self)
        self.fix_button.setEnabled(False)
        self.fix_button.clicked.connect(self._request_fix)

        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        for box in (self.errors, self.warnings, self.infos):
            filters.addWidget(box)
        filters.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(filters)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.fix_button)

        self._binding = DocumentBinding()
        self.set_document(document)

    # ------------------------------------------------------------------ data

    def set_document(self, document: FactoryDocument) -> None:
        """Show another factory's findings, and stop listening to the previous one.

        The report is read off the document rather than asked of the engine:
        looking at a factory again does not change it.
        """
        self._binding.unbind()
        self.document = document
        self._binding.bind(document.reportChanged, self.show_report)
        self.show_report(document.report)

    def show_report(self, report: FactoryReport | None) -> None:
        self._report = report
        self.refresh()

    def wanted_severities(self) -> set[Severity]:
        chosen = {
            Severity.ERROR: self.errors.isChecked(),
            Severity.WARNING: self.warnings.isChecked(),
            Severity.INFO: self.infos.isChecked(),
        }
        return {severity for severity, kept in chosen.items() if kept}

    def visible_diagnostics(self) -> list[Diagnostic]:
        if self._report is None:
            return []
        wanted = self.wanted_severities()
        return [item for item in self._report.diagnostics if item.severity in wanted]

    def refresh(self) -> None:
        """Rebuild the list, keeping the selected finding when it is still there."""
        previous = self.current_diagnostic()
        self.list.clear()
        for finding in self.visible_diagnostics():
            self.list.addItem(_row_for(finding))
        if previous is not None:
            self.select_matching(previous)
        if self.list.count() == 0:
            self.list.addItem(_empty_row(self._report))
        self._update_fix_button()

    def select_matching(self, wanted: Diagnostic) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(_ROLE_DIAGNOSTIC) == wanted:
                self.list.setCurrentRow(row)
                return

    def current_diagnostic(self) -> Diagnostic | None:
        """The selected finding, or ``None`` on the placeholder row."""
        row = self.list.currentRow()
        if row < 0 or row >= self.list.count():
            return None
        found = self.list.item(row).data(_ROLE_DIAGNOSTIC)
        return found if isinstance(found, Diagnostic) else None

    # --------------------------------------------------------------- actions

    def _announce_target(self, *_args: object) -> None:
        self._update_fix_button()
        finding = self.current_diagnostic()
        if finding is None:
            return
        self.targetPicked.emit(finding.node_id or "", finding.edge_id or "")

    def _update_fix_button(self) -> None:
        finding = self.current_diagnostic()
        label = ACTIONABLE.get(finding.code) if finding is not None else None
        self.fix_button.setEnabled(label is not None)
        self.fix_button.setText(label or "Aucune correction automatique")

    def _request_fix(self) -> None:
        finding = self.current_diagnostic()
        if finding is None or finding.code not in ACTIONABLE:
            return
        self.fixRequested.emit(finding.node_id or "", finding.edge_id or "")


def _row_for(finding: Diagnostic) -> QListWidgetItem:
    target = finding.node_id or finding.edge_id or "usine"
    item = QListWidgetItem(f"[{SEVERITY_LABELS[finding.severity]}] {target}\n{finding.message}")
    item.setForeground(QColor(SEVERITY_COLOURS[finding.severity]))
    item.setData(_ROLE_DIAGNOSTIC, finding)
    item.setToolTip(finding.message)
    return item


def _empty_row(report: FactoryReport | None) -> QListWidgetItem:
    if report is None or not report.nodes:
        text = "Rien à signaler : l'usine est vide."
    elif not report.diagnostics:
        text = "Aucun diagnostic : l'usine est nominale."
    else:
        text = "Aucun diagnostic de ce niveau."
    item = QListWidgetItem(text)
    item.setFlags(Qt.ItemFlag.NoItemFlags)
    item.setForeground(QColor(theme.TEXT_MUTED))
    return item
