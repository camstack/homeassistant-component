/**
 * CamStack iframe panel for Home Assistant.
 * Displays CamStack in a fullscreen iframe.
 * Debug: add ?debug=1 to HA URL to show iframe URL and load status.
 */
class CamstackPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._loadTimeout = null;
  }

  set config(c) {
    this._config = c;
    if (!c?.url) return;
    const url = c.url;
    const showDebug = window.location.search.includes("debug=1");

    const iframe = document.createElement("iframe");
    iframe.src = url;
    iframe.style.cssText =
      "position:absolute;top:0;left:0;width:100%;height:100%;border:none;";

    const container = document.createElement("div");
    container.style.cssText = "position:relative;width:100%;height:100%;background:#0a0a0a;";
    container.appendChild(iframe);

    if (showDebug) {
      const debug = document.createElement("div");
      debug.style.cssText =
        "position:absolute;top:8px;left:8px;right:8px;padding:8px;background:rgba(0,0,0,0.85);color:#0f0;font-family:monospace;font-size:11px;z-index:9999;border-radius:4px;word-break:break-all;";
      debug.textContent = "CamStack URL: " + url;
      container.appendChild(debug);
    }

    const errorEl = document.createElement("div");
    errorEl.style.cssText =
      "position:absolute;bottom:16px;left:16px;right:16px;padding:12px;background:rgba(180,0,0,0.9);color:#fff;font-size:14px;z-index:9999;border-radius:8px;display:none;";
    errorEl.innerHTML =
      "<strong>CamStack non caricato</strong><br>Verifica l'URL e la connettività. " +
      '<a href="#" style="color:#fff;text-decoration:underline;">Apri in nuova scheda</a>';
    const link = errorEl.querySelector("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener";
    container.appendChild(errorEl);

    let loaded = false;
    iframe.onload = () => {
      loaded = true;
      if (this._loadTimeout) clearTimeout(this._loadTimeout);
      this._loadTimeout = null;
    };

    this._loadTimeout = setTimeout(() => {
      if (!loaded) {
        errorEl.style.display = "block";
      }
      this._loadTimeout = null;
    }, 12000);

    this.shadowRoot.innerHTML = "";
    this.shadowRoot.appendChild(container);
  }

  get config() {
    return this._config;
  }
}

customElements.define("camstack-panel", CamstackPanel);
