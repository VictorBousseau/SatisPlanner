"""Where the application's files live, in development and once packaged.

Two questions with different answers, and confusing them is the classic packaging bug.

**Read-only resources** -- the game database, the icons shipped alongside it -- sit
next to the package while developing, and are unpacked beside the executable by
PyInstaller, which announces that directory in ``sys._MEIPASS``. Deriving them from
``__file__`` happens to work for a one-directory build, but only by accident of how
the archive is laid out; asking ``sys`` is the answer that stays true.

**Writable files** -- the log, a user's own icon export -- never belong next to the
executable. An application installed under ``C:\\Program Files`` cannot write there,
and the first thing it would try to write is the crash log. They go under
``%LOCALAPPDATA%``.

Nothing here imports Qt. ``QStandardPaths`` answers the second question too, but the
log has to exist before there is a ``QApplication`` -- a crash handler that needs the
toolkit to be up is a crash handler that fails exactly when it is wanted.
"""

import os
import sys
from pathlib import Path
from typing import Final

APPLICATION_NAME: Final = "SatisPlanner"

# Sub-directories of the application's own data directory.
LOG_SUBPATH: Final = "logs"
ICON_SUBPATH: Final = "icons"


def is_frozen() -> bool:
    """True when running from a PyInstaller build rather than from sources."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_directory() -> Path:
    """The directory holding the database and the embedded icons.

    Packaged, this is ``<exe>/_internal/satisplanner/resources``: the build script
    adds the data under that exact name so the two layouts agree.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base is not None:
        return Path(base) / "satisplanner" / "resources"
    return Path(__file__).resolve().parent / "resources"


def app_data_directory() -> Path:
    """Per-user writable directory. Not created here -- see :func:`ensure_directory`.

    ``%LOCALAPPDATA%`` rather than the roaming profile: a log and a few hundred
    exported icons have no business being synchronised across a domain's machines.
    """
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / ".satisplanner"
    return base / APPLICATION_NAME


def log_directory() -> Path:
    return app_data_directory() / LOG_SUBPATH


def default_user_icon_directory() -> Path:
    """Where a user's own FModel export is looked for when they name no other."""
    return app_data_directory() / ICON_SUBPATH


def ensure_directory(path: Path) -> Path | None:
    """Create a directory, returning ``None`` rather than raising if it cannot be.

    Callers here are the logger and the icon index, and neither has any business
    stopping the application because a directory could not be made.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path
