"""Parser tests, run against a real slice of the game files.

The control table of the specification is asserted end to end here: from the raw
UTF-16 file down to a per-minute rate.
"""

import json
from pathlib import Path

import pytest

from satisplanner.core import constants
from satisplanner.core.models import AttachmentRole, ItemForm, Recipe, SplitterMode
from satisplanner.data.docs_parser import (
    DocsFileError,
    GameDataset,
    french_labels,
    locate_docs_directory,
    parse_building_costs,
    parse_class_list,
    parse_dataset,
    parse_icon_filename,
    parse_item_amounts,
    read_locale,
    read_text_file,
    select_reference_file,
)
from tests.conftest import FRENCH_FIXTURE, REFERENCE_FIXTURE, slot_of

# --------------------------------------------------------------------------- #
# Encoding
# --------------------------------------------------------------------------- #


def test_the_fixture_really_is_utf16_with_a_bom() -> None:
    assert REFERENCE_FIXTURE.read_bytes()[:2] == b"\xff\xfe"


def test_reading_as_utf8_fails_which_is_the_whole_point() -> None:
    with pytest.raises(UnicodeDecodeError):
        REFERENCE_FIXTURE.read_text(encoding="utf-8")


def test_read_text_file_handles_the_game_encoding() -> None:
    text = read_text_file(REFERENCE_FIXTURE)
    assert text.startswith("[")  # no BOM left over
    assert json.loads(text)


def test_read_text_file_also_accepts_utf8(tmp_path: Path) -> None:
    path = tmp_path / "plain.json"
    path.write_text('[{"NativeClass": "x", "Classes": []}]', encoding="utf-8")
    assert json.loads(read_text_file(path))


def test_undecodable_file_raises_a_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_bytes(b"\x81\x82\x83")
    with pytest.raises(DocsFileError, match="impossible de décoder"):
        read_text_file(path)


# --------------------------------------------------------------------------- #
# Locating the source file
# --------------------------------------------------------------------------- #


def _docs_dir(tmp_path: Path, *names: str) -> Path:
    docs = tmp_path / "CommunityResources" / "Docs"
    docs.mkdir(parents=True)
    for name in names:
        (docs / name).write_text("[]", encoding="utf-16")
    return docs


def test_docs_directory_is_found_from_the_game_root(tmp_path: Path) -> None:
    docs = _docs_dir(tmp_path, "en-US.json")
    assert locate_docs_directory(tmp_path) == docs


def test_docs_directory_can_also_be_passed_directly(tmp_path: Path) -> None:
    docs = _docs_dir(tmp_path, "en-US.json")
    assert locate_docs_directory(docs) == docs


def test_missing_docs_directory_is_reported(tmp_path: Path) -> None:
    with pytest.raises(DocsFileError, match="vérifiez --game-dir"):
        locate_docs_directory(tmp_path)


def test_en_us_wins_over_the_other_locales(tmp_path: Path) -> None:
    docs = _docs_dir(tmp_path, "af.json", "de.json", "en-GB.json", "en-US.json", "fr.json")
    path, preferred = select_reference_file(docs)
    assert (path.name, preferred) == ("en-US.json", True)


def test_another_english_variant_is_used_when_en_us_is_absent(tmp_path: Path) -> None:
    docs = _docs_dir(tmp_path, "de.json", "en-CA.json", "en-GB.json")
    path, preferred = select_reference_file(docs)
    assert (path.name, preferred) == ("en-GB.json", True)


def test_the_historical_docs_json_is_still_accepted(tmp_path: Path) -> None:
    docs = _docs_dir(tmp_path, "Docs.json", "ja.json")
    path, preferred = select_reference_file(docs)
    assert (path.name, preferred) == ("Docs.json", True)


def test_last_resort_falls_back_and_flags_it(tmp_path: Path) -> None:
    docs = _docs_dir(tmp_path, "ja.json", "zh-Hans.json")
    path, preferred = select_reference_file(docs)
    assert path.name == "ja.json"
    assert preferred is False, "un choix par défaut doit être signale a l'utilisateur"


# --------------------------------------------------------------------------- #
# Composite properties
# --------------------------------------------------------------------------- #


