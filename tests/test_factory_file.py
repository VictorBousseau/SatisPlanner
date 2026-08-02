"""Saving, reloading, sharing -- and refusing, politely, everything malformed.

The refusal cases matter as much as the round trip: a share code arrives through a
chat client that wraps lines and eats padding, and a ``.sfp`` can come from a version
that did not exist when this code was written.
"""

import json
import zipfile
from pathlib import Path

import pytest

from satisplanner.core.graph import (
    SCHEMA_VERSION,
    FactoryGraph,
    GeneratorNode,
    MachineNode,
    NodeKind,
    StorageNode,
)
from satisplanner.core.models import GameData
from satisplanner.data import factory_file
from satisplanner.data.db import GAME_VERSION
from satisplanner.data.factory_file import (
    GRAPH_MEMBER,
    MANIFEST_MEMBER,
    SHARE_PREFIX,
    THUMBNAIL_MEMBER,
    FactoryFileError,
    Manifest,
)
from tests.conftest import load_graph

# A one-pixel PNG, enough to prove the member survives the round trip.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


@pytest.fixture
def looping_factory() -> FactoryGraph:
    """The recycling loop plus a buffer: a cycle and a stateful node in one file."""
    graph = load_graph("recycling_loop")
    graph.add_node(
        StorageNode(
            id="tampon",
            storage_class="Build_PipeStorageTank_C",
            item_class="Desc_LiquidFuel_C",
            initial_content=250.0,
            position=(120.0, -60.0),
        )
    )
    return graph


# --------------------------------------------------------------------------- #
# The .sfp round trip
# --------------------------------------------------------------------------- #


def test_a_factory_survives_being_written_and_read_back(
    tmp_path: Path, looping_factory: FactoryGraph
) -> None:
    path = tmp_path / "usine.sfp"
    factory_file.save(path, looping_factory, TINY_PNG)
    reloaded = factory_file.load(path)

    assert reloaded.graph == looping_factory, "le graphe doit revenir identique, boucle comprise"
    assert reloaded.thumbnail == TINY_PNG
    assert reloaded.warnings == []
    assert reloaded.is_clean


def test_the_archive_holds_the_three_expected_members(
    tmp_path: Path, looping_factory: FactoryGraph
) -> None:
    path = tmp_path / "usine.sfp"
    factory_file.save(path, looping_factory, TINY_PNG)
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == {GRAPH_MEMBER, MANIFEST_MEMBER, THUMBNAIL_MEMBER}
        manifest = json.loads(archive.read(MANIFEST_MEMBER))
    assert manifest["game_version"] == GAME_VERSION
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["saved_at"], "la date d'enregistrement est renseignee"


def test_a_thumbnail_is_optional_on_both_sides(
    tmp_path: Path, looping_factory: FactoryGraph
) -> None:
    path = tmp_path / "sans_vignette.sfp"
    factory_file.save(path, looping_factory)
    with zipfile.ZipFile(path) as archive:
        assert THUMBNAIL_MEMBER not in archive.namelist()
    assert factory_file.load(path).thumbnail is None


def test_positions_and_machine_counts_come_back_exactly(tmp_path: Path) -> None:
    """A save that rounds a position or a count would move the user's factory."""
    graph = FactoryGraph()
    graph.add_node(
        MachineNode(
            id="m1", recipe_class="Recipe_IronPlate_C", machine_count=4.33, position=(-137.5, 91.25)
        )
    )
    path = tmp_path / "precis.sfp"
    factory_file.save(path, graph)
    node = factory_file.load(path).graph.node("m1")
    assert isinstance(node, MachineNode)
    assert node.machine_count == 4.33
    assert node.position == (-137.5, 91.25)


# --------------------------------------------------------------------------- #
# Refusing a bad file
# --------------------------------------------------------------------------- #


def test_a_missing_file_says_so(tmp_path: Path) -> None:
    with pytest.raises(FactoryFileError, match="introuvable"):
        factory_file.load(tmp_path / "jamais_ecrit.sfp")


def test_something_that_is_not_a_zip_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "faux.sfp"
    path.write_text("ceci n'est pas une archive", encoding="utf-8")
    with pytest.raises(FactoryFileError, match="illisible ou endommagé"):
        factory_file.load(path)


def test_a_zip_without_the_graph_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "vide.sfp"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("autre.txt", "rien")
    with pytest.raises(FactoryFileError, match="n'est pas une usine SatisPlanner"):
        factory_file.load(path)


def test_a_graph_that_is_not_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "casse.sfp"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(GRAPH_MEMBER, "{ceci n'est pas du json")
    with pytest.raises(FactoryFileError, match="n'est pas du JSON valide"):
        factory_file.load(path)


