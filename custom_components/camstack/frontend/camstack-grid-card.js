/**
 * CamStack grid card for Lovelace.
 *
 * Frames the viewer's own embed bundle
 * (`<hub>/viewer/camstack/embed/index.html?mode=grid`) — the same player the
 * CamStack apps use — and drives its host handshake.
 *
 * ## Why a handshake and not a share link
 *
 * The embed has two configuration sources: a `#t=`-carrying share URL, and the
 * `embed-ready` → `embed-config` postMessage handshake. The grid page reads the
 * URL source ONLY when it is not inside an iframe (`EmbedGridPage` does not
 * pass `urlFirst`), so a `<iframe src="…#t=…">` grid hangs for ten seconds and
 * then reports "config handshake timed out". A card must answer the handshake.
 *
 * ## Where the token comes from — and when it does NOT come at all
 *
 * Not from here. The integration holds the hub's OAuth credential and mints a
 * short-lived, device-scoped `grid-view` share token on request
 * (`POST /api/camstack/embed_token`). This file never sees an account
 * credential and never stores the one it gets.
 *
 * On the RELAYED path it never sees the share token either. Home Assistant
 * strips the browser's `Authorization` and injects that token server-side on
 * every forwarded request (`proxy.py`), so a copy of it in this page would
 * authenticate nothing and only widen the blast radius: a bearer credential
 * sitting in a dashboard is copyable, and works against the hub from outside
 * Home Assistant for the rest of its hour. So the mint is asked WITHOUT
 * `direct`, the answer carries no token, and the embed is handed a RELATIVE
 * `serverUrl` — which is how it knows the transport authenticates for it
 * (`transport-auth.ts` in the viewer). Only a card with an explicit `url_base`
 * frames the hub directly, and only that one asks for the credential.
 *
 * ## Where the frame points
 *
 * At Home Assistant, not at the hub. The mint answer carries `proxy_base`, a
 * same-origin path under which Home Assistant relays the embed page, tRPC
 * (HTTP and WebSocket) and the media routes to the hub with the token
 * injected (`proxy.py`). A browser therefore never has to trust the hub's
 * certificate — the reason a card was a white rectangle on every phone. The
 * grant behind that path is stable per scope, so a re-minted token never
 * changes the frame URL. An explicit `url_base` on the card bypasses the
 * relay and frames the hub directly; only then is the certificate probed.
 *
 * ## Why the iframe is rebuilt so rarely
 *
 * `set hass` fires several times a second on a busy instance. Rebuilding the
 * iframe restarts every WebRTC session on the wall, so the frame is recreated
 * only when the composed URL changes; a changed device list or layout is sent
 * over the open channel instead.
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
/** The embed posts `embed-ready` within this, or the card says the player did not start. */
const EMBED_READY_TIMEOUT_MS = 12000;

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

const CARD_TAG = "camstack-grid-card";
const EDITOR_TAG = "camstack-grid-card-editor";
const EMBED_PATH = "/viewer/camstack/embed/index.html";
const DEFAULT_HEIGHT = 400;
const DEFAULT_ASPECT = "16:9";
/** Re-mint this long before the token dies, so a stream never drops on expiry. */
const TOKEN_RENEW_MARGIN_MS = 120000;

const ASPECT_RATIOS = {
  "16:9": 16 / 9,
  "4:3": 4 / 3,
  "3:2": 3 / 2,
  "1:1": 1,
};

/** The hub device id Home Assistant records on a CamStack camera entity. */
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

/**
 * The device ids a config asks for, in the order the operator wrote them.
 *
 * `entities` wins over `device_ids` when both are present: an entity id
 * survives a hub renumbering and a raw id does not.
 */
function resolveDeviceIds(hass, config) {
  const ids = [];
  const push = (value) => {
    if (Number.isInteger(value) && value >= 0 && !ids.includes(value)) {
      ids.push(value);
    }
  };
  if (Array.isArray(config.entities)) {
    for (const entityId of config.entities) {
      push(deviceIdOf(hass, entityId));
    }
  }
  if (!ids.length && Array.isArray(config.device_ids)) {
    for (const value of config.device_ids) {
      push(typeof value === "string" ? parseInt(value, 10) : value);
    }
  }
  return ids;
}

