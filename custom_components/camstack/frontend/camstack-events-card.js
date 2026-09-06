/**
 * CamStack events card for Lovelace.
 *
 * Frames the viewer's own events embed
 * (`<hub>/viewer/camstack/embed/index.html?mode=events&v=1&…#t=<token>`) — the
 * same reel the CamStack apps show.
 *
 * ## Why this one is a URL and the grid card is a handshake
 *
 * `EmbedEventsPage` acquires its config with `{ urlFirst: true }`, so the query
 * params and the `#t=` fragment are honoured INSIDE an iframe. The grid page
 * does not, which is why its card has to answer `embed-ready` instead. Two
 * cards, two mechanisms, because the embed has two.
 *
 * The token rides the FRAGMENT deliberately: a fragment is never sent to the
 * server, so it does not land in the hub's access log, in a proxy log, or in a
 * Referer header.
 *
 * ## A known gap: thumbnails
 *
 * The hub's event-media plane (`/addon/pipeline-analytics/event-media/…`)
 * accepts a session/OAuth token or a `cst_` scoped token, and answers 401 to a
 * `csv_` SHARE token — even for a device inside that token's own scope. The
 * integration mints share tokens (it will not put an account credential in a
 * browser), so this card lists its events with the text and the badges and
 * WITHOUT the pictures until the hub's gate learns to accept a scoped share
 * token. See the component README.
 *
 * ## Height
 *
 * A reel's row height is fixed by the hub's own layout constants (card edge
 * 80/106/132 px plus 28 px of padding and footer). Anything shorter silently
 * cuts the time off the bottom of every card, so the card computes the height
 * rather than offering it as a free number.
 */
/**
 * The probe lives in a sibling module. `import.meta.url` carries the `?v=`
 * Lovelace loads this card with, and the sibling is asked for with the same
 * query, so a release never pairs a new card with a cached old probe.
 */
const VERSION_QUERY = new URL(import.meta.url).search;
const { probeHub, buildUnreachableNotice } = await import(
  `./camstack-hub-probe.js${VERSION_QUERY}`
);
const CARD_TAG = "camstack-events-card";
const EDITOR_TAG = "camstack-events-card-editor";
const EMBED_PATH = "/viewer/camstack/embed/index.html";
const EVENTS_URL_VERSION = "1";
const DEFAULT_FIELDS = "label,sublabel,camera,time,badges";
const DEFAULT_GALLERY_HEIGHT = 420;
/** Re-mint this long before the token dies. */
const TOKEN_RENEW_MARGIN_MS = 120000;

/**
 * Reel row height per thumbnail size, from the embed's `events-config.ts`:
 * `cardEdgePx` (80 / 106 / 132) + 3*2 padding + 14 footer + 4*2 gap.
 */
const REEL_ROW_HEIGHT = { small: 108, medium: 134, large: 160 };

function deviceIdOf(hass, entityId) {
  const state = hass && hass.states && hass.states[entityId];
  const raw = state && state.attributes && state.attributes.camstack_device_id;
  return Number.isInteger(raw) ? raw : null;
}

function friendlyName(hass, entityId) {
  const state = hass && hass.states && hass.states[entityId];
  return (
    (state && state.attributes && state.attributes.friendly_name) || entityId
  );
}

function csv(value) {
  if (Array.isArray(value)) {
    return value.map((entry) => String(entry).trim()).filter(Boolean).join(",");
  }
  return String(value || "").trim();
}

class CamstackEventsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._resolvedBase = null;
    this._entryId = null;
    this._probeToken = 0;
    this._cameras = [];
    this._resolving = false;
    this._renderedUrl = null;
    this._iframe = null;
    this._status = null;
    this._token = null;
    this._tokenExpiresAt = null;
    this._tokenKey = null;
    this._pendingToken = null;
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

  disconnectedCallback() {
    // Invalidates a probe still in flight, so a late answer cannot paint over
    // a card that has since been re-pointed or torn down.
    this._probeToken += 1;
  }

  /**
   * Ask the browser — not the iframe — whether it will show the hub, and
   * replace a refused frame with the reason instead of a white rectangle.
   */
  _watchFrame(url, iframe) {
    const token = ++this._probeToken;
    const origin = new URL(url).origin;
    probeHub(origin).then((result) => {
      if (token !== this._probeToken || result !== "unreachable") {
        return;
      }
      iframe.replaceWith(buildUnreachableNotice(origin));
      this._iframe = null;
      this._setStatus(null);
    });
  }

  getCardSize() {
    return Math.ceil(this._frameHeight() / 50);
  }

  async _resolveBase() {
    if (this._resolving) {
      return;
    }
    this._resolving = true;
    try {
      const result = await this._hass.callApi("GET", "camstack/config");
      const entries = (result && result.entries) || [];
      const wanted = this._config.entry_id;
      const entry =
        entries.find((item) => !wanted || item.entry_id === wanted) || null;
      this._resolvedBase = entry ? entry.url_base : null;
      this._entryId = entry ? entry.entry_id : null;
      this._cameras = (entry && entry.cameras) || [];
    } catch {
      this._resolvedBase = null;
      this._cameras = [];
    } finally {
      this._resolving = false;
      this._render();
      this._refresh();
    }
  }

  _baseUrl() {
    const explicit = (this._config.url_base || "").trim().replace(/\/$/, "");
    return explicit || this._resolvedBase || null;
  }

  /**
   * The devices this card reports on.
   *
   * An empty selection means "every camera this hub exports", NOT "every
   * camera the token can see": a share scope needs at least one explicit id,
   * so the list is filled in from the integration's own export membership.
   */
  _deviceIds() {
    const ids = [];
    const push = (value) => {
      if (Number.isInteger(value) && value >= 0 && !ids.includes(value)) {
        ids.push(value);
      }
    };
    if (Array.isArray(this._config.entities)) {
      for (const entityId of this._config.entities) {
        push(deviceIdOf(this._hass, entityId));
      }
    }
    if (!ids.length && Array.isArray(this._config.device_ids)) {
      for (const value of this._config.device_ids) {
        push(typeof value === "string" ? parseInt(value, 10) : value);
      }
    }
    if (!ids.length) {
      for (const camera of this._cameras) {
        push(camera && camera.id);
      }
    }
    return ids;
  }

  _names(deviceIds) {
    const byId = new Map();
    if (Array.isArray(this._config.entities)) {
      for (const entityId of this._config.entities) {
        const id = deviceIdOf(this._hass, entityId);
        if (id !== null) {
          byId.set(id, friendlyName(this._hass, entityId));
        }
      }
    }
    const parts = [];
    for (const id of deviceIds) {
      const name = byId.get(id);
      if (name) {
        // SINGLE `encodeURIComponent` per name — the hub's parser reads the
        // raw query and splits before decoding, so a double encode arrives
        // literally and a bare comma cuts the name in half.
        parts.push(`${id}:${encodeURIComponent(name)}`);
      }
    }
    return parts.join(",");
  }

  async _token_for(deviceIds) {
    const key = deviceIds.join(",");
    const fresh =
      this._token !== null &&
      this._tokenKey === key &&
      (this._tokenExpiresAt === null ||
        this._tokenExpiresAt - Date.now() > TOKEN_RENEW_MARGIN_MS);
    if (fresh) {
      return this._token;
    }
    if (this._pendingToken && this._tokenKey === key) {
      return this._pendingToken;
    }
    this._tokenKey = key;
    this._pendingToken = this._hass
      .callApi("POST", "camstack/embed_token", {
        kind: "events-view",
        device_ids: deviceIds,
        ...(this._entryId ? { entry_id: this._entryId } : {}),
      })
      .then((result) => {
        this._token = (result && result.token) || null;
        this._tokenExpiresAt =
          result && typeof result.expires_at === "number"
            ? result.expires_at * 1000
            : null;
        return this._token;
      })
      .finally(() => {
        this._pendingToken = null;
      });
    return this._pendingToken;
  }

  _view() {
    return this._config.view === "gallery" ? "gallery" : "reel";
  }

  _thumb() {
    const raw = this._config.thumb;
    return raw === "medium" || raw === "large" ? raw : "small";
  }

  _rows() {
    const raw = parseInt(this._config.rows, 10);
    return Number.isInteger(raw) && raw >= 1 && raw <= 6 ? raw : 1;
  }

  _frameHeight() {
    if (Number(this._config.height) > 0) {
      return Number(this._config.height);
    }
    if (this._view() === "reel") {
      return REEL_ROW_HEIGHT[this._thumb()] * this._rows();
    }
    return DEFAULT_GALLERY_HEIGHT;
  }

  _query(deviceIds) {
    const params = new URLSearchParams();
    params.set("mode", "events");
    params.set("v", EVENTS_URL_VERSION);
    params.set("view", this._view());
    params.set("thumb", this._thumb());
    params.set("theme", this._config.theme || "auto");
    params.set("fields", this._config.fields || DEFAULT_FIELDS);
    if (this._view() === "reel") {
      params.set("rows", String(this._rows()));
    } else if (this._config.columns) {
      params.set("cols", String(this._config.columns));
    }
    params.set("devices", deviceIds.join(","));
    if (this._config.classes) {
      params.set("classes", csv(this._config.classes));
    }
    if (this._config.attributes) {
      params.set("attrs", csv(this._config.attributes));
    }
    if (this._config.search) {
      params.set("q", String(this._config.search));
    }
    if (this._config.sort) {
      params.set("sort", String(this._config.sort));
    }
    if (Number(this._config.max) > 0) {
      params.set("max", String(parseInt(this._config.max, 10)));
    }
    if (Number(this._config.refresh_ms) > 0) {
      params.set("refresh", String(parseInt(this._config.refresh_ms, 10)));
    }
    if (Number(this._config.max_age_ms) > 0) {
      params.set("age", String(parseInt(this._config.max_age_ms, 10)));
    }
    // Appended raw: `names` carries per-name percent-encoding that
    // URLSearchParams would encode a second time.
    const names = this._names(deviceIds);
    return names ? `${params.toString()}&names=${names}` : params.toString();
  }

  _render() {
    const base = this._baseUrl();
    if (!base || !this._hass) {
      this._buildCard(null);
      return;
    }
    this._refresh();
  }

  async _refresh() {
    const base = this._baseUrl();
    if (!base || !this._hass) {
      return;
    }
    const deviceIds = this._deviceIds();
    if (!deviceIds.length) {
      this._buildCard(null);
      this._setStatus(
        "No CamStack cameras available yet. Pick cameras, or wait for the integration to load."
      );
      return;
    }
    let token;
    try {
      token = await this._token_for(deviceIds);
    } catch (err) {
      this._buildCard(null);
      this._setStatus(
        `CamStack refused a viewing token: ${(err && err.message) || err}`
      );
      return;
    }
    if (!token) {
      this._buildCard(null);
      this._setStatus("CamStack did not issue a viewing token.");
      return;
    }
    const url = `${base}${EMBED_PATH}?${this._query(deviceIds)}#t=${token}`;
    if (url === this._renderedUrl) {
      if (this._iframe) {
        this._iframe.style.cssText = this._frameStyle();
      }
      return;
    }
    this._renderedUrl = url;
    this._buildCard(url);
  }

  _frameStyle() {
    return `width:100%;height:${this._frameHeight()}px;border:none;display:block;border-radius:8px;`;
  }

  _setStatus(text) {
    if (!this._status) {
      return;
    }
    this._status.textContent = text || "";
    this._status.style.display = text ? "block" : "none";
  }

  _buildCard(url) {
    const card = document.createElement("ha-card");
    if (this._config.title) {
      card.setAttribute("header", this._config.title);
    }
    const wrapper = document.createElement("div");
    wrapper.style.cssText = "position:relative;padding:8px;";

    if (url) {
      const iframe = document.createElement("iframe");
      iframe.src = url;
      iframe.allow = "autoplay; fullscreen";
      iframe.style.cssText = this._frameStyle();
      wrapper.appendChild(iframe);
      this._iframe = iframe;
      this._watchFrame(url, iframe);
    } else {
      const empty = document.createElement("div");
      empty.style.cssText = "padding:16px;color:var(--secondary-text-color);";
      empty.textContent = this._resolving
        ? "Waiting for the CamStack integration…"
        : "No CamStack hub configured. Add the CamStack integration, or set url_base on this card.";
      wrapper.appendChild(empty);
      this._iframe = null;
    }

    const status = document.createElement("div");
    status.style.cssText =
      "position:absolute;left:16px;right:16px;bottom:16px;padding:10px;display:none;" +
      "background:rgba(180,0,0,0.92);color:#fff;font-size:13px;border-radius:6px;z-index:2;";
    wrapper.appendChild(status);

    card.appendChild(wrapper);
    this._status = status;
    this.shadowRoot.replaceChildren(card);
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TAG);
  }

  static getStubConfig() {
    return { view: "reel", thumb: "small", rows: 1 };
  }
}

