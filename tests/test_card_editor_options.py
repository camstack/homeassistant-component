"""The card editors offer the embed's options — as CHOICES, not as free text.

Three separate rots are guarded here, and they are separate on purpose:

1. **Reachability.** The embed accepts a documented set of options
   (`camstack/embed/src/embed/events-url-config.ts`, and the table in
   `EVENTS-EMBED.md`). Every one of them that a dashboard can sensibly own must
   be composable BY THE CARD and settable FROM THE EDITOR. An option only the
   YAML editor can reach is an option the operator does not have.

2. **No free text over a closed set.** `view`, `thumb`, `theme`, `sort`,
   `smode`, `rows`, `cols`, `fields` and `attrs` each have a finite set of legal
   values in the embed's Zod schemas. A text input there is a typo generator:
   the embed's parse rejects the whole URL and the card shows an empty frame
   with no explanation. Multi-valued options (`fields`, `attrs`) get a real
   multi-selection, never a comma string the operator has to compose by hand.

3. **The lists do not drift from the embed.** The cards are plain JavaScript and
   cannot import the viewer's TypeScript, so the values are MIRRORED in an
   `EMBED_CONTRACT` block. A mirror nobody diffs is a mirror that rots, so:
   `test_*_contract_matches_this_file` pins the mirror to the expectation
   written here (always runs, CI included), and
   `test_contract_matches_the_viewer_source` pins BOTH to the Zod enums
   themselves whenever the viewer checkout is next to this one — which is the
   case on every developer machine and is the run that catches the embed adding
   or removing a value.

`classes` is deliberately NOT in the closed-set list: the event taxonomy comes
from the hub at runtime (`events-taxonomy.ts` — "Nothing here is hardcoded,
deliberately"), so the card offers the known macro classes as SUGGESTIONS and
still accepts anything typed.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

ASSET_DIR = Path(__file__).parent.parent / "custom_components" / "camstack" / "frontend"
EVENTS_CARD = ASSET_DIR / "camstack-events-card.js"
GRID_CARD = ASSET_DIR / "camstack-grid-card.js"

# The viewer checkout, when it is a sibling of this one — or wherever
# `CAMSTACK_VIEWER_EMBED_SRC` says, for a checkout that is not (a git worktree,
# say). Absent in CI: this repo is checked out standalone there, which is why
# the mirror is pinned twice.
VIEWER_EMBED_SRC = Path(
    os.environ.get(
        "CAMSTACK_VIEWER_EMBED_SRC",
        Path(__file__).parent.parent.parent
        / "camstack-server"
        / "camstack"
        / "embed"
        / "src"
        / "embed",
    )
)

# --- what the embed accepts, as this repo believes it -----------------------

EXPECTED_EVENTS_CONTRACT: dict[str, list[str]] = {
    "view": ["reel", "gallery"],
    "thumb": ["small", "medium", "large"],
    "theme": ["auto", "dark", "light"],
    "sort": ["time", "importance"],
    "search_mode": ["text", "semantic"],
    "fields": ["label", "sublabel", "camera", "time", "badges"],
    "attributes": ["face", "plate"],
}

EXPECTED_GRID_CONTRACT: dict[str, list[str]] = {
    "quality": ["auto", "high", "mid", "low"],
}

# `v1 query param` → the editor control id that sets it.
EVENTS_PARAM_TO_CONTROL = {
    "view": "view",
    "rows": "rows",
    "cols": "columns",
    "thumb": "thumb",
    "classes": "classes",
    "attrs": "attributes",
    "q": "search",
    "smode": "search_mode",
    "slimit": "semantic_limit",
    "sscore": "semantic_min_score",
    "sort": "sort",
    "fields": "fields",
    "page": "page_size",
    "max": "max",
    "refresh": "refresh_ms",
    "age": "max_age_ms",
    "theme": "theme",
}

# Composed by the card from something other than an editor control, with the
# reason. Changing one of these to "the editor should own it" is a decision, so
# it has to be made here rather than by an assertion quietly not covering it.
EVENTS_PARAMS_WITHOUT_A_CONTROL = {
    # The camera picker owns it.
    "devices": "the entity picker",
    # Built from each entity's friendly name — a rename in Home Assistant must
    # not need a card edit.
    "names": "the Home Assistant friendly names",
    # `v` and `mode` identify the embed, not a preference.
    "v": "the embed's own version gate",
    "mode": "the embed's own mode gate",
}

# Absolute epoch bounds. A dashboard card is permanent; a frozen `since`/`until`
# pair would silently stop the card dead on a date nobody remembers setting.
# `age` (a rolling window) is the option that belongs on a card, and it is here.
EVENTS_PARAMS_DELIBERATELY_UNEXPOSED = {"since", "until"}

CLOSED_SET_CONTROLS = {
    "view",
    "thumb",
    "theme",
    "sort",
    "search_mode",
    "rows",
    "columns",
    "fields",
    "attributes",
}

# Bounded numbers: an input the browser itself constrains, never a text box.
NUMERIC_CONTROLS = {
    "height",
    "semantic_limit",
    "semantic_min_score",
    "page_size",
    "max",
    "refresh_ms",
    "max_age_ms",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _editor_source(source: str, editor_class: str) -> str:
    """Everything from the editor class to the end of the module."""
    index = source.index(f"class {editor_class} ")
    return source[index:]


def _contract(source: str) -> dict[str, list[str]]:
    """Read the `EMBED_CONTRACT` literal's string lists, as the card declares them."""
    start = source.index("const EMBED_CONTRACT = {")
    body = source[start : source.index("\n};", start)]
    found: dict[str, list[str]] = {}
    for key, raw in re.findall(r"(\w+):\s*\[([^\]]*)\]", body):
        values = re.findall(r'"([^"]*)"', raw)
        if values:
            found[key] = values
    return found


