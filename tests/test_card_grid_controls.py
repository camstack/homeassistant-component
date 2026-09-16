"""The grid card's control bar is the VIEWER's bar, and its mirror does not rot.

The operator's instruction for this surface was "esattamente come
sull'applicazione viewer". So nothing about it was designed here: the control
set and its order come from `GridControlBar.tsx`, the highlight triggers and
their values from `HighlightTriggerRows.tsx` + `audio-level.ts` + the grid
store, and the automatic arrangement's column rule from `use-responsive.ts`.

A Lovelace card is plain JavaScript and cannot import the viewer's TypeScript,
so those values are MIRRORED in `camstack-grid-controls.js` — the same
arrangement `EMBED_CONTRACT` already lives with, and with the same discipline:

* the tests that pin the mirror to the expectation written HERE always run,
  CI included, so drift is a failing expectation an author has to look at;
* the tests that pin BOTH to the viewer's own source run whenever the viewer is
  checked out beside this repo, and are what catch the VIEWER changing a value.

The three things this file refuses to let happen:

1. A control the viewer's bar does not have, or in an order it does not use.
2. An audio threshold that is not a meter rung — the presets ARE rungs 2/4/6
   (`audio-level.ts`), and a free dB number here would ask the hub for a
   threshold the app's own meter cannot show.
3. A camera narrower than the automatic arrangement promises.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

ASSET_DIR = Path(__file__).parent.parent / "custom_components" / "camstack" / "frontend"
GRID_CARD = ASSET_DIR / "camstack-grid-card.js"
GRID_CONTROLS = ASSET_DIR / "camstack-grid-controls.js"

# The viewer's APP source (`src/`), not its embed bundle — the bar, the trigger
# rows and the responsive rules all live there. Same override and the same
# "absent in CI" story as `VIEWER_EMBED_SRC` in `test_card_editor_options.py`.
VIEWER_APP_SRC = Path(
    os.environ.get(
        "CAMSTACK_VIEWER_APP_SRC",
        Path(__file__).parent.parent.parent / "camstack-server" / "camstack" / "src",
    )
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- 1. the control set -----------------------------------------------------

# The bar's buttons, in the order the operator sees them. `mic` is the one entry
# the viewer's bar does not have, and it is not a control: it is the refusal
# notice for the in-tile talk button, which the embed draws on every tile and
# which cannot work behind this card's credential (see test_the_mic_* below).
EXPECTED_BAR = ("pause", "audio", "mic", "quality", "layout", "highlight", "activeOnly")

# How a card button maps onto the viewer bar's own `key`. `highlight` is the
# viewer's `menu` (the Zap button that opens `GridMenuPanel`) under the name it
# actually has on this surface.
VIEWER_KEY = {"highlight": "menu"}

# The viewer bar entries a Lovelace card has no honest answer for, each with the
# reason it is absent — named here so "we decided not to" stays distinguishable
# from "nobody noticed", exactly as the ignored embed messages are.
ABSENT_FROM_THE_CARD = {
    "timeline": "this card mounts no timeline, so there is no chrome to show or hide",
    "share": "the standalone share link is a viewer surface; a dashboard shares its own way",
    "edit": "the grid builder is a viewer surface; this wall's membership is the Lovelace config",
}


def _bar_actions() -> list[str]:
    """The bar's buttons, in declaration order, from the `definitions` list."""
    source = _read(GRID_CONTROLS)
    start = source.index("  const definitions = [")
    body = source[start : source.index("\n  ];", start)]
    return re.findall(r'^\s*\["(\w+)"', body, re.M)


def test_the_bar_draws_the_viewer_s_controls_in_the_viewer_s_order() -> None:
    assert _bar_actions() == list(EXPECTED_BAR)


def test_the_bar_is_a_subsequence_of_the_viewer_s_bar() -> None:
    """Order is the assertion, not membership.

    An operator who learned that quality sits left of the highlight menu must
    find it there on the wall AND on the dashboard. A set comparison would let
    the two drift into different orders and still pass.
    """
    if not VIEWER_APP_SRC.is_dir():
        pytest.skip(f"viewer checkout not found at {VIEWER_APP_SRC}")
    source = _read(VIEWER_APP_SRC / "components" / "grid" / "GridControlBar.tsx")
    start = source.index("const actions: ControlBarAction[] = [")
    viewer = re.findall(r"key:\s*'(\w+)'", source[start:])
    assert viewer, "the viewer's bar no longer declares its actions as `key:`"

    for absent, reason in ABSENT_FROM_THE_CARD.items():
        assert absent in viewer, (
            f"`{absent}` is no longer in the viewer's bar, so the card's reason "
            f"for omitting it ({reason}) describes nothing"
        )

    wanted = [VIEWER_KEY.get(key, key) for key in EXPECTED_BAR if key != "mic"]
    position = {key: index for index, key in enumerate(viewer)}
    missing = [key for key in wanted if key not in position]
    assert not missing, (
        f"the card's bar has controls the viewer's does not: {missing}. The "
        "viewer is the source of truth for what a wall's bar contains."
    )
    order = [position[key] for key in wanted]
    assert order == sorted(order), (
        f"the card's bar orders its controls {wanted}, which is not the order "
        f"the viewer's bar uses ({viewer})"
    )