// ── the editor ─────────────────────────────────────────────────────────────

class CamstackEventsCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    const first = this._hass === null;
    this._hass = hass;
    if (first) {
      this._render();
    }
  }

  _cameras() {
    const states = (this._hass && this._hass.states) || {};
    return Object.keys(states)
      .filter(
        (id) => id.startsWith("camera.") && deviceIdOf(this._hass, id) !== null
      )
      .sort();
  }

  _render() {
    const config = this._config;
    const wrapper = document.createElement("div");
    wrapper.style.cssText =
      "padding:8px;display:flex;flex-direction:column;gap:14px;";
    wrapper.append(
      field("title", "Title (optional)", "text", config.title, ""),
      picker(
        "entities",
        "Cameras (none selected = every exported camera)",
        config.entities || [],
        this._cameras(),
        (id) => friendlyName(this._hass, id)
      ),
      select("view", "Layout", config.view || "reel", [
        ["reel", "Reel (horizontal strip)"],
        ["gallery", "Gallery (grid)"],
      ]),
      select("thumb", "Thumbnail size", config.thumb || "small", [
        ["small", "Small"],
        ["medium", "Medium"],
        ["large", "Large"],
      ]),
      select("rows", "Reel rows", String(config.rows || 1), [
        ["1", "1"],
        ["2", "2"],
        ["3", "3"],
        ["4", "4"],
        ["5", "5"],
        ["6", "6"],
      ]),
      select("columns", "Gallery columns", String(config.columns || "auto"), [
        ["auto", "Automatic"],
        ["2", "2"],
        ["3", "3"],
        ["4", "4"],
        ["6", "6"],
        ["8", "8"],
      ]),
      field(
        "height",
        "Height in px (empty = fitted to the reel)",
        "number",
        config.height,
        ""
      ),
      field(
        "classes",
        "Object classes (comma separated, empty = all)",
        "text",
        csv(config.classes),
        "person, vehicle"
      ),
      field("search", "Search text (optional)", "text", config.search, ""),
      select("theme", "Theme", config.theme || "auto", [
        ["auto", "Follow the dashboard"],
        ["dark", "Dark"],
        ["light", "Light"],
      ]),
      field(
        "url_base",
        "Hub URL override (optional)",
        "text",
        config.url_base,
        "Leave empty to use the configured CamStack integration"
      )
    );
    wrapper.addEventListener("change", () => this._emit());
    wrapper.addEventListener("input", () => this._emit());
    this.shadowRoot.replaceChildren(wrapper);
  }

  _emit() {
    const root = this.shadowRoot;
    const config = { ...this._config };
    const text = (id) => {
      const el = root.getElementById(id);
      return el ? String(el.value).trim() : "";
    };
    const assign = (key, value) => {
      if (value) {
        config[key] = value;
      } else {
        delete config[key];
      }
    };
    assign("title", text("title"));
    const box = root.querySelector('[data-picker="entities"]');
    const selected = box
      ? Array.from(box.querySelectorAll("input[type=checkbox]"))
          .filter((input) => input.checked)
          .map((input) => input.value)
      : [];
    if (selected.length) {
      config.entities = selected;
    } else {
      delete config.entities;
    }
    config.view = text("view") === "gallery" ? "gallery" : "reel";
    config.thumb = text("thumb") || "small";
    config.rows = parseInt(text("rows"), 10) || 1;
    const columns = text("columns");
    assign("columns", columns === "auto" ? "" : columns);
    const height = parseInt(text("height"), 10);
    if (height > 0) {
      config.height = height;
    } else {
      delete config.height;
    }
    const classes = text("classes")
      .split(",")
      .map((entry) => entry.trim())
      .filter(Boolean);
    if (classes.length) {
      config.classes = classes;
    } else {
      delete config.classes;
    }
    assign("search", text("search"));
    config.theme = text("theme") || "auto";
    assign("url_base", text("url_base"));
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config },
        bubbles: true,
        composed: true,
      })
    );
  }
}

