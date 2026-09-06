"""The card files register themselves the way the Lovelace picker expects.

`window.customCards[].type` is the BARE tag. The picker prepends `custom:`
itself, so an entry registered as `custom:camstack-grid-card` is looked up as
`custom:custom:camstack-grid-card`, stripped once to `custom:camstack-grid-card`,
and rejected with "Custom element not found" two seconds later — for a module
that loaded and defined its element perfectly well.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from custom_components.camstack.const import CARD_FILENAMES

ASSET_DIR = Path(__file__).parent.parent / "custom_components" / "camstack" / "frontend"

# `type: CARD_TAG,` or `type: "literal",` — whichever a card uses, captured.
CUSTOM_CARDS_TYPE = re.compile(
    r"customCards\.push\(\{\s*type:\s*(?P<expr>[^,]+),", re.MULTILINE
)
CARD_TAG_CONST = re.compile(r'const CARD_TAG = "(?P<tag>[a-z0-9-]+)";')
# The probe is imported with the `?v=` query the card itself was loaded with,
# so a release never pairs a new card with a cached old probe.
PROBE_IMPORT = re.compile(
    r"const VERSION_QUERY = new URL\(import\.meta\.url\)\.search;\s*"
    r"const \{ probeHub, buildUnreachableNotice \} = await import\(\s*"
    r"`\./camstack-hub-probe\.js\$\{VERSION_QUERY\}`\s*\);"
)


@pytest.mark.parametrize("filename", CARD_FILENAMES)
def test_custom_cards_entry_uses_the_bare_tag(filename: str) -> None:
    """The registered type equals the defined tag, with no `custom:` prefix."""
    source = (ASSET_DIR / filename).read_text(encoding="utf-8")
    tag = CARD_TAG_CONST.search(source)
    entry = CUSTOM_CARDS_TYPE.search(source)
    assert tag is not None, f"{filename}: no CARD_TAG constant"
    assert entry is not None, f"{filename}: no window.customCards.push({{ type }})"
    expr = entry.group("expr").strip()
    assert expr in ("CARD_TAG", f'"{tag.group("tag")}"'), (
        f"{filename}: customCards type is {expr}; the picker adds `custom:` itself"
    )


@pytest.mark.parametrize("filename", CARD_FILENAMES)
def test_every_card_probes_the_hub_through_the_versioned_sibling(
    filename: str,
) -> None:
    """A refused frame is explained, not left white, by this release's own probe."""
    source = (ASSET_DIR / filename).read_text(encoding="utf-8")
    assert PROBE_IMPORT.search(source), f"{filename}: no versioned probe import"
    assert "this._watchFrame(" in source, f"{filename}: the frame is never watched"


def test_the_probe_module_exports_what_the_cards_import() -> None:
    source = (ASSET_DIR / "camstack-hub-probe.js").read_text(encoding="utf-8")
    assert "export async function probeHub(" in source
    assert "export function buildUnreachableNotice(" in source


@pytest.mark.parametrize("filename", CARD_FILENAMES)
def test_every_card_frames_the_relay_and_probes_only_a_direct_hub(
    filename: str,
) -> None:
    """The frame goes to the mint answer's `proxy_base`.

    The certificate probe is for the operator who set `url_base` and framed
    the hub directly.
    """
    source = (ASSET_DIR / filename).read_text(encoding="utf-8")
    assert "result.proxy_base" in source, f"{filename}: ignores proxy_base"
    assert "${window.location.origin}${this._proxyBase}" in source
    # The certificate probe belongs to a DIRECT frame only: a relayed one is
    # same-origin, and its answer is read back instead (`probeRelayedFrame`).
    assert "probeHub(origin)" in source, f"{filename}: no certificate probe"
    assert "probeRelayedFrame(" in source, (
        f"{filename}: a relayed frame is not read back"
    )
    assert "_isRelayed()" in source, f"{filename}: the two probes are not told apart"
    assert "this._frameBase()" in source


@pytest.mark.parametrize("filename", CARD_FILENAMES)
def test_a_relayed_frame_is_read_back_re_minted_and_retried(filename: str) -> None:
    """The relay is same-origin, so its failure is legible — and must be acted on.

    A hub that is restarting answered the card with the relay's own JSON,
    painted as raw text inside the dashboard (operator screenshot). And a Home
    Assistant restart forgets every grant while the open page keeps the token
    it minted, so the card would stay dead until someone reloaded the browser.
    """
    source = (ASSET_DIR / filename).read_text(encoding="utf-8")
    assert "async function probeRelayedFrame(" in source
    assert '"stale-grant"' in source, f"{filename}: a forgotten grant is not recognised"
    assert "_forgetGrant()" in source, (
        f"{filename}: a forgotten grant is never re-minted"
    )
    assert "RELAY_RETRY_MS" in source, (
        f"{filename}: an unreachable hub is never retried"
    )
    assert "_clearRetryTimer()" in source, (
        f"{filename}: the retry timer outlives the card"
    )


def test_the_panel_frames_the_relay_when_its_config_names_one() -> None:
    source = (ASSET_DIR / "camstack-panel.js").read_text(encoding="utf-8")
    assert "config.proxy_base" in source
    assert "_renderRelayedFrame(`${window.location.origin}${proxyBase}/`)" in source


@pytest.mark.parametrize("filename", CARD_FILENAMES)
def test_a_card_keeps_asking_which_hub_instead_of_declaring_none(filename: str) -> None:
    """A Home Assistant restart must not park a card on "no hub configured".

    The address lookup ran ONCE, on the first `hass`. During a restart the
    entry is not loaded yet, the call throws, and the card said the hub was
    absent — for the rest of the page's life (operator screenshot,
    2026-09-06). Absence may only be claimed by an answer that named no
    entry; a failure is a wait, retried on the relay's ladder.
    """
    source = (ASSET_DIR / filename).read_text(encoding="utf-8")
    assert "this._baseState = 'pending'" in source, f"{filename}: no tri-state"
    assert "this._baseState = entry ? 'known' : 'absent'" in source, (
        f"{filename}: absence is not read off an answer"
    )
    assert "_scheduleBaseRetry()" in source, (
        f"{filename}: a failed lookup is never retried"
    )
    assert "this._baseState !== 'absent'" in source, (
        f"{filename}: the empty state still races the in-flight flag"
    )
    assert "_clearBaseTimer()" in source, (
        f"{filename}: the retry timer outlives the card"
    )
