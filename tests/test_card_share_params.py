"""Every option the viewer's share builders freeze reaches the embed from a card.

The viewer's share dialogs offer the same view two ways: an iframe link and a
Home Assistant card YAML. The operator's rule is that every builder option is
honoured in BOTH outputs. The link already carries all of them, so each card
key here is proven against the param the link writes for the same choice:

* events — `camera_names` → `names=<id>:<encoded name>` (`encodeDeviceNames`
  in the embed's `events-url-config.ts`): the builder's local renames, which a
  card configured with `device_ids` had no way to say.
* grid — `audio_on` → `audioOnIds` (the link's `audio=`): which tiles start
  audible.
* grid — `highlight_*_color` / `highlight_thickness` → `highlight.appearance`
  (inside the link's `hl=`): the border colours.

And one removal: events are newest first, always. There is no `sort` left in
the card, and a dashboard still carrying `sort: importance` must open as
before — newest first — rather than fail.

These run the cards' real code (see `card_js.py`); they read back the URL or
the `embed-config` message the card built.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from .card_js import camera_state, run_card

FRONTEND = Path(__file__).parent.parent / "custom_components" / "camstack" / "frontend"
EVENTS_CARD = FRONTEND / "camstack-events-card.js"

# --- events: the URL the card frames ---------------------------------------


def _events_query(config: dict, states: dict, device_ids: list[int]) -> str:
    return run_card(
        "camstack-events-card.js",
        f"""
        const card = new Card();
        card._config = {json.dumps(config)};
        card._hass = hassWith({json.dumps(states)});
        return card._query({json.dumps(device_ids)});
        """,
    )


def _raw_param(query: str, name: str) -> str | None:
    """Return the RAW value, as the embed's `rawQueryParam` reads it."""
    for part in query.split("&"):
        key, _, value = part.partition("=")
        if key == name:
            return value
    return None


def test_camera_names_become_the_link_s_names_param() -> None:
    query = _events_query(
        {"device_ids": [617, 615], "camera_names": {"617": "Garage, retro"}},
        {},
        [617, 615],
    )
    # One encodeURIComponent per name, exactly as `encodeDeviceNames` writes it.
    assert _raw_param(query, "names") == "617:Garage%2C%20retro"


def test_camera_names_accept_yaml_integer_keys_and_drop_cameras_not_shown() -> None:
    # YAML `617: Garage` arrives as a numeric key; the browser stringifies it,
    # so this is the same object as {"617": ...} — but 999 is not on the card.
    query = _events_query(
        {"device_ids": [617], "camera_names": {"617": "Garage", "999": "Elsewhere"}},
        {},
        [617],
    )
    assert _raw_param(query, "names") == "617:Garage"


def test_an_explicit_name_wins_over_the_entity_s_friendly_name() -> None:
    states = {
        "camera.garage": camera_state(617, "Garage (HA)"),
        "camera.porch": camera_state(615, "Porch (HA)"),
    }
    query = _events_query(
        {
            "entities": ["camera.garage", "camera.porch"],
            "camera_names": {"617": "Box"},
        },
        states,
        [617, 615],
    )
    assert _raw_param(query, "names") == "617:Box,615:Porch%20(HA)"


def test_a_blank_or_non_string_name_is_ignored() -> None:
    query = _events_query(
        {"device_ids": [617, 615], "camera_names": {"617": "  ", "615": 12}},
        {},
        [617, 615],
    )
    assert _raw_param(query, "names") is None


def test_an_old_sort_key_is_ignored_and_the_card_is_newest_first() -> None:
    query = _events_query({"device_ids": [617], "sort": "importance"}, {}, [617])
    assert "sort" not in parse_qs(query), "the card still sends an order"
    assert "importance" not in query


def test_the_events_card_has_no_order_left() -> None:
    source = EVENTS_CARD.read_text(encoding="utf-8")
    assert not re.search(r"\bsort:\s*\[", source), "EMBED_CONTRACT still has `sort`"
    assert '"sort"' not in source, "the card or its editor still names `sort`"
    assert '"importance"' not in source, "the card still offers importance"


# --- grid: the embed-config message the card posts --------------------------

# The card's transport stubbed to a direct frame that has a token, so
# `_sendConfig` runs to the postMessage. Nothing here computes a wall setting.
_GRID_DIRECT = """
const sent = [];
const card = new Card();
card._render = () => {};
card._baseUrl = () => "http://hub.test";
card._frameBase = () => "http://hub.test";
card._serverUrl = () => "http://hub.test";
card._isDirect = () => true;
card._isRelayed = () => false;
card._token_for = async () => ({ token: "t" });
card._layout = () => "auto";
card._setStatus = () => {};
card._iframe = { contentWindow: { postMessage: (message) => sent.push(message) } };
"""


def _grid_embed_config(config: dict) -> dict:
    return run_card(
        "camstack-grid-card.js",
        f"""{_GRID_DIRECT}
        card._hass = hassWith({{}});
        card.setConfig({json.dumps(config)});
        sent.length = 0;
        await card._sendConfig();
        return sent.find((m) => m.type === "embed-config").config;
        """,
    )


def test_audio_on_seeds_the_audible_tiles() -> None:
    config = _grid_embed_config({"device_ids": [617, 615, 614], "audio_on": [615, 614]})
    assert config["audioOnIds"] == [615, 614]
    # The link decodes the same way: everything muted except the listed tiles.
    assert config["muted"] is True


