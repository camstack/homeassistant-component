"""Run a Lovelace card's REAL JavaScript in Node, without a browser.

The guards in `test_card_*.py` are mostly greps: they prove a contract is
NAMED. The share-parameter tests need more than that — "the card turns
`camera_names` into `names=617:Garage%2C%20retro`" is a statement about the
code's OUTPUT, and only running it can check it. Playwright is too heavy for
CI (`tests/browser/` says why), but the cards only touch the DOM when they
render, so a handful of stubs is enough to construct one and call the methods
that compose what reaches the embed.

The stubs are deliberately inert: nothing here supplies a value the card is
supposed to compute. A test passes `hass` states and the card config, and reads
back the URL or the `embed-config` message the card produced.

Skipped when `node` is not on PATH. GitHub's `ubuntu-latest` image ships it, so
CI runs these.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

FRONTEND = Path(__file__).parent.parent / "custom_components" / "camstack" / "frontend"

# Installed before the card module is imported: it registers custom elements
# and touches `window` at load time.
_PRELUDE = """
const registry = {};
globalThis.customElements = {
  get: (name) => registry[name],
  define: (name, ctor) => { registry[name] = ctor; },
};
class StubElement {
  attachShadow() { this.shadowRoot = { innerHTML: "", querySelector: () => null,
    querySelectorAll: () => [], appendChild: () => {} }; return this.shadowRoot; }
  dispatchEvent() { return true; }
  getBoundingClientRect() { return { width: 0, height: 0 }; }
}
globalThis.HTMLElement = StubElement;
globalThis.window = globalThis;
globalThis.location = { origin: "http://ha.test" };
globalThis.innerWidth = 1280;
globalThis.innerHeight = 800;
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
"""


def run_card(card_file: str, body: str) -> Any:
    """Import `card_file`, run `body` (an async function body), return its JSON.

    `body` sees `Card` (the card element class) and `hassWith(states)`, and must
    `return` a JSON-serialisable value.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module_url = (FRONTEND / card_file).resolve().as_uri()
    tag = json.dumps(card_file.removesuffix(".js"))
    script = f"""{_PRELUDE}
await import({json.dumps(module_url)});
const Card = registry[{tag}];
const hassWith = (states) => ({{ states, callApi: async () => ({{}}) }});
const result = await (async () => {{ {body} }})();
process.stdout.write(JSON.stringify(result));
"""
    done = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def camera_state(device_id: int, friendly_name: str) -> dict[str, Any]:
    """Build a `camera.*` state as the integration publishes it."""
    return {
        "attributes": {
            "camstack_device_id": device_id,
            "friendly_name": friendly_name,
        }
    }
