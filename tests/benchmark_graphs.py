"""Deterministic factories of a chosen size, for measuring rather than for testing.

Two runs of :func:`benchmark_graph` with the same size produce byte-identical
graphs, which is the whole point: a measurement is only comparable to another
measurement taken on the same thing. :data:`BENCHMARK_VERSION` is bumped whenever
the shape changes, so a threshold recorded against version 1 cannot silently be
compared to a figure taken on version 2.

The factory is assembled from four repeating **cells**, plus two structures that
appear exactly once because the engine treats them specially:

* a **recycling loop** -- recycled plastic feeds recycled rubber feeds recycled
  plastic -- which is the case the fixed point exists for;
* a **draining buffer** -- a full tank with no supply -- which is what makes
  :func:`engine.solve` run a second pair of fixed points.

Every one of the seven node kinds appears, every machine has a route out for
every product it makes, and one belt is deliberately left at Mk.1 under a flow
that does not fit, so that the uncapped companion run is genuinely needed. A
factory where no line is ever saturated would flatter any measurement taken on
the engine.
"""

from typing import Final

from satisplanner.core.graph import (
    Edge,
    ExternalSourceNode,
    FactoryGraph,
    GeneratorNode,
    MachineNode,
    Node,
    OutputNode,
    ResourceNode,
    StorageNode,
    WaterExtractorNode,
)
from satisplanner.core.models import Purity

# Bump whenever the generated shape changes. Thresholds quote it.
BENCHMARK_VERSION: Final = 1

# The three sizes the lot is measured at.
BENCHMARK_SIZES: Final[tuple[int, ...]] = (50, 200, 500)

BELT: Final = "Build_ConveyorBeltMk3_C"
SMALL_BELT: Final = "Build_ConveyorBeltMk1_C"
PIPE: Final = "Build_PipelineMK2_C"

# Grid the nodes are laid out on, wide enough that two boxes never overlap -- a
# canvas whose items all sit on top of each other would not measure anything a
# user will ever see.
COLUMN_WIDTH: Final = 340.0
ROW_HEIGHT: Final = 300.0
COLUMNS: Final = 12

_PURITIES: Final = (Purity.IMPURE, Purity.NORMAL, Purity.PURE)
_MINERS: Final = ("Build_MinerMk1_C", "Build_MinerMk2_C", "Build_MinerMk3_C")
_CLOCKS: Final = (1.0, 1.0, 0.75, 1.5, 1.0, 2.5)


class _Builder:
    """Collects nodes and edges, handing out positions and edge identifiers."""

    def __init__(self) -> None:
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        # Every smelter, so the odd nodes left over at the end can be hung off
        # them in turn rather than all on the first one.
        self.smelters: list[str] = []

    def add(self, node: Node) -> str:
        index = len(self.nodes)
        column, row = index % COLUMNS, index // COLUMNS
        moved = node.model_copy(update={"position": (column * COLUMN_WIDTH, row * ROW_HEIGHT)})
        self.nodes.append(moved)
        return moved.id

    def link(self, source: str, target: str, item_class: str, transport: str) -> None:
        self.edges.append(
            Edge(
                id=f"e{len(self.edges) + 1}",
                source=source,
                target=target,
                item_class=item_class,
                transport_class=transport,
            )
        )

    @property
    def size(self) -> int:
        return len(self.nodes)


def _recycling_loop(builder: _Builder, index: int) -> None:
    """Five nodes whose flows only settle at a fixed point. Costs 6 edges."""
    fuel = builder.add(
        ExternalSourceNode(
            id=f"entree{index}", item_class="Desc_LiquidFuel_C", rate_per_minute=120.0
        )
    )
    plastic = builder.add(
        MachineNode(id=f"machine{index}a", recipe_class="Recipe_Alternate_Plastic_1_C")
    )
    rubber = builder.add(
        MachineNode(id=f"machine{index}b", recipe_class="Recipe_Alternate_RecycledRubber_C")
    )
    plastic_out = builder.add(OutputNode(id=f"sortie{index}a", item_class="Desc_Plastic_C"))
    rubber_out = builder.add(OutputNode(id=f"sortie{index}b", item_class="Desc_Rubber_C"))

    builder.link(fuel, plastic, "Desc_LiquidFuel_C", PIPE)
    builder.link(fuel, rubber, "Desc_LiquidFuel_C", PIPE)
    builder.link(plastic, rubber, "Desc_Plastic_C", BELT)
    builder.link(rubber, plastic, "Desc_Rubber_C", BELT)
    builder.link(plastic, plastic_out, "Desc_Plastic_C", BELT)
    builder.link(rubber, rubber_out, "Desc_Rubber_C", BELT)