def test_ingredient_parsing_keeps_class_names_and_order() -> None:
    raw = (
        "((ItemClass=\"/Script/Engine.BlueprintGeneratedClass'/Game/FactoryGame/Resource/Parts/"
        "IronPlate/Desc_IronPlate.Desc_IronPlate_C'\",Amount=6),"
        "(ItemClass=\"/Script/Engine.BlueprintGeneratedClass'/Game/FactoryGame/Resource/Parts/"
        "IronScrew/Desc_IronScrew.Desc_IronScrew_C'\",Amount=12))"
    )
    assert parse_item_amounts(raw) == [("Desc_IronPlate_C", 6), ("Desc_IronScrew_C", 12)]


def test_empty_composite_properties_are_not_an_error() -> None:
    assert parse_item_amounts("") == []
    assert parse_class_list("") == []


def test_produced_in_parsing_reduces_paths_to_class_names() -> None:
    raw = (
        '("/Game/FactoryGame/Buildable/Factory/SmelterMk1/Build_SmelterMk1.Build_SmelterMk1_C",'
        '"/Script/FactoryGame.FGBuildableAutomatedWorkBench")'
    )
    assert parse_class_list(raw) == ["Build_SmelterMk1_C", "FGBuildableAutomatedWorkBench"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Texture2D /Game/FactoryGame/Resource/Parts/Plastic/UI/IconDesc_Plastic_256"
            ".IconDesc_Plastic_256",
            "IconDesc_Plastic_256.png",
        ),
        # A 512 asset is rewritten: only the 256 variant is exported.
        (
            "Texture2D /Game/FactoryGame/Resource/RawResources/CrudeOil/UI/LiquidOil_Pipe_512"
            ".LiquidOil_Pipe_512",
            "LiquidOil_Pipe_256.png",
        ),
        ("", None),
        (None, None),
    ],
)
def test_icon_file_name_extraction(raw: str | None, expected: str | None) -> None:
    assert parse_icon_filename(raw) == expected


# --------------------------------------------------------------------------- #
# Control table, from the file to the rate
# --------------------------------------------------------------------------- #


def test_smelter_iron_ingot(recipes: dict[str, Recipe]) -> None:
    recipe = recipes["Recipe_IngotIron_C"]
    assert recipe.building_class == "Build_SmelterMk1_C"
    assert slot_of(recipe.ingredients, "Desc_OreIron_C").rate_per_minute == 30
    assert slot_of(recipe.products, "Desc_IronIngot_C").rate_per_minute == 30
    assert recipe.involves_fluid is False


def test_foundry_steel_ingot(recipes: dict[str, Recipe]) -> None:
    recipe = recipes["Recipe_IngotSteel_C"]
    assert recipe.building_class == "Build_FoundryMk1_C"
    assert slot_of(recipe.ingredients, "Desc_OreIron_C").rate_per_minute == 45
    assert slot_of(recipe.ingredients, "Desc_Coal_C").rate_per_minute == 45
    assert slot_of(recipe.products, "Desc_SteelIngot_C").rate_per_minute == 45


def test_constructor_iron_plate(recipes: dict[str, Recipe]) -> None:
    recipe = recipes["Recipe_IronPlate_C"]
    assert recipe.building_class == "Build_ConstructorMk1_C"
    assert slot_of(recipe.ingredients, "Desc_IronIngot_C").rate_per_minute == 30
    assert slot_of(recipe.products, "Desc_IronPlate_C").rate_per_minute == 20


def test_assembler_reinforced_iron_plate(recipes: dict[str, Recipe]) -> None:
    recipe = recipes["Recipe_IronPlateReinforced_C"]
    assert recipe.building_class == "Build_AssemblerMk1_C"
    assert slot_of(recipe.ingredients, "Desc_IronPlate_C").rate_per_minute == 30
    assert slot_of(recipe.ingredients, "Desc_IronScrew_C").rate_per_minute == 60
    assert slot_of(recipe.products, "Desc_IronPlateReinforced_C").rate_per_minute == 5


