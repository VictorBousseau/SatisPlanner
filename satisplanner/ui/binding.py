"""Connections made and undone as one piece.

Rebinding a shared panel from one document to another is the kind of change that
never fails loudly. A connection left behind does not stop the panel working: it
makes it redraw twice, then three times, then four, and what it displays stays
correct the whole way down. Nothing shows until a factory is big enough for the
third redraw to be felt, and by then the forgotten connection is months old.

The answer is not to re-read both halves, it is to stop writing them twice.
Everything bound goes through :meth:`DocumentBinding.bind`, which notes it, and
:meth:`unbind` plays that note backwards. Forgetting to undo a connection would
mean forgetting to make it.
"""

import logging
from collections.abc import Callable

from PySide6.QtCore import SignalInstance

logger = logging.getLogger(__name__)


class DocumentBinding:
    """Every connection belonging to the document currently being shown."""

    def __init__(self) -> None:
        self._links: list[tuple[SignalInstance, Callable[..., object]]] = []

    def bind(self, signal: SignalInstance, slot: Callable[..., object]) -> None:
        """Connect, and keep what it takes to disconnect."""
        signal.connect(slot)
        self._links.append((signal, slot))

    def unbind(self) -> None:
        """Undo the lot, last first, and start again from nothing."""
        for signal, slot in reversed(self._links):
            signal.disconnect(slot)
        self._links.clear()

    def __len__(self) -> int:
        """How many connections are live. The same after every rebinding."""
        return len(self._links)
