/**
 * CamStack Grid Card for Home Assistant Lovelace.
 * Displays a CamStack grid in an iframe.
 */
const CARD_VERSION = "0.1.0";

class CamstackGridCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
  }
  set config(c) { this._config = c || {}; }
  get config() { return this._config; }
  set hass(hass) { this._hass = hass; this._render(); }
  _render() {
    const c = this._config;
    const entities = (c.entities || []).join(", ");
    this.shadowRoot.innerHTML = `
      <div class="card-config" style="padding: 16px;">
        <div style="margin-bottom: 16px;">
          <label>CamStack URL base</label>
          <input type="text" id="url_base" value="${(c.url_base || "").replace(/"/g, "&quot;")}" placeholder="https://camstack.example.com" style="width: 100%; padding: 8px; margin-top: 4px; box-sizing: border-box;">
        </div>
        <div style="margin-bottom: 16px;">
          <label>Entities (camera IDs, comma-separated)</label>
          <input type="text" id="entities" value="${(entities || "").replace(/"/g, "&quot;")}" placeholder="camera.front_door, camera.back_yard" style="width: 100%; padding: 8px; margin-top: 4px; box-sizing: border-box;">
        </div>
        <div style="margin-bottom: 16px;">
          <label>Grid ID (optional, overrides entities)</label>
          <input type="text" id="grid_id" value="${(c.grid_id || "").replace(/"/g, "&quot;")}" placeholder="grid_xxx" style="width: 100%; padding: 8px; margin-top: 4px; box-sizing: border-box;">
        </div>
        <div style="margin-bottom: 16px;">
          <label>Height (px)</label>
          <input type="number" id="height" value="${c.height || 400}" style="width: 100%; padding: 8px; margin-top: 4px; box-sizing: border-box;">
        </div>
      </div>
    `;
    this.shadowRoot.querySelectorAll("input").forEach((el) => {
      el.addEventListener("change", () => this._fireChanged());
      el.addEventListener("input", () => this._fireChanged());
    });
  }
  _fireChanged() {
    const urlBase = this.shadowRoot.getElementById("url_base")?.value || "";
    const entitiesStr = this.shadowRoot.getElementById("entities")?.value || "";
    const gridId = this.shadowRoot.getElementById("grid_id")?.value || "";
    const height = parseInt(this.shadowRoot.getElementById("height")?.value, 10) || 400;
    const entities = entitiesStr.split(",").map((e) => e.trim()).filter(Boolean);
    const config = { ...this._config, url_base: urlBase.trim(), height };
    if (entities.length) config.entities = entities;
    else delete config.entities;
    if (gridId) config.grid_id = gridId.trim();
    else delete config.grid_id;
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config }, bubbles: true }));
  }
}
customElements.define("camstack-grid-card-editor", CamstackGridCardEditor);

class CamstackGridCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
  }

  setConfig(config) {
    this._config = config || {};
  }

  set hass(hass) {
    this._hass = hass;
    this._updateContent();
  }

  _buildGridUrl() {
    const urlBase = (this._config.url_base || "").replace(/\/$/, "");
    if (!urlBase) return null;

    const params = new URLSearchParams();
    if (this._config.entities && Array.isArray(this._config.entities) && this._config.entities.length > 0) {
      const names = this._config.entities.map((e) => {
        const state = this._hass?.states[e];
        return state?.attributes?.friendly_name || e.split(".")[1] || e;
      });
      params.set("cameras", names.join(","));
    } else if (this._config.grid_id) {
      params.set("gridId", this._config.grid_id);
    } else if (this._config.cameras) {
      params.set("cameras", this._config.cameras);
    }

    if (this._config.audio !== false) params.set("audio", "1");
    if (this._config.resolution) params.set("resolution", this._config.resolution);
    const qs = params.toString();
    return `${urlBase}/grid-live${qs ? `?${qs}` : ""}`;
  }

  _updateContent() {
    const url = this._buildGridUrl();
    const height = this._config.height || 400;

    if (!url) {
      this.shadowRoot.innerHTML = `
        <ha-card>
          <div style="padding: 16px; color: var(--secondary-text-color);">
            Configure url_base and entities/cameras/grid_id
          </div>
        </ha-card>
      `;
      return;
    }

    this.shadowRoot.innerHTML = `
      <ha-card>
        ${this._config.title ? `<h2 style="padding: 8px 16px 0; margin: 0; font-size: 1.2em;">${this._config.title}</h2>` : ""}
        <div style="position: relative; padding: 8px;">
          <iframe
            src="${url}"
            style="width: 100%; height: ${height}px; border: none; border-radius: 4px;"
            allow="autoplay"
          ></iframe>
        </div>
      </ha-card>
    `;
  }

  static getConfigElement() {
    return document.createElement("camstack-grid-card-editor");
  }

  static getStubConfig() {
    return {
      url_base: "",
      entities: [],
      height: 400,
    };
  }
}

customElements.define("camstack-grid-card", CamstackGridCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "custom:camstack-grid-card",
  name: "CamStack Grid",
  preview: true,
  description: "Display a CamStack camera grid",
});

console.info(`%cCamStack Grid Card ${CARD_VERSION}`, "color: #007AFF; font-weight: bold;");