# --- 2. the highlight vocabulary --------------------------------------------

EXPECTED_THRESHOLDS = [-60, -53, -46, -39, -32, -24]
EXPECTED_RUNGS = [2, 4, 6]
EXPECTED_HOLD_SEC = 5
EXPECTED_MACRO_CLASSES = ["person", "vehicle", "animal", "face", "plate"]
EXPECTED_HOLD_OPTIONS = [2, 3, 5, 8]


# The viewer annotates its exports (`: readonly number[] = [...]`), the card
# cannot — one pattern reads both rather than two that can disagree.
def _array_body(source: str, name: str) -> str:
    match = re.search(rf"\b{name}\b[^=\n]*=\s*\[([^\]]*)\]", source)
    assert match is not None, f"{name} is no longer an array literal"
    return match.group(1)


def _numbers(source: str, name: str) -> list[int]:
    return [int(value) for value in re.findall(r"-?\d+", _array_body(source, name))]


def _strings(source: str, name: str) -> list[str]:
    return re.findall(r"['\"]([^'\"]+)['\"]", _array_body(source, name))


def test_the_mirror_matches_this_file() -> None:
    """Always runs, CI included."""
    source = _read(GRID_CONTROLS)
    assert _numbers(source, "AUDIO_LEVEL_THRESHOLDS_DBFS") == EXPECTED_THRESHOLDS
    assert _numbers(source, "AUDIO_HIGHLIGHT_LEVELS") == EXPECTED_RUNGS
    assert _numbers(source, "DETECTION_HOLD_OPTIONS") == EXPECTED_HOLD_OPTIONS
    assert _strings(source, "DETECTION_MACRO_CLASSES") == EXPECTED_MACRO_CLASSES
    hold = re.search(r"AUDIO_HIGHLIGHT_HOLD_SEC\s*=\s*(\d+)", source)
    assert hold is not None and int(hold.group(1)) == EXPECTED_HOLD_SEC


def test_the_audio_presets_are_meter_rungs_and_not_a_dial() -> None:
    """The three presets must resolve to rungs 2 / 4 / 6 and nothing else.

    A number typed in here instead would be a threshold the app's own meter
    cannot draw — the operator would set a level they can never see reached.
    """
    source = _read(GRID_CONTROLS)
    mapping = re.search(r"UI_TO_RUNG\s*=\s*\{([^}]*)\}", source)
    assert mapping is not None, "the preset→rung mapping is gone"
    rungs = [int(value) for value in re.findall(r"\b\w+:\s*(\d+)", mapping.group(1))]
    assert rungs == EXPECTED_RUNGS

    # …and the only way a dB reaches the embed is through that mapping. The one
    # literal allowed is inside GRID_HIGHLIGHT_DEFAULTS, which is the viewer's
    # own default (-30) and is inert while `audio` is `off`.
    assert "dbfsForAudioUi(" in source
    defaults = re.search(
        r"GRID_HIGHLIGHT_DEFAULTS = Object\.freeze\(\{.*?\n\}\)", source, re.S
    )
    assert defaults is not None
    elsewhere = source.replace(defaults.group(0), "")
    assert not re.search(r"audioDb:\s*-?\d", elsewhere), (
        "a dB threshold is written as a literal outside the defaults; it must "
        "come from `dbfsForAudioUi`, the viewer's own rung lookup"
    )


