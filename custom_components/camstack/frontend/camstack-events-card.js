/**
 * CamStack events card for Lovelace.
 *
 * Frames the viewer's own events embed
 * (`<base>/viewer/camstack/embed/index.html?mode=events&v=1&…`) — the same reel
 * the CamStack apps show.
 *
 * ## Why this one is a URL and the grid card is a handshake
 *
 * `EmbedEventsPage` acquires its config with `{ urlFirst: true }`, so the query
 * params and the `#t=` fragment are honoured INSIDE an iframe. The grid page
 * does not, which is why its card has to answer `embed-ready` instead. Two
 * cards, two mechanisms, because the embed has two.
 *
 * ## The token is no longer in this URL
 *
 * It used to ride the `#t=` fragment. A fragment is never sent to a server, so
 * it stayed out of access logs and Referer headers — but it stayed in the
 * BROWSER: in the address bar, in history, and in every screenshot of the
 * dashboard. Anyone who could read it could use that bearer credential against
 * the hub from outside Home Assistant for the rest of its hour.
 *
 * On the relayed path it bought nothing, because Home Assistant strips the
 * browser's `Authorization` and injects the share token server-side on every
 * forwarded request (`proxy.py`). So the mint is asked WITHOUT `direct`, the
 * answer carries no token, and the frame URL carries no fragment: the embed
 * reads the same-origin PATH it was served under as "the transport
 * authenticates for you" (`transport-auth.ts` in the viewer). A card with an
 * explicit `url_base` frames the hub directly, has no relay in front of it,
 * and still gets `#t=` — the one case that needs it.
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
/**
 * Check a RELAYED frame before trusting it, and say what went wrong.
 *
 * The relay is same-origin with this page, so unlike a frame pointed at the
 * hub its answer can simply be read. Three outcomes matter:
 *
 *  - `ok` — the hub answered; nothing to say.
 *  - `stale-grant` (401/404) — Home Assistant restarted and forgot the grant
 *    behind this URL, while this page kept the token it minted before the
 *    restart. The card must FORGET that token and mint again; without this the
 *    card is dead until someone reloads the browser.
 *  - `unreachable` — the hub did not answer (it is restarting, or down). The
 *    relay's own JSON would otherwise be painted into the card as raw text,
 *    which is what an operator saw during a hub update.
 */
async function probeRelayedFrame(url, fetchImpl = fetch) {
  try {
    const response = await fetchImpl(url, { cache: "no-store", credentials: "same-origin" });
    if (response.ok) {
      return { state: "ok" };
    }
    if (response.status === 401 || response.status === 404) {
      return { state: "stale-grant" };
    }
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body && typeof body.message === "string") detail = body.message;
    } catch {
      // A non-JSON body tells us nothing the status has not already said.
    }
    return { state: "unreachable", detail };
  } catch (err) {
    return { state: "unreachable", detail: (err && err.message) || String(err) };
  }
}

/** Backoff for a hub that is coming back up: a restart takes tens of seconds. */
const RELAY_RETRY_MS = [4000, 8000, 15000, 30000];

const CARD_TAG = "camstack-events-card";
const EDITOR_TAG = "camstack-events-card-editor";
const EMBED_PATH = "/viewer/camstack/embed/index.html";
const EVENTS_URL_VERSION = "1";
const DEFAULT_GALLERY_HEIGHT = 420;

/**
 * The events embed's own option contract, MIRRORED.
 *
 * Every list below is a Zod enum in `camstack/embed/src/embed/events-config.ts`
 * (or `events-share-url.ts` for the attributes). A Lovelace card is plain
 * JavaScript and cannot import TypeScript, so the values are copied — and a
 * copy nobody diffs is a copy that rots. `tests/test_card_editor_options.py`
 * reads this block and compares it to those enums whenever the viewer is
 * checked out beside this repo, and to its own expectation always.
 *
 * They exist so that no option with a finite set of values is ever a text box:
 * the embed rejects an unknown value and the card is then an empty frame with
 * nothing to say about why.
 *
 * The numeric pairs are `[min, max]` from the same schema; `0` as a max means
 * the schema sets none. `classes` is the exception and is a SUGGESTION list:
 * the taxonomy is served by the hub at runtime (`events-taxonomy.ts`), so a
 * class this list has never heard of must still be typeable.
 */
