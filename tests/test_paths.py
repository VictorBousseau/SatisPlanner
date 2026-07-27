"""Where the application looks for its files, developed and frozen.

The frozen case is the one that cannot be tried by hand every time, and it is the one
that breaks: an executable that starts perfectly and shows an empty palette because
the database was looked for inside an archive rather than beside it.
"""

from pathlib import Path

import pytest

from satisplanner import paths
from satisplanner.data import db, icons


def test_resources_sit_next_to_the_package_when_running_from_sources() -> None:
    assert paths.resource_directory() == Path(paths.__file__).resolve().parent / "resources"
    assert db.default_database_path().is_file(), "la base livree doit être presente"


def test_a_frozen_run_reads_the_unpacked_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """PyInstaller announces where it unpacked; that is where the data really is."""
    monkeypatch.setattr("sys._MEIPASS", r"C:\ailleurs\_internal", raising=False)
    expected = Path(r"C:\ailleurs\_internal") / "satisplanner" / "resources"
    assert paths.resource_directory() == expected
    assert db.default_database_path() == expected / db.DEFAULT_DATABASE_NAME
    assert icons.embedded_icon_directory() == expected / "icons"


def test_the_frozen_flag_needs_both_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    assert paths.is_frozen() is False
    monkeypatch.setattr("sys.frozen", True, raising=False)
    assert paths.is_frozen() is False, "sys.frozen seul ne suffit pas"
    monkeypatch.setattr("sys._MEIPASS", r"C:\ailleurs", raising=False)
    assert paths.is_frozen() is True


def test_writable_files_go_under_local_app_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\quelqu-un\AppData\Local")
    assert paths.app_data_directory() == Path(r"C:\Users\quelqu-un\AppData\Local\SatisPlanner")
    assert paths.log_directory().name == paths.LOG_SUBPATH
    assert paths.default_user_icon_directory().name == paths.ICON_SUBPATH


def test_a_missing_local_app_data_still_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path is always returned: the logger has nowhere else to go."""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert paths.app_data_directory().name == "SatisPlanner"


def test_ensure_directory_creates_and_never_raises(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c"
    assert paths.ensure_directory(target) == target
    assert target.is_dir()

    # A file where a directory should be: refused, but not with an exception -- the
    # caller is the crash logger and it has no business crashing.
    blocked = tmp_path / "fichier"
    blocked.write_text("", encoding="utf-8")
    assert paths.ensure_directory(blocked / "sous-dossier") is None