def test_the_highlight_vocabulary_matches_the_viewer() -> None:
    if not VIEWER_APP_SRC.is_dir():
        pytest.skip(f"viewer checkout not found at {VIEWER_APP_SRC}")
    audio_level = _read(VIEWER_APP_SRC / "components" / "camera" / "audio-level.ts")
    store = _read(VIEWER_APP_SRC / "store" / "grid-store.ts")

    assert _numbers(audio_level, "AUDIO_LEVEL_THRESHOLDS_DBFS") == EXPECTED_THRESHOLDS
    assert _numbers(audio_level, "AUDIO_HIGHLIGHT_LEVELS") == EXPECTED_RUNGS
    hold = re.search(r"AUDIO_HIGHLIGHT_HOLD_SEC\s*=\s*(\d+)", audio_level)
    assert hold is not None and int(hold.group(1)) == EXPECTED_HOLD_SEC
    assert _strings(store, "DETECTION_MACRO_CLASSES") == EXPECTED_MACRO_CLASSES

    # The defaults, field for field. `clusterLabel` is the viewer's timeline
    # setting and is deliberately not mirrored — this card mounts no timeline.
    block = re.search(
        r"GRID_HIGHLIGHT_DEFAULTS[^=]*=\s*\{(?P<body>.*?)\n\}", store, re.S
    )
    assert block is not None
    viewer_defaults = dict(
        re.findall(r"^\s*(\w+):\s*([^,\n]+),", block.group("body"), re.M)
    )
    viewer_defaults.pop("clusterLabel", None)

    card_block = re.search(
        r"GRID_HIGHLIGHT_DEFAULTS = Object\.freeze\(\{(?P<body>.*?)\n\}\)",
        _read(GRID_CONTROLS),
        re.S,
    )
    assert card_block is not None
    card_defaults = dict(
        re.findall(r"^\s*(\w+):\s*([^,\n]+),", card_block.group("body"), re.M)
    )
    # TypeScript quotes with `'`, this repo's JavaScript with `"` — a quote
    # style is not a drift.
    def unquote(values: dict[str, str]) -> dict[str, str]:
        return {key: value.strip("'\"") for key, value in values.items()}

    assert unquote(card_defaults) == unquote(viewer_defaults), (
        "the card's highlight defaults drifted from the viewer's grid store"
    )


def test_the_highlight_rides_the_open_channel() -> None:
    """A wall option that only reaches the seed config is dead until a rebuild.

    The same rule `test_every_wall_option_rides_the_open_channel` states for the
    others; the highlight is here because it is the one the bar edits live.
    """
    source = _read(GRID_CARD)
    start = source.index("  _publishWall(force) {")
    body = source[start : source.index("\n  }\n", start)]
    assert "setHighlight" in body, (
        "the highlight never rides a command, so every press of the bar's "
        "highlight menu does nothing until the iframe is rebuilt"
    )
    assert "highlight: this._highlightCommand()" in source, (
        "a page mounting now is not seeded with the highlight, so the wall "
        "renders its first frames unlit"
    )


# --- 3. the mic -------------------------------------------------------------


def test_the_mic_is_refused_with_its_reason_and_never_faked() -> None:
    """Talk-back cannot work behind this card's credential, and says so.

    The embed's tile draws a talk disc on every camera (`GridTilePlayer.tsx`),
    and the hub's share-token perimeter excludes `intercom.*` from the
    `grid-view` scope on purpose — which is the only scope this integration
    mints. So the button exists and cannot work.

    A control shown disabled WITH the reason is the honest answer. Hiding it
    would make a capability the operator knows the app has look like it does not
    exist; wiring it up would be a failure at the hub with no explanation.
    """
    source = _read(GRID_CONTROLS)
    reason = re.search(
        r"MIC_UNAVAILABLE_REASON\s*=\s*(?P<body>(?:\s*\"[^\"]*\"\s*\+?)+);", source
    )
    assert reason is not None, "the mic has no reason to give"
    text = " ".join(re.findall(r'"([^"]*)"', reason.group("body")))
    for word in ("grid-view", "intercom"):
        assert word in text, f"the mic's reason does not name `{word}`: {text}"

    assert 'disabled: true, title: MIC_UNAVAILABLE_REASON' in source, (
        "the mic rows are not disabled with their reason"
    )
    # Never shown as live, and never asked for: `setActiveMic` is a real embed
    # command, and sending it would open a session the hub refuses.
    assert "setActiveMic" not in source and "setActiveMic" not in _read(GRID_CARD), (
        "the card asks the embed for a mic session it cannot have"
    )


# --- 4. the automatic arrangement -------------------------------------------

# `useGridColumns` in `camstack/src/hooks/use-responsive.ts`.
EXPECTED_AUTO = {
    "AUTO_TABLET_WIDE_MIN": 700,
    "AUTO_DESKTOP_MIN": 1100,
    "AUTO_WIDE_DESKTOP_STEP": 360,
    "AUTO_MAX_COLUMNS": 6,
}
VIEWER_AUTO = {
    "AUTO_TABLET_WIDE_MIN": "TABLET_WIDE_MIN",
    "AUTO_DESKTOP_MIN": "DESKTOP_MIN",
    "AUTO_WIDE_DESKTOP_STEP": "WIDE_DESKTOP_STEP",
    "AUTO_MAX_COLUMNS": "MAX_GRID_COLUMNS",
}