def test_refinery_plastic_locks_litres_fluids_and_byproducts(
    recipes: dict[str, Recipe],
) -> None:
    """The most important row of the control table.

    30 m3 of oil -> 20 plastic + 10 m3 of heavy oil residue. It validates the
    fluid conversion, the division by 1000 and the byproduct handling at once.
    """
    recipe = recipes["Recipe_Plastic_C"]
    assert recipe.building_class == "Build_OilRefinery_C"
    assert recipe.involves_fluid is True
    assert recipe.product_count == 2

    oil = slot_of(recipe.ingredients, "Desc_LiquidOil_C")
    assert oil.rate_per_minute == 30
    assert oil.amount_per_cycle == 3, "3000 L par cycle valent 3 m3, pas 3000"

    assert slot_of(recipe.products, "Desc_Plastic_C").rate_per_minute == 20
    assert slot_of(recipe.products, "Desc_HeavyOilResidue_C").rate_per_minute == 10


def test_refinery_rubber_has_a_different_byproduct_ratio(
    recipes: dict[str, Recipe],
) -> None:
    recipe = recipes["Recipe_Rubber_C"]
    assert slot_of(recipe.ingredients, "Desc_LiquidOil_C").rate_per_minute == 30
    assert slot_of(recipe.products, "Desc_Rubber_C").rate_per_minute == 20
    assert slot_of(recipe.products, "Desc_HeavyOilResidue_C").rate_per_minute == 20


def test_manufacturer_computer(recipes: dict[str, Recipe]) -> None:
    """Computer: 2.5/min, as expected.

    The ingredients, however, are those of Satisfactory 1.2 as read from the game
    files -- 4 circuit boards, 8 cables and 16 plastic over 24 s -- and not the
    10/9/18 of the pre-1.0 recipe. The control table's 25 / 22.5 / 45 belongs to
    that older recipe; the game data wins.
    """
    recipe = recipes["Recipe_Computer_C"]
    assert recipe.building_class == "Build_ManufacturerMk1_C"
    assert recipe.cycle_seconds == 24
    assert slot_of(recipe.products, "Desc_Computer_C").rate_per_minute == 2.5
    assert slot_of(recipe.ingredients, "Desc_CircuitBoard_C").rate_per_minute == 10
    assert slot_of(recipe.ingredients, "Desc_Cable_C").rate_per_minute == 20
    assert slot_of(recipe.ingredients, "Desc_Plastic_C").rate_per_minute == 40


def test_packager_moves_a_fluid_into_a_solid(recipes: dict[str, Recipe]) -> None:
    recipe = recipes["Recipe_PackagedWater_C"]
    assert recipe.building_class == "Build_Packager_C"
    assert slot_of(recipe.ingredients, "Desc_Water_C").rate_per_minute == 60
    assert slot_of(recipe.ingredients, "Desc_FluidCanister_C").rate_per_minute == 60
    assert slot_of(recipe.products, "Desc_PackagedWater_C").rate_per_minute == 60


def test_recycling_loop_recipes_are_present_and_marked_alternate(
    recipes: dict[str, Recipe],
) -> None:
    """The phase 2 fixed point needs both halves of the loop."""
    plastic = recipes["Recipe_Alternate_Plastic_1_C"]
    rubber = recipes["Recipe_Alternate_RecycledRubber_C"]
    assert plastic.is_alternate and rubber.is_alternate
    # Recycled Plastic consumes rubber and fuel, and produces plastic.
    assert slot_of(plastic.ingredients, "Desc_Rubber_C").rate_per_minute == 30
    assert slot_of(plastic.ingredients, "Desc_LiquidFuel_C").rate_per_minute == 30
    assert slot_of(plastic.products, "Desc_Plastic_C").rate_per_minute == 60
    # ... and the other way round, which is what closes the cycle.
    assert slot_of(rubber.ingredients, "Desc_Plastic_C").rate_per_minute == 30
    assert slot_of(rubber.products, "Desc_Rubber_C").rate_per_minute == 60


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #


def test_out_of_scope_recipes_are_dropped(recipes: dict[str, Recipe]) -> None:
    # Present in the fixture on purpose: a Blender recipe and a build gun recipe.
    assert "Recipe_NitricAcid_C" not in recipes, "le Melangeur est hors périmètre V1"
    assert "Recipe_Wall_8x4_01_C" not in recipes, "le pistolet n'est pas une machine"