# A control is rendered by one of the card's own field helpers, and WHICH helper
# is the whole point of test (b).
CONTROL_CALL = re.compile(
    r"\b(?P<helper>field|select|picker|checkboxGroup|suggestField|textField"
    r'|numberField|selectField|checkboxField|cameraPicker)\(\s*"(?P<id>[a-z_]+)"'
)


def _controls(editor_source: str) -> dict[str, str]:
    """Map each control id to the helper that renders it."""
    return {
        m.group("id"): m.group("helper") for m in CONTROL_CALL.finditer(editor_source)
    }


# --- 1. reachability --------------------------------------------------------


def test_the_events_card_composes_every_option_it_claims() -> None:
    source = _read(EVENTS_CARD)
    start = source.index("  _query(deviceIds) {")
    query = source[start : source.index("\n  }\n", start)]
    expected = set(EVENTS_PARAM_TO_CONTROL) | set(EVENTS_PARAMS_WITHOUT_A_CONTROL)
    for param in sorted(expected - {"names"}):
        assert f'"{param}"' in query, (
            f"the events card's `_query` never names `{param}`; the embed accepts it"
        )
    # `names` is appended raw — URLSearchParams would double-encode it.
    assert "&names=" in query, "the events card no longer sends `names=`"


def test_every_events_option_is_reachable_from_the_editor() -> None:
    editor = _editor_source(_read(EVENTS_CARD), "CamstackEventsCardEditor")
    controls = _controls(editor)
    for param, control in sorted(EVENTS_PARAM_TO_CONTROL.items()):
        assert control in controls, (
            f"`{param}` (embed option) has no `{control}` control in the events "
            "editor — it can only be set by hand-editing YAML"
        )


def test_the_unexposed_events_options_are_named_and_stay_named() -> None:
    """A deliberate omission is a decision; an accidental one is a bug."""
    editor = _editor_source(_read(EVENTS_CARD), "CamstackEventsCardEditor")
    controls = _controls(editor)
    for param in sorted(EVENTS_PARAMS_DELIBERATELY_UNEXPOSED):
        assert param not in controls, (
            f"`{param}` grew an editor control; if that is intended, move it out "
            "of EVENTS_PARAMS_DELIBERATELY_UNEXPOSED and say why here"
        )


# --- 2. no free text over a closed set --------------------------------------


def test_no_closed_set_option_is_a_free_text_input() -> None:
    editor = _editor_source(_read(EVENTS_CARD), "CamstackEventsCardEditor")
    controls = _controls(editor)
    for control in sorted(CLOSED_SET_CONTROLS):
        helper = controls.get(control)
        assert helper in ("select", "checkboxGroup"), (
            f"`{control}` has a closed set of values in the embed's schema but "
            f"is rendered by `{helper}`; it must be a select or a checkbox group"
        )
        assert not re.search(
            rf'field\(\s*"{control}"\s*,\s*"[^"]*"\s*,\s*"text"', editor
        ), f"`{control}` is still a text input"


def test_the_multi_valued_options_are_a_real_multi_selection() -> None:
    """`fields` and `attrs` are sets, not comma strings the operator composes."""
    editor = _editor_source(_read(EVENTS_CARD), "CamstackEventsCardEditor")
    controls = _controls(editor)
    for control in ("fields", "attributes"):
        assert controls.get(control) == "checkboxGroup", (
            f"`{control}` is a SET of embed values; a comma-separated text field "
            "is the thing this test exists to prevent"
        )