def _draining_buffer(builder: _Builder, index: int) -> None:
    """A full tank with nothing feeding it: four nodes, and a second solve."""
    tank = builder.add(
        StorageNode(
            id=f"tampon{index}",
            storage_class="Build_PipeStorageTank_C",
            item_class="Desc_LiquidOil_C",
            initial_content=400.0,
        )
    )
    refinery = builder.add(MachineNode(id=f"machine{index}c", recipe_class="Recipe_Plastic_C"))
    plastic_out = builder.add(OutputNode(id=f"sortie{index}c", item_class="Desc_Plastic_C"))
    flare = builder.add(
        OutputNode(id=f"rejet{index}", item_class="Desc_HeavyOilResidue_C", is_sink=True)
    )

    builder.link(tank, refinery, "Desc_LiquidOil_C", PIPE)
    builder.link(refinery, plastic_out, "Desc_Plastic_C", BELT)
    builder.link(refinery, flare, "Desc_HeavyOilResidue_C", PIPE)


def _iron_cell(builder: _Builder, index: int) -> None:
    """Ore to reinforced plates through a buffer: eight nodes, eight edges.

    The first cell's mining belt is left at Mk.1 on purpose. One line too small in
    a factory is normal, and it is what keeps the uncapped companion run honest.

    Every recipe here is one the test fixtures carry as well as the shipped
    database, so the same generated factory can be measured against the real
    catalogue and asserted against the slice.
    """
    deposit = builder.add(
        ResourceNode(
            id=f"gisement{index}",
            item_class="Desc_OreIron_C",
            extractor_class=_MINERS[index % len(_MINERS)],
            purity=_PURITIES[index % len(_PURITIES)],
            count=float(1 + index % 3),
            clock_speed=_CLOCKS[index % len(_CLOCKS)],
        )
    )
    smelter = builder.add(
        MachineNode(id=f"machine{index}d", recipe_class="Recipe_IngotIron_C", machine_count=4.0)
    )
    builder.smelters.append(smelter)
    buffer = builder.add(
        StorageNode(id=f"tampon{index}b", storage_class="Build_StorageContainerMk2_C")
    )
    plates = builder.add(
        MachineNode(id=f"machine{index}e", recipe_class="Recipe_IronPlate_C", machine_count=2.0)
    )
    rods = builder.add(
        MachineNode(id=f"machine{index}f", recipe_class="Recipe_IronRod_C", machine_count=3.0)
    )
    screws = builder.add(
        MachineNode(id=f"machine{index}g", recipe_class="Recipe_Screw_C", machine_count=2.0)
    )
    reinforced = builder.add(
        MachineNode(
            id=f"machine{index}i",
            recipe_class="Recipe_IronPlateReinforced_C",
            machine_count=1.0,
        )
    )
    out = builder.add(OutputNode(id=f"sortie{index}d", item_class="Desc_IronPlateReinforced_C"))

    builder.link(deposit, smelter, "Desc_OreIron_C", SMALL_BELT if index == 1 else BELT)
    builder.link(smelter, buffer, "Desc_IronIngot_C", BELT)
    builder.link(buffer, plates, "Desc_IronIngot_C", BELT)
    builder.link(buffer, rods, "Desc_IronIngot_C", BELT)
    builder.link(rods, screws, "Desc_IronRod_C", BELT)
    builder.link(plates, reinforced, "Desc_IronPlate_C", BELT)
    builder.link(screws, reinforced, "Desc_IronScrew_C", BELT)
    builder.link(reinforced, out, "Desc_IronPlateReinforced_C", BELT)


