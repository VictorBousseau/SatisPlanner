"""Getting a factory out of the application: PNG, PDF, and the save thumbnail.

The PNG is the canvas as it is drawn, at a fixed scale rather than at the current
zoom -- an export whose resolution depends on how far the user happened to be zoomed
in is a surprise, not a feature.

The PDF puts the canvas on the first page and, optionally, the totals on the next.
Both come from the same generators the panels use, so a printed report and the panel
beside it always carry the same numbers.
"""

import logging
from pathlib import Path
from typing import Final

from PySide6.QtCore import QBuffer, QByteArray, QMarginsF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QImage, QPageSize, QPainter, QPdfWriter, QTextDocument
from PySide6.QtWidgets import QGraphicsScene

from satisplanner.core.i18n import _
from satisplanner.core.models import GameData
from satisplanner.core.results import FactoryReport
from satisplanner.ui import report_html, theme

logger = logging.getLogger(__name__)

# Margin left around the factory, in scene units.
EXPORT_MARGIN: Final = 40.0

# Scene units per exported pixel. Fixed on purpose: see the module docstring.
EXPORT_SCALE: Final = 2.0

# Above this, the image is scaled down rather than refused: a very large factory
# should still export, just not at 30 000 pixels a side.
MAX_EXPORT_SIDE: Final = 8000

THUMBNAIL_SIDE: Final = 480

PDF_RESOLUTION: Final = 300
PDF_MARGIN_MM: Final = 10.0


def scene_bounds(scene: QGraphicsScene) -> QRectF:
    """The factory's own extent, with a margin. Empty when there is nothing to draw."""
    rect = scene.itemsBoundingRect()
    if rect.isEmpty():
        return QRectF()
    return rect.adjusted(-EXPORT_MARGIN, -EXPORT_MARGIN, EXPORT_MARGIN, EXPORT_MARGIN)


def render_scene(
    scene: QGraphicsScene, scale: float = EXPORT_SCALE, region: QRectF | None = None
) -> QImage | None:
    """The canvas as an image, or ``None`` when there is nothing in view.

    ``region`` narrows the picture to a part of the scene. A module saved from a
    selection wants a picture of that selection, not of the factory it was lifted
    out of.
    """
    bounds = scene_bounds(scene) if region is None else region
    if bounds.isEmpty():
        return None
    scale = min(scale, MAX_EXPORT_SIDE / max(bounds.width(), bounds.height()))
    image = QImage(
        max(int(bounds.width() * scale), 1),
        max(int(bounds.height() * scale), 1),
        QImage.Format.Format_ARGB32,
    )
    image.fill(QColor(theme.CANVAS_BACKGROUND))
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        scene.render(painter, QRectF(image.rect()), bounds)
    finally:
        painter.end()
    return image


def export_png(scene: QGraphicsScene, path: Path) -> bool:
    """Write the canvas to ``path``. False when there was nothing to write."""
    image = render_scene(scene)
    if image is None:
        return False
    # The format is left to the extension: PySide's own stubs say this argument takes
    # bytes, and the runtime rejects bytes. Not passing it avoids having to be wrong
    # for one of the two.
    written = bool(image.save(str(path)))
    logger.info("export PNG %s : %s", path, "ok" if written else "échec")
    return written


def thumbnail_bytes(scene: QGraphicsScene, region: QRectF | None = None) -> bytes | None:
    """A small PNG for the ``.sfp`` archive or for a module, or ``None`` for nothing."""
    image = render_scene(scene, scale=1.0, region=region)
    if image is None:
        return None
    small = image.scaled(
        THUMBNAIL_SIDE,
        THUMBNAIL_SIDE,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    payload = QByteArray()
    buffer = QBuffer(payload)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    try:
        # See export_png: the stubs and the runtime disagree about this argument.
        if not small.save(buffer, "PNG"):  # type: ignore[call-overload]
            logger.debug("vignette non générée")
            return None
    finally:
        buffer.close()
    return bytes(payload.data())


def export_pdf(
    scene: QGraphicsScene,
    path: Path,
    report: FactoryReport | None,
    game_data: GameData,
    *,
    include_totals: bool,
) -> bool:
    """Canvas on the first page, totals and diagnostics on the next if asked."""
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(PDF_RESOLUTION)
    writer.setPageMargins(QMarginsF(*(PDF_MARGIN_MM,) * 4))
    writer.setTitle(_("SatisPlanner — usine"))

    painter = QPainter()
    if not painter.begin(writer):
        logger.warning("impossible d'ouvrir %s en écriture", path)
        return False
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        page = QRectF(0, 0, writer.width(), writer.height())
        drawn = _draw_canvas_page(painter, scene, page)
        if include_totals and report is not None:
            if drawn:
                writer.newPage()
            _draw_totals_page(painter, writer, report, game_data)
    finally:
        painter.end()
    logger.info("export PDF %s", path)
    return True


def _draw_canvas_page(painter: QPainter, scene: QGraphicsScene, page: QRectF) -> bool:
    bounds = scene_bounds(scene)
    if bounds.isEmpty():
        return False
    scene.render(painter, _fitted(bounds, page), bounds, Qt.AspectRatioMode.KeepAspectRatio)
    return True


def _fitted(source: QRectF, page: QRectF) -> QRectF:
    """Centre ``source``'s aspect ratio inside ``page`` without distorting it."""
    scale = min(page.width() / source.width(), page.height() / source.height())
    size = QSizeF(source.width() * scale, source.height() * scale)
    return QRectF(
        page.left() + (page.width() - size.width()) / 2,
        page.top() + (page.height() - size.height()) / 2,
        size.width(),
        size.height(),
    )


def _draw_totals_page(
    painter: QPainter, writer: QPdfWriter, report: FactoryReport, game_data: GameData
) -> None:
    """The totals, printed from the very HTML the panel shows."""
    document = QTextDocument()
    document.setPageSize(QSizeF(writer.width(), writer.height()))
    document.setDefaultStyleSheet(report_html.stylesheet())
    document.setHtml(
        report_html.document(report, game_data) + report_html.diagnostics_section(report)
    )
    document.drawContents(painter)
