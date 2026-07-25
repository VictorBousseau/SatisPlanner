"""Icon index: resolution by file name, whatever the export tree looks like."""

from pathlib import Path

from satisplanner.data.icons import USER_ICON_SUBPATH, IconIndex, default_icon_roots


def _icon(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return path


def test_resolution_ignores_the_directory_layout(tmp_path: Path) -> None:
    """FModel's tree is arbitrary, so only the file name may matter."""
    deep = _icon(tmp_path / "Resource" / "Parts" / "Plastic" / "UI", "IconDesc_Plastic_256.png")
    index = IconIndex([tmp_path])
    assert index.resolve("IconDesc_Plastic_256.png") == deep


def test_resolution_is_case_insensitive(tmp_path: Path) -> None:
    _icon(tmp_path, "IconDesc_Plastic_256.png")
    index = IconIndex([tmp_path])
    assert index.resolve("icondesc_plastic_256.PNG") is not None


def test_unknown_and_empty_names_resolve_to_none(tmp_path: Path) -> None:
    index = IconIndex([tmp_path])
    assert index.resolve("absent.png") is None
    assert index.resolve(None) is None
    assert index.resolve("") is None


def test_the_first_root_wins(tmp_path: Path) -> None:
    embedded = _icon(tmp_path / "embedded", "IconDesc_Plastic_256.png")
    _icon(tmp_path / "user", "IconDesc_Plastic_256.png")
    index = IconIndex([tmp_path / "embedded", tmp_path / "user"])
    assert index.resolve("IconDesc_Plastic_256.png") == embedded


def test_a_user_directory_completes_the_embedded_one(tmp_path: Path) -> None:
    _icon(tmp_path / "embedded", "a_256.png")
    extra = _icon(tmp_path / "user", "b_256.png")
    index = IconIndex([tmp_path / "embedded", tmp_path / "user"])
    assert index.resolve("b_256.png") == extra
    assert len(index) == 2


def test_non_image_files_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    _icon(tmp_path, "IconDesc_Plastic_256.png")
    assert len(IconIndex([tmp_path])) == 1


def test_a_missing_root_is_not_an_error(tmp_path: Path) -> None:
    index = IconIndex([tmp_path / "does-not-exist"])
    assert len(index) == 0
    assert index.resolve("anything.png") is None


def test_missing_lists_only_unresolved_names(tmp_path: Path) -> None:
    _icon(tmp_path, "present_256.png")
    index = IconIndex([tmp_path])
    assert index.missing(["present_256.png", "absent_256.png", None]) == ["absent_256.png"]


def test_default_roots_skip_directories_that_do_not_exist(tmp_path: Path) -> None:
    roots = default_icon_roots(app_data_directory=tmp_path / "nope")
    assert all(root.is_dir() for root in roots)


def test_the_user_directory_comes_after_the_embedded_one(tmp_path: Path) -> None:
    """First root wins, so a user export never shadows what the package ships."""
    user_icons = tmp_path / USER_ICON_SUBPATH
    user_icons.mkdir(parents=True)
    roots = default_icon_roots(app_data_directory=tmp_path)
    assert roots[-1] == user_icons
