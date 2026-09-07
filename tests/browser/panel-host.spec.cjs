/**
 * The panel, mounted the way Home Assistant actually mounts it.
 *
 * Home Assistant hands a custom panel its `hass`, `narrow`, `route` and
 * **`panel`** properties. It never assigns `config`. This class only had a
 * `config` setter, so nothing ran: `_config` stayed null, the shadow root
 * stayed empty, and the panel was a white page with no error in any console —
 * an element that renders nothing is not a failure the browser reports. That
 * shipped, and it was diagnosed in the live DOM rather than from a log.
 *
 * The second half is the sizing. A custom element is `display: inline` and has
 * no height, and the document Home Assistant builds for an embedded panel gives
 * its body none either. Every child sizes with `height: 100%`, so with the
 * setter fixed and the sizing missing the frame is still 0 x 0 — content
 * present, nothing visible, indistinguishable from the first bug.
 *
 * Run it (Playwright lives in the camstack-server tree, not in this repo):
 *
 *   NODE_PATH=../camstack-server/node_modules node tests/browser/panel-host.spec.cjs
 *
 * Counter-proof — put the panel back the way it shipped and watch this go red:
 *
 *   BREAK_FIX=1 NODE_PATH=… node tests/browser/panel-host.spec.cjs
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const FRONTEND = path.join(__dirname, "..", "..", "custom_components", "camstack", "frontend");
const ORIGIN = "http://camstack.test";
const PROXY_BASE = "/api/camstack/p/grant-1";

/** The panel source, optionally with the fix removed. */
function panelSource() {
  const source = fs.readFileSync(path.join(FRONTEND, "camstack-panel.js"), "utf8");
  if (!process.env.BREAK_FIX) return source;
  const setterStart = source.indexOf("  set panel(panel) {");
  const setterEnd = source.indexOf("  set config(config) {");
  assert.ok(setterStart !== -1 && setterEnd > setterStart, "the panel setter was not found");
  const connectedStart = source.indexOf("  connectedCallback() {");
  const connectedEnd = source.indexOf("  disconnectedCallback() {");
  assert.ok(connectedStart !== -1 && connectedEnd > connectedStart, "connectedCallback not found");
  return (
    source.slice(0, connectedStart) +
    source.slice(connectedEnd, setterStart) +
    source.slice(setterEnd)
  );
}

/** The document Home Assistant builds for an embedded panel: a bare body. */
const HOST_HTML = `<!doctype html><meta charset="utf-8"><title>panel host</title><body></body>`;

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext();
  await context.route(`${ORIGIN}/**`, (route) => {
    const url = route.request().url();
    if (url.startsWith(`${ORIGIN}${PROXY_BASE}`)) {
      return route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html>relayed" });
    }
    return route.fulfill({ status: 200, contentType: "text/html", body: HOST_HTML });
  });
  const page = await context.newPage();
  await page.goto(`${ORIGIN}/host`);
  await page.addScriptTag({ content: panelSource() });

  const result = await page.evaluate(async (proxyBase) => {
    const el = document.createElement("camstack-panel");
    document.body.appendChild(el);
    // Exactly what Home Assistant assigns, and nothing else.
    el.hass = { states: {} };
    el.narrow = false;
    el.route = { path: "" };
    el.panel = {
      component_name: "custom",
      title: "CamStack",
      config: { url: "https://hub.test:4443", proxy_base: proxyBase },
    };
    await new Promise((r) => setTimeout(r, 300));
    const frame = el.shadowRoot.querySelector("iframe");
    const before = frame;
    // Home Assistant re-assigns `panel` on navigation; an unchanged config must
    // not rebuild the frame, or the admin UI reloads under the operator.
    for (let i = 0; i < 10; i += 1) el.panel = el.panel;
    await new Promise((r) => setTimeout(r, 100));
    return {
      frameSrc: frame ? frame.src : null,
      hostHeight: el.getBoundingClientRect().height,
      frameHeight: frame ? frame.getBoundingClientRect().height : 0,
      sameNode: before === el.shadowRoot.querySelector("iframe"),
    };
  }, PROXY_BASE);

  await browser.close();

  assert.ok(
    result.frameSrc && result.frameSrc.includes(PROXY_BASE),
    `the panel framed nothing — Home Assistant sets \`panel\`, not \`config\` (got ${result.frameSrc})`
  );
  assert.ok(result.hostHeight > 0, "the host element collapsed to zero height");
  assert.ok(result.frameHeight > 0, "the frame collapsed to zero height");
  assert.ok(result.sameNode, "re-assigning an unchanged `panel` rebuilt the frame");
  console.log("panel-host: ok", JSON.stringify(result));
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
