/**
 * Can a finger scroll a CamStack wall on a phone?
 *
 * ## What was measured, before anything was changed
 *
 * The operator's report was "lo scrolling non è funzionante su mobile". Three
 * candidate causes were put up and two of them FALSIFIED:
 *
 *   • the card's `overflow` does not produce a scrollable area — false. With a
 *     PLAIN framed document the very same scroller moves 391 px for the drag
 *     below.
 *   • the card captures the pointer for its own chrome — false. The card binds
 *     no touch handler at all.
 *   • the framed document consumes the gesture — TRUE. `ZoomPan.tsx:160` in the
 *     viewer's embed puts `touch-action: none` on a full-bleed overlay over
 *     every tile and captures the pointer on `pointerdown`, so it can pinch and
 *     pan one camera. `touch-action: none` means "no default touch behaviour
 *     here", and that includes chaining the scroll out to the host. The same
 *     drag then moves the scroller 0 px.
 *
 * That cause is in the VIEWER, not in this card, and nothing a host can set
 * reaches inside a frame. So the card's answer is the one thing it does own:
 * where the frame ENDS. A scrolling wall keeps a strip of its scroller
 * uncovered, and a drag there still belongs to the host — 684 px of scroll for
 * the drag that produced 0.
 *
 * The better answer is upstream of that: the `auto` arrangement (the default)
 * never scrolls, so on a phone the only scroll left is the dashboard's own.
 *
 * Run it (Playwright lives in the camstack-server tree, not in this repo):
 *
 *   NODE_PATH=../camstack-server/node_modules node tests/browser/grid-card-scroll.spec.cjs
 *
 * Not wired into CI, for the same reason as `grid-card-host.spec.cjs`: the
 * workflow installs Python only.
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium, devices } = require("playwright");

const FRONTEND = path.join(__dirname, "..", "..", "custom_components", "camstack", "frontend");
const ORIGIN = "http://camstack.test";
const PROXY_BASE = "/api/camstack/proxy/e1";
const EMBED_PATH = "/viewer/camstack/embed/index.html";

/**
 * The embed stub, faithful in the ONE respect this file is about: the tile
 * overlay's `touch-action: none` and its pointer capture, copied from
 * `camstack/embed/src/components/ZoomPan.tsx`.
 */
const EMBED_HTML = `<!doctype html><meta charset="utf-8"><title>stub embed</title>
<body style="margin:0;background:#111">
<div id="zoompan" style="position:absolute;inset:0;overflow:hidden;touch-action:none"></div>
<script>
  const zp = document.getElementById("zoompan");
  zp.addEventListener("pointerdown", (e) => { zp.setPointerCapture(e.pointerId); });
  parent.postMessage({ type: "embed-ready", mode: "grid" }, "*");
</script>
</body>`;

const HOST_HTML = `<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>scroll harness</title>
<body style="margin:0">
<div style="height:600px;background:#eee"></div>
<div id="slot"></div>
<div style="height:1600px;background:#ddd"></div>
<script type="module">
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
    for (const name of [
      "camstack-grid-card.js",
      "camstack-grid-controls.js",
      "camstack-hub-probe.js",
    ]) {
      if (url.pathname === `/local/${name}`) {
        return route.fulfill({
          contentType: "text/javascript",
          body: fs.readFileSync(path.join(FRONTEND, name), "utf8"),
        });
      }
    }
    if (url.pathname === `${PROXY_BASE}${EMBED_PATH}`) {
      return route.fulfill({ contentType: "text/html", body: EMBED_HTML });
    }
    return route.fulfill({ status: 404, body: "not routed" });
  });
}

const HASS_SETUP = `
  window.__makeHass = () => ({
    states: Object.fromEntries(
      Array.from({ length: 8 }, (_, i) => [
        "camera.c" + i,
        { attributes: { camstack_device_id: 300 + i, friendly_name: "Camera " + i } },
      ])
    ),
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

/** A finger, dragged up by 160 px in eight steps. */
async function dragUp(page, x, y) {
  const client = await page.context().newCDPSession(page);
  await client.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
  for (let i = 1; i <= 8; i += 1) {
    await client.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [{ x, y: y - i * 20 }],
    });
  }
  await client.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await page.waitForTimeout(350);
}

