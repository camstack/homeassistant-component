"""The events card's tile settings and its track-detail dialog.

* The three tile settings travel under the VIEWER's param names
  (`tileLabelMode`, `tileDetailCamera`, `tileDetailZone`), from snake_case card
  keys, and only when the card sets them: an absent key is the viewer's default
  on both sides.
* A `trackClick` opens the detail only when it comes from THIS card's own
  iframe window AND from the origin the card framed. Both checks matter; the
  tests fail if either is removed.
* The detail frame is the same embed (same path, same relay or `#t=`), in
  `mode=track` with the three ids the click carried.

Run against the card's real code (see `card_js.py`).
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

from .card_js import run_card

RELAYED = "http://ha.test/api/camstack/p/grant/viewer/camstack/embed/index.html?mode=events&v=1"
DIRECT = "https://hub.test:4443/viewer/camstack/embed/index.html?mode=events&v=1#t=tok"


def _query(config: dict) -> str:
    return run_card(
        "camstack-events-card.js",
        f"""
        const card = new Card();
        card._config = {json.dumps(config)};
        card._hass = hassWith({{}});
        return card._query([7]);
        """,
    )


def test_tile_settings_are_sent_under_the_viewer_names() -> None:
    query = parse_qs(
        _query(
            {
                "tile_label_mode": "label",
                "tile_detail_camera": True,
                "tile_detail_zone": False,
            }
        )
    )
    assert query["tileLabelMode"] == ["label"]
    assert query["tileDetailCamera"] == ["1"]
    assert query["tileDetailZone"] == ["0"]


def test_unset_or_illegal_tile_settings_are_not_sent() -> None:
    query = parse_qs(_query({"tile_label_mode": "loud", "tile_detail_camera": "yes"}))
    assert "tileLabelMode" not in query
    assert "tileDetailCamera" not in query
    assert "tileDetailZone" not in query


def _click_outcome(
    *, source_is_frame: bool, origin: str, message: dict, url: str
) -> bool:
    """Return whether the card opened a detail for this message."""
    return run_card(
        "camstack-events-card.js",
        f"""
        const card = new Card();
        const frameWindow = {{}};
        card._iframe = {{ contentWindow: frameWindow }};
        card._renderedUrl = {json.dumps(url)};
        let opened = false;
        card._openDetail = () => {{ opened = true; }};
        card._handleMessage({{
          source: {"frameWindow" if source_is_frame else "{}"},
          origin: {json.dumps(origin)},
          data: {json.dumps(message)},
        }});
        return opened;
        """,
    )


CLICK = {
    "v": 1,
    "type": "trackClick",
    "trackId": "t-1",
    "deviceId": 7,
    "bestEventId": "e-1",
}


def test_a_click_from_our_frame_and_origin_opens_the_detail() -> None:
    assert _click_outcome(
        source_is_frame=True, origin="http://ha.test", message=CLICK, url=RELAYED
    )
    assert _click_outcome(
        source_is_frame=True, origin="https://hub.test:4443", message=CLICK, url=DIRECT
    )


def test_a_click_from_another_window_is_ignored() -> None:
    assert not _click_outcome(
        source_is_frame=False, origin="http://ha.test", message=CLICK, url=RELAYED
    )


def test_a_click_from_another_origin_is_ignored() -> None:
    assert not _click_outcome(
        source_is_frame=True, origin="https://evil.test", message=CLICK, url=RELAYED
    )
    # The hub's origin is not Home Assistant's when the card is relayed.
    assert not _click_outcome(
        source_is_frame=True, origin="https://hub.test:4443", message=CLICK, url=RELAYED
    )


def test_only_a_well_formed_trackclick_is_acted_on() -> None:
    for bad in (
        {**CLICK, "type": "ready"},
        {**CLICK, "v": 2},
        {**CLICK, "trackId": ""},
        {**CLICK, "deviceId": "7"},
        "trackClick",
    ):
        assert not _click_outcome(
            source_is_frame=True, origin="http://ha.test", message=bad, url=RELAYED
        )


def _detail_url(url: str, theme: str = "dark") -> str:
    return run_card(
        "camstack-events-card.js",
        f"""
        const card = new Card();
        card._config = {{ theme: {json.dumps(theme)} }};
        card._hass = hassWith({{}});
        card._renderedUrl = {json.dumps(url)};
        return card._detailUrl({json.dumps(CLICK)});
        """,
    )


def test_the_detail_frame_is_the_same_embed_in_track_mode() -> None:
    parts = urlsplit(_detail_url(RELAYED))
    assert parts.path == "/api/camstack/p/grant/viewer/camstack/embed/index.html"
    assert parts.fragment == ""
    query = parse_qs(parts.query)
    assert query["mode"] == ["track"]
    assert query["v"] == ["1"]
    assert query["id"] == ["t-1"]
    assert query["device"] == ["7"]
    assert query["best"] == ["e-1"]
    assert query["theme"] == ["dark"]


def test_a_direct_card_keeps_its_fragment_credential_in_the_detail_frame() -> None:
    parts = urlsplit(_detail_url(DIRECT))
    assert parts.netloc == "hub.test:4443"
    assert parts.fragment == "t=tok"
