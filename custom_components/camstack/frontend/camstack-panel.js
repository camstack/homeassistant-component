/**
 * CamStack iframe panel for Home Assistant.
 * Displays CamStack in a fullscreen iframe.
 */
class CamstackPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
  }

  set config(c) {
    this._config = c;
    if (!c?.url) return;
    const iframe = document.createElement("iframe");
    iframe.src = c.url;
    iframe.style.cssText =
      "position:absolute;top:0;left:0;width:100%;height:100%;border:none;";
    this.shadowRoot.innerHTML = "";
    this.shadowRoot.appendChild(iframe);
  }

  get config() {
    return this._config;
  }
}

customElements.define("camstack-panel", CamstackPanel);