def test_every_recipe_belongs_to_a_v1_machine(dataset: GameDataset) -> None:
    machines = {building.class_name for building in dataset.buildings}
    assert {recipe.building_class for recipe in dataset.recipes} <= machines


def test_belts_cover_the_six_tiers(dataset: GameDataset) -> None:
    assert [
        (belt.tier, belt.items_per_minute) for belt in sorted(dataset.belts, key=lambda b: b.tier)
    ] == [(1, 60), (2, 120), (3, 270), (4, 480), (5, 780), (6, 1200)]


def test_pipes_expose_two_tiers_without_the_cosmetic_twins(dataset: GameDataset) -> None:
    assert [
        (pipe.tier, pipe.cubic_metres_per_minute)
        for pipe in sorted(dataset.pipes, key=lambda p: p.tier)
    ] == [(1, 300), (2, 600)]
    assert all("NoIndicator" not in pipe.class_name for pipe in dataset.pipes)


def test_extractors_carry_their_rate_and_purity_flag(dataset: GameDataset) -> None:
    extractors = {extractor.class_name: extractor for extractor in dataset.extractors}
    assert extractors["Build_MinerMk1_C"].rate_per_minute == 60
    assert extractors["Build_MinerMk2_C"].rate_per_minute == 120
    assert extractors["Build_MinerMk3_C"].rate_per_minute == 240
    assert extractors["Build_MinerMk1_C"].item_class is None, "une foreuse accepte tout solide"
    assert extractors["Build_MinerMk1_C"].has_purity is True

    oil = extractors["Build_OilPump_C"]
    assert (oil.rate_per_minute, oil.item_class, oil.has_purity) == (120, "Desc_LiquidOil_C", True)

    water = extractors["Build_WaterPump_C"]
    assert water.rate_per_minute == 120
    assert water.item_class == "Desc_Water_C"
    assert water.has_purity is False, "l'extracteur d'eau a un débit fixe"

    assert "Build_FrackingExtractor_C" not in extractors, "les puits sont hors périmètre V1"


def test_storages_expose_slots_for_solids_and_volume_for_fluids(dataset: GameDataset) -> None:
    storages = {storage.class_name: storage for storage in dataset.storages}
    assert storages["Build_StorageContainerMk1_C"].slots == 24
    assert storages["Build_StorageContainerMk2_C"].slots == 48
    # mStorageCapacity is already in m3 -- it must not go through the litre division.
    assert storages["Build_PipeStorageTank_C"].capacity_m3 == 400
    assert storages["Build_IndustrialTank_C"].capacity_m3 == 2400


def test_line_attachments_are_parsed_with_their_roles(dataset: GameDataset) -> None:
    """Three splitters, a merger and a pipe junction, each with the job it can do."""
    attachments = {attachment.class_name: attachment for attachment in dataset.attachments}
    assert set(attachments) == {
        "Build_ConveyorAttachmentSplitter_C",
        "Build_ConveyorAttachmentSplitterSmart_C",
        "Build_ConveyorAttachmentSplitterProgrammable_C",
        "Build_ConveyorAttachmentMerger_C",
        "Build_PipelineJunction_Cross_C",
    }
    assert attachments["Build_ConveyorAttachmentSplitter_C"].roles == (AttachmentRole.SPLIT,)
    assert attachments["Build_ConveyorAttachmentMerger_C"].roles == (AttachmentRole.MERGE,)
    junction = attachments["Build_PipelineJunction_Cross_C"]
    assert junction.roles == (AttachmentRole.SPLIT, AttachmentRole.MERGE)
    assert junction.form is ItemForm.LIQUID


def test_the_three_splitters_are_told_apart_by_their_mode(dataset: GameDataset) -> None:
    """Nothing else in the row says which is which, and they do not cost the same."""
    attachments = {attachment.class_name: attachment for attachment in dataset.attachments}
    plain = attachments["Build_ConveyorAttachmentSplitter_C"]
    smart = attachments["Build_ConveyorAttachmentSplitterSmart_C"]
    programmable = attachments["Build_ConveyorAttachmentSplitterProgrammable_C"]
    assert plain.splitter_mode is SplitterMode.STANDARD
    assert smart.splitter_mode is SplitterMode.SMART
    assert programmable.splitter_mode is SplitterMode.PROGRAMMABLE
    # A merger has no modes, and neither has a junction: the game has no filtering
    # one, so ``None`` says that rather than "the standard one".
    assert attachments["Build_ConveyorAttachmentMerger_C"].splitter_mode is None
    assert attachments["Build_PipelineJunction_Cross_C"].splitter_mode is None


