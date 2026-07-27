"""Colours by item: the palette itself, and the two readability rules around it.

The contrast tests are the point of this file. A palette is the one feature here
whose failure mode is not a wrong number but an unreadable canvas, and "do not pick
a bad colour" is not a design. The shipped defaults are held to a measured standard,
and any colour a user picks is answered by flipping the text rather than by hoping.
"""

import pytest

from satisplanner.core.families import ItemFamily
from satisplanner.core.models import GameData
from satisplanner.data import db
from satisplanner.ui import theme
from satisplanner.ui.item_colours import (
    DARK_TEXT,
    DEFAULT_FAMILY_COLOURS,
    GUARANTEED_TEXT_CONTRAST,
    LIGHT_TEXT,
    ItemPalette,
    contrast_ratio,
    is_colour,
    relative_luminance,
    text_colour_on,
)

# WCAG AA for text of this size. Not an aspiration: below this the labels on a node
# stop being comfortably readable, and the node is nothing but labels.
MIN_TEXT_CONTRAST = 4.5

# The liseré is a 2 px band of solid colour, not text, so it is held to the lower
# WCAG bar for a graphical object. Below this the difference between a running node
# and a starved one stops being visible at a glance, which is the whole job it does.
MIN_STATE_CONTRAST = 3.0

# The three the specification names: running, held back, stopped. ``STATE_IDLE`` is
# deliberately not among them -- it is the border of a node that has not been solved
# yet, which is a moment rather than a state, and holding "nothing computed" to the
# same standard as "this machine is stopped" would buy nothing.
STATE_COLOURS = (theme.STATE_NOMINAL, theme.STATE_STARVED, theme.STATE_BLOCKED)


@pytest.fixture(scope="module")
def shipped() -> GameData:
    return db.load_game_data_from_file(db.default_database_path())


# --------------------------------------------------------------------------- #
# Readability
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("family", list(ItemFamily))
def test_the_text_is_readable_on_every_default_colour(family: ItemFamily) -> None:
    """§4.4: a label lost on a dark grey background costs more than the colour gains."""
    background = DEFAULT_FAMILY_COLOURS[family]
    ratio = contrast_ratio(text_colour_on(background), background)
    assert ratio >= MIN_TEXT_CONTRAST, (
        f"{family.value} ({background}) : contraste du texte {ratio:.2f}, minimum "
        f"{MIN_TEXT_CONTRAST}"
    )


@pytest.mark.parametrize("family", list(ItemFamily))
@pytest.mark.parametrize("state", STATE_COLOURS)
def test_the_state_border_stays_distinguishable_on_every_default_colour(
    family: ItemFamily, state: str
) -> None:
    """§4.4: the colour says what flows, the liseré says whether it flows."""
    background = DEFAULT_FAMILY_COLOURS[family]
    ratio = contrast_ratio(state, background)
    assert ratio >= MIN_STATE_CONTRAST, (
        f"liseré {state} sur {family.value} ({background}) : contraste {ratio:.2f}, "
        f"minimum {MIN_STATE_CONTRAST}"
    )


@pytest.mark.parametrize(
    ("background", "expected"),
    [
        ("#ffffff", DARK_TEXT),
        ("#f0e68c", DARK_TEXT),
        ("#000000", LIGHT_TEXT),
        ("#243f52", LIGHT_TEXT),
    ],
)
def test_the_text_flips_with_the_luminance_of_what_it_sits_on(
    background: str, expected: str
) -> None:
    """A user free to choose any colour must not be free to make their own text vanish."""
    assert text_colour_on(background) == expected


def test_a_user_chosen_colour_of_any_brightness_stays_readable() -> None:
    """Every grey, not a handful of chosen ones, and every shipped colour above it.

    The bar here is the floor two inks can guarantee against a mid-grey, which is
    lower than the 4.5 the defaults clear -- and it is the honest number: a user who
    picks #737373 cannot be given better without changing the ink for that one node.
    """
    for level in range(256):
        background = f"#{level:02x}{level:02x}{level:02x}"
        ratio = contrast_ratio(text_colour_on(background), background)
        assert ratio >= GUARANTEED_TEXT_CONTRAST, f"{background} : contraste {ratio:.2f}"


def test_a_user_chosen_colour_of_any_hue_stays_readable() -> None:
    """The greys are the hard case, but the claim is about every colour there is."""
    worst = min(
        contrast_ratio(text_colour_on(colour), colour)
        for red in range(0, 256, 32)
        for green in range(0, 256, 32)
        for blue in range(0, 256, 32)
        for colour in [f"#{red:02x}{green:02x}{blue:02x}"]
    )
    assert worst >= GUARANTEED_TEXT_CONTRAST, f"pire contraste rencontre : {worst:.2f}"


