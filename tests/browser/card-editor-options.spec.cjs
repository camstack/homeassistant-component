/**
 * The card editors, run for real: does what an operator picks reach the embed?
 *
 * `tests/test_card_editor_options.py` next door is a grep — it proves the
 * options are NAMED and that none of them is a text box over a closed set.
 * This one proves they WORK, in Chromium, and it exists for three things a
 * grep cannot see:
 *
 *  1. What the events editor EMITS. Ticking "hide the importance dot" has to
 *     come out as `fields=label,sublabel,camera,time` in the embed's query —
 *     an editor that writes a key the card's `_query` does not read is an
 *     editor full of controls that do nothing.
 *  2. That an out-of-range number is DROPPED. The embed fails the whole URL
 *     parse over one bad value, so a `max` of 900000 must cost the operator
 *     that field, never the card.
 *  3. That a changed wall option rides the OPEN CHANNEL. `acquireConfig`
 *     resolves once, so re-sending `embed-config` reaches nobody: until
 *     `_publishWall` existed, moving the column count did nothing until
 *     something rebuilt the iframe — which renegotiates every WebRTC session
 *     on the wall. The assertion is about a message that actually crossed the
 *     frame boundary, and about the frame still being the same NODE after.
 *
 * Run it (Playwright lives in the camstack-server tree, not in this repo):
 *
 *   NODE_PATH=../camstack-server/node_modules node tests/browser/card-editor-options.spec.cjs
 *
 * Counter-proof — put the card back the way it was and watch this go red:
 *
 *   BREAK_FIX=1 NODE_PATH=… node tests/browser/card-editor-options.spec.cjs
 *
 * Not wired into CI, for the same reason as `grid-card-host.spec.cjs`: the
 * workflow installs Python only. The greps run there; this runs when a card
 * changes.
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

function read(name) {
  return fs.readFileSync(path.join(FRONTEND, name), "utf8");
}

/**
 * The grid card, optionally with this work removed.
 *
 * The counter-proof takes out exactly two things: the push onto the open
 * channel, and the derivation that turns a visibility cap into a one-row wall.
 * What is left is the card as it shipped — and these assertions have to fail
 * against it, or they are not testing what they claim to.
 */
function gridSource() {
  const source = read("camstack-grid-card.js");
  if (!process.env.BREAK_FIX) {
    return source;
  }
  const publish = "      this._publishWall(false);\n";
  const derive =
    "    if (this._scrollGeometry(deviceIds)) {\n" +
    "      return deviceIds.length;\n" +
    "    }\n";
  assert.ok(source.includes(publish), "the publish call was not found");
  assert.ok(source.includes(derive), "the layout derivation was not found");
  return source.replace(publish, "").replace(derive, "");
}

const EMBED_HTML = `<!doctype html><meta charset="utf-8"><title>stub embed</title>
<script>
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (data && data.type === "__emit") {
      parent.postMessage(data.msg, "*");
      return;
    }
    parent.postMessage({ type: "__spy", payload: data }, "*");
  });
  parent.postMessage({ type: "embed-ready", mode: "grid" }, "*");
</script>`;

const HOST_HTML = `<!doctype html><meta charset="utf-8"><title>editor harness</title>
<body>
<script type="module">
  window.__spy = [];
  window.addEventListener("message", (event) => {
    if (event.data && event.data.type === "__spy") {
      window.__spy.push(event.data.payload);
    }
  });
  await import("./local/camstack-events-card.js?v=test");
  await import("./local/camstack-grid-card.js?v=test");
  window.__cardsLoaded = true;
</script>
</body>`;

async function serve(page) {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/") {
      return route.fulfill({ contentType: "text/html", body: HOST_HTML });
    }
    if (url.pathname === "/local/camstack-grid-card.js") {
      return route.fulfill({ contentType: "text/javascript", body: gridSource() });
    }
    if (url.pathname === "/local/camstack-events-card.js") {
      return route.fulfill({
        contentType: "text/javascript",
        body: read("camstack-events-card.js"),
      });
    }
    if (url.pathname === "/local/camstack-hub-probe.js") {
      return route.fulfill({
        contentType: "text/javascript",
        body: read("camstack-hub-probe.js"),
      });
    }
    if (url.pathname === `${PROXY_BASE}${EMBED_PATH}`) {
      return route.fulfill({ contentType: "text/html", body: EMBED_HTML });
    }
    return route.fulfill({ status: 404, body: "not routed" });
  });
}

