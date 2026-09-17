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

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "camstack"
ASSET_DIR = COMPONENT_DIR / "frontend"
GRID_CARD = ASSET_DIR / "camstack-grid-card.js"
GRID_CONTROLS = ASSET_DIR / "camstack-grid-controls.js"
EMBED_TOKEN = COMPONENT_DIR / "embed_token.py"

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
    "share": (
        "the standalone share link is a viewer surface; a dashboard shares its own way"
    ),
    "edit": (
        "the grid builder is a viewer surface; this wall's membership is the "
        "Lovelace config"
    ),
}


def _bar_actions() -> list[str]:
    """Return the bar's buttons, in declaration order, from `definitions`."""
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


# The four states the talk control can be in, and the gate each one names.
# They exist separately because each has a DIFFERENT fix, and because
# not-yet-known must never be drawn as not-granted (D315).
TALK_STATES = ("granted", "off", "declined", "unknown")


def test_the_talk_control_names_which_gate_refused_it() -> None:
    """Never hidden, never live-but-broken, and never blaming the wrong gate.

    Talk-back rides an opt-in `talk` flag on the hub's share-token scope. Who
    may ask for it is the INTEGRATION's option — see
    `test_only_the_entry_option_grants_talk_back`. So a refusal has three
    possible authors and a fourth state for "nobody has answered yet"; telling
    an operator to turn on an option while the truth is that no mint has come
    back sends them looking in the wrong place.
    """
    source = _read(GRID_CONTROLS)
    block = re.search(r"TALK_REASONS = \{(?P<body>.*?)\n\};", source, re.S)
    assert block is not None, "the talk control has no reasons to give"
    for state in TALK_STATES:
        assert re.search(rf"^\s*{state}:", block.group("body"), re.M), (
            f"`{state}` has no reason; a refusal with no author is a dead button"
        )

    # The `off` reason must send the operator to the INTEGRATION's option, not
    # to the card — the card cannot grant it.
    reasons = block.group("body")
    off = re.search(r"off:(?P<body>.*?)\n  \w+:", reasons, re.S)
    assert off is not None
    assert "integration" in off.group("body"), (
        "the refusal does not name the integration option, which is the only "
        "thing that can grant talk-back"
    )
    # …and the `unknown` reason must NOT, or not-yet-known reads as off.
    unknown = re.search(r"unknown:(?P<body>.*)", reasons, re.S)
    assert unknown is not None
    assert "Turn on" not in unknown.group("body"), (
        "not-yet-known tells the operator to flip an option that may not exist"
    )

    # Unknown is its own answer, folded into neither.
    start = source.index("export function talkState(")
    body = source[start : source.index("\n}", start)]
    assert "granted === null" in body and "undefined" in body, (
        "an unanswered mint is being folded into `off`"
    )

    assert "disabled: refused, title: reason" in source, (
        "the talk rows are not disabled with their reason"
    )
    # The card holds no mic session of its own — the TILE's button does — so it
    # must never send the embed's `setActiveMic`, which would claim one.
    assert "setActiveMic" not in source and "setActiveMic" not in _read(GRID_CARD), (
        "the card claims a mic session it does not own"
    )


def test_only_the_entry_option_grants_talk_back() -> None:
    """A dashboard edit must not hand anybody the microphone of the house.

    The mint endpoint is `requires_auth` with no admin gate — the operator's
    decision — and a Lovelace config is editable by anyone who can edit a
    dashboard. So the grant is the config ENTRY's option and the card may only
    restrict it. The behaviour is proven in `tests/test_embed_token.py`; this is
    the guard that the shape does not quietly invert.
    """
    mint = _read(EMBED_TOKEN)
    assert "CONF_TALK_ENABLED" in mint, "the mint never reads the entry's option"
    start = mint.index("def async_talk_allowed(")
    body = mint[start : mint.index("\n\n\nclass ", start)]
    assert "entry.options.get(CONF_TALK_ENABLED" in body, (
        "the option is not read from `options`, so an existing entry would need "
        "a migration to answer at all"
    )
    assert "DEFAULT_TALK_ENABLED" in body, "the default is not the named one"

    # AND, never OR: the option grants and the card narrows.
    granted = re.search(r"granted = \(\s*talk is True(?P<body>.*?)\)\n", mint, re.S)
    assert granted is not None, "the card's ask and the entry's option are not ANDed"
    assert "async_talk_allowed" in granted.group("body")

    # Absent, never `false`: the hub's schema says absent means off, and a
    # payload that changed shape for every caller would break the compat story.
    assert '**({"talk": True} if talk else {})' in mint, (
        "the scope sends `talk` unconditionally"
    )


def test_a_talking_token_is_not_interchangeable_with_a_silent_one() -> None:
    """`talk` belongs in the cache key AND in the grant key.

    Two tokens for the same cameras are different CREDENTIALS when one of them
    can speak into the house. The cache is keyed per (entry, kind, device ids)
    so a re-rendering card does not mint per render — add talk-back without
    touching that key and the first mint of a scope is served to every later
    request for it, in both directions.
    """
    mint = _read(EMBED_TOKEN)
    assert "key = (entry_id, kind, tuple(device_ids), talk)" in mint, (
        "the token cache cannot tell a talking token from a silent one"
    )
    assert '"talk" if key[3] else "no-talk"' in mint, (
        "the same-origin grant cannot tell them apart, so the relay would "
        "inject whichever token was issued last"
    )
    # The card's own token cache has the same split, or it keeps a stale one
    # after it changes its mind.
    card = _read(GRID_CARD)
    assert '_talkAsked() ? "talk" : "no-talk"' in card, (
        "the card reuses a token minted for a different talk-back answer"
    )
    # And the card reads what it GOT, never what it asked for.
    assert 'typeof result.talk === "boolean" ? result.talk : null' in card, (
        "the card assumes the grant it asked for"
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
    assert "width:calc(100% - ${SCROLL_GUTTER_PX}px)" in source, (
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
