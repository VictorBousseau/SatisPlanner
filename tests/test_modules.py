"""The module library: what it saves, what it refuses, and what it labels.

Two things here are worth more than the rest.

The **interface** is computed rather than assumed. A module lifted out of the middle
of a chain has nothing feeding it and nowhere to send what it makes, and in this
engine both of those resolve to zero -- so a naive reading would label every useful
module "produces nothing". The figures are therefore taken with the module fed and
drained, and the tests below pin them against arithmetic done on paper.

The **library survives its own files**. One unreadable module must cost one module,
never the library, so the failure paths get as much room as the happy one.
"""

import json
from pathlib import Path

import pytest

from satisplanner.core import interface
from satisplanner.core.graph import (
    ExternalSourceNode,
    FactoryGraph,
    MachineNode,
    OutputNode,
)
from satisplanner.core.models import GameData
from satisplanner.data import factory_file, module_file
from satisplanner.data.module_file import FactoryModule, ModuleError

BELT = "Build_ConveyorBeltMk3_C"
INGOT = "Desc_IronIngot_C"
PLATE = "Desc_IronPlate_C"
ROD = "Desc_IronRod_C"
SCREW = "Desc_IronScrew_C"
REINFORCED = "Desc_IronPlateReinforced_C"


def plates(game_data: GameData, machines: float = 2.0) -> FactoryGraph:
    """``machines`` constructors turning ingots into plates, wired to nothing.

    One constructor is 30 ingots/min in and 20 plates/min out.
    """
    del game_data
    return FactoryGraph(
        nodes=[MachineNode(id="plaque1", recipe_class="Recipe_IronPlate_C", machine_count=machines)]
    )


def module_of(graph: FactoryGraph, game_data: GameData, name: str = "Essai") -> FactoryModule:
    face = interface.interface_of(graph, game_data)
    return FactoryModule(
        name=name,
        share_code=factory_file.encode_share_code(graph),
        inputs=face.inputs,
        outputs=face.outputs,
    )


# --------------------------------------------------------------------------- #
# The interface, resolved fed and drained
# --------------------------------------------------------------------------- #


def test_a_module_lifted_from_the_middle_of_a_chain_still_has_figures(
    game_data: GameData,
) -> None:
    """Two constructors: 60 ingots a minute in, 40 plates a minute out.

    Left as it stands the module has no supply and no outlet, and this engine
    answers zero to both. The label is taken with the boundary served, which is
    the only reading that says anything about the module.
    """
    face = interface.interface_of(plates(game_data), game_data)

    assert face.inputs == {INGOT: 60.0}
    assert face.outputs == {PLATE: 40.0}


def test_the_open_ports_are_the_interface_to_wire_up(game_data: GameData) -> None:
    face = interface.interface_of(plates(game_data), game_data)

    assert {(port.item_class, port.is_output) for port in face.ports} == {
        (INGOT, False),
        (PLATE, True),
    }
    assert not face.is_closed


def test_a_port_an_edge_already_serves_is_not_an_open_port(game_data: GameData) -> None:
    """The interface is what is *left* to wire, so an internal line is not part of it."""
    graph = FactoryGraph(
        nodes=[
            MachineNode(id="tige1", recipe_class="Recipe_IronRod_C", machine_count=1),
            MachineNode(id="vis1", recipe_class="Recipe_Screw_C", machine_count=1),
        ]
    )
    graph.connect("tige1", "vis1", ROD, BELT, game_data)

    face = interface.interface_of(graph, game_data)

    assert {(port.node_id, port.item_class) for port in face.ports} == {
        ("tige1", INGOT),
        ("vis1", SCREW),
    }


def test_a_module_that_needs_nothing_from_outside_says_so(game_data: GameData) -> None:
    """A closed module: its own source, its own exit, nothing to connect."""
    graph = FactoryGraph(
        nodes=[
            ExternalSourceNode(id="entree1", item_class=INGOT, rate_per_minute=30.0),
            MachineNode(id="plaque1", recipe_class="Recipe_IronPlate_C", machine_count=1),
            OutputNode(id="sortie1", item_class=PLATE),
        ]
    )
    graph.connect("entree1", "plaque1", INGOT, BELT, game_data)
    graph.connect("plaque1", "sortie1", PLATE, BELT, game_data)

    face = interface.interface_of(graph, game_data)

    assert face.is_closed
    assert face.inputs == {}
    assert face.outputs == {PLATE: 20.0}, "sa sortie propre compte comme une sortie"