def test_an_inconsistent_graph_is_refused_with_the_reason(tmp_path: Path) -> None:
    """An edge pointing at a node that is not there: the graph's own rule, surfaced."""
    path = tmp_path / "incoherent.sfp"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [{"kind": "output", "id": "out", "item_class": "Desc_IronPlate_C"}],
        "edges": [
            {
                "id": "e1",
                "source": "fantome",
                "target": "out",
                "item_class": "Desc_IronPlate_C",
                "transport_class": "Build_ConveyorBeltMk1_C",
            }
        ],
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(GRAPH_MEMBER, json.dumps(payload))
    with pytest.raises(FactoryFileError, match="incohérente"):
        factory_file.load(path)


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #


def test_a_file_from_the_future_is_refused_rather_than_guessed_at(tmp_path: Path) -> None:
    path = tmp_path / "futur.sfp"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(GRAPH_MEMBER, json.dumps({"nodes": [], "edges": []}))
        archive.writestr(
            MANIFEST_MEMBER,
            json.dumps({"schema_version": SCHEMA_VERSION + 5, "game_version": GAME_VERSION}),
        )
    with pytest.raises(FactoryFileError, match="version plus récente"):
        factory_file.load(path)


def test_a_file_from_another_game_version_opens_with_a_warning(
    tmp_path: Path, looping_factory: FactoryGraph
) -> None:
    """Refusing would lose someone's layout; opening silently would hide changed recipes."""
    path = tmp_path / "ancienne.sfp"
    factory_file.save(path, looping_factory)
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members[MANIFEST_MEMBER])
    manifest["game_version"] = "1.0"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(GRAPH_MEMBER, members[GRAPH_MEMBER])
        archive.writestr(MANIFEST_MEMBER, json.dumps(manifest))

    reloaded = factory_file.load(path)
    assert reloaded.graph == looping_factory, "l'usine est bien ouverte"
    assert len(reloaded.warnings) == 1
    assert "1.0" in reloaded.warnings[0]
    assert "recettes ont pu changer" in reloaded.warnings[0]


def test_an_unreadable_schema_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "version_texte.sfp"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(GRAPH_MEMBER, json.dumps({"nodes": [], "edges": []}))
        archive.writestr(MANIFEST_MEMBER, json.dumps({"schema_version": "deux"}))
    with pytest.raises(FactoryFileError, match="version de schéma illisible"):
        factory_file.load(path)


def test_a_document_from_before_the_clock_existed_still_opens(tmp_path: Path) -> None:
    """The real schema 1 to 2 step: a V1 file, opened by this build.

    Nothing has to be written -- the field defaults to 100 %, which is what a file
    from before it existed meant -- but the walk must go through and the note must
    say what happened.
    """
    graph = FactoryGraph()
    graph.add_node(MachineNode(id="m1", recipe_class="Recipe_IngotIron_C"))
    payload = json.loads(graph.model_dump_json())
    del payload["nodes"][0]["clock_speed"]
    payload["schema_version"] = 1

    path = tmp_path / "ancienne.sfp"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(factory_file.GRAPH_MEMBER, json.dumps(payload))
        archive.writestr(
            factory_file.MANIFEST_MEMBER,
            json.dumps({**Manifest.current().as_dict(), "schema_version": 1}),
        )

    loaded = factory_file.load(path)
    node = loaded.graph.node("m1")
    assert isinstance(node, MachineNode)
    assert node.clock_speed == 1.0
    assert any("schéma 1 au schéma 2" in warning for warning in loaded.warnings)
    # ...and the walk carried on to the current version rather than stopping at 2.
    assert any("schéma 2 au schéma 3" in warning for warning in loaded.warnings)


def test_a_generator_survives_the_round_trip(tmp_path: Path) -> None:
    graph = FactoryGraph()
    graph.add_node(
        GeneratorNode(
            id="g1",
            generator_class="Build_GeneratorCoal_C",
            fuel_class="Desc_PetroleumCoke_C",
            count=4.0,
        )
    )
    path = tmp_path / "centrale.sfp"
    factory_file.save(path, graph)

    node = factory_file.load(path).graph.node("g1")
    assert isinstance(node, GeneratorNode)
    assert node.generator_class == "Build_GeneratorCoal_C"
    assert node.fuel_class == "Desc_PetroleumCoke_C"
    assert node.count == 4.0
    # No clock field at all, and not one pinned to 100 %: absent is the honest shape
    # while a generator's production exponent is not modelled.
    assert not hasattr(node, "clock_speed")