/** Tile captions, so the wall reads with Home Assistant's names, not the hub's. */
function resolveLabels(hass, config) {
  const labels = {};
  if (!Array.isArray(config.entities)) {
    return labels;
  }
  for (const entityId of config.entities) {
    const id = deviceIdOf(hass, entityId);
    if (id !== null) {
      labels[String(id)] = friendlyName(hass, entityId);
    }
  }
  return labels;
}

class CamstackGridCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._resolvedBase = null;
    this._entryId = null;
    this._resolving = false;
    this._renderedKey = null;
    this._iframe = null;
    this._status = null;
    this._token = null;
    /** True once a mint has ANSWERED for `_tokenKey`. The relayed path has no
     *  token to test for freshness, so the grant's presence is the state. */
    this._grantKnown = false;
    this._proxyBase = null;
    this._tokenExpiresAt = 0;
    this._tokenKey = null;
    this._pendingToken = null;
    this._sentConfigKey = null;
    this._probeToken = 0;
    this._readyTimer = null;
    this._retryTimer = null;
    this._retryAttempt = 0;
    this._grantRetried = false;
    this._embedReady = false;
    this._onMessage = this._onMessage.bind(this);
  }

  connectedCallback() {
    window.addEventListener("message", this._onMessage);
  }

  disconnectedCallback() {
    window.removeEventListener("message", this._onMessage);
    this._clearReadyTimer();
    this._clearRetryTimer();
    // Invalidates a probe still in flight, so a late answer cannot paint over
    // a card that has since been re-pointed or torn down.
    this._probeToken += 1;
  }

  _clearReadyTimer() {
    if (this._readyTimer !== null) {
      clearTimeout(this._readyTimer);
      this._readyTimer = null;
    }
  }

  /**
   * Ask the browser — not the iframe — whether it will show the hub, and
   * replace a refused frame with the reason. Also bounds the wait for the
   * embed's `embed-ready`: a hub that is reachable but whose player never
   * comes up is reported instead of sitting blank.
   */
  /**
   * Watch a frame we just mounted.
   *
   * A DIRECT frame can only fail one way the page can see: the browser
   * refusing the hub's certificate, which `probeHub` reports. A RELAYED
   * frame is same-origin, so its failure can be read and acted on — a
   * forgotten grant is re-minted, an unreachable hub is retried.
   */
  _watchFrame(frameUrl, iframe) {
    const token = ++this._probeToken;
    const origin = new URL(frameUrl).origin;
    this._embedReady = false;
    if (this._isRelayed()) {
      this._watchRelayed(frameUrl, iframe, token);
    } else {
      probeHub(origin).then((result) => {
        if (token !== this._probeToken || result !== "unreachable") {
          return;
        }
        this._clearReadyTimer();
        iframe.replaceWith(buildUnreachableNotice(origin));
        this._iframe = null;
        this._setStatus(null);
      });
    }
    this._clearReadyTimer();
    this._readyTimer = setTimeout(() => {
      this._readyTimer = null;
      if (token !== this._probeToken || this._iframe !== iframe) {
        return;
      }
      if (!this._embedReady) {
        this._setStatus(
          "The hub is reachable, but its player did not answer. Open the hub in a new tab to see why."
        );
      }
    }, EMBED_READY_TIMEOUT_MS);
  }

  _watchRelayed(frameUrl, iframe, token) {
    probeRelayedFrame(frameUrl).then((result) => {
      if (token !== this._probeToken || this._iframe !== iframe) {
        return;
      }
      if (result.state === "ok") {
        this._retryAttempt = 0;
        this._grantRetried = false;
        return;
      }
      this._clearReadyTimer();
      if (result.state === "stale-grant") {
        // The token this page holds names a grant Home Assistant no longer
        // knows. Forget it and re-render: `_ensureFrame` mints a new one.
        // ONE immediate attempt, then the ladder — a relay that answers 404
        // forever (a card pointed at a removed entry) would otherwise mint a
        // share token per animation frame. Measured: 950 mints in 6 s.
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
    this._tokenExpiresAt = 0;
    this._renderedKey = null;
    this._sentConfigKey = null;
  }

  _retryFrame(delayMs) {
    this._clearRetryTimer();
    this._retryTimer = setTimeout(() => {
      this._retryTimer = null;
      this._renderedKey = null;
      this._render();
    }, delayMs);
  }

  _clearRetryTimer() {
    if (this._retryTimer !== null) {
      clearTimeout(this._retryTimer);
      this._retryTimer = null;
    }
  }

  setConfig(config) {
    this._config = config || {};
    this._renderedKey = null;
    this._sentConfigKey = null;
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
    return Math.ceil(this._frameHeight() / 50);
  }

  // ── hub address ──────────────────────────────────────────────────────────

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
    } catch {
      // The integration is not loaded, or this user may not call it. The card
      // falls back to its own `url_base`, and says so when it has none.
      this._resolvedBase = null;
    } finally {
      this._resolving = false;
      this._render();
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

  /**
   * The origin the frame and its `serverUrl` use: the relay under Home
   * Assistant's own origin, or the hub itself when the operator asked for it.
   */
  _frameBase() {
    if (this._isRelayed()) {
      return `${window.location.origin}${this._proxyBase}`;
    }
    return this._baseUrl();
  }

  /**
   * The base the EMBED addresses the hub with — a same-origin PATH on the
   * relayed route, never the absolute form the iframe `src` needs.
   *
   * The relative shape is the marker: the embed reads it as "a relay on your
   * own origin authenticates for you" and sends no credential (see
   * `transport-auth.ts`). Written absolute it would look like a direct hub
   * address and the embed would insist on a token this page no longer has.
   */
  _serverUrl() {
    return this._isRelayed() ? this._proxyBase : this._baseUrl();
  }

  // ── the credential ───────────────────────────────────────────────────────

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
        kind: "grid-view",
        device_ids: deviceIds,
        // Asked for ONLY by a card that frames the hub itself. Without it the
        // answer carries no token — see the header.
        ...(this._isDirect() ? { direct: true } : {}),
        ...(this._entryId ? { entry_id: this._entryId } : {}),
      })
      .then((result) => {
        this._token = (result && result.token) || null;
        this._grantKnown = true;
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

  // ── the handshake ────────────────────────────────────────────────────────

  _onMessage(event) {
    if (!this._iframe || event.source !== this._iframe.contentWindow) {
      return;
    }
    const data = event.data;
    if (!data || typeof data !== "object") {
      return;
    }
    if (data.type === "embed-ready" && data.mode === "grid") {
      this._embedReady = true;
      this._clearReadyTimer();
      this._sentConfigKey = null;
      this._sendConfig();
      return;
    }
    if (data.type === "state" && data.state === "error") {
      this._setStatus(data.message || "The CamStack embed reported an error.");
    } else if (data.type === "state" && data.state === "ready") {
      this._setStatus(null);
    }
  }

  async _sendConfig() {
    const deviceIds = resolveDeviceIds(this._hass, this._config);
    if (!this._baseUrl() || !deviceIds.length || !this._iframe) {
      return;
    }
    let grant;
    try {
      grant = await this._token_for(deviceIds);
    } catch (err) {
      this._setStatus(
        `CamStack refused a viewing token: ${(err && err.message) || err}`
      );
      return;
    }
    const base = this._frameBase();
    const serverUrl = this._serverUrl();
    // A direct frame needs the credential; a relayed one needs the grant, and
    // is broken without it in exactly the same way.
    const usable = this._isDirect() ? Boolean(grant && grant.token) : this._isRelayed();
    if (!usable || !base || !serverUrl || !this._iframe) {
      this._setStatus("CamStack did not issue a viewing token.");
      return;
    }
    const config = {
      serverUrl,
      ...(grant && grant.token ? { token: grant.token } : {}),
      devices: deviceIds,
      layout: this._layout(),
      quality: this._config.quality || "auto",
      showName: this._config.show_names !== false,
      showBadges: this._config.show_badges !== false,
      muted: this._config.muted !== false,
      paused: false,
      labels: resolveLabels(this._hass, this._config),
      ...(this._config.active_only === true ? { activeOnly: true } : {}),
      ...(this._config.show_boxes === true ? { showBoxes: true } : {}),
    };
    const key = JSON.stringify({ ...config, token: undefined });
    if (key === this._sentConfigKey) {
      return;
    }
    this._sentConfigKey = key;
    // Targeted at one origin, never "*": on the direct path this message
    // carries the token, and on the relayed path it still names the cameras
    // this dashboard shows.
    this._iframe.contentWindow.postMessage(
      { type: "embed-config", config },
      new URL(base, window.location.origin).origin
    );
  }

  _layout() {
    const raw = this._config.layout;
    if (raw === undefined || raw === null || raw === "auto" || raw === "") {
      return "auto";
    }
    const columns = typeof raw === "string" ? parseInt(raw, 10) : raw;
    return Number.isInteger(columns) && columns >= 1 ? columns : "auto";
  }

  // ── rendering ────────────────────────────────────────────────────────────

  _frameHeight() {
    return Number(this._config.height) > 0
      ? Number(this._config.height)
      : DEFAULT_HEIGHT;
  }

  _frameStyle() {
    const aspect = this._config.aspect_ratio || DEFAULT_ASPECT;
    if (aspect !== "none" && ASPECT_RATIOS[aspect]) {
      // `aspect-ratio` keeps the wall the right shape on a phone and on a wall
      // display without the operator retyping a pixel height per breakpoint.
      return `width:100%;aspect-ratio:${aspect.replace(":", " / ")};border:none;display:block;border-radius:8px;`;
    }
    return `width:100%;height:${this._frameHeight()}px;border:none;display:block;border-radius:8px;`;
  }

  _setStatus(text) {
    if (!this._status) {
      return;
    }
    this._status.textContent = text || "";
    this._status.style.display = text ? "block" : "none";
  }

  _render() {
    const deviceIds = this._hass
      ? resolveDeviceIds(this._hass, this._config)
      : [];
    if (!this._baseUrl() || !this._hass) {
      this._mountFrame(
        null,
        this._resolving
          ? "Waiting for the CamStack integration…"
          : "No CamStack hub configured. Add the CamStack integration, or set url_base on this card."
      );
      return;
    }
    if (!deviceIds.length) {
      this._mountFrame(
        null,
        "No CamStack cameras selected. Pick camera entities, or set device_ids."
      );
      return;
    }
    this._ensureFrame(deviceIds);
  }

  /**
   * The frame needs the grant before it can be pointed anywhere: the relay
   * path comes with the token. Both are cached, so this is cheap on the
   * re-render `set hass` fires several times a second.
   */
  async _ensureFrame(deviceIds) {
    let grant;
    try {
      grant = await this._token_for(deviceIds);
    } catch (err) {
      this._mountFrame(
        null,
        `CamStack refused a viewing token: ${(err && err.message) || err}`
      );
      return;
    }
    const ready = this._isDirect() ? Boolean(grant && grant.token) : this._isRelayed();
    const base = ready ? this._frameBase() : null;
    if (!base) {
      this._mountFrame(null, "CamStack did not issue a viewing token.");
      return;
    }
    // Only the frame's OWN inputs are in the key. The device list travels over
    // the open channel; putting it here would restart every stream on a rename.
    this._mountFrame(`${base}${EMBED_PATH}?mode=grid`, null);
    if (this._iframe) {
      this._iframe.style.cssText = this._frameStyle();
      this._sendConfig();
    }
  }

  /** Rebuilds only when the frame URL — or the text shown in its place — changes. */
  _mountFrame(frameUrl, emptyText) {
    const key = frameUrl || `empty:${emptyText}`;
    if (key === this._renderedKey) {
      return;
    }
    this._renderedKey = key;
    this._sentConfigKey = null;
    this._buildCard(frameUrl, emptyText);
  }

  _buildCard(frameUrl, emptyText) {
    const card = document.createElement("ha-card");
    if (this._config.title) {
      card.setAttribute("header", this._config.title);
    }
    const wrapper = document.createElement("div");
    wrapper.style.cssText = "position:relative;padding:8px;";

    if (!frameUrl) {
      const empty = document.createElement("div");
      empty.style.cssText = "padding:16px;color:var(--secondary-text-color);";
      empty.textContent = emptyText || "";
      wrapper.appendChild(empty);
      card.appendChild(wrapper);
      this.shadowRoot.replaceChildren(card);
      this._iframe = null;
      this._status = null;
      return;
    }

    const iframe = document.createElement("iframe");
    iframe.src = frameUrl;
    iframe.allow = "autoplay; fullscreen; microphone";
    iframe.style.cssText = this._frameStyle();
    wrapper.appendChild(iframe);

    const status = document.createElement("div");
    status.style.cssText =
      "position:absolute;left:16px;right:16px;bottom:16px;padding:10px;display:none;" +
      "background:rgba(180,0,0,0.92);color:#fff;font-size:13px;border-radius:6px;z-index:2;";
    wrapper.appendChild(status);

    card.appendChild(wrapper);
    this._iframe = iframe;
    this._status = status;
    this.shadowRoot.replaceChildren(card);
    // Always watched: `_watchFrame` picks the probe that fits the frame — the
    // certificate probe for a direct hub, the readable answer for a relay.
    this._watchFrame(frameUrl, iframe);
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TAG);
  }

  static getStubConfig(hass) {
    const entities = Object.keys((hass && hass.states) || {})
      .filter((id) => id.startsWith("camera.") && deviceIdOf(hass, id) !== null)
      .slice(0, 4);
    return { entities, aspect_ratio: DEFAULT_ASPECT };
  }
}

// ── the editor ─────────────────────────────────────────────────────────────

class CamstackGridCardEditor extends HTMLElement {
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

  /** Every CamStack camera entity, which is the only thing worth offering. */
  _cameras() {
    const states = (this._hass && this._hass.states) || {};
    return Object.keys(states)
      .filter((id) => id.startsWith("camera.") && deviceIdOf(this._hass, id) !== null)
      .sort();
  }

  _render() {
    const config = this._config;
    const wrapper = document.createElement("div");
    wrapper.style.cssText = "padding:8px;display:flex;flex-direction:column;gap:14px;";

    wrapper.append(
      textField("title", "Title (optional)", config.title, ""),
      cameraPicker("entities", "Cameras", config.entities || [], this._cameras(), (id) =>
        friendlyName(this._hass, id)
      ),
      selectField("layout", "Columns", config.layout ?? "auto", [
        ["auto", "Automatic"],
        ["1", "1"],
        ["2", "2"],
        ["3", "3"],
        ["4", "4"],
        ["5", "5"],
        ["6", "6"],
      ]),
      selectField("aspect_ratio", "Shape", config.aspect_ratio || DEFAULT_ASPECT, [
        ["16:9", "16:9"],
        ["4:3", "4:3"],
        ["3:2", "3:2"],
        ["1:1", "Square"],
        ["none", "Fixed height (px)"],
      ]),
      numberField("height", "Height in px (used when shape is fixed)", config.height ?? DEFAULT_HEIGHT),
      selectField("quality", "Stream quality", config.quality || "auto", [
        ["auto", "Automatic"],
        ["high", "High"],
        ["mid", "Medium"],
        ["low", "Low"],
      ]),
      checkboxField("show_names", "Show the camera name on each tile", config.show_names !== false),
      checkboxField("show_boxes", "Show detection boxes", config.show_boxes === true),
      checkboxField("active_only", "Only cameras that are currently active", config.active_only === true),
      textField(
        "url_base",
        "Hub URL override (optional)",
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
    setOrDelete(config, "title", readText(root, "title"));
    config.entities = readChecked(root, "entities");
    const layout = readText(root, "layout");
    config.layout = layout === "auto" ? "auto" : parseInt(layout, 10);
    config.aspect_ratio = readText(root, "aspect_ratio") || DEFAULT_ASPECT;
    config.height = parseInt(readText(root, "height"), 10) || DEFAULT_HEIGHT;
    config.quality = readText(root, "quality") || "auto";
    config.show_names = readBool(root, "show_names");
    setBoolOrDelete(config, "show_boxes", readBool(root, "show_boxes"));
    setBoolOrDelete(config, "active_only", readBool(root, "active_only"));
    setOrDelete(config, "url_base", readText(root, "url_base"));
    this.dispatchEvent(
      new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true })
    );
  }
}

// ── editor field helpers (shared shape with the events card editor) ─────────

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

function textField(id, label, value, placeholder) {
  const input = document.createElement("input");
  input.id = id;
  input.type = "text";
  input.value = value == null ? "" : String(value);
  if (placeholder) {
    input.placeholder = placeholder;
  }
  input.style.cssText = "padding:8px;box-sizing:border-box;width:100%;";
  return labelled(id, label, input);
}

function numberField(id, label, value) {
  const input = document.createElement("input");
  input.id = id;
  input.type = "number";
  input.value = value == null ? "" : String(value);
  input.style.cssText = "padding:8px;box-sizing:border-box;width:100%;";
  return labelled(id, label, input);
}

function selectField(id, label, value, options) {
  const select = document.createElement("select");
  select.id = id;
  select.style.cssText = "padding:8px;box-sizing:border-box;width:100%;";
  for (const [optionValue, optionLabel] of options) {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = optionLabel;
    option.selected = String(value) === optionValue;
    select.appendChild(option);
  }
  return labelled(id, label, select);
}

function checkboxField(id, label, checked) {
  const row = document.createElement("label");
  row.style.cssText = "display:flex;align-items:center;gap:8px;font-size:14px;";
  const input = document.createElement("input");
  input.id = id;
  input.type = "checkbox";
  input.checked = !!checked;
  const caption = document.createElement("span");
  caption.textContent = label;
  row.append(input, caption);
  return row;
}

/**
 * A checkbox per camera rather than a free-text list of entity ids.
 *
 * The previous editor asked the operator to type comma-separated entity ids,
 * which is exactly the field where a typo produces an empty grid and no error.
 */
function cameraPicker(id, label, selected, entityIds, nameOf) {
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
    row.style.cssText = "display:flex;align-items:center;gap:8px;font-size:14px;";
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

function readText(root, id) {
  const el = root.getElementById(id);
  return el ? String(el.value).trim() : "";
}

function readBool(root, id) {
  const el = root.getElementById(id);
  return el ? !!el.checked : false;
}

function readChecked(root, pickerId) {
  const box = root.querySelector(`[data-picker="${pickerId}"]`);
  if (!box) {
    return [];
  }
  return Array.from(box.querySelectorAll("input[type=checkbox]"))
    .filter((input) => input.checked)
    .map((input) => input.value);
}

function setOrDelete(config, key, value) {
  if (value) {
    config[key] = value;
  } else {
    delete config[key];
  }
}

function setBoolOrDelete(config, key, value) {
  if (value) {
    config[key] = true;
  } else {
    delete config[key];
  }
}

if (!customElements.get(EDITOR_TAG)) {
  customElements.define(EDITOR_TAG, CamstackGridCardEditor);
}
if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, CamstackGridCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TAG)) {
  window.customCards.push({
    type: CARD_TAG,
    name: "CamStack Grid",
    preview: true,
    description: "A live CamStack camera wall, pointed at the configured hub",
    documentationURL: "https://github.com/camstack/homeassistant-component",
  });
}