def test_an_internal_bottleneck_shows_in_the_label(game_data: GameData) -> None:
    """The label is not the nameplate: what the module *does* is what is written.

    Four plate constructors and one screw constructor feeding one assembler. The
    assembler wants 60 screws a minute and gets 40, so it runs at two thirds and
    the whole module with it -- and the label says 3,333 reinforced plates rather
    than the 5 the nameplate would promise.
    """
    graph = FactoryGraph(
        nodes=[
            MachineNode(id="plaque1", recipe_class="Recipe_IronPlate_C", machine_count=4),
            MachineNode(id="vis1", recipe_class="Recipe_Screw_C", machine_count=1),
            MachineNode(
                id="renforcee1", recipe_class="Recipe_IronPlateReinforced_C", machine_count=1
            ),
        ]
    )
    graph.connect("plaque1", "renforcee1", PLATE, BELT, game_data)
    graph.connect("vis1", "renforcee1", SCREW, BELT, game_data)

    face = interface.interface_of(graph, game_data)

    assert face.outputs[REINFORCED] == pytest.approx(10 / 3)
    assert face.inputs[ROD] == 10.0
    assert face.inputs[INGOT] == pytest.approx(30.0), (
        "les constructeurs de plaques sont en contre-pression et n'avalent pas leur nominal"
    )


def test_an_empty_selection_has_no_interface(game_data: GameData) -> None:
    face = interface.interface_of(FactoryGraph(), game_data)
    assert face.is_closed
    assert face.inputs == face.outputs == {}


# --------------------------------------------------------------------------- #
# Saving and reading back
# --------------------------------------------------------------------------- #


def test_a_saved_module_comes_back_with_the_same_factory(
    game_data: GameData, tmp_path: Path
) -> None:
    graph = plates(game_data)
    saved = module_file.save_module(module_of(graph, game_data, "40 plaques"), tmp_path)

    (reread,) = module_file.load_library(tmp_path)[0]

    assert reread.name == "40 plaques"
    assert reread.inputs == {INGOT: 60.0}
    assert reread.outputs == {PLATE: 40.0}
    assert [node.id for node in reread.graph().nodes] == ["plaque1"]
    assert reread.path == saved.path


def test_saving_stamps_a_date_without_being_asked(game_data: GameData, tmp_path: Path) -> None:
    saved = module_file.save_module(module_of(plates(game_data), game_data), tmp_path)
    assert saved.saved_at, "un module sans date ne peut pas être trié dans le temps"


def test_two_modules_of_the_same_name_do_not_overwrite_each_other(
    game_data: GameData, tmp_path: Path
) -> None:
    """A library where saving twice silently loses the first is not a library."""
    first = module_file.save_module(module_of(plates(game_data), game_data, "Plaques"), tmp_path)
    second = module_file.save_module(module_of(plates(game_data), game_data, "Plaques"), tmp_path)

    assert first.path != second.path
    assert len(module_file.load_library(tmp_path)[0]) == 2


def test_re_saving_over_its_own_file_replaces_it(game_data: GameData, tmp_path: Path) -> None:
    first = module_file.save_module(module_of(plates(game_data), game_data, "Plaques"), tmp_path)
    module_file.save_module(
        module_of(plates(game_data, machines=3.0), game_data, "Plaques"),
        tmp_path,
        replacing=first.path,
    )

    (only,) = module_file.load_library(tmp_path)[0]
    assert only.outputs == {PLATE: 60.0}


def test_renaming_moves_the_file_rather_than_leaving_a_twin(
    game_data: GameData, tmp_path: Path
) -> None:
    saved = module_file.save_module(module_of(plates(game_data), game_data, "Avant"), tmp_path)

    renamed = module_file.rename_module(saved, "Après")

    modules, _ = module_file.load_library(tmp_path)
    assert [module.name for module in modules] == ["Après"]
    assert renamed.path is not None and renamed.path.exists()
    assert not saved.path.exists()  # type: ignore[union-attr]


def test_describing_keeps_everything_else(game_data: GameData, tmp_path: Path) -> None:
    saved = module_file.save_module(module_of(plates(game_data), game_data, "Plaques"), tmp_path)

    module_file.rename_module(saved, "Plaques", "Sert de base aux plaques renforcées.")

    (only,) = module_file.load_library(tmp_path)[0]
    assert only.description == "Sert de base aux plaques renforcées."
    assert only.outputs == {PLATE: 40.0}


def test_deleting_removes_the_file(game_data: GameData, tmp_path: Path) -> None:
    saved = module_file.save_module(module_of(plates(game_data), game_data), tmp_path)
    module_file.delete_module(saved)
    assert module_file.load_library(tmp_path)[0] == []