def test_bounded_numbers_are_number_inputs() -> None:
    editor = _editor_source(_read(EVENTS_CARD), "CamstackEventsCardEditor")
    for control in sorted(NUMERIC_CONTROLS):
        assert re.search(
            rf'field\(\s*"{control}"\s*,\s*"[^"]*"\s*,\s*"number"', editor
        ), f"`{control}` is not a number input"


def test_the_open_set_keeps_free_text_and_offers_suggestions() -> None:
    """The taxonomy is a hub runtime fact: suggest, never constrain."""
    editor = _editor_source(_read(EVENTS_CARD), "CamstackEventsCardEditor")
    controls = _controls(editor)
    assert controls.get("classes") == "suggestField", (
        "`classes` must stay typeable — the hub can learn a class tomorrow"
    )
    assert "datalist" in editor, "the class suggestions are not offered"


def test_hiding_the_importance_dot_is_one_checkbox() -> None:
    """The operator's ask: hide importance. It rides the `badges` field."""
    source = _read(EVENTS_CARD)
    assert "badges" in _contract(source)["fields"]
    editor = _editor_source(source, "CamstackEventsCardEditor")
    assert re.search(r'checkboxGroup\(\s*"fields"', editor)


# --- 3. the mirror does not drift -------------------------------------------


def test_the_events_contract_matches_this_file() -> None:
    found = _contract(_read(EVENTS_CARD))
    for key, values in EXPECTED_EVENTS_CONTRACT.items():
        assert found.get(key) == values, f"EMBED_CONTRACT.{key} drifted"


def test_the_grid_contract_matches_this_file() -> None:
    found = _contract(_read(GRID_CARD))
    for key, values in EXPECTED_GRID_CONTRACT.items():
        assert found.get(key) == values, f"EMBED_CONTRACT.{key} drifted"


def _ts_enum(source: str, name: str) -> list[str]:
    match = re.search(rf"{name}\s*=\s*z\.enum\(\[(?P<body>[^\]]*)\]", source)
    assert match is not None, f"{name} is no longer a z.enum in the viewer"
    return re.findall(r"'([^']*)'", match.group("body"))


def _ts_const_array(source: str, name: str) -> list[str]:
    match = re.search(rf"{name}\s*=\s*\[(?P<body>[^\]]*)\]", source)
    assert match is not None, f"{name} is no longer an array literal in the viewer"
    return re.findall(r"'([^']*)'", match.group("body"))


def test_contract_matches_the_viewer_source() -> None:
    """The run that catches the EMBED adding or removing a value.

    Skipped when the viewer is not checked out next to this repo (CI). That is
    exactly why the mirror is also pinned to this file above: there, drift shows
    up as a failing expectation the author has to look at.
    """
    if not VIEWER_EMBED_SRC.is_dir():
        pytest.skip(f"viewer checkout not found at {VIEWER_EMBED_SRC}")

    events_config = _read(VIEWER_EMBED_SRC / "events-config.ts")
    share_url = _read(VIEWER_EMBED_SRC / "events-share-url.ts")
    grid_config = _read(VIEWER_EMBED_SRC / "embed-grid-config.ts")

    from_viewer = {
        "view": _ts_enum(events_config, "eventsViewSchema"),
        "thumb": _ts_enum(events_config, "eventsThumbSizeSchema"),
        "theme": _ts_enum(events_config, "eventsThemeSchema"),
        "sort": _ts_enum(events_config, "eventsSortSchema"),
        "search_mode": _ts_enum(events_config, "eventsSearchModeSchema"),
        "attributes": _ts_const_array(share_url, "EVENTS_ATTRIBUTES"),
    }
    # `fields` is a z.object, and its KEYS are the allow-list `fields=` accepts.
    fields_block = re.search(
        r"eventsFieldsSchema = z\.object\(\{(?P<body>.*?)\}\)", events_config, re.S
    )
    assert fields_block is not None
    from_viewer["fields"] = re.findall(
        r"^\s*(\w+):\s*z\.boolean\(\)", fields_block.group("body"), re.M
    )

    events = _contract(_read(EVENTS_CARD))
    for key, values in from_viewer.items():
        assert events.get(key) == values, (
            f"the events card's `{key}` is {events.get(key)} but the embed says "
            f"{values} — update EMBED_CONTRACT and this test together"
        )

    grid = _contract(_read(GRID_CARD))
    assert grid.get("quality") == _ts_enum(grid_config, "gridQualitySchema"), (
        "the grid card's stream-quality list no longer matches the embed"
    )