function labelled(id, label, control) {
  const row = document.createElement("div");
  row.style.cssText = "display:flex;flex-direction:column;gap:4px;";
  const caption = document.createElement("label");
  caption.setAttribute("for", id);
  caption.textContent = label;
  caption.style.cssText = "font-size:13px;color:var(--secondary-text-color);";
  row.append(caption, control);
  return row;
}

function field(id, label, type, value, placeholder) {
  const input = document.createElement("input");
  input.id = id;
  input.type = type;
  input.value = value == null ? "" : String(value);
  if (placeholder) {
    input.placeholder = placeholder;
  }
  input.style.cssText = "padding:8px;box-sizing:border-box;width:100%;";
  return labelled(id, label, input);
}

function select(id, label, value, options) {
  const el = document.createElement("select");
  el.id = id;
  el.style.cssText = "padding:8px;box-sizing:border-box;width:100%;";
  for (const [optionValue, optionLabel] of options) {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = optionLabel;
    option.selected = String(value) === optionValue;
    el.appendChild(option);
  }
  return labelled(id, label, el);
}

function picker(id, label, selected, entityIds, nameOf) {
  const box = document.createElement("div");
  box.dataset.picker = id;
  box.style.cssText =
    "display:flex;flex-direction:column;gap:4px;max-height:240px;overflow:auto;" +
    "border:1px solid var(--divider-color,#444);border-radius:6px;padding:8px;";
  if (!entityIds.length) {
    const empty = document.createElement("div");
    empty.style.cssText = "color:var(--secondary-text-color);font-size:13px;";
    empty.textContent =
      "No CamStack camera entities found. Export the cameras to Home Assistant on the hub first.";
    box.appendChild(empty);
  }
  for (const entityId of entityIds) {
    const row = document.createElement("label");
    row.style.cssText =
      "display:flex;align-items:center;gap:8px;font-size:14px;";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = entityId;
    input.checked = selected.includes(entityId);
    const caption = document.createElement("span");
    caption.textContent = nameOf(entityId);
    row.append(input, caption);
    box.appendChild(row);
  }
  return labelled(id, label, box);
}

if (!customElements.get(EDITOR_TAG)) {
  customElements.define(EDITOR_TAG, CamstackEventsCardEditor);
}
if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, CamstackEventsCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TAG)) {
  window.customCards.push({
    type: CARD_TAG,
    name: "CamStack Events",
    preview: true,
    description: "A reel of the latest CamStack detections",
    documentationURL: "https://github.com/camstack/homeassistant-component",
  });
}