def test_an_absent_library_is_empty_rather_than_an_error(tmp_path: Path) -> None:
    assert module_file.load_library(tmp_path / "jamais-cree") == ([], [])


# --------------------------------------------------------------------------- #
# What must not bring the library down
# --------------------------------------------------------------------------- #


def test_a_corrupt_file_costs_one_module_and_not_the_library(
    game_data: GameData, tmp_path: Path
) -> None:
    """The reason there is one file per module rather than one library file."""
    module_file.save_module(module_of(plates(game_data), game_data, "Bon"), tmp_path)
    (tmp_path / f"casse{module_file.MODULE_SUFFIX}").write_text("{ pas du json", encoding="utf-8")

    modules, problems = module_file.load_library(tmp_path)

    assert [module.name for module in modules] == ["Bon"]
    assert len(problems) == 1
    assert "casse" in problems[0]


def test_a_file_with_no_code_in_it_is_refused_by_name(tmp_path: Path) -> None:
    path = tmp_path / f"vide{module_file.MODULE_SUFFIX}"
    path.write_text(json.dumps({"name": "Vide"}), encoding="utf-8")

    with pytest.raises(ModuleError, match="aucun module"):
        module_file.load_module(path)


def test_a_module_from_a_newer_version_is_refused_with_a_sentence(tmp_path: Path) -> None:
    path = tmp_path / f"futur{module_file.MODULE_SUFFIX}"
    path.write_text(
        json.dumps({"module_version": 99, "name": "Futur", "code": "SFP1:x"}), encoding="utf-8"
    )

    with pytest.raises(ModuleError, match="plus récente"):
        module_file.load_module(path)


def test_a_broken_thumbnail_costs_the_picture_and_not_the_module(
    game_data: GameData, tmp_path: Path
) -> None:
    saved = module_file.save_module(module_of(plates(game_data), game_data, "Bon"), tmp_path)
    assert saved.path is not None
    raw = json.loads(saved.path.read_text(encoding="utf-8"))
    raw["thumbnail"] = "ceci n'est pas du base64 !!"
    saved.path.write_text(json.dumps(raw), encoding="utf-8")

    reread = module_file.load_module(saved.path)

    assert reread.thumbnail is None
    assert reread.outputs == {PLATE: 40.0}


def test_a_module_whose_code_is_rubbish_fails_when_it_is_opened_not_when_it_is_listed(
    tmp_path: Path,
) -> None:
    """Listing the library must not decompress every module in it."""
    path = tmp_path / f"faux{module_file.MODULE_SUFFIX}"
    path.write_text(json.dumps({"name": "Faux", "code": "pas un code"}), encoding="utf-8")

    modules, problems = module_file.load_library(tmp_path)

    assert problems == []
    assert [module.name for module in modules] == ["Faux"]
    with pytest.raises(ModuleError, match="Faux"):
        modules[0].graph()


# --------------------------------------------------------------------------- #
# The payload really is the share code
# --------------------------------------------------------------------------- #


def test_a_module_written_under_an_older_schema_is_migrated_on_reading(
    game_data: GameData, tmp_path: Path
) -> None:
    """The whole reason the payload is a share code and not a second format.

    The code is rewritten by hand with an old schema version and a field spelt the
    way that version spelt it; reading it back goes through ``factory_file.migrate``
    without the library knowing anything about migrations.
    """
    graph = plates(game_data)
    ancient = _share_code_at_schema(graph, schema_version=1)
    path = tmp_path / f"ancien{module_file.MODULE_SUFFIX}"
    path.write_text(
        json.dumps({"module_version": 1, "name": "Ancien", "code": ancient}), encoding="utf-8"
    )

    module = module_file.load_module(path)

    assert [node.id for node in module.graph().nodes] == ["plaque1"]


def _share_code_at_schema(graph: FactoryGraph, schema_version: int) -> str:
    """The same code a build of that vintage would have produced."""
    import base64
    import zlib

    envelope = {
        "manifest": {**factory_file.Manifest.current().as_dict(), "schema_version": schema_version},
        "graph": json.loads(graph.model_dump_json()),
    }
    payload = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    packed = base64.urlsafe_b64encode(zlib.compress(payload, level=9)).decode("ascii")
    return f"{factory_file.SHARE_PREFIX}{packed}"


def test_the_slug_folds_accents_and_keeps_the_name_readable() -> None:
    assert module_file.slug("40 plaques de fer/min") == "40-plaques-de-fer-min"
    assert module_file.slug("Cadre modulaire lourd") == "cadre-modulaire-lourd"
    assert module_file.slug("!!!") == "module", "un nom sans une lettre reste un fichier valide"