# --- the grid card's arrangement --------------------------------------------


def test_the_grid_card_has_one_control_for_the_arrangement() -> None:
    """One control decides the arrangement, and each answer owns its fields.

    What this replaces: `max_visible` and `layout` used to disable each other
    and each said so in its own label ("not used while the wall scrolls", "used
    when shape is fixed"). Four controls were always on screen and the operator
    had to hold a state machine to know which two were live.

    A mode-specific field must NOT be rendered unconditionally — a greyed-out
    control is still a control you can misread, and this test is what stops the
    next edit from quietly putting them all back on screen.
    """
    source = _read(GRID_CARD)
    editor = _editor_source(source, "CamstackGridCardEditor")
    controls = _controls(editor)
    assert controls.get("layout_mode") == "selectField", (
        "the grid editor has no arrangement control"
    )
    for legacy in ("max_visible", "layout"):
        assert legacy not in controls, (
            f"`{legacy}` is still a control — it is legacy, read only to derive "
            "a mode for a dashboard written before `layout_mode` existed"
        )
    # The mode-specific fields live behind `_modeFields`, never inline in
    # `_render`. `_editor_source` runs to the end of the module, so asking
    # `_controls` would see them wherever they are declared — the question that
    # separates "behind a mode" from "always on screen" is which METHOD renders
    # them, so that is what this reads.
    assert "_modeFields()" in editor, "no per-mode field set"
    render_start = editor.index("  _render() {")
    render = editor[render_start : editor.index("\n  }\n", render_start)]
    for key in ("max_tile_width", "max_rows", "columns", "rows"):
        assert f'"{key}"' not in render, (
            f"`{key}` is rendered inline in `_render`, so it is on screen in "
            "every mode — it belongs to one mode only, behind `_modeFields`"
        )
    assert '"layout_mode"' in render, "the arrangement control is not unconditional"


def test_the_grid_card_scrolls_rows_not_a_strip() -> None:
    """The wall scrolls VERTICALLY, and one authority decides the geometry.

    The embed fits every tile into its box and never scrolls (there is no
    `overflow` in `EmbedGridPage`). So the scroller is the HOST's, and the plan
    DRIVES the embed's own column count rather than competing with it.

    Vertical because a wall of rows scrolls that way, and because the cap the
    operator sets is "how many ROWS before it scrolls" — which the old one-row
    horizontal strip could not express at all.
    """
    source = _read(GRID_CARD)
    assert "planGrid(" in source, "nothing computes the wall geometry"
    assert "overflow-y:auto" in source, "the wall never scrolls vertically"
    assert "overflow-x:auto" not in source, (
        "the one-row horizontal strip is back — `max_rows` cannot be said in it"
    )
    # One authority: the embed is told the plan's column count, so the host's
    # scroller and the embed's layout cannot disagree about the wall's shape.
    assert re.search(r"_plan\(deviceIds\)\.columns", source), (
        "`_layout` no longer returns the plan's columns, so the scroller and "
        "the embed can lay the wall out differently"
    )
    # `flow` caps a tile's width in px, which is only knowable from the card's
    # measured width — a breakpoint guess would be a promise it cannot keep.
    assert "ResizeObserver" in source, "the card never measures its own width"


def test_every_wall_option_rides_the_open_channel() -> None:
    """Every wall option travels as a command, not only as the initial config.

    An option that only reaches the initial config does nothing until something
    rebuilds the iframe — and rebuilding it restarts every WebRTC session on the
    wall. `acquireConfig` resolves once, so a second `embed-config` is read by
    nobody; the commands are the live path, and a remounted page has to be told
    again.
    """
    source = _read(GRID_CARD)
    start = source.index("  _publishWall(force) {")
    body = source[start : source.index("\n  }\n", start)]
    for command in (
        "setDevices",
        "setLayout",
        "setQuality",
        "setShowName",
        "setActiveOnly",
        "setShowBoxes",
        "setHighlight",
    ):
        assert f'"{command}"' in body or f"{command}:" in body, (
            f"`{command}` is never pushed, so the option that drives it is dead "
            "until the frame is rebuilt"
        )
    republish = source.index("  _republishHostState() {")
    body = source[republish : source.index("\n  }\n", republish)]
    assert "_publishWall(true)" in body, (
        "a page that has just (re)mounted is never told the wall settings again"
    )


def test_the_python_of_this_file_stays_valid() -> None:
    """Cheap guard: this module is grep-driven, so a typo must not pass as a skip."""
    ast.parse(_read(Path(__file__)))
