/**
 * CamStack sidebar panel.
 *
 * The URL is supplied by the integration, which derives it from the host and
 * port of the config entry. This file never asks for an address and never
 * stores one: a panel pointing somewhere other than the entities is a fault
 * nothing would ever report.
 *
 * Add `?debug=1` to the Home Assistant URL to show the resolved address.
 */
const LOG = "[camstack-panel]";
const LOAD_TIMEOUT_MS = 12000;

class CamstackPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._loadTimeout = null;
  }

  disconnectedCallback() {
    this._clearTimeout();
  }

  _clearTimeout() {
    if (this._loadTimeout !== null) {
      clearTimeout(this._loadTimeout);
      this._loadTimeout = null;
    }
  }

  set config(config) {
    this._config = config;
    const url = config && config.url;
    if (!url) {
      // The integration refuses to register a panel without a URL, so this can
      // only be a stale registration. Say so rather than showing a blank page.
      this._renderMessage(
        "CamStack is not configured yet. Open Settings → Devices & services → CamStack."
      );
      return;
    }
    this._renderFrame(url);
  }

  get config() {
    return this._config;
  }

  _renderMessage(text) {
    const box = document.createElement("div");
    box.style.cssText =
      "display:flex;align-items:center;justify-content:center;height:100%;padding:24px;" +
      "text-align:center;font-family:var(--paper-font-body1_-_font-family,sans-serif);" +
      "color:var(--primary-text-color,#e1e1e1);background:var(--primary-background-color,#111);";
    box.textContent = text;
    this.shadowRoot.replaceChildren(box);
  }

  _renderFrame(url) {
    this._clearTimeout();

    const container = document.createElement("div");
    container.style.cssText =
      "position:relative;width:100%;height:100%;background:var(--primary-background-color,#0a0a0a);";

    const iframe = document.createElement("iframe");
    iframe.src = url;
    iframe.allow = "autoplay; fullscreen; microphone";
    iframe.style.cssText =
      "position:absolute;inset:0;width:100%;height:100%;border:none;";
    container.appendChild(iframe);

    if (window.location.search.includes("debug=1")) {
      const debug = document.createElement("div");
      debug.style.cssText =
        "position:absolute;top:8px;left:8px;right:8px;padding:8px;background:rgba(0,0,0,0.85);" +
        "color:#0f0;font-family:monospace;font-size:11px;z-index:9999;border-radius:4px;word-break:break-all;";
      debug.textContent = `${LOG} ${url}`;
      container.appendChild(debug);
    }

    const error = document.createElement("div");
    error.style.cssText =
      "position:absolute;bottom:16px;left:16px;right:16px;padding:12px;background:rgba(180,0,0,0.92);" +
      "color:#fff;font-size:14px;z-index:9999;border-radius:8px;display:none;";
    const message = document.createElement("strong");
    message.textContent = "CamStack did not load";
    const detail = document.createElement("div");
    detail.textContent =
      "Check that this browser can reach the hub, and that its certificate is accepted.";
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "Open in a new tab";
    link.style.cssText = "color:#fff;text-decoration:underline;";
    error.append(message, detail, link);
    container.appendChild(error);

    let loaded = false;
    iframe.addEventListener("load", () => {
      loaded = true;
      this._clearTimeout();
      error.style.display = "none";
    });

    this._loadTimeout = setTimeout(() => {
      this._loadTimeout = null;
      if (!loaded) {
        // A cross-origin iframe reports nothing on failure: no error event, no
        // status. A deadline is the only signal there is.
        error.style.display = "block";
      }
    }, LOAD_TIMEOUT_MS);

    this.shadowRoot.replaceChildren(container);
  }
}

if (!customElements.get("camstack-panel")) {
  customElements.define("camstack-panel", CamstackPanel);
}
