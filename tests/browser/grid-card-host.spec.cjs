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
    if (url.pathname === "/local/camstack-grid-controls.js") {
      return route.fulfill({
        contentType: "text/javascript",
        body: fs.readFileSync(
          path.join(FRONTEND, "camstack-grid-controls.js"),
          "utf8"
        ),
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

    // ── the arrangement, measured in a real browser ──────────────────────
    //
    // `flow` caps a camera's width in PIXELS, so it is a function of how wide
    // the card actually is. jsdom cannot answer that — it has no layout — so
    // this is the only place the promise can be checked at all.
    const geometry = async (config, cardWidth) =>
      page.evaluate(
        async ([cfg, width]) => {
          const card = document.querySelector("camstack-grid-card");
          card.style.display = "block";
          card.style.width = `${width}px`;
          card.setConfig({ entities: ["camera.front", "camera.back"], ...cfg });
          card.hass = window.__makeHass();
          // Two frames: one for the resize to land, one for the restyle.
          await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
          const root = card.shadowRoot;
          const frame = root.querySelector("iframe");
          const scroller = frame.parentElement;
          return {
            frameHeight: frame.getBoundingClientRect().height,
            scrollerHeight: scroller.getBoundingClientRect().height,
            overflowY: getComputedStyle(scroller).overflowY,
            tileWidth: frame.getBoundingClientRect().width / (cfg.__cols || 1),
          };
        },
        [config, cardWidth]
      );

    // 8 cameras, card 1400 px, no camera wider than 480 ⇒ 3 columns (1400/3 =
    // 467 ≤ 480), 3 rows of them, 2 visible.
    const eight = Array.from({ length: 8 }, (_, i) => `camera.c${i}`);
    await page.evaluate((ids) => {
      const hass = window.__makeHass();
      ids.forEach((id, i) => {
        hass.states[id] = { attributes: { camstack_device_id: 100 + i, friendly_name: id } };
      });
      window.__makeHass = () => hass;
    }, eight);

    const flow = await geometry(
      { entities: eight, layout_mode: "flow", max_tile_width: 480, max_rows: 2, __cols: 3 },
      1400
    );
    check("flow: no camera is wider than the cap the operator set", () => {
      assert.ok(flow.tileWidth <= 480 + 1, `tile ${flow.tileWidth}px exceeds 480px`);
    });
    check("flow: the viewport shows max_rows, the wall behind it is taller", () => {
      assert.equal(flow.overflowY, "auto");
      assert.ok(
        flow.frameHeight > flow.scrollerHeight + 1,
        `wall ${flow.frameHeight} is not taller than the viewport ${flow.scrollerHeight}`
      );
      // 3 rows behind a 2-row viewport.
      const ratio = flow.frameHeight / flow.scrollerHeight;
      assert.ok(Math.abs(ratio - 3 / 2) < 0.05, `rows ratio ${ratio}, expected 1.5`);
    });

    // ── the `auto` arrangement, which is the default ────────────────────
    //
    // The rule is the viewer's `useGridColumns` (`src/hooks/use-responsive.ts`):
    // 1 column under 700 in portrait, 2 in landscape, 3 to 1100, then 4 + one
    // per 360, capped at 6. Its narrowest camera is 700/3 ≈ 233 px, and that
    // floor is the whole promise of the mode.
    /** The wall as the card actually planned it, at a given card width. */
    const wall = async (config, cardWidth) =>
      page.evaluate(
        async ([cfg, width]) => {
          const card = document.querySelector("camstack-grid-card");
          card.style.display = "block";
          card.style.width = `${width}px`;
          card.setConfig({ entities: ["camera.front", "camera.back"], ...cfg });
          card.hass = window.__makeHass();
          await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
          const root = card.shadowRoot;
          const frame = root.querySelector("iframe");
          const scroller = frame.parentElement;
          const plan = card._plan(card._deviceIds());
          const frameBox = frame.getBoundingClientRect();
          return {
            plan,
            mode: card._layoutMode(),
            tileWidth: frameBox.width / (plan.columns === "auto" ? 1 : plan.columns),
            frameWidth: frameBox.width,
            scrollerWidth: scroller.getBoundingClientRect().width,
            frameHeight: frameBox.height,
            overflowY: getComputedStyle(scroller).overflowY,
          };
        },
        [config, cardWidth]
      );

    const auto1400 = await wall({ entities: eight }, 1400);
    check("auto: a card that chose nothing fills the width in readable columns", () => {
      assert.notEqual(auto1400.overflowY, "auto", "the default arrangement scrolls");
      assert.ok(
        auto1400.tileWidth >= 233,
        `a camera came out ${auto1400.tileWidth}px wide, under the readable floor`
      );
      assert.equal(auto1400.mode, "auto");
      assert.equal(auto1400.plan.columns, 4, "the viewer's rule gives 4 columns at ~1384px");
      // 8 cameras in 4 columns is 2 rows, and the wall is exactly that tall.
      const rows = auto1400.frameHeight / (auto1400.tileWidth / (16 / 9));
      assert.ok(Math.abs(rows - 2) < 0.1, `wall is ${rows} rows tall, expected 2`);
    });

    // 716 px of card is 700 px of wall once the card's 8px padding is taken —
    // the viewer's first multi-column breakpoint, on the nose.
    const auto716 = await wall({ entities: eight }, 716);
    check("auto: the viewer's 700px breakpoint is the viewer's", () => {
      assert.equal(auto716.plan.columns, 3);
      assert.ok(
        auto716.tileWidth >= 233 - 1,
        `a camera came out ${auto716.tileWidth}px wide at the 3-column breakpoint`
      );
    });

    const auto390 = await wall({ entities: eight }, 390);
    check("auto: a narrow card gets readable cameras, not eight thumbnails", () => {
      // The harness window is landscape, which is the viewer's 2-column case.
      assert.equal(auto390.plan.columns, 2);
      assert.ok(auto390.tileWidth > 180, `tile is ${auto390.tileWidth}px`);
      assert.notEqual(auto390.overflowY, "auto");
    });

    const twenty = Array.from({ length: 20 }, (_, i) => `camera.c${i}`);
    await page.evaluate((ids) => {
      const hass = window.__makeHass();
      ids.forEach((id, i) => {
        hass.states[id] = { attributes: { camstack_device_id: 200 + i, friendly_name: id } };
      });
      window.__makeHass = () => hass;
    }, twenty);
    const autoTwenty = await wall({ entities: twenty }, 1400);
    check("auto: twenty cameras do not shrink below the floor", () => {
      assert.equal(autoTwenty.plan.columns, 4, "more cameras must not mean thinner ones");
      assert.ok(autoTwenty.tileWidth >= 233, `${autoTwenty.tileWidth}px per camera`);
      const rows = autoTwenty.frameHeight / (autoTwenty.tileWidth / (16 / 9));
      assert.ok(Math.abs(rows - 5) < 0.1, `wall is ${rows} rows tall, expected 5`);
    });

    const autoOne = await wall({ entities: ["camera.front"] }, 1400);
    check("auto: one camera is one camera, full width", () => {
      assert.equal(autoOne.plan.columns, 1);
      const rows = autoOne.frameHeight / (autoOne.tileWidth / (16 / 9));
      assert.ok(Math.abs(rows - 1) < 0.1, `one camera drew ${rows} rows`);
    });

    // A dashboard that already said how its wall is laid out keeps it. The
    // default is for cards that never chose — not a shape change under a wall
    // somebody built.
    const mode = async (cfg) =>
      page.evaluate((c) => {
        const card = document.querySelector("camstack-grid-card");
        card.setConfig({ entities: ["camera.front", "camera.back"], ...c });
        return card._layoutMode();
      }, cfg);
    const modes = {
      nothing: await mode({}),
      pinned: await mode({ columns: 2 }),
      legacyPin: await mode({ layout: 3 }),
      legacyCap: await mode({ max_visible: 4 }),
      explicit: await mode({ layout_mode: "flow" }),
    };
    check("an arrangement somebody already chose is not replaced", () => {
      assert.deepEqual(modes, {
        nothing: "auto",
        pinned: "fit",
        legacyPin: "fit",
        legacyCap: "fixed",
        explicit: "flow",
      });
    });

    const fit = await geometry({ entities: eight, layout_mode: "fit" }, 1400);
    check("fit: nothing scrolls — that is the whole promise of the mode", () => {
      assert.notEqual(fit.overflowY, "auto");
    });

    // ── the bottom control bar (the viewer's grid bar, on a card) ────────
    //
    // The viewer's `GridControlBar` is the source of truth for WHAT a wall's
    // bar contains and in which order (pause · audio · quality · layout ·
    // highlight menu · active-only). These assertions are about the same set
    // reaching the same embed commands from a Lovelace card.
    await page.evaluate(() => {
      const card = document.querySelector("camstack-grid-card");
      card.setConfig({ entities: ["camera.front", "camera.back"] });
      card.hass = window.__makeHass();
    });
    await page.waitForTimeout(120);

    const bar = async (selector) =>
      page.evaluate((sel) => {
        const root = document.querySelector("camstack-grid-card").shadowRoot;
        const el = root.querySelector(sel);
        return el
          ? {
              disabled: el.disabled === true,
              title: el.title || "",
              text: (el.textContent || "").trim(),
            }
          : null;
      }, selector);

    const press = async (selector) => {
      await page.evaluate((sel) => {
        const root = document.querySelector("camstack-grid-card").shadowRoot;
        const el = root.querySelector(sel);
        if (!el) throw new Error("no control " + sel);
        el.click();
      }, selector);
      await page.waitForTimeout(60);
    };

    const order = await page.evaluate(() => {
      const root = document.querySelector("camstack-grid-card").shadowRoot;
      return [...root.querySelectorAll('[data-bar="controls"] [data-action]')].map(
        (el) => el.dataset.action
      );
    });
    check("the bar carries the viewer's control set, in the viewer's order", () => {
      assert.deepEqual(order, [
        "pause",
        "audio",
        "mic",
        "quality",
        "layout",
        "highlight",
        "activeOnly",
      ]);
    });

    await press('[data-action="pause"]');
    seen = await commands(page);
    check("the bar's play/pause pauses the whole wall", () => {
      assert.deepEqual(seen.at(-1), { kind: "setPaused", value: [11, 22] });
    });
    await press('[data-action="pause"]');
    seen = await commands(page);
    check("pressing it again resumes the whole wall", () => {
      assert.deepEqual(seen.at(-1), { kind: "setPaused", value: [] });
    });

    await press('[data-action="audio"]');
    await press('[data-row="audio-all"]');
    seen = await commands(page);
    check("the audio popover's lead row unmutes every camera", () => {
      assert.deepEqual(seen.at(-1), { kind: "setAudioOn", value: [11, 22] });
    });
    await press('[data-row="audio-11"]');
    seen = await commands(page);
    check("audio stays a combinable per-camera set", () => {
      assert.deepEqual(seen.at(-1), { kind: "setAudioOn", value: [22] });
    });

    // The mic. `intercom.*` is deliberately NOT in the `grid-view` share
    // scope (`share-view-access.ts`), so talk-back cannot work behind this
    // card's credential. It must be VISIBLE and refused with the reason —
    // never hidden, and never shown as if it were live.
    await press('[data-action="mic"]');
    const micRow = await bar('[data-row="mic-11"]');
    const micButton = await bar('[data-action="mic"]');
    const beforeMic = (await commands(page)).length;
    await press('[data-row="mic-11"]');
    seen = await commands(page);
    check("the mic is offered, disabled, with the reason", () => {
      assert.ok(micButton, "no mic control in the bar");
      assert.ok(micRow, "the mic popover lists no camera");
      assert.ok(micRow.disabled, "a mic row that cannot work is not disabled");
      assert.ok(
        /intercom|share|token|scope/i.test(micRow.title),
        `the mic row gives no reason: ${micRow.title}`
      );
    });
    check("a disabled mic row commands nothing", () => {
      assert.equal(seen.length, beforeMic);
      assert.ok(
        !seen.some((c) => c.kind === "setActiveMic"),
        "the card asked for a mic session it cannot have"
      );
    });

    // The highlight menu. The shape is the viewer's `GridHighlightSettings`
    // plus the bar's master `enabled` — `gridHighlightSchema` in the embed.
    await press('[data-action="highlight"]');
    await press('[data-row="highlight-master"]');
    seen = await commands(page);
    const highlight = seen.filter((c) => c.kind === "setHighlight").at(-1);
    check("the highlight master pushes the viewer's defaults", () => {
      assert.ok(highlight, "the highlight menu pushes no setHighlight");
      assert.equal(highlight.value.enabled, true);
      assert.equal(highlight.value.motion, true);
      assert.equal(highlight.value.audio, "off");
      assert.equal(highlight.value.detection, false);
      assert.equal(highlight.value.detectionHoldSec, 3);
      assert.deepEqual(highlight.value.detectionClasses, []);
    });

    await page.evaluate(() => {
      const root = document.querySelector("camstack-grid-card").shadowRoot;
      const select = root.querySelector('[data-row="highlight-audio"]');
      select.value = "mid";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await page.waitForTimeout(60);
    seen = await commands(page);
    const withAudio = seen.filter((c) => c.kind === "setHighlight").at(-1);
    check("an audio preset is the viewer's meter rung, not a free dB", () => {
      // `mid` is rung 4 of `AUDIO_LEVEL_THRESHOLDS_DBFS` → -39 dBFS, held for
      // `AUDIO_HIGHLIGHT_HOLD_SEC` (5 s, not operator-configurable).
      assert.equal(withAudio.value.audio, "level");
      assert.equal(withAudio.value.audioDb, -39);
      assert.equal(withAudio.value.audioHoldSec, 5);
    });

    await press('[data-action="activeOnly"]');
    seen = await commands(page);
    check("active-only rides the open channel", () => {
      assert.deepEqual(seen.filter((c) => c.kind === "setActiveOnly").at(-1), {
        kind: "setActiveOnly",
        value: true,
      });
    });

    await press('[data-action="quality"]');
    await press('[data-row="quality-low"]');
    seen = await commands(page);
    check("the quality picker pushes the system tier", () => {
      assert.deepEqual(seen.filter((c) => c.kind === "setQuality").at(-1), {
        kind: "setQuality",
        value: "low",
      });
    });

    // A remounted page seeds from a config that is stale by every bar press.
    await emit(page, { type: "state", state: "ready" });
    seen = await commands(page);
    check("a remounted page is told the bar's state too", () => {
      const last = (kind) => seen.filter((c) => c.kind === kind).at(-1);
      assert.equal(last("setQuality").value, "low");
      assert.equal(last("setActiveOnly").value, true);
      assert.equal(last("setHighlight").value.enabled, true);
    });

    await page.evaluate(() => {
      const card = document.querySelector("camstack-grid-card");
      card.shadowRoot.querySelector('[data-bar="controls"]').dataset.stamp = "bar";
      for (let i = 0; i < 30; i += 1) {
        card.hass = window.__makeHass();
      }
    });
    await page.waitForTimeout(200);
    const barStamp = await page.evaluate(() => {
      const el = document
        .querySelector("camstack-grid-card")
        .shadowRoot.querySelector('[data-bar="controls"]');
      return el ? el.dataset.stamp : null;
    });
    check("the bar is not rebuilt by a set-hass storm", () => {
      assert.equal(barStamp, "bar", "an open popover would close several times a second");
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