def test_a_generator_whose_building_left_the_catalogue_is_pruned(game_data: GameData) -> None:
    graph = FactoryGraph()
    graph.add_node(
        GeneratorNode(id="g1", generator_class="Build_GeneratorNuclear_C", fuel_class="Desc_Coal_C")
    )
    missing, removed = factory_file.prune_unknown(graph, game_data)
    assert missing == ["Build_GeneratorNuclear_C"]
    assert removed == ["g1"]


def test_a_clock_survives_the_round_trip(tmp_path: Path) -> None:
    graph = FactoryGraph()
    graph.add_node(MachineNode(id="m1", recipe_class="Recipe_IngotIron_C", clock_speed=2.5))
    path = tmp_path / "surcadencée.sfp"
    factory_file.save(path, graph)

    node = factory_file.load(path).graph.node("m1")
    assert isinstance(node, MachineNode)
    assert node.clock_speed == 2.5


def test_migration_is_a_single_door_that_already_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mechanism itself, with two planted steps rather than the real one."""
    monkeypatch.setattr(factory_file, "SCHEMA_VERSION", SCHEMA_VERSION + 2)
    monkeypatch.setattr(
        factory_file,
        "MIGRATIONS",
        {
            SCHEMA_VERSION: lambda payload: ({**payload, "etape": "une"}, []),
            SCHEMA_VERSION + 1: lambda payload: ({**payload, "etape": "deux"}, ["ceci est écrit"]),
        },
    )
    payload, notes = factory_file.migrate({"nodes": []}, SCHEMA_VERSION)
    assert payload["etape"] == "deux", "les etapes s'enchainent jusqu'a la version courante"
    # One line per version crossed, plus whatever a step had to say for itself.
    assert len(notes) == 3
    assert "ceci est écrit" in notes


def test_a_document_already_current_is_left_alone() -> None:
    payload, notes = factory_file.migrate({"nodes": [], "edges": []}, SCHEMA_VERSION)
    assert payload == {"nodes": [], "edges": []}
    assert notes == []


# --------------------------------------------------------------------------- #
# Classes the catalogue no longer knows
# --------------------------------------------------------------------------- #


def test_a_factory_referring_to_an_unknown_recipe_is_reported(game_data: GameData) -> None:
    graph = load_graph("iron_plate")
    graph.add_node(MachineNode(id="disparue", recipe_class="Recipe_Supprimee_En_1_3_C"))
    missing = factory_file.unknown_classes(graph, game_data)
    assert missing == ["Recipe_Supprimee_En_1_3_C"]
    assert "1 classe(s)" in factory_file.describe_unknown(missing)


def test_a_known_factory_reports_nothing_missing(game_data: GameData) -> None:
    for name in ("iron_plate", "recycling_loop", "plastic_chain", "buffer_draining"):
        assert factory_file.unknown_classes(load_graph(name), game_data) == [], name


def test_every_kind_of_dangling_reference_is_caught(game_data: GameData) -> None:
    graph = FactoryGraph(
        nodes=[
            {"kind": "resource", "id": "a", "item_class": "Desc_X_C", "extractor_class": "B_Y_C"},
            {"kind": "storage", "id": "b", "storage_class": "B_Z_C", "item_class": "Desc_W_C"},
            {"kind": "water_extractor", "id": "c", "extractor_class": "B_V_C"},
            {"kind": "external_source", "id": "d", "item_class": "Desc_U_C"},
        ]
    )
    assert factory_file.unknown_classes(graph, game_data) == [
        "B_V_C",
        "B_Y_C",
        "B_Z_C",
        "Desc_U_C",
        "Desc_W_C",
        "Desc_X_C",
    ]


# --------------------------------------------------------------------------- #
# Share code
# --------------------------------------------------------------------------- #


def test_a_share_code_round_trips(looping_factory: FactoryGraph) -> None:
    code = factory_file.encode_share_code(looping_factory)
    assert code.startswith(SHARE_PREFIX)
    assert factory_file.decode_share_code(code).graph == looping_factory


def test_a_share_code_survives_being_wrapped_by_a_chat_client(
    looping_factory: FactoryGraph,
) -> None:
    """Line breaks and spaces are what a pasted code actually looks like."""
    code = factory_file.encode_share_code(looping_factory)
    mangled = "\n".join(code[index : index + 40] for index in range(0, len(code), 40))
    assert factory_file.decode_share_code(f"  {mangled}  ").graph == looping_factory


def test_a_share_code_stripped_of_its_padding_still_reads(
    looping_factory: FactoryGraph,
) -> None:
    code = factory_file.encode_share_code(looping_factory).rstrip("=")
    assert factory_file.decode_share_code(code).graph == looping_factory


def test_an_empty_code_is_refused() -> None:
    with pytest.raises(FactoryFileError, match="aucun code"):
        factory_file.decode_share_code("   ")


def test_a_code_without_the_prefix_is_refused() -> None:
    with pytest.raises(FactoryFileError, match="pas un code SatisPlanner"):
        factory_file.decode_share_code("bonjour, voici mon usine")


def test_a_truncated_code_is_refused(looping_factory: FactoryGraph) -> None:
    code = factory_file.encode_share_code(looping_factory)
    # Either half of a code can fail: the base64 may not decode at all, or it may
    # decode into a compressed stream that stops short. Both are the same sentence
    # to a reader, and this accepts either.
    with pytest.raises(FactoryFileError, match=r"tronqué|abîmé"):
        factory_file.decode_share_code(code[: len(code) // 2])


def test_a_corrupted_code_is_refused(looping_factory: FactoryGraph) -> None:
    """One flipped character in the middle: the payload no longer inflates."""
    code = factory_file.encode_share_code(looping_factory)
    middle = len(code) // 2
    swapped = "A" if code[middle] != "A" else "B"
    with pytest.raises(FactoryFileError):
        factory_file.decode_share_code(code[:middle] + swapped + code[middle + 1 :])


def test_a_code_from_a_future_schema_is_refused(looping_factory: FactoryGraph) -> None:
    import base64
    import zlib

    envelope = {
        "manifest": {**Manifest.current().as_dict(), "schema_version": SCHEMA_VERSION + 1},
        "graph": json.loads(looping_factory.model_dump_json()),
    }
    body = base64.urlsafe_b64encode(zlib.compress(json.dumps(envelope).encode("utf-8")))
    with pytest.raises(FactoryFileError, match="version plus récente"):
        factory_file.decode_share_code(SHARE_PREFIX + body.decode("ascii"))


def test_a_code_that_decompresses_to_something_else_is_refused() -> None:
    import base64
    import zlib

    body = base64.urlsafe_b64encode(zlib.compress(b'{"pas": "une usine"}'))
    with pytest.raises(FactoryFileError, match="ne contient pas d'usine"):
        factory_file.decode_share_code(SHARE_PREFIX + body.decode("ascii"))


def test_a_code_that_decompresses_to_junk_is_refused() -> None:
    import base64
    import zlib

    body = base64.urlsafe_b64encode(zlib.compress(b"\xff\xfe pas du json"))
    with pytest.raises(FactoryFileError, match="pas une usine lisible"):
        factory_file.decode_share_code(SHARE_PREFIX + body.decode("ascii"))


def test_an_absurdly_long_code_is_refused_before_being_inflated() -> None:
    """A zlib bomb must not be able to make the application allocate gigabytes."""
    with pytest.raises(FactoryFileError, match="trop long"):
        factory_file.decode_share_code(SHARE_PREFIX + "A" * (factory_file.MAX_SHARE_CODE_LENGTH))


def test_the_error_message_never_leaks_a_stack_trace(looping_factory: FactoryGraph) -> None:
    code = factory_file.encode_share_code(looping_factory)
    with pytest.raises(FactoryFileError) as caught:
        factory_file.decode_share_code(code[:20])
    message = str(caught.value)
    assert "Traceback" not in message
    assert "Error" not in message
    assert message.endswith(".")


def test_the_documented_example_is_still_a_valid_factory(game_data: GameData) -> None:
    """``docs/exemple-usine.json`` is the format's reference, so it must load.

    A hand-written example that quietly stops matching the schema is worse than no
    example at all -- somebody will copy it. This is six lines to make sure the
    document in ``docs/format-usine.md`` describes the application that exists.
    """
    from satisplanner.core import engine

    path = Path(__file__).resolve().parent.parent / "docs" / "exemple-usine.json"
    graph = FactoryGraph.model_validate_json(path.read_text(encoding="utf-8"))

    assert graph.schema_version == SCHEMA_VERSION
    kinds = {node.kind for node in graph.nodes}
    assert kinds == set(NodeKind), "les neuf types doivent y être"
    report = engine.solve(graph, game_data)
    assert report.converged
    assert not report.has_errors(), "l'exemple de référence ne doit rien avoir de casse"
    # The table in ``format-usine.md`` says every node runs at 100 %. A reference
    # example that quietly settles at 80 % somewhere would teach the wrong thing
    # about the very rule it exists to show.
    starved = [
        solution.node_id for solution in report.nodes if solution.ratio < 1.0 - 1e-9
    ]
    assert not starved, f"l'exemple doit tourner à 100 % partout : {starved}"