def test_relative_luminance_matches_the_reference_values() -> None:
    """The WCAG definition, checked at its two ends so a rewrite cannot drift."""
    assert relative_luminance("#000000") == pytest.approx(0.0)
    assert relative_luminance("#ffffff") == pytest.approx(1.0)
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)


# --------------------------------------------------------------------------- #
# The palette
# --------------------------------------------------------------------------- #


def test_an_untouched_palette_gives_every_item_its_family_colour(shipped: GameData) -> None:
    palette = ItemPalette()
    assert palette.is_default()
    assert (
        palette.colour_for(shipped.item("Desc_OreIron_C")) == DEFAULT_FAMILY_COLOURS[ItemFamily.RAW]
    )
    assert (
        palette.colour_for(shipped.item("Desc_Water_C")) == DEFAULT_FAMILY_COLOURS[ItemFamily.FLUID]
    )


def test_an_item_override_beats_its_family(shipped: GameData) -> None:
    palette = ItemPalette()
    palette.set_family(ItemFamily.RAW, "#112233")
    palette.set_item("Desc_OreIron_C", "#445566")

    assert palette.colour_for(shipped.item("Desc_OreIron_C")) == "#445566"
    assert palette.colour_for(shipped.item("Desc_Coal_C")) == "#112233", (
        "les autres membres de la famille suivent la famille"
    )


def test_a_single_item_can_be_put_back_without_losing_the_rest(shipped: GameData) -> None:
    palette = ItemPalette()
    palette.set_family(ItemFamily.RAW, "#112233")
    palette.set_item("Desc_OreIron_C", "#445566")

    palette.reset_item("Desc_OreIron_C")

    assert palette.colour_for(shipped.item("Desc_OreIron_C")) == "#112233"


def test_everything_can_be_put_back_at_once(shipped: GameData) -> None:
    palette = ItemPalette()
    palette.set_family(ItemFamily.RAW, "#112233")
    palette.set_item("Desc_OreIron_C", "#445566")

    palette.reset()

    assert palette.is_default()
    assert (
        palette.colour_for(shipped.item("Desc_OreIron_C")) == DEFAULT_FAMILY_COLOURS[ItemFamily.RAW]
    )


# --------------------------------------------------------------------------- #
# Export and import
# --------------------------------------------------------------------------- #


def test_a_palette_survives_being_written_and_read_back() -> None:
    palette = ItemPalette()
    palette.set_family(ItemFamily.FLUID, "#0a1b2c")
    palette.set_item("Desc_IronIngot_C", "#ddeeff")

    reloaded = ItemPalette.from_json(palette.to_json())

    assert reloaded.families == palette.families
    assert reloaded.items == palette.items


def test_only_what_differs_from_the_defaults_is_written() -> None:
    """So that a new shipped default reaches somebody who exported last year."""
    palette = ItemPalette()
    palette.set_family(ItemFamily.FLUID, "#0a1b2c")

    written = ItemPalette.from_json(palette.to_json())

    assert set(written.families) == {ItemFamily.FLUID}
    assert not written.items


@pytest.mark.parametrize(
    "text",
    ["", "pas du json", "[]", "null", '{"families": "bleu"}', '{"items": 3}'],
)
def test_a_broken_palette_file_costs_nothing_but_the_palette(text: str) -> None:
    """Decoration must never be able to take a factory down with it."""
    assert ItemPalette.from_json(text).is_default()


def test_an_entry_that_is_not_a_colour_is_dropped_and_the_rest_kept() -> None:
    """One bad line costs that line, not the file."""
    payload = (
        '{"families": {"fluid": "#0a1b2c", "raw": "rouge", "inconnue": "#000000"},'
        ' "items": {"Desc_IronIngot_C": "#ddeeff", "Desc_Coal_C": "#xyz"}}'
    )

    palette = ItemPalette.from_json(payload)

    assert palette.families == {ItemFamily.FLUID: "#0a1b2c"}
    assert palette.items == {"Desc_IronIngot_C": "#ddeeff"}


@pytest.mark.parametrize(
    ("value", "valid"),
    [("#000000", True), ("#AbCdEf", True), ("#fff", False), ("red", False), ("000000", False)],
)
def test_only_full_hexadecimal_colours_are_accepted(value: str, valid: bool) -> None:
    assert is_colour(value) is valid
