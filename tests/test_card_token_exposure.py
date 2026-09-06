"""The share token must not reach the browser on the relayed path.

This is a GREP, not a behaviour test, and deliberately so: the two cards are
plain ES modules loaded by Lovelace, there is no harness here that runs them,
and the failure this guards against is a one-word regression — someone puts
`token` back in the config object, or `#t=<token>` back in the iframe URL,
because it "was there before and everything worked". It did work. It also
handed a bearer credential to the address bar, to browser history, and to every
screenshot of the dashboard, where anyone could copy it and use it against the
hub from outside Home Assistant for the rest of its hour.

On the relayed path that copy authenticates nothing: `proxy.py` strips the
browser's `Authorization` and injects the share token server-side on every
forwarded request. So the rule the cards must keep is simple — a token may
leave this page only under an explicit `_isDirect()` branch, i.e. only when the
card was configured with `url_base` and frames the hub with no relay in front
of it.

The guard names the two ways a token can LEAVE a card — the iframe's URL
fragment, and the config object posted into the embed — rather than every line
that happens to spell the word.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FRONTEND = Path(__file__).parent.parent / "custom_components" / "camstack" / "frontend"

CARDS = ("camstack-grid-card.js", "camstack-events-card.js")


def _code_lines(source: str) -> list[tuple[int, str]]:
    """Every line that is code, not comment — a doc block may say "#t=" freely."""
    return [
        (number, line)
        for number, line in enumerate(source.splitlines(), start=1)
        if not line.strip().startswith(("*", "//", "/*"))
    ]


def _embed_config_object(source: str) -> str:
    """Return the object literal a card posts into the embed, braces matched.

    Named by its assignment (`const config = {`) rather than by a line offset,
    so the guard survives any edit that is not about what the config carries.
    """
    marker = "const config = {"
    start = source.find(marker)
    assert start != -1, "the card no longer builds an embed config object"
    depth = 0
    for index in range(start + len(marker) - 1, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError("unbalanced braces in the embed config object")


@pytest.mark.parametrize("card", CARDS)
def test_a_url_fragment_carries_a_token_only_on_the_direct_path(card: str) -> None:
    """`#t=` is the events card's old leak. It may exist only under a gate."""
    source = (FRONTEND / card).read_text(encoding="utf-8")
    for number, line in _code_lines(source):
        if "#t=" not in line:
            continue
        assert "_isDirect()" in line, (
            f"{card}:{number} puts a share token in the frame URL without an "
            f"`_isDirect()` gate — on the relayed path Home Assistant already "
            f"injects it server-side:\n  {line.strip()}"
        )


def test_the_embed_config_carries_a_token_only_on_the_direct_path() -> None:
    """The grid card's leak was the config object, not the URL."""
    config = _embed_config_object(
        (FRONTEND / "camstack-grid-card.js").read_text(encoding="utf-8")
    )
    for line in config.splitlines():
        if "token" not in line or line.strip().startswith(("*", "//")):
            continue
        assert "grant.token ?" in line or "_isDirect()" in line, (
            "the grid card posts a share token into the embed unconditionally; "
            "on the relayed path the browser must hold no credential:\n  "
            + line.strip()
        )


@pytest.mark.parametrize("card", CARDS)
def test_a_card_asks_for_the_token_only_on_the_direct_path(card: str) -> None:
    """`direct: true` is what makes the mint answer carry a token at all."""
    source = (FRONTEND / card).read_text(encoding="utf-8")
    asks = [line for _, line in _code_lines(source) if "direct: true" in line]
    assert asks, f"{card} never asks for a token — the direct path is broken"
    for line in asks:
        assert "_isDirect()" in line, (
            f"{card} asks the mint endpoint for a token without checking "
            f"`_isDirect()`: {line.strip()}"
        )


def test_the_grid_card_hands_the_embed_a_same_origin_path_when_relayed() -> None:
    """The RELATIVE `serverUrl` is what tells the embed the transport is authed.

    Written absolute it reads as a direct hub address, and the embed refuses a
    config with no token (`transport-auth.ts` in the viewer).
    """
    config = _embed_config_object(
        (FRONTEND / "camstack-grid-card.js").read_text(encoding="utf-8")
    )
    assert "serverUrl," in config, (
        "the config must carry the computed `_serverUrl()` — a relayed card "
        "sends the same-origin path, not the absolute frame base"
    )
    source = (FRONTEND / "camstack-grid-card.js").read_text(encoding="utf-8")
    assert "return this._isRelayed() ? this._proxyBase : this._baseUrl();" in source


def test_the_events_card_still_has_a_direct_path() -> None:
    """A guard that passes because the feature was deleted is not a guard."""
    source = (FRONTEND / "camstack-events-card.js").read_text(encoding="utf-8")
    fragments = [line for _, line in _code_lines(source) if "#t=" in line]
    assert fragments, (
        "the events card no longer builds a `#t=` fragment at all — a card with "
        "an explicit `url_base` frames the hub directly and still needs it"
    )
