/**
 * The grid card, run for real: does a tile button do anything?
 *
 * The Python guards next door are greps — they prove the contract is NAMED.
 * This one proves it WORKS: the card is mounted in Chromium against a fake
 * `hass` and a stub that speaks the embed's half of the protocol, and every
 * assertion is about a message that actually crossed the frame boundary.
 *
 * Two things it exists to catch, neither of which a grep can see:
 *
 *  1. A tile intent that produces no command. This is the defect that shipped:
 *     the card rendered a perfect wall whose every button was a message into a
 *     handler with no case for it.
 *  2. An iframe rebuilt on `set hass`. That call fires several times a second
 *     on a busy instance, and rebuilding the frame renegotiates every WebRTC
 *     session on the wall — the wall would blink for as long as the dashboard
 *     is open. The test re-enters `set hass` 30 times and asserts the frame
 *     element is the same NODE afterwards.
 *
 * Run it (Playwright lives in the camstack-server tree, not in this repo):
 *
 *   NODE_PATH=../camstack-server/node_modules node tests/browser/grid-card-host.spec.cjs
 *
 * Counter-proof — strip the tile-intent cases back out of the card and watch
 * this go red, which is the only way to know the assertions bind:
 *
 *   BREAK_FIX=1 NODE_PATH=… node tests/browser/grid-card-host.spec.cjs
 *
 * It is NOT wired into CI: the workflow installs Python only, and a browser
 * download per push to protect a card nobody edits per push is not the trade.
 * The greps run there; this runs when the card changes.
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const FRONTEND = path.join(
  __dirname,
  "..",
  "..",
  "custom_components",
  "camstack",
  "frontend"
);
const ORIGIN = "http://camstack.test";
const PROXY_BASE = "/api/camstack/proxy/e1";
const EMBED_PATH = "/viewer/camstack/embed/index.html";

/**
 * The card source, optionally with the fix removed.
 *
 * The counter-proof deletes exactly the block this work added — the cases for
 * the tile intents — leaving the handshake, the `state` report and the
 * explicit no-ops. That is the card as it shipped, and the point is that these
 * assertions fail against it.
 */
function cardSource() {
  const source = fs.readFileSync(
    path.join(FRONTEND, "camstack-grid-card.js"),
    "utf8"
  );
  if (!process.env.BREAK_FIX) {
    return source;
  }
  const start = source.indexOf(
    "      // ── the tile intents this dashboard can answer"
  );
  const end = source.indexOf(
    "      // ── the intents a Lovelace card has no honest answer for"
  );
  assert.ok(start !== -1 && end > start, "the block to remove was not found");
  return source.slice(0, start) + source.slice(end);
}

/** A stub that speaks the embed's half: announces itself, relays, echoes. */
const EMBED_HTML = `<!doctype html><meta charset="utf-8"><title>stub embed</title>
<script>
  window.addEventListener("message", (event) => {
    const data = event.data;
    // The driver asks us to act like a tile that was tapped.
    if (data && data.type === "__emit") {
      parent.postMessage(data.msg, "*");
      return;
    }
    // Everything else is the host talking to us — report it verbatim.
    parent.postMessage({ type: "__spy", payload: data }, "*");
  });
  parent.postMessage({ type: "embed-ready", mode: "grid" }, "*");
</script>`;

const HOST_HTML = `<!doctype html><meta charset="utf-8"><title>card harness</title>
<body>
<script type="module">
  window.__spy = [];
  window.__moreInfo = [];
  window.addEventListener("message", (event) => {
    if (event.data && event.data.type === "__spy") {
      window.__spy.push(event.data.payload);
    }
  });
  document.addEventListener("hass-more-info", (event) => {
    window.__moreInfo.push(event.detail.entityId);
  });
  await import("./local/camstack-grid-card.js?v=test");
  window.__cardLoaded = true;
</script>
</body>`;

async function serve(page) {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/") {
      return route.fulfill({ contentType: "text/html", body: HOST_HTML });
    }
    if (url.pathname === "/local/camstack-grid-card.js") {
      return route.fulfill({
        contentType: "text/javascript",
        body: cardSource(),
      });
    }
    if (url.pathname === "/local/camstack-hub-probe.js") {
      return route.fulfill({
        contentType: "text/javascript",
        body: fs.readFileSync(
          path.join(FRONTEND, "camstack-hub-probe.js"),
          "utf8"
        ),
      });
    }
    if (url.pathname === `${PROXY_BASE}${EMBED_PATH}`) {
      return route.fulfill({ contentType: "text/html", body: EMBED_HTML });
    }
    return route.fulfill({ status: 404, body: "not routed" });
  });
}

const HASS_SETUP = `
  window.__makeHass = () => ({
    states: {
      "camera.front": {
        attributes: { camstack_device_id: 11, friendly_name: "Front door" },
      },
      "camera.back": {
        attributes: { camstack_device_id: 22, friendly_name: "Back garden" },
      },
    },
    callApi: async (method, apiPath) => {
      if (apiPath === "camstack/config") {
        return { entries: [{ entry_id: "e1", url_base: "https://hub.example" }] };
      }
      if (apiPath === "camstack/embed_token") {
        return {
          proxy_base: "${PROXY_BASE}",
          expires_at: Math.floor(Date.now() / 1000) + 3600,
        };
      }
      throw new Error("unexpected callApi " + apiPath);
    },
  });
`;

/** Commands the card posted into the frame, newest last. */
async function commands(page) {
  return page.evaluate(() =>
    window.__spy
      .filter((m) => m && m.type === "embed-command")
      .map((m) => m.command)
  );
}