def test_the_priority_merger_stays_out_of_scope(dataset: GameDataset) -> None:
    """It is in the fixture on purpose: it is the merger's answer to the smart
    splitter, and it is not modelled."""
    classes = {attachment.class_name for attachment in dataset.attachments}
    assert "Build_ConveyorAttachmentMergerPriority_C" not in classes


def test_attachments_use_the_french_labels_of_the_game(dataset: GameDataset) -> None:
    """ "Groupeur", not the "fusionneur" of a translation done by hand."""
    names = {building.class_name: building.display_name_fr for building in dataset.buildings}
    assert names["Build_ConveyorAttachmentSplitter_C"] == "Répartiteur de convoyeurs"
    assert names["Build_ConveyorAttachmentSplitterSmart_C"] == "Répartiteur intelligent"
    assert names["Build_ConveyorAttachmentSplitterProgrammable_C"] == "Répartiteur programmable"
    assert names["Build_ConveyorAttachmentMerger_C"] == "Groupeur de convoyeurs"
    assert names["Build_PipelineJunction_Cross_C"] == "Jonction de pipeline"


def test_machine_power_draw(dataset: GameDataset) -> None:
    power = {building.class_name: building.power_mw for building in dataset.buildings}
    assert power["Build_SmelterMk1_C"] == 4
    assert power["Build_FoundryMk1_C"] == 16
    assert power["Build_OilRefinery_C"] == 30
    assert power["Build_ManufacturerMk1_C"] == 55
    assert "Build_Blender_C" not in power, "le Melangeur est hors périmètre V1"


# --------------------------------------------------------------------------- #
# Items and labels
# --------------------------------------------------------------------------- #


def test_item_forms_and_stack_sizes(dataset: GameDataset) -> None:
    items = {item.class_name: item for item in dataset.items}
    assert items["Desc_Plastic_C"].form is ItemForm.SOLID
    assert items["Desc_Plastic_C"].stack_size == 200
    assert items["Desc_HeavyOilResidue_C"].form is ItemForm.LIQUID
    assert items["Desc_HeavyOilResidue_C"].stack_size == 50
    assert items["Desc_OreIron_C"].is_raw_resource is True
    assert items["Desc_IronPlate_C"].is_raw_resource is False


def test_french_labels_are_used(dataset: GameDataset) -> None:
    items = {item.class_name: item for item in dataset.items}
    assert items["Desc_OreIron_C"].display_name == "Iron Ore"
    assert items["Desc_OreIron_C"].display_name_fr == "Minerai de fer"
    assert items["Desc_HeavyOilResidue_C"].display_name_fr == "Résidus de pétrole lourd"
    buildings = {b.class_name: b.display_name_fr for b in dataset.buildings}
    assert buildings["Build_OilRefinery_C"] == "Raffinerie"


def test_missing_french_locale_falls_back_to_english_without_failing() -> None:
    dataset = parse_dataset(
        read_locale(REFERENCE_FIXTURE), {}, source_file="x.json", french_file=None
    )
    items = {item.class_name: item for item in dataset.items}
    assert items["Desc_OreIron_C"].display_name_fr == "Iron Ore"
    assert dataset.french_file is None


def test_the_alternate_flag_never_depends_on_the_french_label() -> None:
    """In French the label reads "... (alternative)", so it cannot be the source."""
    labels = french_labels(read_locale(FRENCH_FIXTURE))
    assert not labels["Recipe_Alternate_Plastic_1_C"].startswith("Alternate:")
    dataset = parse_dataset(
        read_locale(REFERENCE_FIXTURE), labels, source_file="x.json", french_file="fr.json"
    )
    alternates = {r.class_name for r in dataset.recipes if r.is_alternate}
    assert "Recipe_Alternate_Plastic_1_C" in alternates
    assert "Recipe_Plastic_C" not in alternates


