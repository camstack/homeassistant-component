"""The grid card answers the embed's tile messages — every one of them.

The embed is a HOST-DRIVEN player. Its in-tile buttons change no state of
their own: they post an intent (`audioToggle`, `pauseToggle`, `tileOpen`, …)
and wait for the host to push the resulting state back
(`setAudioOn`, `setPaused`, …). The vocabulary is
`camstack/embed/src/embed/grid-messages.ts` in the viewer.

Until 2026-09-06 this card handled `embed-ready` and `state` and nothing else,
so an operator's report read: "the cards work, but no button does anything".
Every button was a message into the void.

These are GREPS, like the other card guards: the cards are plain ES modules
Lovelace loads, and the regression this protects against is a message type
being added to the contract (or removed from the switch) with nobody noticing.
The BEHAVIOUR — that a toggle produces the right command and does not rebuild
the iframe — is proven in `tests/browser/grid-card-host.spec.cjs`, which runs
the card in Chromium.

A type may be handled or explicitly ignored. It may not be silently dropped:
an unlisted type is a button that does nothing, which is the exact defect.
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND = Path(__file__).parent.parent / "custom_components" / "camstack" / "frontend"
GRID_CARD = FRONTEND / "camstack-grid-card.js"

# Every `GridHostMessage.type` the embed can post, mirrored from
# `grid-messages.ts`. `state` is the report, not a tile intent, and is handled
# alongside the `embed-ready` handshake.
EMBED_MESSAGE_TYPES = (
    "state",
    "tileTap",
    "tileLongPress",
    "tileOpen",
    "audioToggle",
    "pauseToggle",
    "ptzOpen",
    "cellPanelOpen",
    "tileRemove",
    "tileResize",
    "layout",
)

# The types this card deliberately does NOT act on. Each must carry a reason in
# a comment right at its `case`, so the next reader learns why rather than
# reading a hole.
IGNORED_TYPES = (
    "ptzOpen",
    "cellPanelOpen",
    "tileRemove",
    "tileResize",
    "layout",
)


def _source() -> str:
    return GRID_CARD.read_text(encoding="utf-8")


def _on_message_body(source: str) -> str:
    """Return the `_onMessage` method body, braces matched."""
    marker = "_onMessage(event) {"
    start = source.find(marker)
    assert start != -1, "the grid card no longer has an _onMessage handler"
    depth = 0
    for index in range(start + len(marker) - 1, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError("_onMessage is not brace-balanced")


def test_every_embed_message_type_reaches_a_case() -> None:
    """No tile intent may fall off the end of the switch."""
    body = _on_message_body(_source())
    missing = [t for t in EMBED_MESSAGE_TYPES if f'case "{t}":' not in body]
    assert not missing, (
        f"the grid card ignores these embed messages silently: {missing}. "
        "Handle them, or add an explicit `case` with a comment saying why not."
    )


def test_every_ignored_type_says_why() -> None:
    """An explicit no-op is only honest with its reason next to it."""
    body = _on_message_body(_source())
    for message_type in IGNORED_TYPES:
        case = body.find(f'case "{message_type}":')
        assert case != -1, f"{message_type}: no case"
        # The comment may sit above the case (shared by a group of them) or in
        # the block below it; either way it is within reach of the reader.
        window = body[max(0, case - 1400) : case + 900]
        assert "//" in window or "/*" in window, (
            f"{message_type}: ignored with no explanation"
        )


def test_the_host_owned_toggles_answer_with_the_embed_s_own_commands() -> None:
    """audio/pause are host-owned SETS pushed back whole (`grid-messages.ts`)."""
    source = _source()
    assert '"setAudioOn"' in source, "audioToggle is never answered"
    assert '"setPaused"' in source, "pauseToggle is never answered"
    assert '"embed-command"' in source, (
        "the card posts no embed-command; the embed accepts commands only in "
        "that envelope (host-bridge.ts `onHostCommand`)"
    )


def test_host_owned_state_survives_a_re_render() -> None:
    """`set hass` fires several times a second; a muted tile must stay muted.

    The state lives on the element, not in a closure rebuilt per render, and
    is re-pushed when the embed reports `ready` (a reconnect remounts the page
    with the config's seed, which is stale the moment a tile was toggled).
    """
    source = _source()
    assert "this._audioOnIds" in source, "the audio set is not kept on the card"
    assert "this._pausedIds" in source, "the paused set is not kept on the card"
    assert "audioOnIds:" in source, "the sets do not seed a fresh embed's config"
    assert "pausedIds:" in source
    assert "_republishHostState()" in source, (
        "nothing re-pushes the host-owned sets when the embed comes back"
    )


def test_a_tile_open_reaches_the_camera_entity() -> None:
    """`tileOpen` is a navigation intent; more-info is Home Assistant's answer."""
    source = _source()
    assert "hass-more-info" in source, "tileOpen opens nothing"
    assert "_entityForDevice(" in source, (
        "the card never maps the hub device id back to an entity"
    )


def test_no_card_posts_data_to_a_wildcard_origin() -> None:
    """A `postMessage` that carries data names its target origin.

    The relayed path no longer ships the share token, but the messages still
    name the cameras a dashboard shows and drive the wall's state; `'*'` hands
    both to whatever ends up in that frame.
    """
    for path in sorted(FRONTEND.glob("*.js")):
        source = path.read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(("*", "//", "/*")):
                continue
            assert not re.search(r'postMessage\([^)]*,\s*[\'"]\*[\'"]', stripped), (
                f"{path.name}:{number}: postMessage to '*'"
            )


def test_the_wildcard_guard_would_catch_a_real_regression() -> None:
    """The guard above is a regex; prove it matches the shape it is aimed at."""
    assert re.search(
        r'postMessage\([^)]*,\s*[\'"]\*[\'"]',
        'frame.postMessage({ type: "embed-command" }, "*");',
    )
