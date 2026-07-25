"""The start-up screen, and an honest note about what it can and cannot hide.

A packaged launch takes a little over two seconds on a warm machine and considerably
more the first time, when Windows is still reading a hundred and thirty megabytes off
the disk and inspecting a freshly built executable it has never seen. Measured from
inside the process, only about a second of that is ours; the rest is the bootloader
unpacking and Qt's own libraries loading, and **no splash screen can cover that part**
-- there is no Qt yet to draw one with.

So this covers the second half: reading the catalogue and building the window, which
is the stretch during which a user would otherwise be looking at an empty desktop
wondering whether their double-click registered. It is shown as early as a
``QApplication`` allows and dismissed the moment the window is up.

It is a bare ``QLabel`` and not a ``QSplashScreen``, which is the class for the job
and was the first thing tried. Showing a ``QSplashScreen`` on this platform costs a
little over a **second**, measured, every time and whatever the pixmap -- a splash
that makes the wait a second longer is not a splash, it is the bug it was meant to
hide. A frameless label showing the same image costs sixteen milliseconds.
"""

import logging
from typing import Final

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

from satisplanner import __version__, paths
from satisplanner.ui import theme

logger = logging.getLogger(__name__)

WIDTH: Final = 460
HEIGHT: Final = 200
ICON_SIDE: Final = 96
CORNER_RADIUS: Final = 10.0

ICON_FILENAME: Final = "satisplanner.ico"


def splash_pixmap() -> QPixmap:
    """The image itself: the application's icon, its name, and what it is doing."""
    pixmap = QPixmap(WIDTH, HEIGHT)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(theme.BACKGROUND)))
        painter.drawRoundedRect(QRectF(0, 0, WIDTH, HEIGHT), CORNER_RADIUS, CORNER_RADIUS)

        icon_file = paths.resource_directory() / ICON_FILENAME
        # Through QIcon, not QPixmap: a .ico holds six squares and QPixmap takes the
        # first one it finds, which is the sixteen-pixel version blown up to ninety-six.
        icon = (
            QIcon(str(icon_file)).pixmap(ICON_SIDE, ICON_SIDE) if icon_file.is_file() else QPixmap()
        )
        left = 28
        if not icon.isNull():
            painter.drawPixmap(left, (HEIGHT - ICON_SIDE) // 2, icon)
            left += ICON_SIDE + 24

        text_area = QRectF(left, 0, WIDTH - left - 24, HEIGHT)
        title = QFont(painter.font())
        title.setPixelSize(30)
        title.setBold(True)
        painter.setFont(title)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            text_area.adjusted(0, 56, 0, 0), int(Qt.AlignmentFlag.AlignLeft), "SatisPlanner"
        )

        small = QFont(painter.font())
        small.setPixelSize(13)
        small.setBold(False)
        painter.setFont(small)
        painter.setPen(QColor(theme.TEXT_MUTED))
        painter.drawText(
            text_area.adjusted(0, 96, 0, 0),
            int(Qt.AlignmentFlag.AlignLeft),
            f"version {__version__} — lecture du catalogue...",
        )
    finally:
        painter.end()
    return pixmap


def show_splash() -> QLabel:
    """Put it on screen now, not at the next turn of the event loop.

    ``show()`` alone only queues the paint, and the loop is not running yet -- the
    whole point is the stretch before it starts -- so the image would appear exactly
    when it was no longer wanted. ``repaint()`` is what makes it synchronous.
    """
    pixmap = splash_pixmap()
    splash = QLabel(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    splash.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    splash.setPixmap(pixmap)
    splash.resize(pixmap.size())
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        centre = screen.availableGeometry().center()
        splash.move(centre.x() - pixmap.width() // 2, centre.y() - pixmap.height() // 2)
    splash.show()
    splash.repaint()
    return splash


def finish_splash(splash: QLabel | None, window: QWidget) -> None:
    """Dismiss it once the window it was covering for is up."""
    del window
    if splash is not None:
        splash.close()