def test_alternate_recipes_the_game_encodes_inconsistently(
    recipes: dict[str, Recipe],
) -> None:
    """Both encodings are authoritative; relying on either one alone mislabels a recipe.

    Turbofuel carries the class-name prefix but its label is plain "Turbofuel";
    Pure Aluminum Ingot is the mirror case. Both are alternate recipes in game.
    """
    turbofuel = recipes["Recipe_Alternate_Turbofuel_C"]
    assert turbofuel.display_name == "Turbofuel"
    assert turbofuel.is_alternate is True

    aluminium = recipes["Recipe_PureAluminumIngot_C"]
    assert not aluminium.class_name.startswith("Recipe_Alternate_")
    assert aluminium.is_alternate is True


def test_the_fixture_parses_without_warnings(dataset: GameDataset) -> None:
    assert dataset.warnings == ()


# --------------------------------------------------------------------------- #
# Overclocking
# --------------------------------------------------------------------------- #


def test_the_power_exponent_is_read_and_not_assumed(dataset: GameDataset) -> None:
    """Every producing building shares one exponent, and it is not 1."""
    buildings = {building.class_name: building for building in dataset.buildings}
    smelter = buildings["Build_SmelterMk1_C"]
    assert smelter.power_exponent == pytest.approx(1.321929)
    assert buildings["Build_MinerMk3_C"].power_exponent == smelter.power_exponent
    assert all(building.power_exponent > 0 for building in dataset.buildings)


def test_only_the_overclocking_shard_is_kept(dataset: GameDataset) -> None:
    """The Somersloop shares the native class and amplifies production instead."""
    shards = {shard.class_name for shard in dataset.power_shards}
    assert shards == {"Desc_CrystalShard_C"}
    assert "Desc_WAT1_C" not in shards, "le Somersloop releve de la V2"

    (shard,) = dataset.power_shards
    assert shard.extra_potential == pytest.approx(0.5)


def test_the_shard_is_also_an_ordinary_item(dataset: GameDataset) -> None:
    """Which is what lets the shopping list show its French name and its icon."""
    items = {item.class_name: item for item in dataset.items}
    assert "Desc_CrystalShard_C" in items
    assert items["Desc_CrystalShard_C"].display_name_fr


def test_the_minimum_clock_matches_what_the_game_declares() -> None:
    """``constants.MIN_CLOCK_SPEED`` repeats ``mMinPotential``; they must agree.

    The graph validates its field without a catalogue in hand, so the bound is
    written down twice. This is the check that stops the two from drifting.
    """
    grouped = read_locale(REFERENCE_FIXTURE)
    declared = {
        cls["mMinPotential"]
        for classes in grouped.values()
        for cls in classes
        if "mMinPotential" in cls
    }
    assert declared, "la fixture doit contenir des bâtiments cadencables"
    assert {float(value) for value in declared} == {constants.MIN_CLOCK_SPEED}


def test_event_content_is_marked_from_the_asset_path(dataset: GameDataset) -> None:
    """FICSMAS items live under /Events/ -- a far safer marker than a name pattern."""
    items = {item.class_name: item for item in dataset.items}
    assert items["Desc_Gift_C"].is_event is True
    assert items["Desc_XmasBall1_C"].is_event is True
    # Ammunition is real content that a factory can produce: it must stay visible.
    assert items["Desc_SpikedRebar_C"].is_event is False
    assert items["Desc_Plastic_C"].is_event is False

    recipes = {recipe.class_name: recipe for recipe in dataset.recipes}
    assert recipes["Recipe_XmasBall1_C"].is_event is True
    assert recipes["Recipe_Plastic_C"].is_event is False


def test_event_content_is_kept_in_the_dataset_never_filtered_out(dataset: GameDataset) -> None:
    """Filtering happens at display time only, like is_alternate."""
    assert any(item.is_event for item in dataset.items)
    assert any(recipe.is_event for recipe in dataset.recipes)


def test_icons_are_resolved_for_the_items_of_the_control_table(dataset: GameDataset) -> None:
    icons = {item.class_name: item.icon_file for item in dataset.items}
    assert icons["Desc_Plastic_C"] == "IconDesc_Plastic_256.png"
    # A _512 asset must have been rewritten to the exported _256 variant.
    assert icons["Desc_LiquidOil_C"] == "LiquidOil_Pipe_256.png"