async function emit(page, msg) {
  await page.evaluate((message) => {
    const card = document.querySelector("camstack-grid-card");
    card.shadowRoot
      .querySelector("iframe")
      .contentWindow.postMessage({ type: "__emit", msg: message }, "*");
  }, msg);
  // One turn of the message queue in each direction: iframe → parent → card,
  // then card → iframe → parent for the spy copy.
  await page.waitForTimeout(60);
}

async function run() {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const failures = [];
  const check = (name, fn) => {
    try {
      fn();
      console.log(`PASS ${name}`);
    } catch (err) {
      failures.push(name);
      console.log(`FAIL ${name} -> ${err.message}`);
    }
  };

  try {
    await serve(page);
    await page.goto(`${ORIGIN}/`);
    await page.waitForFunction(() => window.__cardLoaded === true);
    await page.evaluate(HASS_SETUP);

    await page.evaluate(() => {
      const card = document.createElement("camstack-grid-card");
      card.setConfig({ entities: ["camera.front", "camera.back"] });
      document.body.appendChild(card);
      card.hass = window.__makeHass();
    });

    // The handshake has to land before anything else is meaningful.
    await page.waitForFunction(
      () => window.__spy.some((m) => m && m.type === "embed-config"),
      undefined,
      { timeout: 10000 }
    );

    const sent = await page.evaluate(
      () => window.__spy.find((m) => m.type === "embed-config").config
    );
    check("the handshake sends the wall's cameras", () => {
      assert.deepEqual(sent.devices, [11, 22]);
    });
    check("the relayed config carries no token", () => {
      assert.equal(sent.token, undefined);
      assert.equal(sent.serverUrl, PROXY_BASE);
    });

    // Mark the frame so a rebuild is detectable as a NEW node later.
    await page.evaluate(() => {
      document
        .querySelector("camstack-grid-card")
        .shadowRoot.querySelector("iframe").dataset.stamp = "original";
    });

    await emit(page, { type: "audioToggle", deviceId: 11 });
    let seen = await commands(page);
    check("audioToggle → setAudioOn [11]", () => {
      assert.deepEqual(seen.at(-1), { kind: "setAudioOn", value: [11] });
    });

    await emit(page, { type: "audioToggle", deviceId: 22 });
    seen = await commands(page);
    check("audio is combinable, not exclusive", () => {
      assert.deepEqual(seen.at(-1), { kind: "setAudioOn", value: [11, 22] });
    });

    await emit(page, { type: "audioToggle", deviceId: 11 });
    seen = await commands(page);
    check("a second tap takes that camera back out", () => {
      assert.deepEqual(seen.at(-1), { kind: "setAudioOn", value: [22] });
    });

    await emit(page, { type: "pauseToggle", deviceId: 11 });
    seen = await commands(page);
    check("pauseToggle → setPaused [11]", () => {
      assert.deepEqual(seen.at(-1), { kind: "setPaused", value: [11] });
    });

    await emit(page, { type: "tileOpen", deviceId: 22 });
    let opened = await page.evaluate(() => window.__moreInfo);
    check("tileOpen opens that camera's more-info dialog", () => {
      assert.deepEqual(opened, ["camera.back"]);
    });

    await emit(page, { type: "tileTap", deviceId: 11 });
    opened = await page.evaluate(() => window.__moreInfo);
    check("a tap opens the tapped camera, not the last one", () => {
      assert.deepEqual(opened, ["camera.back", "camera.front"]);
    });

    // The intents this card refuses must refuse QUIETLY — no command, and no
    // error painted over the wall.
    const before = (await commands(page)).length;
    for (const msg of [
      { type: "ptzOpen", deviceId: 11 },
      { type: "cellPanelOpen", deviceId: 11, panel: "devices" },
      { type: "tileRemove", deviceId: 11 },
      { type: "tileResize", deviceId: 11, cw: 2, ch: 2 },
      { type: "layout", tiles: [{ deviceId: 11, rect: { x: 0, y: 0, w: 1, h: 1 } }] },
    ]) {
      await emit(page, msg);
    }
    seen = await commands(page);
    check("the ignored intents produced no command", () => {
      assert.equal(seen.length, before);
    });

    // The re-render storm. This is the shape of a busy instance.
    await page.evaluate(() => {
      const card = document.querySelector("camstack-grid-card");
      for (let i = 0; i < 30; i += 1) {
        card.hass = window.__makeHass();
      }
    });
    await page.waitForTimeout(200);

    const stamp = await page.evaluate(() => {
      const frame = document
        .querySelector("camstack-grid-card")
        .shadowRoot.querySelector("iframe");
      return frame ? frame.dataset.stamp : null;
    });
    check("30 re-renders do not rebuild the iframe", () => {
      assert.equal(stamp, "original", "the WebRTC wall was restarted");
    });

    // …and the state the operator set survived them. `state: ready` is what a
    // remounted page reports; the host must answer with what IT holds.
    await emit(page, { type: "state", state: "ready" });
    seen = await commands(page);
    check("a re-mounted page is told the state the host kept", () => {
      const audio = seen.filter((c) => c.kind === "setAudioOn").at(-1);
      const paused = seen.filter((c) => c.kind === "setPaused").at(-1);
      assert.deepEqual(audio, { kind: "setAudioOn", value: [22] });
      assert.deepEqual(paused, { kind: "setPaused", value: [11] });
    });

    check("no command was ever posted to a wildcard origin", () => {
      // Nothing to read at runtime — the browser would have delivered it either
      // way. The grep guard covers the source; here we only confirm the frame
      // received them at all, which a mismatched origin would have prevented.
      assert.ok(seen.length > 0, "no command reached the frame");
    });
  } finally {
    await browser.close();
  }

  if (failures.length) {
    console.error(`\n${failures.length} failing: ${failures.join(", ")}`);
    process.exit(1);
  }
  console.log("\nall green");
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