const EMBED_CONTRACT = {
  view: ["reel", "gallery"],
  thumb: ["small", "medium", "large"],
  theme: ["auto", "dark", "light"],
  sort: ["time", "importance"],
  search_mode: ["text", "semantic"],
  fields: ["label", "sublabel", "camera", "time", "badges"],
  attributes: ["face", "plate"],
  classes: ["person", "vehicle", "animal", "audio", "motion"],
  rows: [1, 6],
  columns: [1, 12],
  page_size: [1, 500],
  max: [1, 5000],
  refresh_ms: [0, 600000],
  max_age_ms: [60000, 0],
  semantic_limit: [1, 200],
  semantic_min_score: [0, 1],
};

/** What each `fields` key actually shows, in the operator's words. `badges`
 *  carries the zone chips AND the importance dot — turning it off is how a
 *  dashboard hides importance. */
const FIELD_LABELS = {
  label: "Title chip over the thumbnail",
  sublabel: "Secondary line (detection class / audio labels)",
  camera: "Camera name",
  time: "Time",
  badges: "Zone chips and the importance dot",
};

const ATTRIBUTE_LABELS = {
  face: "Has a face",
  plate: "Has a plate",
};

const DEFAULT_FIELDS = EMBED_CONTRACT.fields.join(",");
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
    // 'pending' until the integration answers; 'absent' ONLY when it
    // answered and named no entry. Absence is never inferred from a failure.
    this._baseState = 'pending';
    this._baseTimer = null;
    this._baseAttempt = 0;
    this._entryId = null;
    this._proxyBase = null;
    this._probeToken = 0;
    this._retryTimer = null;
    this._retryAttempt = 0;
    this._grantRetried = false;
    this._cameras = [];
    this._resolving = false;
    this._renderedUrl = null;
    this._iframe = null;
    this._status = null;
    this._token = null;
    /** True once a mint has ANSWERED for `_tokenKey`. The relayed path has no
     *  token to test for freshness, so the grant's presence is the state. */
    this._grantKnown = false;
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
    this._clearRetryTimer();
    this._clearBaseTimer();
  }

  /**
   * Ask the browser — not the iframe — whether it will show the hub, and
   * replace a refused frame with the reason instead of a white rectangle.
   */
  /**
   * Watch a frame we just mounted.
   *
   * A DIRECT frame can only fail one way the page can see: the browser
   * refusing the hub's certificate. A RELAYED frame is same-origin, so its
   * failure can be read — a forgotten grant is re-minted, an unreachable
   * hub is retried instead of painting the relay's JSON into the card.
   */
  _watchFrame(url, iframe) {
    const token = ++this._probeToken;
    const origin = new URL(url).origin;
    if (!this._isRelayed()) {
      probeHub(origin).then((result) => {
        if (token !== this._probeToken || result !== "unreachable") {
          return;
        }
        iframe.replaceWith(buildUnreachableNotice(origin));
        this._iframe = null;
        this._setStatus(null);
      });
      return;
    }
    probeRelayedFrame(url).then((result) => {
      if (token !== this._probeToken || this._iframe !== iframe) {
        return;
      }
      if (result.state === "ok") {
        this._retryAttempt = 0;
        this._grantRetried = false;
        return;
      }
      if (result.state === "stale-grant") {
        // ONE immediate re-mint, then the ladder: see the grid card for the
        // mint storm this bound retires.
        this._forgetGrant();
        const immediate = !this._grantRetried;
        this._grantRetried = true;
        this._retryFrame(immediate ? 0 : this._nextRetryDelay());
        return;
      }
      this._setStatus(`The CamStack hub did not answer: ${result.detail}. Retrying…`);
      this._retryFrame(this._nextRetryDelay());
    });
  }

  /** Next backoff step, saturating at the last rung. */
  _nextRetryDelay() {
    return RELAY_RETRY_MS[Math.min(this._retryAttempt++, RELAY_RETRY_MS.length - 1)];
  }

  /** Drop the minted credential so the next render asks for a new one. */
  _forgetGrant() {
    this._token = null;
    this._grantKnown = false;
    this._tokenKey = null;
    this._proxyBase = null;
    this._tokenExpiresAt = null;
    this._renderedUrl = null;
  }

  _retryFrame(delayMs) {
    this._clearRetryTimer();
    this._retryTimer = setTimeout(() => {
      this._retryTimer = null;
      this._renderedUrl = null;
      this._render();
    }, delayMs);
  }

  _clearRetryTimer() {
    if (this._retryTimer !== null) {
      clearTimeout(this._retryTimer);
      this._retryTimer = null;
    }
  }

  getCardSize() {
    return Math.ceil(this._frameHeight() / 50);
  }

  /**
   * Ask the integration which hub to point at — and keep asking.
   *
   * Called once per card before 2026-09-06, which made a Home Assistant
   * RESTART fatal to an open dashboard: the entry is not loaded when the
   * first `hass` arrives, this throws, and the card parks on "No CamStack hub
   * configured" until someone reloads the browser. A failure is a WAIT now —
   * not-yet-known must never look like not-installed.
   */
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
      // The endpoint ANSWERED: absence is a fact now, not a guess.
      this._baseState = entry ? 'known' : 'absent';
      this._baseAttempt = 0;
    } catch {
      // Not an answer — Home Assistant may still be starting. Keep waiting and
      // ask again; never turn a failed read into "no hub configured".
      this._resolvedBase = null;
      this._cameras = [];
      this._baseState = 'pending';
      this._scheduleBaseRetry();
    } finally {
      this._resolving = false;
      this._render();
      this._refresh();
    }
  }

  _scheduleBaseRetry() {
    if (this._baseTimer !== null) {
      return;
    }
    const delay = RELAY_RETRY_MS[Math.min(this._baseAttempt++, RELAY_RETRY_MS.length - 1)];
    this._baseTimer = setTimeout(() => {
      this._baseTimer = null;
      this._resolveBase();
    }, delay);
  }

  _clearBaseTimer() {
    if (this._baseTimer !== null) {
      clearTimeout(this._baseTimer);
      this._baseTimer = null;
    }
  }

  _baseUrl() {
    const explicit = (this._config.url_base || "").trim().replace(/\/$/, "");
    return explicit || this._resolvedBase || null;
  }

  /** True when this card was told to frame the hub itself — the only case
   *  where the browser still needs a share token of its own. Read from the
   *  card's config alone, because the mint request has to carry it. */
  _isDirect() {
    return (this._config.url_base || "").trim() !== "";
  }

  /** True when the frame goes through Home Assistant's relay, not to the hub. */
  _isRelayed() {
    return !this._isDirect() && this._proxyBase !== null;
  }

  /** The relay under Home Assistant's origin, or the hub when asked for it. */
  _frameBase() {
    if (this._isRelayed()) {
      return `${window.location.origin}${this._proxyBase}`;
    }
    return this._baseUrl();
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

  /**
   * Resolves to `{ token, proxyBase }` — the relay path, and the credential
   * ONLY when this card frames the hub directly. `token` is null on the
   * relayed path by design; `proxyBase` is what authorises the frame there.
   */
  async _token_for(deviceIds) {
    const key = deviceIds.join(",");
    const fresh =
      this._grantKnown &&
      this._tokenKey === key &&
      (this._tokenExpiresAt === null ||
        this._tokenExpiresAt - Date.now() > TOKEN_RENEW_MARGIN_MS);
    if (fresh) {
      return { token: this._token, proxyBase: this._proxyBase };
    }
    if (this._pendingToken && this._tokenKey === key) {
      return this._pendingToken;
    }
    this._tokenKey = key;
    this._pendingToken = this._hass
      .callApi("POST", "camstack/embed_token", {
        kind: "events-view",
        device_ids: deviceIds,
        // Asked for ONLY by a card that frames the hub itself. Without it the
        // answer carries no token — see the header.
        ...(this._isDirect() ? { direct: true } : {}),
        ...(this._entryId ? { entry_id: this._entryId } : {}),
      })
      .then((result) => {
        this._token = (result && result.token) || null;
        this._grantKnown = true;
        // The same-origin relay path the token is bound to (see the grid
        // card): a frame under it needs no certificate trust in the browser.
        this._proxyBase =
          result && typeof result.proxy_base === "string" && result.proxy_base
            ? result.proxy_base
            : null;
        this._tokenExpiresAt =
          result && typeof result.expires_at === "number"
            ? result.expires_at * 1000
            : null;
        return { token: this._token, proxyBase: this._proxyBase };
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

  /**
   * The `fields=` allow-list.
   *
   * An EMPTY selection is a real ask — the embed reads `fields=` as "images
   * only" — so an array must be sent verbatim even when it is empty, and only
   * an ABSENT `fields` may fall back to the default. A pre-0.5.16 card stored
   * the csv string; it is still honoured so an upgrade changes no dashboard.
   */
  _fields() {
    const raw = this._config.fields;
    if (Array.isArray(raw)) {
      return raw
        .map((entry) => String(entry).trim())
        .filter((entry) => EMBED_CONTRACT.fields.includes(entry))
        .join(",");
    }
    if (typeof raw === "string") {
      return raw.trim();
    }
    return DEFAULT_FIELDS;
  }

  /** One bounded integer from the config, or "" when it is unset or illegal —
   *  the embed rejects the whole URL over an out-of-range value. */
  _bounded(key, [min, max]) {
    const raw = this._config[key];
    if (raw === undefined || raw === null || raw === "") {
      return "";
    }
    const n = Number(raw);
    if (!Number.isFinite(n) || n < min || (max > 0 && n > max)) {
      return "";
    }
    return String(n);
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
    // Always sent: an empty allow-list means "images only", which absence does
    // not — see `_fields`.
    params.set("fields", this._fields());
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
    if (EMBED_CONTRACT.search_mode.includes(this._config.search_mode)) {
      params.set("smode", String(this._config.search_mode));
    }
    if (EMBED_CONTRACT.sort.includes(this._config.sort)) {
      params.set("sort", String(this._config.sort));
    }
    // Every remaining option is a bounded number the embed validates: an
    // out-of-range value fails the WHOLE parse, so it is dropped here instead.
    for (const [key, param] of [
      ["semantic_limit", "slimit"],
      ["semantic_min_score", "sscore"],
      ["page_size", "page"],
      ["max", "max"],
      ["refresh_ms", "refresh"],
      ["max_age_ms", "age"],
    ]) {
      const value = this._bounded(key, EMBED_CONTRACT[key]);
      if (value !== "") {
        params.set(param, value);
      }
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
    let grant;
    try {
      grant = await this._token_for(deviceIds);
    } catch (err) {
      this._buildCard(null);
      this._setStatus(
        `CamStack refused a viewing token: ${(err && err.message) || err}`
      );
      return;
    }
    // A direct frame needs the credential; a relayed one needs the grant, and
    // is broken without it in exactly the same way.
    const usable = this._isDirect() ? Boolean(grant && grant.token) : this._isRelayed();
    if (!usable) {
      this._buildCard(null);
      this._setStatus("CamStack did not issue a viewing token.");
      return;
    }
    // No fragment on the relayed path: the credential is not this browser's to
    // hold. See the header.
    const fragment = this._isDirect() ? `#t=${grant.token}` : "";
    const url = `${this._frameBase()}${EMBED_PATH}?${this._query(deviceIds)}${fragment}`;
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
      // Always watched: `_watchFrame` picks the probe that fits the frame.
      this._watchFrame(url, iframe);
    } else {
      const empty = document.createElement("div");
      empty.style.cssText = "padding:16px;color:var(--secondary-text-color);";
      empty.textContent = this._baseState !== 'absent'
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
      select("rows", "Reel rows", String(config.rows || 1), range(EMBED_CONTRACT.rows)),
      select("columns", "Gallery columns", String(config.columns || "auto"), [
        ["auto", "Automatic"],
        ...range(EMBED_CONTRACT.columns),
      ]),
      field(
        "height",
        "Height in px (empty = fitted to the reel)",
        "number",
        config.height,
        ""
      ),
      select("sort", "Order", config.sort || "time", [
        ["time", "Newest first"],
        ["importance", "Most important first (of what is loaded)"],
      ]),
      checkboxGroup(
        "fields",
        "What each card shows",
        fieldSelection(config),
        EMBED_CONTRACT.fields.map((key) => [key, FIELD_LABELS[key]])
      ),
      checkboxGroup(
        "attributes",
        "Only tracks that carry (nothing ticked = no attribute filter)",
        Array.isArray(config.attributes) ? config.attributes : [],
        EMBED_CONTRACT.attributes.map((key) => [key, ATTRIBUTE_LABELS[key]])
      ),
      suggestField(
        "classes",
        "Object classes (comma separated, empty = all)",
        csv(config.classes),
        "person, vehicle",
        EMBED_CONTRACT.classes
      ),
      field("search", "Search text (optional)", "text", config.search, ""),
      select("search_mode", "How the search text is matched", config.search_mode || "text", [
        ["text", "Text — narrows what is already loaded"],
        ["semantic", "Semantic — a CLIP query over the whole window"],
      ]),
      field(
        "semantic_limit",
        "Semantic results (1–200, semantic search only)",
        "number",
        config.semantic_limit,
        "50"
      ),
      field(
        "semantic_min_score",
        "Minimum semantic score (0–1, semantic search only)",
        "number",
        config.semantic_min_score,
        "0.2"
      ),
      field("page_size", "Rows per page (1–500)", "number", config.page_size, "60"),
      field("max", "Maximum rows held (1–5000)", "number", config.max, "5000"),
      field(
        "refresh_ms",
        "Auto-refresh in ms (0 = off, max 600000)",
        "number",
        config.refresh_ms,
        "0"
      ),
      field(
        "max_age_ms",
        "Only events newer than, in ms (minimum 60000)",
        "number",
        config.max_age_ms,
        "2592000000"
      ),
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
    config.sort = text("sort") === "importance" ? "importance" : "time";
    config.search_mode = text("search_mode") === "semantic" ? "semantic" : "text";
    // Written even when EMPTY: the embed reads an empty allow-list as "images
    // only", and a deleted key would silently restore all five.
    config.fields = readGroup(root, "fields");
    const attributes = readGroup(root, "attributes");
    if (attributes.length) {
      config.attributes = attributes;
    } else {
      delete config.attributes;
    }
    for (const key of [
      "semantic_limit",
      "semantic_min_score",
      "page_size",
      "max",
      "refresh_ms",
      "max_age_ms",
    ]) {
      const [min, max] = EMBED_CONTRACT[key];
      const value = Number(text(key));
      if (text(key) !== "" && Number.isFinite(value) && value >= min && (max <= 0 || value <= max)) {
        config[key] = value;
      } else {
        delete config[key];
      }
    }
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

/**
 * A text input with SUGGESTIONS — the open-set counterpart of `select`.
 *
 * Used for `classes`, whose vocabulary the hub serves at runtime: constraining
 * it to a list this file happens to know would hide every class the hub learns
 * after this release. So the known macro classes are offered and anything is
 * still typeable.
 */
function suggestField(id, label, value, placeholder, suggestions) {
  const wrap = document.createElement("div");
  const input = document.createElement("input");
  input.id = id;
  input.type = "text";
  input.value = value == null ? "" : String(value);
  input.setAttribute("list", `${id}-suggestions`);
  if (placeholder) {
    input.placeholder = placeholder;
  }
  input.style.cssText = "padding:8px;box-sizing:border-box;width:100%;";
  const list = document.createElement("datalist");
  list.id = `${id}-suggestions`;
  for (const suggestion of suggestions) {
    const option = document.createElement("option");
    option.value = suggestion;
    list.appendChild(option);
  }
  wrap.append(input, list);
  return labelled(id, label, wrap);
}

/**
 * A real multi-selection over a CLOSED set — never a comma string.
 *
 * `fields` and `attrs` are sets in the embed's schema. Asking an operator to
 * compose `label,sublabel,camera` by hand is asking for the one typo the embed
 * answers by refusing the whole URL.
 */
function checkboxGroup(id, label, selected, options) {
  const box = document.createElement("div");
  box.dataset.group = id;
  box.style.cssText =
    "display:flex;flex-direction:column;gap:4px;border:1px solid var(--divider-color,#444);" +
    "border-radius:6px;padding:8px;";
  for (const [value, caption] of options) {
    const row = document.createElement("label");
    row.style.cssText = "display:flex;align-items:center;gap:8px;font-size:14px;";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = value;
    input.checked = selected.includes(value);
    const text = document.createElement("span");
    text.textContent = caption;
    row.append(input, text);
    box.appendChild(row);
  }
  return labelled(id, label, box);
}

function readGroup(root, id) {
  const box = root.querySelector(`[data-group="${id}"]`);
  if (!box) {
    return [];
  }
  return Array.from(box.querySelectorAll("input[type=checkbox]"))
    .filter((input) => input.checked)
    .map((input) => input.value);
}

/** `[min, max]` from the embed contract as `[value, label]` select options. */
function range([min, max]) {
  const options = [];
  for (let n = min; n <= max; n += 1) {
    options.push([String(n), String(n)]);
  }
  return options;
}

/** Which `fields` keys are ticked, honouring the pre-0.5.16 csv string and
 *  treating an EMPTY array as the deliberate "images only". */
function fieldSelection(config) {
  const raw = config.fields;
  if (Array.isArray(raw)) {
    return raw.map((entry) => String(entry));
  }
  if (typeof raw === "string") {
    return raw.split(",").map((entry) => entry.trim());
  }
  return EMBED_CONTRACT.fields.slice();
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
