"""The edits a node accepts, each with exactly one implementation.

Every one of these can now be reached from **three** places -- a context menu on the
canvas, a cell in the table, and a double-click on the value where it is drawn -- and
the specification asks that all three produce the same command. When the clock was
added, "the same" was two functions written to agree and a test to prove they still
did; at three fields that was a promise waiting to be broken, and at three doors it
would already be broken. So the rule is structural: every door calls the function
below, and there is nothing left to keep in step.

Each returns ``None`` when the node now holds the requested value, or a French
sentence saying why it cannot. That is the same shape as ``commands.can_connect``,
and it lets the canvas show the reason in the status bar, the table refuse the edit
and leave the cell alone, and the inline editor stay open with what was typed still
in it.
"""

import logging
import math
from dataclasses import dataclass

from satisplanner.core import constants, formatting
from satisplanner.core.graph import (
    ExternalSourceNode,
    GeneratorNode,
    MachineNode,
    Node,
    OutputNode,
    ResourceNode,
    StorageNode,
    WaterExtractorNode,
)
from satisplanner.core.models import Purity, UnknownClassError
from satisplanner.ui.catalogue import PURITY_LABELS, extractor_choices, fuel_choices
from satisplanner.ui.commands import SetNodeFieldCommand, SetTransportCommand
from satisplanner.ui.document import FactoryDocument

logger = logging.getLogger(__name__)

# Below this two values are the same number written twice.
EPSILON = 1e-9


@dataclass(frozen=True)
class Quantity:
    """The number a given kind of node is sized by, and what to call it.

    The table has one "Quantite" column rather than a machine-count column that is
    blank on two rows out of three, the canvas has one number on the face of the
    node, and both mean whatever this says.
    """

    field: str
    label: str
    minimum: float = 0.0


def quantity_of(node: Node) -> Quantity | None:
    """Which field the quantity of this node is, if it has one."""
    match node:
        case MachineNode():
            return Quantity("machine_count", "machine(s)")
        case ResourceNode() | WaterExtractorNode():
            # Strictly positive in the model: zero extractors is a deleted node.
            return Quantity("count", "extracteur(s)", minimum=1e-9)
        case GeneratorNode():
            return Quantity("count", "générateur(s)", minimum=1e-9)
        case ExternalSourceNode():
            return Quantity("rate_per_minute", "/min")
        case StorageNode():
            return Quantity("initial_content", "en stock")
        case OutputNode():
            return None


def set_quantity(document: FactoryDocument, node_id: str, value: float) -> str | None:
    """Set however many of a thing a node stands for: machines, extractors, m3 in a tank.

    Refused rather than clamped when it is out of domain, for the same reason as the
    clock: a negative machine count silently turned into zero teaches the field
    accepts anything.
    """
    node = document.graph.node(node_id)
    quantity = quantity_of(node)
    if quantity is None:
        return "Ce nœud n'a pas de quantité."
    if math.isnan(value):  # what was typed was not a number at all
        return "Ce n'est pas un nombre."
    if value < quantity.minimum:
        floor = formatting.number(quantity.minimum)
        return f"Valeur hors domaine : {floor} au minimum."
    if abs(getattr(node, quantity.field) - value) < EPSILON:
        return None
    document.undo_stack.push(
        SetNodeFieldCommand(
            document,
            node_id,
            quantity.field,
            value,
            f"{node_id} : {formatting.number(value)} {quantity.label}",
        )
    )
    return None


def set_transport(document: FactoryDocument, edge_id: str, transport_class: str) -> str | None:
    """Change a line's tier, the last field that had a command of its own.

    Belongs here with the rest for exactly the same reason: the context menu on a
    line and a double-click on it must push one command, not two that agree.
    """
    edge = document.graph.edge(edge_id)
    game_data = document.game_data
    try:
        item = game_data.item(edge.item_class)
        matches = game_data.transport_form_matches(transport_class, item.form)
    except UnknownClassError as exc:
        return str(exc)
    if not matches:
        needed = "une tuyauterie" if item.form.is_fluid else "un convoyeur"
        return f"{item.display_name_fr} demande {needed}."
    if edge.transport_class == transport_class:
        return None
    label = game_data.buildings[transport_class].display_name_fr
    document.undo_stack.push(SetTransportCommand(document, edge_id, transport_class, label))
    return None


def set_clock_speed(document: FactoryDocument, node_id: str, clock_speed: float) -> str | None:
    """Over- or underclock a node, within the range the game allows.

    Out of range is refused rather than clamped: a 400 % silently corrected to 250 %
    teaches the user that the field accepts anything, which it does not.
    """
    node = document.graph.node(node_id)
    if not isinstance(node, MachineNode | ResourceNode | WaterExtractorNode):
        return "Ce nœud n'a pas de cadence."
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
            f"cadence à {formatting.percent(clock_speed)}",
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
        return "Seul un gisement a une pureté."
    try:
        wanted = Purity(purity)
    except ValueError:
        return f"Pureté inconnue : {purity}"
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


def set_fuel(document: FactoryDocument, node_id: str, fuel_class: str) -> str | None:
    """Change what a bank of generators burns.

    Refused when the building does not accept the fuel, on the catalogue's own list:
    a fuel generator runs on fuel, turbofuel or liquid biofuel, and a coal generator
    on none of them. The refusal is the same shape as the one an oil pump gets on an
    iron deposit -- a sentence in the status bar, never a silent correction.

    Changing the fuel changes the whole node: 20 m3/min of fuel and 7.5 of turbofuel
    buy the same 250 MW, which is precisely why this has to be one command and not a
    delete followed by a placement.
    """
    node = document.graph.node(node_id)
    if not isinstance(node, GeneratorNode):
        return "Seul un générateur a un carburant."
    allowed = dict(fuel_choices(document.game_data, node.generator_class))
    if fuel_class not in allowed:
        building = document.game_data.buildings.get(node.generator_class)
        name = building.display_name_fr if building else node.generator_class
        return f"{name} ne brûle pas ce carburant."
    if node.fuel_class == fuel_class:
        return None
    document.undo_stack.push(
        SetNodeFieldCommand(
            document, node_id, "fuel_class", fuel_class, f"carburant : {allowed[fuel_class]}"
        )
    )
    return None
