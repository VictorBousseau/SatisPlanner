"""Extract a small test fixture from the game's locale files.

The real locale files weigh 10 MB each and must not be committed. This tool keeps
only the classes needed to lock the control values of the specification, and writes
them back **in the original format**: UTF-16 LE with a BOM, tab-indented, same
nesting. The fixture must therefore exercise the same decoding path as the real
file, BOM included.

    py -3.12 tools/extract_fixture.py --game-dir "C:\\...\\Satisfactory"

Maintenance tool: run it again only when the game version changes.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from satisplanner.data.docs_parser import (
    BUILD_GUN,
    FRENCH_FILENAME,
    locate_docs_directory,
    parse_class_list,
    parse_item_amounts,
    read_text_file,
    select_reference_file,
)

logger = logging.getLogger("extract_fixture")

FIXTURE_DIR: Final = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# Every class the fixture must contain, grouped by the reason it is there.
KEEP: Final[frozenset[str]] = frozenset(
    {
        # -- raw resources of the control table
        "Desc_OreIron_C",
        "Desc_Coal_C",
        "Desc_LiquidOil_C",
        "Desc_Water_C",
        "Desc_OreCopper_C",
        # -- intermediate items
        "Desc_IronIngot_C",
        "Desc_IronRod_C",
        "Desc_CopperIngot_C",
        "Desc_CopperSheet_C",
        "Desc_SteelIngot_C",
        "Desc_IronPlate_C",
        "Desc_IronScrew_C",
        "Desc_IronPlateReinforced_C",
        "Desc_Plastic_C",
        "Desc_Rubber_C",
        "Desc_HeavyOilResidue_C",
        "Desc_LiquidFuel_C",
        "Desc_PolymerResin_C",
        "Desc_CircuitBoard_C",
        "Desc_Cable_C",
        "Desc_Wire_C",
        "Desc_Computer_C",
        "Desc_FluidCanister_C",
        "Desc_PackagedWater_C",
        # -- the two recipes that encode "alternate" inconsistently
        "Desc_LiquidTurboFuel_C",
        "Desc_CompactedCoal_C",
        "Desc_AluminumScrap_C",
        "Desc_AluminumIngot_C",
        "Recipe_Alternate_Turbofuel_C",  # prefix in the class name, not in the label
        "Recipe_PureAluminumIngot_C",  # label says Alternate:, class name does not
        # -- recipes of the control table
        "Recipe_IngotIron_C",
        "Recipe_IngotSteel_C",
        "Recipe_IronPlate_C",
        "Recipe_IronRod_C",
        "Recipe_Screw_C",
        "Recipe_IngotCopper_C",
        "Recipe_CopperSheet_C",
        "Recipe_IronPlateReinforced_C",
        "Recipe_Plastic_C",  # fluid in, solid + fluid byproduct out
        "Recipe_Rubber_C",  # idem, different byproduct ratio
        "Recipe_ResidualPlastic_C",
        # Consumes heavy oil residue: needed to test partial absorption of a byproduct.
        "Recipe_ResidualFuel_C",
        "Recipe_Computer_C",
        "Recipe_Cable_C",
        "Recipe_Wire_C",
        "Recipe_CircuitBoard_C",
        # -- recycling loop, the phase 2 fixed point
        "Recipe_Alternate_Plastic_1_C",
        "Recipe_Alternate_RecycledRubber_C",
        # -- packager: fluid in, solid out
        "Recipe_PackagedWater_C",
        # -- out of scope on purpose, to prove the filtering works
        "Recipe_NitricAcid_C",  # Blender, excluded machine
        "Recipe_Wall_8x4_01_C",  # build gun only, not a machine
        # -- event content (FICSMAS) and real ammunition, to test the is_event marker
        "Desc_Gift_C",
        "Desc_XmasBall1_C",
        "Desc_SpikedRebar_C",
        "Recipe_XmasBall1_C",
        "Recipe_SpikedRebar_C",
        # -- machines
        "Build_SmelterMk1_C",
        "Build_FoundryMk1_C",
        "Build_ConstructorMk1_C",
        "Build_AssemblerMk1_C",
        "Build_ManufacturerMk1_C",
        "Build_OilRefinery_C",
        "Build_Packager_C",
        "Build_Blender_C",  # excluded machine, kept to test exclusion
        # -- extraction
        "Build_MinerMk1_C",
        "Build_MinerMk2_C",
        "Build_MinerMk3_C",
        "Build_OilPump_C",
        "Build_WaterPump_C",
        "Build_FrackingExtractor_C",  # out of scope, kept to test exclusion
        # -- transport, all tiers plus the cosmetic twins to test deduplication
        "Build_ConveyorBeltMk1_C",
        "Build_ConveyorBeltMk2_C",
        "Build_ConveyorBeltMk3_C",
        "Build_ConveyorBeltMk4_C",
        "Build_ConveyorBeltMk5_C",
        "Build_ConveyorBeltMk6_C",
        "Build_Pipeline_C",
        "Build_PipelineMK2_C",
        "Build_Pipeline_NoIndicator_C",
        "Build_PipelineMK2_NoIndicator_C",
        # -- line attachments, counted in the shopping list, plus the two V2 variants
        # kept to prove they stay out of scope
        "Build_ConveyorAttachmentSplitter_C",
        "Build_ConveyorAttachmentMerger_C",
        "Build_PipelineJunction_Cross_C",
        "Build_ConveyorAttachmentSplitterSmart_C",
        "Build_ConveyorAttachmentMergerPriority_C",
        # -- overclocking: the shard that raises a clock ceiling, and the Somersloop
        # kept alongside it to prove the production-boost kind stays out of scope
        "Desc_CrystalShard_C",
        "Desc_WAT1_C",
        # -- electricity: the three generators in scope, plus the two out of scope
        # kept to prove the filtering. Rocket and ionized fuel are deliberately
        # **absent** although the fuel generator lists them: that is what exercises
        # the "fuel outside the catalogue is dropped" path.
        "Build_GeneratorBiomass_Automated_C",
        "Build_GeneratorCoal_C",
        "Build_GeneratorFuel_C",
        "Build_GeneratorGeoThermal_C",
        "Build_GeneratorNuclear_C",
        "Desc_PetroleumCoke_C",
        "Desc_Leaves_C",
        "Desc_Wood_C",
        "Desc_Mycelia_C",
        "Desc_GenericBiomass_C",
        "Desc_Biofuel_C",
        "Desc_PackagedBiofuel_C",
        "Desc_LiquidBiofuel_C",
        # -- storage
        "Build_StorageContainerMk1_C",
        "Build_StorageContainerMk2_C",
        "Build_PipeStorageTank_C",
        "Build_IndustrialTank_C",
    }
)

# Icons of a `Build_X_C` live on its `Desc_X_C` twin, so every kept buildable
# drags its descriptor along rather than us listing them by hand.
KEEP_ALL: Final[frozenset[str]] = KEEP | frozenset(
    name.replace("Build_", "Desc_", 1) for name in KEEP if name.startswith("Build_")
)


def build_cost_closure(raw: list[dict[str, Any]], wanted: frozenset[str]) -> frozenset[str]:
    """The build recipe of every kept building, and the parts it calls for.

    Derived rather than listed. Thirty recipe names and their twenty ingredients
    written out by hand would be twenty-odd lines that stop matching the moment a
    building joins the list above -- and the failure would be a build cost quietly
    missing from the fixture, which is exactly the thing the fixture is there to
    catch.
    """
    found: set[str] = set()
    for entry in raw:
        for cls in entry["Classes"]:
            if BUILD_GUN not in parse_class_list(cls.get("mProducedIn", "")):
                continue
            products = parse_item_amounts(cls.get("mProduct", ""))
            if len(products) != 1 or products[0][0] not in wanted:
                continue
            found.add(cls["ClassName"])
            found.update(item for item, _ in parse_item_amounts(cls.get("mIngredients", "")))
    return frozenset(found)


def subset(raw: list[dict[str, Any]], wanted: frozenset[str]) -> list[dict[str, Any]]:
    """Keep only the wanted classes, preserving the native-class grouping."""
    kept: list[dict[str, Any]] = []
    for entry in raw:
        classes = [cls for cls in entry["Classes"] if cls.get("ClassName") in wanted]
        if classes:
            kept.append({"NativeClass": entry["NativeClass"], "Classes": classes})
    return kept


def write_fixture(data: list[dict[str, Any]], path: Path) -> None:
    """Write UTF-16 LE with a BOM and tab indentation, like the game does."""
    text = json.dumps(data, indent="\t", ensure_ascii=False)
    path.write_text(text, encoding="utf-16")  # the codec emits the BOM
    logger.info("%s : %d groupe(s), %.1f Ko", path.name, len(data), path.stat().st_size / 1024)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=FIXTURE_DIR)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    docs_dir = locate_docs_directory(args.game_dir)
    reference_path, _ = select_reference_file(docs_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Worked out once, on the reference file, and applied to both: the two locales
    # must contain exactly the same classes or the labels stop lining up.
    reference_raw = json.loads(read_text_file(reference_path))
    wanted = KEEP_ALL | build_cost_closure(reference_raw, KEEP_ALL)
    logger.info(
        "%d classe(s) retenue(s), dont %d par les recettes de construction",
        len(wanted),
        len(wanted) - len(KEEP_ALL),
    )

    found: set[str] = set()
    for source, target in (
        (reference_path, args.output_dir / "docs_en-US.json"),
        (docs_dir / FRENCH_FILENAME, args.output_dir / "docs_fr.json"),
    ):
        if not source.is_file():
            logger.warning("%s absent, fixture non écrite", source.name)
            continue
        data = subset(json.loads(read_text_file(source)), wanted)
        write_fixture(data, target)
        found.update(cls["ClassName"] for entry in data for cls in entry["Classes"])

    missing = sorted(KEEP - found)
    if missing:
        logger.warning("%d classe(s) demandee(s) introuvable(s) : %s", len(missing), missing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
