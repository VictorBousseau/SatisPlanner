"""The edits a node accepts, each with exactly one implementation.

Every one of these can be reached from two places -- a context menu on the canvas and
a cell in the table -- and the specification asks that both produce the same command.
When the clock was added, "the same" was two functions written to agree and a test to
prove they still did; three fields later that is a promise waiting to be broken. So
the rule is now structural: the menu and the table both call the function below, and
there is nothing left to keep in step.

Each returns ``None`` when the node now holds the requested value, or a French
sentence saying why it cannot. That is the same shape as ``commands.can_connect``,
and it lets the canvas show the reason in the status bar while the table simply
refuses the edit and leaves the cell alone.
"""

import logging

from satisplanner.core import constants, formatting
from satisplanner.core.graph import (
    MachineNode,
    ResourceNode,
    WaterExtractorNode,
)
from satisplanner.core.models import Purity
from satisplanner.ui.catalogue import PURITY_LABELS, extractor_choices
from satisplanner.ui.commands import SetNodeFieldCommand
from satisplanner.ui.document import FactoryDocument

logger = logging.getLogger(__name__)

# Below this two values are the same number written twice.
EPSILON = 1e-9


def set_clock_speed(document: FactoryDocument, node_id: str, clock_speed: float) -> str | None:
    """Over- or underclock a node, within the range the game allows.

    Out of range is refused rather than clamped: a 400 % silently corrected to 250 %
    teaches the user that the field accepts anything, which it does not.
    """
    node = document.graph.node(node_id)
    if not isinstance(node, MachineNode | ResourceNode | WaterExtractorNode):
        return "Ce noeud n'a pas de cadence."
    if not constants.MIN_CLOCK_SPEED <= clock_speed <= constants.MAX_CLOCK_SPEED:
        return (
            f"Cadence hors domaine : {formatting.percent(constants.MIN_CLOCK_SPEED)} a "
            f"{formatting.percent(constants.MAX_CLOCK_SPEED)}."
        )
    if abs(node.clock_speed - clock_speed) < EPSILON:
        return None
    document.undo_stack.push(
        SetNodeFieldCommand(
            document,
            node_id,
            "clock_speed",
            clock_speed,
            f"cadence a {formatting.percent(clock_speed)}",
        )
    )
    return None


def set_purity(document: FactoryDocument, node_id: str, purity: Purity | str) -> str | None:
    """Set a deposit's purity, which multiplies what **every** extractor on it pulls.

    One node is one deposit: the purity applies to all ``count`` extractors standing
    on it, because that is what a deposit is. Two deposits of different purities are
    two nodes, and the application has no way to express anything else -- which is
    the honest modelling, not a limitation to work around.
    """
    node = document.graph.node(node_id)
    if not isinstance(node, ResourceNode):
        return "Seul un gisement a une purete."
    try:
        wanted = Purity(purity)
    except ValueError:
        return f"Purete inconnue : {purity}"
    if node.purity is wanted:
        return None
    document.undo_stack.push(
        SetNodeFieldCommand(
            document, node_id, "purity", wanted, f"gisement {PURITY_LABELS[wanted].lower()}"
        )
    )
    return None


def set_extractor(document: FactoryDocument, node_id: str, extractor_class: str) -> str | None:
    """Swap the extractor working a deposit, without losing the node or its lines.

    Refused when the extractor could not work this resource at all. The check is the
    catalogue's own -- form, and the single resource a specialised extractor names --
    so upgrading a Mk.2 to a Mk.3 goes through and putting an oil pump on iron does
    not.
    """
    node = document.graph.node(node_id)
    if not isinstance(node, ResourceNode):
        return "Seul un gisement a un extracteur interchangeable."
    allowed = dict(extractor_choices(document.game_data, node.item_class))
    if extractor_class not in allowed:
        item = document.game_data.item(node.item_class).display_name_fr
        return f"Cet extracteur ne peut pas travailler {item}."
    if node.extractor_class == extractor_class:
        return None
    document.undo_stack.push(
        SetNodeFieldCommand(
            document, node_id, "extractor_class", extractor_class, allowed[extractor_class]
        )
    )
    return None