def _power_cell(builder: _Builder, index: int) -> None:
    """A coal bank and an imported-coal bank sharing a pump: five nodes, four edges."""
    coal = builder.add(
        ResourceNode(
            id=f"gisement{index}b",
            item_class="Desc_Coal_C",
            extractor_class="Build_MinerMk2_C",
            purity=_PURITIES[index % len(_PURITIES)],
            count=2.0,
        )
    )
    pump = builder.add(
        WaterExtractorNode(id=f"pompe{index}", extractor_class="Build_WaterPump_C", count=2.0)
    )
    imported = builder.add(
        ExternalSourceNode(id=f"entree{index}b", item_class="Desc_Coal_C", rate_per_minute=90.0)
    )
    burner = builder.add(
        GeneratorNode(
            id=f"generateur{index}",
            generator_class="Build_GeneratorCoal_C",
            fuel_class="Desc_Coal_C",
            count=4.0,
        )
    )
    imported_burner = builder.add(
        GeneratorNode(
            id=f"generateur{index}b",
            generator_class="Build_GeneratorCoal_C",
            fuel_class="Desc_Coal_C",
            count=3.0,
        )
    )

    builder.link(coal, burner, "Desc_Coal_C", BELT)
    builder.link(pump, burner, "Desc_Water_C", PIPE)
    builder.link(imported, imported_burner, "Desc_Coal_C", BELT)
    builder.link(pump, imported_burner, "Desc_Water_C", PIPE)


def _oil_cell(builder: _Builder, index: int) -> None:
    """An oil deposit refined into plastic, residue flared: four nodes, three edges."""
    well = builder.add(
        ResourceNode(
            id=f"gisement{index}c",
            item_class="Desc_LiquidOil_C",
            extractor_class="Build_OilPump_C",
            purity=_PURITIES[index % len(_PURITIES)],
        )
    )
    refinery = builder.add(
        MachineNode(id=f"machine{index}j", recipe_class="Recipe_Plastic_C", machine_count=3.0)
    )
    plastic_out = builder.add(OutputNode(id=f"sortie{index}e", item_class="Desc_Plastic_C"))
    flare = builder.add(
        OutputNode(id=f"rejet{index}b", item_class="Desc_HeavyOilResidue_C", is_sink=True)
    )

    builder.link(well, refinery, "Desc_LiquidOil_C", PIPE)
    builder.link(refinery, plastic_out, "Desc_Plastic_C", BELT)
    builder.link(refinery, flare, "Desc_HeavyOilResidue_C", PIPE)


# One round of cells, and what it costs in nodes.
_CELLS: Final = (_iron_cell, _power_cell, _oil_cell)
_ROUND_SIZE: Final = 8 + 5 + 4

# The two structures that appear once.
_SPECIAL_SIZE: Final = 5 + 4

# Below this there is no room for one round of cells on top of the two special
# structures, and a graph missing either of them measures a different engine.
MINIMUM_SIZE: Final = _SPECIAL_SIZE + _ROUND_SIZE


def benchmark_graph(size: int) -> FactoryGraph:
    """A connected factory of exactly ``size`` nodes, always the same one.

    Sizes below :data:`MINIMUM_SIZE` are refused rather than silently rounded up:
    the recycling loop and the draining buffer are not optional, and a graph
    without them would not be measuring the same engine.
    """
    if size < MINIMUM_SIZE:
        msg = f"un graphe d'essai fait au moins {MINIMUM_SIZE} noeuds, pas {size}"
        raise ValueError(msg)

    builder = _Builder()
    _recycling_loop(builder, 0)
    _draining_buffer(builder, 0)

    index = 1
    while size - builder.size >= _ROUND_SIZE:
        for cell in _CELLS:
            cell(builder, index)
        index += 1

    # The remainder -- at most one round short of a whole one -- as extra exits
    # hung off the smelters in turn. Spread rather than stacked on the first, so
    # that no single cell ends up carrying a splitter bank the others do not have
    # and the measurement stays about size rather than about one odd node.
    for extra in range(size - builder.size):
        exit_id = builder.add(OutputNode(id=f"sortie0x{extra}", item_class="Desc_IronIngot_C"))
        builder.link(
            builder.smelters[extra % len(builder.smelters)], exit_id, "Desc_IronIngot_C", BELT
        )

    return FactoryGraph(nodes=builder.nodes, edges=builder.edges)