def test_audio_on_drops_cameras_not_on_the_wall_and_junk() -> None:
    config = _grid_embed_config(
        {"device_ids": [617, 615], "audio_on": [615, 999, "x", -1]}
    )
    assert config["audioOnIds"] == [615]


def test_no_audio_on_means_every_tile_starts_silent() -> None:
    config = _grid_embed_config({"device_ids": [617, 615]})
    assert config["audioOnIds"] == []


def test_a_saved_audio_on_reaches_an_open_wall() -> None:
    """`embed-config` is read once; a later change must ride a command."""
    commands = run_card(
        "camstack-grid-card.js",
        f"""{_GRID_DIRECT}
        card._hass = hassWith({{}});
        card.setConfig({{ device_ids: [617, 615] }});
        sent.length = 0;
        card.setConfig({{ device_ids: [617, 615], audio_on: [617] }});
        return sent.filter((m) => m.type === "embed-command").map((m) => m.command);
        """,
    )
    assert {"kind": "setAudioOn", "value": [617]} in commands


def test_border_colours_become_the_highlight_appearance() -> None:
    config = _grid_embed_config(
        {
            "device_ids": [617],
            "highlight": True,
            "highlight_motion_color": "#00FF00",
            "highlight_detection_color": "#123",
            "highlight_thickness": 5,
        }
    )
    # The embed's schema needs all four once `appearance` is present; a key the
    # card did not set is the viewer's default (`settings-store.ts`).
    assert config["highlight"]["appearance"] == {
        "motionColor": "#00FF00",
        "audioColor": "#3B82F6",
        "detectionColor": "#123",
        "thickness": 5,
    }


def test_no_border_keys_means_the_embed_draws_its_defaults() -> None:
    config = _grid_embed_config({"device_ids": [617], "highlight": True})
    assert "appearance" not in config["highlight"]


def test_an_unusable_colour_or_thickness_falls_back_to_the_default() -> None:
    config = _grid_embed_config(
        {
            "device_ids": [617],
            "highlight": True,
            "highlight_audio_color": "red; background:url(x)",
            "highlight_thickness": 40,
            "highlight_motion_color": "#ABCDEF",
        }
    )
    assert config["highlight"]["appearance"] == {
        "motionColor": "#ABCDEF",
        "audioColor": "#3B82F6",
        "detectionColor": "#22C55E",
        "thickness": 3,
    }


def test_the_bar_keeps_the_configured_colours() -> None:
    """A live trigger change from the bar must not drop the border colours."""
    highlight = run_card(
        "camstack-grid-card.js",
        f"""{_GRID_DIRECT}
        card._hass = hassWith({{}});
        card.setConfig({{ device_ids: [617], highlight: true,
                          highlight_motion_color: "#00FF00" }});
        card._barHost().patchHighlight({{ detection: true }});
        return card._highlightCommand();
        """,
    )
    assert highlight["detection"] is True
    assert highlight["appearance"]["motionColor"] == "#00FF00"


def test_the_grid_card_does_not_pass_camera_names_as_the_wall_s_words() -> None:
    """The grid embed has NO per-camera name channel.

    Tiles are named from the hub's `deviceManager.listAll`. Its `labels` is the
    page's translated UI words (`tile-labels.ts`), so a `{ "617": "Garage" }`
    there is ignored — and a card that sends it reads as if Home Assistant's
    names reached the wall.
    """
    states = {"camera.garage": camera_state(617, "Garage (HA)")}
    config = run_card(
        "camstack-grid-card.js",
        f"""{_GRID_DIRECT}
        card._hass = hassWith({json.dumps(states)});
        card.setConfig({{ entities: ["camera.garage"] }});
        sent.length = 0;
        await card._sendConfig();
        return sent.find((m) => m.type === "embed-config").config;
        """,
    )
    assert config["devices"] == [617]
    assert "labels" not in config


# --- the appearance defaults are the viewer's -------------------------------

EXPECTED_APPEARANCE_DEFAULTS = {
    "motionColor": "#DC2626",
    "audioColor": "#3B82F6",
    "detectionColor": "#22C55E",
    "thickness": "3",
}

VIEWER_APP_SRC = Path(
    os.environ.get(
        "CAMSTACK_VIEWER_APP_SRC",
        Path(__file__).parent.parent.parent / "camstack-server" / "camstack" / "src",
    )
)


def _appearance_block(source: str, anchor: str) -> dict[str, str]:
    start = source.index(anchor)
    body = source[start : source.index("})", start)]
    return {
        key: value.strip("'\"")
        for key, value in re.findall(r"^\s*(\w+):\s*([^,\n]+),", body, re.M)
    }


def test_the_appearance_defaults_mirror_this_file() -> None:
    source = (FRONTEND / "camstack-grid-controls.js").read_text(encoding="utf-8")
    found = _appearance_block(source, "HIGHLIGHT_APPEARANCE_DEFAULTS = Object.freeze({")
    assert found == EXPECTED_APPEARANCE_DEFAULTS


def test_the_appearance_defaults_mirror_the_viewer() -> None:
    store = VIEWER_APP_SRC / "store" / "settings-store.ts"
    if not store.is_file():
        pytest.skip(f"viewer checkout not found at {VIEWER_APP_SRC}")
    found = _appearance_block(
        store.read_text(encoding="utf-8"), "highlight: Object.freeze({"
    )
    assert found == EXPECTED_APPEARANCE_DEFAULTS, (
        "the viewer's default border appearance moved; update "
        "HIGHLIGHT_APPEARANCE_DEFAULTS in camstack-grid-controls.js and this test"
    )
