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
