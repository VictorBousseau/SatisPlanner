"""Draw the application's own icon and write it as a multi-size ``.ico``.

A maintenance script, run when the icon changes and not otherwise; its output,
``satisplanner/resources/satisplanner.ico``, is committed and is what the build
script hands to PyInstaller.

The drawing is deliberately generic -- three nodes and the two lines between them,
in the interface's accent colour. It says "graph of connected things", which is what
this application is, and it borrows nothing from the game: no logo, no wordmark, no
recognisable machine. The icons that *do* belong to Coffee Stain Studios are the ones
this repository never carries.

Qt can write a ``.ico``, but only one image into it, and Windows then rescales that
one badly for the 16-pixel case in the taskbar. The container is therefore assembled
here from six separately drawn PNGs. That is the format's own documented layout --
since Vista an entry may hold a PNG whole -- and it is thirty lines against a
dependency.
"""

import argparse
import logging
import struct
import sys
from pathlib import Path
from typing import Final

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from satisplanner import paths
from satisplanner.ui import theme

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT: Final = paths.resource_directory() / "satisplanner.ico"

# The sizes Windows actually asks for: taskbar, alt-tab, explorer, and the large tile.
SIZES: Final[tuple[int, ...]] = (16, 24, 32, 48, 128, 256)

# Node centres and radius, as fractions of the side, so one drawing serves every size.
NODES: Final[tuple[tuple[float, float], ...]] = ((0.28, 0.30), (0.28, 0.70), (0.74, 0.50))
NODE_RADIUS: Final = 0.115
LINE_WIDTH: Final = 0.055
CORNER_RADIUS: Final = 0.20


def draw_icon(side: int) -> QPixmap:
    """One square of the icon at ``side`` pixels."""
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        edge = float(side)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(theme.SURFACE)))
        radius = edge * CORNER_RADIUS
        painter.drawRoundedRect(QRectF(0, 0, edge, edge), radius, radius)

        centres = [QPointF(x * edge, y * edge) for x, y in NODES]
        painter.setPen(
            QPen(
                QColor(theme.TEXT_MUTED),
                max(1.0, edge * LINE_WIDTH),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.drawLine(centres[0], centres[2])
        painter.drawLine(centres[1], centres[2])

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(theme.ACCENT)))
        node_radius = edge * NODE_RADIUS
        for centre in centres:
            painter.drawEllipse(centre, node_radius, node_radius)
    finally:
        painter.end()
    return pixmap


def png_bytes(pixmap: QPixmap) -> bytes:
    """Encode one square as PNG, in memory."""
    store = QByteArray()
    buffer = QBuffer(store)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(store.data())


def ico_bytes(images: dict[int, bytes]) -> bytes:
    """Assemble PNG-in-ICO: a six-byte header, one directory entry each, then data.

    A side of 256 is written as 0 in the directory, which is how the format spells
    "two hundred and fifty-six" in a single byte.
    """
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    offset = len(header) + 16 * count
    directory = b""
    payload = b""
    for side, data in sorted(images.items()):
        stored = 0 if side >= 256 else side
        directory += struct.pack(
            "<BBBBHHII", stored, stored, 0, 0, 1, 32, len(data), offset + len(payload)
        )
        payload += data
    return header + directory + payload


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    parser = argparse.ArgumentParser(description="Génère l'icône de l'application.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    # A QPainter needs a running application, even for an off-screen pixmap.
    app = QApplication.instance() or QApplication([])
    del app

    images = {side: png_bytes(draw_icon(side)) for side in SIZES}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(ico_bytes(images))
    logger.info(
        "%s écrit (%d tailles, %d octets)", args.output, len(images), args.output.stat().st_size
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