# --------------------------------------------------------------------------- #
# Build costs
# --------------------------------------------------------------------------- #


def test_the_build_cost_of_a_smelter_is_read_not_written(dataset: GameDataset) -> None:
    """The figure comes from the game file, and carries the recipe it came from."""
    costs = {cost.class_name: cost for cost in dataset.building_costs}
    smelter = costs["Build_SmelterMk1_C"]
    assert smelter.amounts == {"Desc_IronRod_C": 5.0, "Desc_Wire_C": 8.0}
    assert smelter.recipe_class == "Recipe_SmelterBasicMk1_C"


def test_every_building_of_the_dataset_is_priced(dataset: GameDataset) -> None:
    priced = {cost.class_name for cost in dataset.building_costs}
    missing = sorted({building.class_name for building in dataset.buildings} - priced)
    assert missing == [], f"batiment(s) sans cout de construction : {missing}"


def _grouped(*recipes: dict[str, str]) -> dict[str, list[dict[str, str]]]:
    return {"FGRecipe": list(recipes)}


_QUOTE = "'"


def _class_path(class_name: str) -> str:
    """A class reference written the way the game writes one, quotes and all."""
    return f'"/Script/Engine.BlueprintGeneratedClass{_QUOTE}/Game/X.{class_name}{_QUOTE}"'


def _recipe(name: str, product: str, ingredients: str, *, gun: bool = True) -> dict[str, str]:
    produced = "BP_BuildGun_C" if gun else "Build_ConstructorMk1_C"
    return {
        "ClassName": name,
        "mProducedIn": f'("/Game/X/{produced}.{produced}")',
        "mProduct": f"((ItemClass={_class_path(product)},Amount=1))",
        "mIngredients": ingredients,
    }


def _amount(item_class: str, amount: int) -> str:
    return f"ItemClass={_class_path(item_class)},Amount={amount}"


def test_a_building_with_no_build_recipe_is_named_in_the_warnings(dataset: GameDataset) -> None:
    """The signal that stops a missing cost from turning into a silent zero."""
    buildings = [b for b in dataset.buildings if b.class_name == "Build_SmelterMk1_C"]
    warnings: list[str] = []

    costs = parse_building_costs(_grouped(), buildings, {}, warnings)

    assert costs == []
    assert len(warnings) == 1
    assert "Build_SmelterMk1_C" in warnings[0]
    assert "sans recette de construction" in warnings[0]


def test_a_cost_calling_for_an_unknown_item_is_dropped_whole(dataset: GameDataset) -> None:
    """Half a cost is worse than none: it looks like a cost and is not one."""
    buildings = [b for b in dataset.buildings if b.class_name == "Build_SmelterMk1_C"]
    warnings: list[str] = []
    grouped = _grouped(
        _recipe(
            "Recipe_Essai_C",
            "Desc_SmelterMk1_C",
            f"(({_amount('Desc_IronRod_C', 5)}),({_amount('Desc_Inconnu_C', 2)}))",
        )
    )

    costs = parse_building_costs(grouped, buildings, {"Desc_IronRod_C": ItemForm.SOLID}, warnings)

    assert costs == [], "un cout partiel ne doit pas être publié"
    assert any("Desc_Inconnu_C" in warning for warning in warnings)
    assert any("sans recette de construction" in warning for warning in warnings)


def test_a_recipe_that_is_not_built_with_the_build_gun_is_not_a_cost(
    dataset: GameDataset,
) -> None:
    """A constructor making iron plates is not the cost of a constructor."""
    buildings = [b for b in dataset.buildings if b.class_name == "Build_SmelterMk1_C"]
    warnings: list[str] = []
    grouped = _grouped(
        _recipe(
            "Recipe_Essai_C",
            "Desc_SmelterMk1_C",
            f"(({_amount('Desc_IronRod_C', 5)}))",
            gun=False,
        )
    )

    costs = parse_building_costs(grouped, buildings, {"Desc_IronRod_C": ItemForm.SOLID}, warnings)

    assert costs == []
