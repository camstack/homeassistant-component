/**
 * CamStack grid card for Lovelace.
 *
 * The hub address is NOT a card option by default: the card asks the
 * integration (`/api/camstack/config`) which hub is configured. `url_base`
 * stays available as an override for setups whose browser reaches the hub at a
 * different address than Home Assistant does.
 *
 * The iframe is rebuilt only when the computed URL changes. Rebuilding it on
 * every `hass` update — which arrives several times a second on a busy
 * instance — restarts the video stream each time.
 */
const CARD_TAG = "camstack-grid-card";
const EDITOR_TAG = "camstack-grid-card-editor";
const DEFAULT_HEIGHT = 400;

class CamstackGridCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._resolvedBase = null;
    this._resolving = null;
    this._renderedUrl = null;
    this._iframe = null;
  }

  setConfig(config) {
    this._config = config || {};
    this._renderedUrl = null;
    this._render();
  }

  set hass(hass) {
    const first = this._hass === null;
    this._hass = hass;
    if (first) {
      this._resolveBase();
    }
    this._render();
  }

  getCardSize() {
    return Math.ceil((this._config.height || DEFAULT_HEIGHT) / 50);
  }

  async _resolveBase() {
    if (this._resolving || this._config.url_base) {
      return;
    }
    this._resolving = this._hass
      .callApi("GET", "camstack/config")
      .then((result) => {
        const entries = (result && result.entries) || [];
        this._resolvedBase = entries.length ? entries[0].url_base : null;
        this._render();
      })
      .catch(() => {
        // The integration is not loaded, or this user may not call it. Either
        // way the card falls back to its own `url_base`, and says so when it
        // has none.
        this._resolvedBase = null;
        this._render();
      });
  }

  _baseUrl() {
    const explicit = (this._config.url_base || "").trim().replace(/\/$/, "");
    return explicit || this._resolvedBase || null;
  }

  _gridUrl() {
    const base = this._baseUrl();
    if (!base) {
      return null;
    }
    const params = new URLSearchParams();
    const entities = this._config.entities;
    if (Array.isArray(entities) && entities.length) {
      const names = entities.map((entityId) => {
        const state = this._hass && this._hass.states[entityId];
        return (
          (state && state.attributes && state.attributes.friendly_name) ||
          entityId.split(".")[1] ||
          entityId
        );
      });
      params.set("cameras", names.join(","));
    } else if (this._config.grid_id) {
      params.set("gridId", this._config.grid_id);
    } else if (this._config.cameras) {
      params.set("cameras", this._config.cameras);
    }
    if (this._config.audio !== false) {
      params.set("audio", "1");
    }
    if (this._config.resolution) {
      params.set("resolution", this._config.resolution);
    }
    const query = params.toString();
    return `${base}/grid-live${query ? `?${query}` : ""}`;
  }

  _render() {
    const url = this._gridUrl();
    if (url === this._renderedUrl) {
      return;
    }
    this._renderedUrl = url;

    const card = document.createElement("ha-card");
    if (this._config.title) {
      card.setAttribute("header", this._config.title);
    }

    if (!url) {
      const empty = document.createElement("div");
      empty.style.cssText = "padding:16px;color:var(--secondary-text-color);";
      empty.textContent = this._resolving
        ? "Waiting for the CamStack integration…"
        : "No CamStack hub configured. Add the CamStack integration, or set url_base on this card.";
      card.appendChild(empty);
      this.shadowRoot.replaceChildren(card);
      this._iframe = null;
      return;
    }

    const wrapper = document.createElement("div");
    wrapper.style.cssText = "position:relative;padding:8px;";
    const iframe = document.createElement("iframe");
    iframe.src = url;
    iframe.allow = "autoplay; fullscreen";
    iframe.style.cssText = `width:100%;height:${
      this._config.height || DEFAULT_HEIGHT
    }px;border:none;border-radius:4px;`;
    wrapper.appendChild(iframe);
    card.appendChild(wrapper);
    this._iframe = iframe;
    this.shadowRoot.replaceChildren(card);
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TAG);
  }

  static getStubConfig() {
    return { entities: [], height: DEFAULT_HEIGHT };
  }
}

class CamstackGridCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
  }

  _render() {
    const config = this._config;
    const field = (id, label, value, placeholder, type) => {
      const row = document.createElement("div");
      row.style.cssText = "margin-bottom:16px;";
      const caption = document.createElement("label");
      caption.textContent = label;
      caption.setAttribute("for", id);
      const input = document.createElement("input");
      input.id = id;
      input.type = type || "text";
      input.value = value == null ? "" : String(value);
      if (placeholder) {
        input.placeholder = placeholder;
      }
      input.style.cssText =
        "width:100%;padding:8px;margin-top:4px;box-sizing:border-box;";
      input.addEventListener("input", () => this._emit());
      row.append(caption, input);
      return row;
    };

    const wrapper = document.createElement("div");
    wrapper.style.cssText = "padding:16px;";
    wrapper.append(
      field(
        "entities",
        "Cameras (entity ids, comma separated)",
        (config.entities || []).join(", "),
        "camera.front_door, camera.back_yard"
      ),
      field("grid_id", "Grid id (optional, overrides the cameras)", config.grid_id, "grid_xxx"),
      field("height", "Height (px)", config.height || DEFAULT_HEIGHT, "", "number"),
      field(
        "url_base",
        "Hub URL override (optional)",
        config.url_base,
        "Leave empty to use the configured CamStack integration"
      )
    );
    this.shadowRoot.replaceChildren(wrapper);
  }

  _value(id) {
    const el = this.shadowRoot.getElementById(id);
    return el ? el.value.trim() : "";
  }

  _emit() {
    const config = { ...this._config };
    const entities = this._value("entities")
      .split(",")
      .map((entry) => entry.trim())
      .filter(Boolean);
    if (entities.length) {
      config.entities = entities;
    } else {
      delete config.entities;
    }
    const gridId = this._value("grid_id");
    if (gridId) {
      config.grid_id = gridId;
    } else {
      delete config.grid_id;
    }
    const urlBase = this._value("url_base");
    if (urlBase) {
      config.url_base = urlBase;
    } else {
      delete config.url_base;
    }
    config.height = parseInt(this._value("height"), 10) || DEFAULT_HEIGHT;
    this.dispatchEvent(
      new CustomEvent("config-changed", { detail: { config }, bubbles: true })
    );
  }
}

if (!customElements.get(EDITOR_TAG)) {
  customElements.define(EDITOR_TAG, CamstackGridCardEditor);
}
if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, CamstackGridCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === `custom:${CARD_TAG}`)) {
  window.customCards.push({
    type: `custom:${CARD_TAG}`,
    name: "CamStack Grid",
    preview: true,
    description: "A CamStack camera grid, pointed at the configured hub",
  });
}
