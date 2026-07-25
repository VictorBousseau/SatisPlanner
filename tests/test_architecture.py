"""Enforce the layering rule by static analysis: ``ui -> core <- data``.

Every import statement of every module in ``satisplanner/core`` is inspected with
``ast`` -- no module is actually imported, so the check holds even for code that
cannot run yet. Relative imports are resolved to their absolute name before
being matched, otherwise ``from ..data import db`` would slip through.
"""

import ast
from pathlib import Path

import pytest

import satisplanner

PACKAGE_ROOT = Path(satisplanner.__file__).parent
SOURCE_ROOT = PACKAGE_ROOT.parent
CORE_ROOT = PACKAGE_ROOT / "core"

# Prefixes that `core` must never depend on: the Qt binding, and the two sibling
# layers. Matching is on dotted prefixes so `PySide6.QtCore` is caught as well.
FORBIDDEN_PREFIXES = (
    "PySide6",
    "shiboken6",
    "satisplanner.ui",
    "satisplanner.data",
)


def core_modules() -> list[Path]:
    """Every Python source file of the core package."""
    return sorted(CORE_ROOT.rglob("*.py"))


def module_name_of(path: Path) -> str:
    """Dotted module name of a source file inside the satisplanner package."""
    parts = list(path.relative_to(SOURCE_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def package_name_of(path: Path) -> str:
    """Dotted name of the package that ``path`` belongs to.

    This is what relative imports are resolved against: for ``core/engine.py``
    it is ``satisplanner.core``, and for ``core/__init__.py`` it is the same --
    dropping the last part is correct in both cases.
    """
    parts = list(path.relative_to(SOURCE_ROOT).with_suffix("").parts)
    parts.pop()
    return ".".join(parts)


def resolve_relative(module: str | None, level: int, package: str) -> str:
    """Turn a relative import into an absolute module name.

    ``level`` 1 means "the package holding the importer", 2 means its parent, and
    so on. ``module`` is empty for ``from . import x``.
    """
    parts = package.split(".")
    base = ".".join(parts[: len(parts) - (level - 1)])
    return f"{base}.{module}" if module else base


def parse_imports(source: str, package: str) -> list[tuple[str, int]]:
    """Absolute module names imported by ``source``, each with its line number."""
    tree = ast.parse(source)
    found: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                found.append((resolve_relative(node.module, node.level, package), node.lineno))
            elif node.module:
                found.append((node.module, node.lineno))

    return found


def is_forbidden(module: str) -> bool:
    """True if ``module`` is, or lives under, a forbidden package."""
    return any(module == p or module.startswith(f"{p}.") for p in FORBIDDEN_PREFIXES)


def test_core_package_is_not_empty() -> None:
    """Guard against the layering test passing merely because core/ has no code."""
    modules = [p for p in core_modules() if p.name != "__init__.py"]
    assert modules, "core/ ne contient aucun module : le test de couches ne prouverait rien"


@pytest.mark.parametrize("path", core_modules(), ids=module_name_of)
def test_core_does_not_import_qt_or_sibling_layers(path: Path) -> None:
    # utf-8-sig, not utf-8: an editor-added BOM would otherwise make ast.parse
    # raise a SyntaxError and mask the layering violation we are looking for.
    imports = parse_imports(path.read_text(encoding="utf-8-sig"), package_name_of(path))
    violations = [
        f"{path.name}:{lineno} importe {module}"
        for module, lineno in imports
        if is_forbidden(module)
    ]
    assert not violations, "core/ doit rester pur :\n" + "\n".join(violations)


def test_checker_catches_absolute_violations() -> None:
    source = "from PySide6.QtWidgets import QWidget\nimport satisplanner.data.db\n"
    modules = [m for m, _ in parse_imports(source, "satisplanner.core")]
    assert [m for m in modules if is_forbidden(m)] == [
        "PySide6.QtWidgets",
        "satisplanner.data.db",
    ]


def test_checker_catches_relative_violations() -> None:
    source = "from ..data import db\nfrom ..ui.theme import ACCENT\n"
    modules = [m for m, _ in parse_imports(source, "satisplanner.core")]
    assert [m for m in modules if is_forbidden(m)] == ["satisplanner.data", "satisplanner.ui.theme"]


def test_sibling_relative_import_is_allowed() -> None:
    source = "from .constants import MAX_ITERATIONS\n"
    modules = [m for m, _ in parse_imports(source, "satisplanner.core")]
    assert modules == ["satisplanner.core.constants"]
    assert not any(is_forbidden(m) for m in modules)


def test_package_name_resolution() -> None:
    assert package_name_of(CORE_ROOT / "engine.py") == "satisplanner.core"
    assert package_name_of(CORE_ROOT / "__init__.py") == "satisplanner.core"
    assert module_name_of(CORE_ROOT / "engine.py") == "satisplanner.core.engine"
    assert module_name_of(CORE_ROOT / "__init__.py") == "satisplanner.core"