async function mount(page, config) {
  await page.evaluate(
    ([cfg, setup]) => {
      // eslint-disable-next-line no-eval
      eval(setup);
      const slot = document.getElementById("slot");
      slot.replaceChildren();
      const card = document.createElement("camstack-grid-card");
      card.setConfig(cfg);
      slot.appendChild(card);
      card.hass = window.__makeHass();
    },
    [config, HASS_SETUP]
  );
  await page.waitForFunction(
    () => {
      const card = document.querySelector("camstack-grid-card");
      return card && card.shadowRoot.querySelector("iframe") !== null;
    },
    undefined,
    { timeout: 10000 }
  );
  await page.waitForTimeout(500);
}

/** The scroller's box, the frame's box, and how far the scroller has moved. */
async function measure(page) {
  return page.evaluate(() => {
    const root = document.querySelector("camstack-grid-card").shadowRoot;
    const frame = root.querySelector("iframe");
    const scroller = frame.parentElement;
    const s = scroller.getBoundingClientRect();
    const f = frame.getBoundingClientRect();
    return {
      scrollTop: scroller.scrollTop,
      scrollable: scroller.scrollHeight > scroller.clientHeight + 1,
      scroller: { x: s.x, y: s.y, w: s.width, h: s.height },
      frameRight: f.right,
    };
  });
}

async function run() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ ...devices["Pixel 5"] });
  const page = await context.newPage();
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

    // A wall that scrolls: eight cameras, two rows visible.
    await mount(page, {
      entities: Array.from({ length: 8 }, (_, i) => `camera.c${i}`),
      layout_mode: "fixed",
      columns: 2,
      rows: 2,
    });

    const scrolling = await measure(page);
    check("the scrolling wall really has somewhere to scroll", () => {
      assert.ok(scrolling.scrollable, "nothing to scroll — the rest of this proves nothing");
    });
    check("a scrolling wall leaves a strip of its scroller uncovered", () => {
      const gutter = scrolling.scroller.x + scrolling.scroller.w - scrolling.frameRight;
      assert.ok(
        gutter >= 20,
        `only ${gutter}px of the scroller is reachable; a finger has nowhere to land`
      );
    });

    // The cause, stated as a measurement: on the frame, nothing moves.
    await dragUp(
      page,
      scrolling.scroller.x + scrolling.scroller.w / 2,
      scrolling.scroller.y + scrolling.scroller.h / 2
    );
    const onFrame = await measure(page);
    check("a drag on the wall is consumed by the embed (the cause, measured)", () => {
      assert.equal(
        onFrame.scrollTop,
        0,
        "this moved — the ZoomPan overlay no longer eats the gesture, and the " +
          "gutter may no longer be needed; re-measure before removing it"
      );
    });

    // The fix: on the gutter, it scrolls.
    await dragUp(
      page,
      scrolling.scroller.x + scrolling.scroller.w - 10,
      scrolling.scroller.y + scrolling.scroller.h / 2
    );
    const onGutter = await measure(page);
    check("a drag on the gutter scrolls the wall", () => {
      assert.ok(
        onGutter.scrollTop > 50,
        `the gutter moved the wall ${onGutter.scrollTop}px; the card is still unscrollable`
      );
    });

    // And the default arrangement never asks the question.
    await mount(page, { entities: Array.from({ length: 8 }, (_, i) => `camera.c${i}`) });
    const auto = await measure(page);
    check("the default arrangement does not scroll at all", () => {
      assert.equal(auto.scrollable, false, "the auto wall scrolls, which is the trap");
    });
    check("and it spends no width on a gutter it does not need", () => {
      const gutter = auto.scroller.x + auto.scroller.w - auto.frameRight;
      assert.ok(gutter < 2, `${gutter}px of the wall's width is wasted`);
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