/** Six cameras, so a cap of two actually bites. */
const HASS_SETUP = `
  window.__entities = [];
  window.__makeHass = () => {
    const states = {};
    for (let i = 1; i <= 6; i += 1) {
      states["camera.c" + i] = {
        attributes: { camstack_device_id: i, friendly_name: "Camera " + i },
      };
      if (window.__entities.length < 6) window.__entities.push("camera.c" + i);
    }
    return {
      states,
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
    };
  };
`;

async function commands(page) {
  return page.evaluate(() =>
    window.__spy.filter((m) => m && m.type === "embed-command").map((m) => m.command)
  );
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
    await page.waitForFunction(() => window.__cardsLoaded === true);
    await page.evaluate(HASS_SETUP);

    // ── the events editor ─────────────────────────────────────────────────
    const events = await page.evaluate(async () => {
      const editor = document.createElement("camstack-events-card-editor");
      document.body.appendChild(editor);
      editor.setConfig({});
      editor.hass = { states: {} };
      const root = editor.shadowRoot;

      const kinds = {};
      for (const el of root.querySelectorAll("select,input")) {
        if (el.id) kinds[el.id] = el.tagName.toLowerCase() + ":" + (el.type || "");
      }
      for (const el of root.querySelectorAll("[data-group]")) {
        kinds[el.dataset.group] = "group";
      }

      const box = (group, value) =>
        Array.from(
          root.querySelector(`[data-group="${group}"]`).querySelectorAll("input")
        ).find((input) => input.value === value);

      // Hide the importance dot, order by importance, keep only tracks with a
      // plate, search semantically, and ask for an illegal number of rows.
      box("fields", "badges").checked = false;
      box("attributes", "plate").checked = true;
      root.getElementById("sort").value = "importance";
      root.getElementById("search_mode").value = "semantic";
      root.getElementById("page_size").value = "30";
      root.getElementById("max").value = "900000";

      const emitted = await new Promise((resolve) => {
        editor.addEventListener("config-changed", (e) => resolve(e.detail.config), {
          once: true,
        });
        root.firstElementChild.dispatchEvent(new Event("change", { bubbles: true }));
      });

      const card = document.createElement("camstack-events-card");
      card.setConfig(emitted);
      const bare = document.createElement("camstack-events-card");
      bare.setConfig({ fields: [] });
      return {
        kinds,
        emitted,
        query: card._query([617, 615]),
        bare: bare._query([617]),
        suggestions: root.querySelectorAll("datalist option").length,
      };
    });
    const params = new URLSearchParams(events.query);

    check("every closed-set option is a select or a checkbox group", () => {
      for (const id of ["view", "thumb", "theme", "sort", "search_mode", "rows", "columns"]) {
        assert.equal(events.kinds[id], "select:select-one", id);
      }
      assert.equal(events.kinds.fields, "group");
      assert.equal(events.kinds.attributes, "group");
    });
    check("the open set keeps its suggestions", () => {
      assert.ok(events.suggestions > 0, "no class suggestions offered");
    });
    check("unticking badges hides the importance dot", () => {
      assert.equal(params.get("fields"), "label,sublabel,camera,time");
    });
    check("order, search mode and page size reach the embed", () => {
      assert.equal(params.get("sort"), "importance");
      assert.equal(params.get("smode"), "semantic");
      assert.equal(params.get("page"), "30");
      assert.equal(params.get("attrs"), "plate");
    });
    check("an out-of-range number costs the field, not the card", () => {
      assert.equal(params.has("max"), false);
      assert.equal("max" in events.emitted, false);
    });
    check("no fields at all is an empty allow-list, not the default", () => {
      assert.equal(new URLSearchParams(events.bare).get("fields"), "");
    });

    // ── the grid card: the cap, and the open channel ──────────────────────
    await page.evaluate(() => {
      const card = document.createElement("camstack-grid-card");
      card.setConfig({ entities: window.__entities, max_visible: 2, layout: 3 });
      document.body.appendChild(card);
      card.hass = window.__makeHass();
    });
    await page.waitForFunction(
      () => window.__spy.some((m) => m && m.type === "embed-config"),
      undefined,
      { timeout: 10000 }
    );
    await page.waitForTimeout(120);

    const seeded = await page.evaluate(
      () => window.__spy.find((m) => m.type === "embed-config").config
    );
    check("a capped wall is seeded as ONE row of every camera", () => {
      assert.equal(seeded.layout, 6);
      assert.deepEqual(seeded.devices, [1, 2, 3, 4, 5, 6]);
    });

    const styled = await page.evaluate(() => {
      const root = document.querySelector("camstack-grid-card").shadowRoot;
      const frame = root.querySelector("iframe");
      frame.dataset.stamp = "original";
      return {
        scroller: root.querySelector('[data-scroller="wall"]').style.cssText,
        frame: frame.style.cssText,
      };
    });
    check("the host scrolls horizontally, in the shape of the strip", () => {
      assert.match(styled.scroller, /overflow(-x)?: ?auto/);
      assert.match(styled.frame, /width: ?300%/);
      assert.match(styled.frame, /aspect-ratio: ?96 ?\/ ?9/);
    });

    // Lift the cap: the wall goes back to the operator's own column count, and
    // that has to travel — not wait for a rebuild that restarts every stream.
    await page.evaluate(() => {
      const card = document.querySelector("camstack-grid-card");
      card.setConfig({ entities: window.__entities, layout: 3, quality: "low" });
    });
    await page.waitForTimeout(200);
    let seen = await commands(page);
    check("lifting the cap pushes the column count on the open channel", () => {
      assert.deepEqual(seen.filter((c) => c.kind === "setLayout").at(-1), {
        kind: "setLayout",
        value: 3,
      });
    });
    check("a changed stream quality travels too", () => {
      assert.deepEqual(seen.filter((c) => c.kind === "setQuality").at(-1), {
        kind: "setQuality",
        value: "low",
      });
    });
    const relaxed = await page.evaluate(() => {
      const root = document.querySelector("camstack-grid-card").shadowRoot;
      return {
        scroller: root.querySelector('[data-scroller="wall"]').style.cssText,
        frame: root.querySelector("iframe").style.cssText,
        stamp: root.querySelector("iframe").dataset.stamp,
      };
    });
    check("lifting the cap restores the plain box", () => {
      assert.doesNotMatch(relaxed.scroller, /overflow(-x)?: ?auto/);
      assert.match(relaxed.frame, /aspect-ratio: ?16 ?\/ ?9/);
    });
    check("none of it rebuilt the iframe", () => {
      assert.equal(relaxed.stamp, "original", "the WebRTC wall was restarted");
    });

    // A `set hass` storm must not re-push a thing.
    const before = (await commands(page)).length;
    await page.evaluate(() => {
      const card = document.querySelector("camstack-grid-card");
      for (let i = 0; i < 20; i += 1) card.hass = window.__makeHass();
    });
    await page.waitForTimeout(250);
    seen = await commands(page);
    check("a re-render storm is silent on the channel", () => {
      assert.equal(seen.length, before);
    });

    // A page that has remounted seeded itself from a config that predates every
    // edit above, so `ready` has to be answered with what the host holds NOW.
    await page.evaluate(() => {
      const card = document.querySelector("camstack-grid-card");
      card.shadowRoot
        .querySelector("iframe")
        .contentWindow.postMessage(
          { type: "__emit", msg: { type: "state", state: "ready" } },
          "*"
        );
    });
    await page.waitForTimeout(150);
    seen = await commands(page);
    check("a re-mounted page is told the wall settings again", () => {
      assert.deepEqual(seen.filter((c) => c.kind === "setLayout").at(-1), {
        kind: "setLayout",
        value: 3,
      });
      assert.deepEqual(seen.filter((c) => c.kind === "setDevices").at(-1), {
        kind: "setDevices",
        value: [1, 2, 3, 4, 5, 6],
      });
    });
  } finally {
    await browser.close();
  }

  console.log(`\n${failures.length} failing`);
  process.exit(failures.length ? 1 : 0);
}

run();