def _const(source: str, name: str) -> int:
    match = re.search(rf"\b{name}\s*=\s*(-?\d+)", source)
    assert match is not None, f"{name} is no longer a number"
    return int(match.group(1))


def test_the_auto_arrangement_mirrors_this_file() -> None:
    source = _read(GRID_CARD)
    for name, value in EXPECTED_AUTO.items():
        assert _const(source, name) == value, f"{name} drifted"
    # The floor the rule can never go under: the narrowest camera it produces is
    # at the first multi-column breakpoint, 700 / 3.
    floor = _const(source, "AUTO_MIN_TILE_WIDTH")
    assert floor == EXPECTED_AUTO["AUTO_TABLET_WIDE_MIN"] // 3, (
        "the stated minimum camera width is not the one the rule produces"
    )


def test_the_auto_arrangement_mirrors_the_viewer() -> None:
    if not VIEWER_APP_SRC.is_dir():
        pytest.skip(f"viewer checkout not found at {VIEWER_APP_SRC}")
    responsive = _read(VIEWER_APP_SRC / "hooks" / "use-responsive.ts")
    for card_name, viewer_name in VIEWER_AUTO.items():
        assert _const(responsive, viewer_name) == EXPECTED_AUTO[card_name], (
            f"the viewer's `{viewer_name}` changed; the card's `{card_name}` "
            "must follow, and so must this expectation"
        )
    # The orientation rule is the half a pixel formula gets wrong: a phone held
    # upright gets ONE camera, turned sideways it gets two.
    assert re.search(r"width > height \? 2 : 1", responsive), (
        "the viewer no longer decides the narrow case by orientation"
    )
    assert re.search(r"landscape \? 2 : 1", _read(GRID_CARD)), (
        "the card no longer decides the narrow case by orientation"
    )


def test_auto_is_the_default_only_for_a_card_that_chose_nothing() -> None:
    """A wall somebody laid out keeps its shape.

    `auto` is the DEFAULT, not a migration. A dashboard written before it
    existed still chose an arrangement in the vocabulary it had — a pinned
    column count, or the legacy `max_visible` cap — and both must keep
    answering.
    """
    source = _read(GRID_CARD)
    modes = re.search(r"const LAYOUT_MODES = \[([^\]]*)\]", source)
    assert modes is not None
    assert re.findall(r'"(\w+)"', modes.group(1))[0] == "auto", (
        "`auto` is not the first arrangement offered"
    )
    start = source.index("  _layoutMode() {")
    body = source[start : source.index("\n  }\n", start)]
    assert '"fixed"' in body, "the legacy `max_visible` cap no longer derives a mode"
    assert "this._config.columns ?? this._config.layout" in body, (
        "a pinned column count no longer counts as an arrangement the operator "
        "chose, so `auto` would replace it under a wall somebody built"
    )
    assert '"auto"' in body, "`_layoutMode` never answers `auto`"


# --- 5. the scroll gutter ---------------------------------------------------


def test_a_scrolling_wall_keeps_a_strip_a_finger_can_reach() -> None:
    """MEASURED, not guessed — see `tests/browser/grid-card-scroll.spec.cjs`.

    The embed puts `touch-action: none` over every tile so it can pinch and pan
    a camera, which means a touch on the frame never reaches the host's
    scroller. With a plain frame the same drag moves it 391 px; with the
    embed's overlay, 0. The card cannot reach inside a frame, so its answer is
    where the frame ENDS.
    """
    source = _read(GRID_CARD)
    gutter = _const(source, "SCROLL_GUTTER_PX")
    assert gutter >= 20, (
        f"a {gutter}px strip is smaller than a fingertip; the wall stays "
        "unscrollable on every phone"
    )
    assert f"width:calc(100% - ${{SCROLL_GUTTER_PX}}px)" in source, (
        "the frame still covers the whole scroller, so there is nothing to grab"
    )
    start = source.index("  _frameStyle() {")
    body = source[start : source.index("\n  }\n", start)]
    assert body.count("SCROLL_GUTTER_PX") >= 1
    # It costs width, so it exists only where there is something to scroll.
    assert "plan.scrolls" in body


def test_the_python_of_this_file_stays_valid() -> None:
    """Cheap guard: this module is grep-driven, so a typo must not pass as a skip."""
    ast.parse(_read(Path(__file__)))
