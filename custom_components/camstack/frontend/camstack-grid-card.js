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
 * over the open channel instead — `_publishWall`.
 *
 * That last clause was a PROMISE, not a fact, until 2026-09-06: the card
 * re-sent `embed-config`, which `acquireConfig` resolves exactly once, so a
 * changed column count or camera list reached the wall only when something
 * else happened to rebuild the frame. Every option this card offers must ride
 * a COMMAND (`_postCommand`), and be re-pushed when the page reports `ready`
 * — a remounted page seeded itself from a config that is stale by every edit
 * since it was sent.
 *
 * ## Why this card has to answer the tiles
 *
 * The embed is HOST-DRIVEN: an in-tile button changes nothing by itself. It
 * posts an intent — `audioToggle`, `pauseToggle`, `tileOpen` — and waits for
 * the host to push the resulting state back over the same channel
 * (`setAudioOn`, `setPaused`, …). That is deliberate on the viewer's side: the
 * audio and pause SETS belong to the host so its own chrome can reflect them,
 * and a page that flipped them locally would disagree with the host the moment
 * anything re-pushed them. The full vocabulary is
 * `camstack/embed/src/embed/grid-messages.ts` (out) and the `onHostCommand`
 * switch in `pages/EmbedGridPage.tsx` (in).
 *
 * Until 2026-09-06 this card read `embed-ready` and `state` and dropped the
 * rest, which is why an operator reported cards that render perfectly and
 * "buttons that do nothing". Every tile intent landed in a handler that had no
 * case for it. `_onMessage` now names EVERY type in the contract: the ones this
 * surface can answer, and the ones a Lovelace dashboard has no honest answer
 * for — spelled out, with the reason, rather than left to fall through.
 *
 * The host-owned sets live on the element (`_audioOnIds`, `_pausedIds`), not in
 * the config or a per-render closure: `set hass` would otherwise un-mute a tile
 * the operator just muted. They seed a fresh embed's config and are re-pushed
 * whenever the page reports `ready` again, because a reconnect remounts it with
 * a seed that is stale by exactly the toggles made since.
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
 * The bottom control bar and the highlight vocabulary it edits — the viewer's
 * grid bar, mirrored. Same sibling-import discipline as the probe: it is not a
 * Lovelace resource of its own, it is asked for with the card's own `?v=`.
 */
const {
  buildControlBar,
  talkState,
  highlightFromConfig,
  withHighlightDefaults,
  DETECTION_MACRO_CLASSES,
  DETECTION_HOLD_OPTIONS,
  LAYOUT_OPTIONS,
} = await import(`./camstack-grid-controls.js${VERSION_QUERY}`);
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

/**
 * The grid embed's option contract, MIRRORED — a Lovelace card is plain
 * JavaScript and cannot import the viewer's TypeScript, so the values are
 * copied from `camstack/embed/src/embed/embed-grid-config.ts` and
 * `tests/test_card_editor_options.py` diffs the copy against those Zod schemas
 * whenever the viewer is checked out beside this repo. It exists so that no
 * option with a finite set of values is ever a text box: the embed normalises
 * or refuses an unknown one, and the card has nothing to say about why.
 *
 * `quality` is `gridQualitySchema` verbatim (the system profile enum plus
 * `auto`); `layout` is the fixed column count `gridLayoutSchema` accepts.
 * `max_visible` is LEGACY: it is read only to derive a layout mode for a
 * dashboard written before `layout_mode` existed — see `_layoutMode`.
 */
const EMBED_CONTRACT = {
  quality: ["auto", "high", "mid", "low"],
  layout: [1, 12],
  max_visible: [1, 12],
  rows: [1, 8],
  max_rows: [1, 8],
};

/**
 * HOW the wall is arranged — one control, three answers, and each answer owns
 * its own fields.
 *
 * What this replaces: `max_visible` and `layout` used to disable each other,
 * and each said so in its own label ("not used while the wall scrolls", "used
 * when shape is fixed"). The operator had to hold a state machine in their head
 * to know which of the four controls in front of them were live. Worse, the
 * only scroll on offer was a single HORIZONTAL row, so "show me two rows and
 * let the rest scroll" — the most ordinary wall there is — could not be said.
 *
 *  - `fit`   every camera visible, nothing scrolls. The wall shrinks to fit.
 *  - `flow`  a camera is never bigger than `max_tile_width`; as many columns as
 *            fit across the card, `max_rows` of them visible, the rest scrolls.
 *  - `fixed` exactly `columns` x `rows` visible, the rest scrolls.
 *
 * `flow` and `fixed` both scroll VERTICALLY, which is the direction a wall of
 * rows scrolls and the direction a mouse wheel and a thumb already do.
 */
const LAYOUT_MODES = ["auto", "fit", "flow", "fixed"];

/**
 * The `auto` arrangement's column rule — the viewer's, copied by SEMANTICS.
 *
 * Source: `camstack/src/hooks/use-responsive.ts` → `useGridColumns`, the one
 * answer the app gives to "how many cameras across, on a box this wide". Its
 * numbers are reproduced verbatim so that a wall in Home Assistant and the same
 * wall in the app break into columns at the same widths:
 *
 *   • under 700   — ONE column in portrait, TWO in landscape. Orientation, not
 *                   arithmetic: a phone held upright gets one readable camera,
 *                   and turning it sideways gets two. This is the part a pixel
 *                   formula gets wrong.
 *   • 700–1100    — three.
 *   • 1100 and up — four, plus one more per 360, capped at six.
 *
 * The narrowest camera the rule can produce is therefore ~233 px (700 / 3) and
 * the widest before it splits again is ~366 px — that band IS the "acceptable
 * horizontal size" the mode promises, and {@link AUTO_MIN_TILE_WIDTH} states
 * its floor so a test can hold the rule to it.
 */
const AUTO_TABLET_WIDE_MIN = 700;
const AUTO_DESKTOP_MIN = 1100;
const AUTO_WIDE_DESKTOP_STEP = 360;
const AUTO_MAX_COLUMNS = 6;
/** The floor the rule above can never go under, in CSS px. */
const AUTO_MIN_TILE_WIDTH = 233;

/**
 * How many cameras across, for a box this wide in a window of this shape.
 *
 * `landscape` is the WINDOW's, not the card's: a Lovelace card has no height of
 * its own to be landscape about (it grows to fit its wall), while "the phone is
 * turned sideways" is exactly the fact the viewer's rule keys on.
 *
 * Pure, so `test_card_grid_controls.py` can hold it against the viewer's.
 */
function autoColumns(cardWidth, landscape) {
  const width = cardWidth > 0 ? cardWidth : 0;
  if (width <= 0) return 1;
  if (width < AUTO_TABLET_WIDE_MIN) return landscape ? 2 : 1;
  if (width < AUTO_DESKTOP_MIN) return 3;
  const extra = Math.floor((width - AUTO_DESKTOP_MIN) / AUTO_WIDE_DESKTOP_STEP);
  return Math.min(AUTO_MAX_COLUMNS, 4 + extra);
}

/**
 * How much of the scroller a finger can reach, in CSS px.
 *
 * MEASURED, not guessed. The embed puts a `touch-action: none` overlay over
 * every tile (`embed/src/components/ZoomPan.tsx:160`, plus a
 * `setPointerCapture` on pointerdown) so it can pinch and pan a camera. A touch
 * that lands on the frame is therefore consumed by the framed document and the
 * host's scroller never sees it — with a plain frame the same scroller moves
 * 391 px for the same drag, and with the embed's overlay it moves 0. That is
 * the whole of "the card does not scroll on mobile", and it is not this card's
 * CSS: nothing the host can set reaches inside the frame.
 *
 * So a scrolling wall keeps a strip of the scroller UNCOVERED, where a drag
 * still belongs to the host. Measured at 24 px: 684 px of scroll for the drag
 * that produced 0 without it.
 *
 * It costs width, so it exists only while there is something to scroll — and
 * the `auto` arrangement, which is the default, never scrolls at all.
 */
const SCROLL_GUTTER_PX = 24;

/** Default cap on one camera's width in `flow`, in CSS px. */
const DEFAULT_MAX_TILE_WIDTH = 480;
const MAX_TILE_WIDTH_RANGE = [160, 1920];

/** Clamp `value` into `[min, max]`, or `fallback` when it is not a number. */
function clampInt(value, [min, max], fallback) {
  const n = typeof value === "string" ? parseInt(value, 10) : value;
  if (!Number.isInteger(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

/**
 * The wall's geometry: how many columns, how many rows exist, how many are
 * shown, and therefore whether it scrolls.
 *
 * Pure, and it takes the card's MEASURED width because that is the only honest
 * source for `flow`: "no camera wider than 480 px" is a statement about pixels
 * on the glass, and a card in a sidebar and a card on a wall display are given
 * very different amounts of them. A guess here would be a promise the layout
 * cannot keep.
 *
 * `cardWidth` of 0 (not yet laid out) resolves to a single column rather than
 * to a division by zero; the ResizeObserver re-runs this the moment a real
 * width exists, and the frame is restyled, never rebuilt.
 */
function planGrid({ mode, total, cardWidth, columns, rows, maxRows, maxTileWidth, landscape }) {
  const count = Math.max(1, total | 0);
  const colCap = EMBED_CONTRACT.layout[1];

  if (mode === "auto") {
    // Fill the width with as many cameras as stay readable, take whatever rows
    // that needs, and never scroll — the host's scroller is the one surface a
    // finger cannot reach on a phone (see SCROLL_GUTTER_PX), so the default
    // arrangement is the one that never asks it to.
    const cols = Math.min(colCap, autoColumns(cardWidth, landscape === true), count);
    const totalRows = Math.ceil(count / cols);
    return { columns: cols, totalRows, visibleRows: totalRows, scrolls: false };
  }

  if (mode === "flow") {
    const width = cardWidth > 0 ? cardWidth : 0;
    const cap = clampInt(maxTileWidth, MAX_TILE_WIDTH_RANGE, DEFAULT_MAX_TILE_WIDTH);
    // CEIL, not floor: `max_tile_width` is a CAP, so we want the FEWEST columns
    // whose tiles still fit under it. Floor gives the most columns whose tiles
    // are at least that wide — the opposite — and a 1400 px card with a 480 px
    // cap would have laid out two tiles of 700 px each, breaking the only
    // promise this setting makes.
    const fit = width > 0 ? Math.ceil(width / cap) : 1;
    const cols = Math.min(colCap, Math.max(1, fit), count);
    const totalRows = Math.ceil(count / cols);
    const visibleRows = Math.min(totalRows, clampInt(maxRows, EMBED_CONTRACT.max_rows, 2));
    return { columns: cols, totalRows, visibleRows, scrolls: totalRows > visibleRows };
  }

  if (mode === "fixed") {
    const cols = Math.min(colCap, clampInt(columns, EMBED_CONTRACT.layout, 3));
    const totalRows = Math.ceil(count / cols);
    const visibleRows = Math.min(totalRows, clampInt(rows, EMBED_CONTRACT.rows, 2));
    return { columns: cols, totalRows, visibleRows, scrolls: totalRows > visibleRows };
  }

  // `fit`: everything on screen. The embed picks the shape unless the operator
  // pinned a column count, and nothing ever scrolls.
  const pinned = columns === "auto" || columns === undefined || columns === null || columns === ""
    ? "auto"
    : clampInt(columns, EMBED_CONTRACT.layout, null);
  const cols = pinned === "auto" || pinned === null ? "auto" : pinned;
  const totalRows = cols === "auto" ? 1 : Math.ceil(count / cols);
  return { columns: cols, totalRows, visibleRows: totalRows, scrolls: false };
}

/** The words for `gridQualitySchema`. An unknown tier keeps its own id rather
 *  than showing a blank option — the contract test is what catches the drift. */
const QUALITY_LABELS = {
  auto: "Automatic",
  high: "High",
  mid: "Medium",
  low: "Low",
};

/** `[min, max]` from the contract as `[value, label]` select options. */
function countOptions([min, max]) {
  const options = [];
  for (let n = min; n <= max; n += 1) {
    options.push([String(n), String(n)]);
  }
  return options;
}

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

/**
 * The camera entity a hub device id came back as — the reverse of `deviceIdOf`.
 *
 * Only the entities the card was configured with are searched: a dashboard may
 * show four cameras out of forty, and a tile intent must not open a camera this
 * card does not display. A card configured with raw `device_ids` has no entity
 * to find, and the caller says so rather than opening something arbitrary.
 */
function entityForDevice(hass, config, deviceId) {
  if (!Array.isArray(config.entities)) {
    return null;
  }
  for (const entityId of config.entities) {
    if (deviceIdOf(hass, entityId) === deviceId) {
      return entityId;
    }
  }
  return null;
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
    // 'pending' until the integration answers; 'absent' ONLY when it
    // answered and named no entry. Absence is never inferred from a failure.
    this._baseState = 'pending';
    this._baseTimer = null;
    this._baseAttempt = 0;
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
    this._scroller = null;
    this._widthObserver = null;
    /** What this card has already pushed on the open channel, per command — so
     *  a `set hass` storm re-sends nothing and a real change re-sends once. */
    this._publishedWall = {};
    /**
     * The host-owned tile state. The embed holds no opinion of its own about
     * either: its buttons post an intent and render what comes back.
     *
     * On the ELEMENT, deliberately. `set hass` re-enters `_render` several
     * times a second and a set rebuilt there would silence a tile the operator
     * unmuted a frame earlier — and re-seeding it through the config would mean
     * rebuilding the iframe, which restarts every WebRTC session on the wall.
     */
    this._audioOnIds = new Set();
    this._pausedIds = new Set();
    /**
     * The bar's SESSION overrides of what the card config says.
     *
     * `null` means "the config answers". They are on the element for the same
     * reason the two sets above are — and they are session-scoped because the
     * persisted document here is the Lovelace config, which a rendered card
     * cannot write (only the config element can, and it has no frame). A press
     * therefore lasts until the dashboard is reloaded, and `setConfig` drops
     * them: the saved document has just spoken and it wins.
     */
    this._sessionQuality = null;
    this._sessionColumns = null;
    this._sessionActiveOnly = null;
    this._sessionShowBoxes = null;
    this._sessionHighlightOn = null;
    this._sessionHighlight = null;
    /**
     * Whether the credential this card holds carries talk-back — `true`,
     * `false`, or `null` for "no mint has answered yet, or the integration is
     * older than the flag". Three states on purpose: not-yet-known must never
     * be drawn as not-granted (D315), because the two have different fixes.
     */
    this._talkGranted = null;
    this._bar = null;
    this._onMessage = this._onMessage.bind(this);
  }

  connectedCallback() {
    window.addEventListener("message", this._onMessage);
    this._observeWidth();
  }

  /**
   * Watch the card's own width.
   *
   * `flow` caps a camera's width in PIXELS, so the column count is a function
   * of how wide this card actually is — which changes with the dashboard
   * column, the sidebar, and the phone being turned. Restyles, never rebuilds:
   * a rebuilt iframe renegotiates every WebRTC session on the wall.
   */
  _observeWidth() {
    if (this._widthObserver || typeof ResizeObserver !== "function") {
      return;
    }
    let lastWidth = -1;
    this._widthObserver = new ResizeObserver(() => {
      const width = this._cardWidth();
      // Sub-pixel jitter must not restyle on every animation frame.
      if (Math.abs(width - lastWidth) < 1) {
        return;
      }
      lastWidth = width;
      this._restyleFrame();
    });
    this._widthObserver.observe(this);
  }

  /** Re-apply the box styles from the current plan. Never touches the URL. */
  _restyleFrame() {
    if (this._iframe) {
      this._iframe.style.cssText = this._frameStyle();
    }
    if (this._scroller) {
      this._scroller.style.cssText = this._scrollerStyle();
    }
  }

  disconnectedCallback() {
    window.removeEventListener("message", this._onMessage);
    if (this._widthObserver) {
      this._widthObserver.disconnect();
      this._widthObserver = null;
    }
    this._clearReadyTimer();
    this._clearRetryTimer();
    this._clearBaseTimer();
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

  /**
   * A new config. It does NOT rebuild the frame.
   *
   * It used to: `_renderedKey = null` forced `_mountFrame` to build a fresh
   * iframe, so every touch of a setting in the card editor renegotiated every
   * WebRTC session on the wall — which is the one thing this card's whole
   * mount discipline exists to avoid, paid on the surface where the operator
   * is watching the wall change. The frame's own inputs (the hub, the grant)
   * are in `_mountFrame`'s key already, so a change to one of them still
   * rebuilds; everything else now travels as a command (`_publishWall`).
   */
  setConfig(config) {
    this._config = config || {};
    this._sentConfigKey = null;
    // The saved document has changed; the session's opinions of it are stale.
    this._sessionQuality = null;
    this._sessionColumns = null;
    this._sessionActiveOnly = null;
    this._sessionShowBoxes = null;
    this._sessionHighlightOn = null;
    this._sessionHighlight = null;
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

  /**
   * Ask the integration which hub to point at — and keep asking.
   *
   * Called once per card before 2026-09-06, which made a Home Assistant
   * RESTART fatal to an open dashboard: the entry is not loaded when the
   * first `hass` arrives, this throws, `_resolvedBase` stays null, and the
   * card parks on "No CamStack hub configured" until someone reloads the
   * browser. Now a failure is a WAIT, retried on the relay's ladder, and only
   * an answer that named no entry may say the hub is absent — not-yet-known
   * must never look like not-installed.
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
      // The endpoint ANSWERED: absence is a fact now, not a guess.
      this._baseState = entry ? 'known' : 'absent';
      this._baseAttempt = 0;
    } catch {
      // Not an answer — Home Assistant may still be starting. Keep waiting and
      // ask again; never turn a failed read into "no hub configured".
      this._resolvedBase = null;
      this._baseState = 'pending';
      this._scheduleBaseRetry();
    } finally {
      this._resolving = false;
      this._render();
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
  /**
   * Does this CARD want talk-back on its token?
   *
   * A card may only DECLINE. The grant is the integration's option (`talk` in
   * the entry's options), because a Lovelace config is editable by anyone who
   * can edit a dashboard and the mint endpoint is open to every authenticated
   * user — a tick box here alone would mean that editing a dashboard hands you
   * the microphone of the house. So the card asks, and the entry answers.
   */
  _talkAsked() {
    return this._config.talk !== false;
  }

  async _token_for(deviceIds) {
    // The ask is part of the key: a token minted WITH talk-back is a different
    // credential from one minted without, and the integration caches them
    // apart. A card that changed its mind must not keep the old one.
    const key = `${deviceIds.join(",")}|${this._talkAsked() ? "talk" : "no-talk"}`;
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
        // Omitted when the card declines, so the request stays byte-identical
        // to the one a card sent before talk-back existed.
        ...(this._talkAsked() ? { talk: true } : {}),
      })
      .then((result) => {
        this._token = (result && result.token) || null;
        this._grantKnown = true;
        // What the token GOT, read from the answer — never assumed from what
        // was asked. `undefined` (an integration older than this feature) is
        // not `false`: it is UNKNOWN, and the control says so rather than
        // blaming an option that may not exist yet.
        this._talkGranted =
          result && typeof result.talk === "boolean" ? result.talk : null;
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
    switch (data.type) {
      case "state":
        if (data.state === "error") {
          this._setStatus(data.message || "The CamStack embed reported an error.");
        } else if (data.state === "ready") {
          this._setStatus(null);
          // The page has (re)mounted. It seeded itself from the config we sent,
          // which is stale by every toggle made since — and after a reconnect
          // that config may be the one from before the operator muted a tile.
          this._republishHostState();
        }
        return;

      // ── the tile intents this dashboard can answer ───────────────────────
      case "tileTap":
      case "tileLongPress":
      case "tileOpen":
        // "Show me this camera, bigger." Home Assistant's answer to that is the
        // entity's more-info dialog: it is the surface every other camera card
        // opens, it is already themed and translated, and it carries the
        // camera's own controls. The rect `tileTap`/`tileLongPress` bring is for
        // a native host anchoring an overlay on top of the WebView; a dashboard
        // has no such overlay, so only the device id is used.
        this._openCamera(data.deviceId);
        return;
      case "audioToggle":
        // Audio is a COMBINABLE, host-owned SET (more than one camera may be
        // audible). The button states an intent; we flip our set and push the
        // whole thing back, which is the only thing the embed accepts.
        this._toggleHostSet(this._audioOnIds, data.deviceId, "setAudioOn");
        return;
      case "pauseToggle":
        this._toggleHostSet(this._pausedIds, data.deviceId, "setPaused");
        return;

      // ── the intents a Lovelace card has no honest answer for ─────────────
      //
      // Ignored, and named here rather than left to fall through: a reader has
      // to be able to tell "we decided not to" from "nobody noticed".
      case "ptzOpen":
        // The PTZ button is drawn only for cameras the host declared in
        // `ptzIds`, and this card declares none — because the controls
        // themselves are the app's own component and a dashboard has nowhere to
        // mount them. Home Assistant exposes CamStack PTZ as entity services,
        // not as a surface a card can open. So the button never appears; if a
        // future embed posts this anyway, doing nothing is the honest answer.
        return;
      case "cellPanelOpen":
        // 'devices' / 'viewOptions' / 'actions' are the viewer app's own
        // panels, shared with its single-camera view. None of them exist here:
        // the linked-device actions are Home Assistant entities the operator
        // puts on the dashboard themselves, and the view options ARE this
        // card's config, which is edited in the card editor. Faking a panel
        // that half-works would be worse than the button doing nothing, and the
        // camera itself is one tap away through more-info.
        return;

      // ── configuration edits, which a rendered card does not own ──────────
      //
      // `tileRemove` / `tileResize` are only drawn when the host declared the
      // wall `editable`, and this card never does: its membership and shape are
      // the Lovelace config, and a rendered card cannot write that (only the
      // config element can, and it has no frame). Acting on them would edit a
      // dashboard nobody asked to edit — and then lose the edit on the next
      // reload, because it was never saved.
      case "tileRemove":
      case "tileResize":
        return;
      case "layout":
        // Tile rects, reported so a NATIVE host can anchor its own chrome over
        // the WebView pixel-perfectly. This card draws no chrome over the frame
        // — the embed draws its own — so the measurements have no consumer.
        return;
      default:
        return;
    }
  }

  /**
   * Flip one device in a host-owned set and push the whole set to the embed.
   *
   * Sets, not per-device deltas: `setAudioOn` / `setPaused` REPLACE the set on
   * the page (see `EmbedGridPage`), which is what keeps host and page from
   * drifting apart over a dropped message.
   */
  _toggleHostSet(set, deviceId, command) {
    if (!Number.isInteger(deviceId)) {
      return;
    }
    if (set.has(deviceId)) {
      set.delete(deviceId);
    } else {
      set.add(deviceId);
    }
    this._postCommand(command, [...set]);
  }

  // ── what the wall is set to right now ────────────────────────────────────
  //
  // One reader per setting, so the bar, the seed config and the open channel
  // can never be told three different things. A session override wins over the
  // card config for as long as the dashboard is open.

  _quality() {
    return this._sessionQuality ?? this._config.quality ?? "auto";
  }

  _activeOnly() {
    return this._sessionActiveOnly ?? this._config.active_only === true;
  }

  _showBoxes() {
    return this._sessionShowBoxes ?? this._config.show_boxes === true;
  }

  /**
   * Is the highlight border lit at all?
   *
   * The viewer's `options.highlightOn` defaults ON for a grid. Here absence
   * means OFF, and deliberately: the embed's own schema says "absent ⇒
   * highlight off", and every dashboard written before this feature existed
   * has no key. Defaulting a card's wall ON would put borders on walls nobody
   * asked to change. Once the operator says yes, everything under it is the
   * viewer's defaults.
   */
  _highlightOn() {
    return this._sessionHighlightOn ?? this._config.highlight === true;
  }

  /** The viewer-shaped `GridHighlightSettings` this wall is running. */
  _highlight() {
    return this._sessionHighlight ?? highlightFromConfig(this._config);
  }

  /** The highlight as the embed takes it: the triggers plus the master switch
   *  (`gridHighlightSchema` — `enabled` is the bar's toggle). */
  _highlightCommand() {
    return { ...this._highlight(), enabled: this._highlightOn() };
  }

  /**
   * The bar's view of this card, and the only way it changes anything.
   *
   * Every setter ends in an embed command through `_publishWall` — the bar
   * keeps no state of its own, so there is no second document to disagree with
   * this one.
   */
  _barHost() {
    return {
      cameras: () =>
        this._deviceIds().map((id) => ({
          id,
          name: friendlyName(this._hass, this._entityForDevice(id)) || `Camera ${id}`,
        })),
      // What talk-back is doing here, derived from the two facts this card has:
      // what it ASKED the mint for, and what the mint ANSWERED.
      talkState: () => talkState(this._talkAsked(), this._talkGranted),
      audioOn: (id) => this._audioOnIds.has(id),
      audioCount: () => this._audioOnIds.size,
      toggleAudio: (id) => this._toggleHostSet(this._audioOnIds, id, "setAudioOn"),
      toggleAllAudio: () => {
        const ids = this._deviceIds();
        const anyOn = ids.some((id) => this._audioOnIds.has(id));
        this._audioOnIds = new Set(anyOn ? [] : ids);
        this._postCommand("setAudioOn", [...this._audioOnIds]);
      },
      anyPaused: () => this._pausedIds.size > 0,
      toggleAllPaused: () => {
        const ids = this._deviceIds();
        this._pausedIds = new Set(this._pausedIds.size > 0 ? [] : ids);
        this._postCommand("setPaused", [...this._pausedIds]);
      },
      quality: () => this._quality(),
      qualityTiers: () => EMBED_CONTRACT.quality,
      qualityLabel: (tier) => QUALITY_LABELS[tier] || tier,
      setQuality: (tier) => {
        this._sessionQuality = tier;
        this._publishWall(false);
      },
      columns: () => this._sessionColumns ?? this._config.columns ?? "auto",
      layoutOptions: () =>
        this._layoutMode() === "fixed"
          ? LAYOUT_OPTIONS.filter((value) => value !== "auto")
          : LAYOUT_OPTIONS,
      /**
       * Why the column picker may be refused.
       *
       * `flow` derives the column count from the card's MEASURED width and a
       * cap in pixels; a pinned count there would be a second authority for
       * one arrangement, which is the bug the card's own layout rewrite
       * removed. Refused with the reason rather than hidden.
       */
      layoutReason: () =>
        this._layoutMode() === "flow"
          ? "This wall flows to a maximum camera width, so the number of " +
            "columns follows the card's width. Change the arrangement in the " +
            "card settings to pin it."
          : null,
      setColumns: (value) => {
        this._sessionColumns = value;
        this._publishWall(false);
        this._restyleFrame();
      },
      activeOnly: () => this._activeOnly(),
      toggleActiveOnly: () => {
        this._sessionActiveOnly = !this._activeOnly();
        this._publishWall(false);
      },
      showBoxes: () => this._showBoxes(),
      setShowBoxes: (value) => {
        this._sessionShowBoxes = value;
        this._publishWall(false);
      },
      highlightOn: () => this._highlightOn(),
      setHighlightOn: (value) => {
        this._sessionHighlightOn = value;
        this._publishWall(false);
      },
      highlight: () => this._highlight(),
      patchHighlight: (patch) => {
        this._sessionHighlight = withHighlightDefaults({ ...this._highlight(), ...patch });
        this._publishWall(false);
      },
      toggleHighlightClass: (macro) => {
        if (!DETECTION_MACRO_CLASSES.includes(macro)) {
          return;
        }
        const current = this._highlight().detectionClasses;
        const next = current.includes(macro)
          ? current.filter((c) => c !== macro)
          : [...current, macro];
        this._sessionHighlight = withHighlightDefaults({
          ...this._highlight(),
          detectionClasses: next,
        });
        this._publishWall(false);
      },
    };
  }

  /** Re-push everything the host owns, after the page has come back up. */
  _republishHostState() {
    this._postCommand("setAudioOn", [...this._audioOnIds]);
    this._postCommand("setPaused", [...this._pausedIds]);
    // The page seeded itself from a config that is stale by every edit since —
    // and after a remount it is stale by all of them.
    this._publishWall(true);
  }

  /**
   * Push the wall settings the CONFIG owns onto the open channel.
   *
   * `acquireConfig` resolves ONCE (`host-bridge.ts`), so a second
   * `embed-config` is read by nobody: until this existed, a changed column
   * count, quality or camera list reached the wall only when something else
   * rebuilt the iframe — and rebuilding it renegotiates every WebRTC session,
   * the exact cost this card is built to avoid. The file header promised the
   * open channel for years; this is the code that keeps the promise.
   *
   * Diffed, not re-sent: `set hass` fires several times a second and each
   * command is a state change to the page. `force` is for a page that has just
   * (re)mounted, where the mirror says "already sent" about a page that never
   * received it.
   */
  _publishWall(force) {
    const deviceIds = this._deviceIds();
    if (!deviceIds.length || !this._iframe) {
      return;
    }
    const wall = {
      setDevices: deviceIds,
      setLayout: this._layout(deviceIds),
      setQuality: this._quality(),
      setShowName: this._config.show_names !== false,
      setActiveOnly: this._activeOnly(),
      setShowBoxes: this._showBoxes(),
      // The border policy for the whole wall — per-GRID in the viewer
      // (`view-options-scope.ts`: "which camera just lit up" only means
      // something if every tile lights up for the same reason), and a card IS
      // a grid here.
      setHighlight: this._highlightCommand(),
    };
    for (const kind of Object.keys(wall)) {
      const encoded = JSON.stringify(wall[kind]);
      if (!force && this._publishedWall[kind] === encoded) {
        continue;
      }
      this._publishedWall[kind] = encoded;
      this._postCommand(kind, wall[kind]);
    }
    if (this._bar) {
      this._bar.sync();
    }
  }

  /**
   * Send one host→page command.
   *
   * The envelope is the embed's, not ours: `onHostCommand` in `host-bridge.ts`
   * reads `{ type: "embed-command", command: { kind, value } }` and drops
   * anything else. Targeted at the frame's origin — never `'*'`: the value
   * names cameras this dashboard shows and drives the wall's state.
   */
  _postCommand(kind, value) {
    const base = this._frameBase();
    if (!this._iframe || !this._iframe.contentWindow || !base) {
      return;
    }
    this._iframe.contentWindow.postMessage(
      { type: "embed-command", command: { kind, value } },
      new URL(base, window.location.origin).origin
    );
  }

  /**
   * Open the camera behind a tile, in the dialog Home Assistant already has.
   *
   * `hass-more-info` is the event every core card fires for this; the frontend
   * mounts the dialog. A card configured with raw `device_ids` has no entity to
   * name, and rather than opening the wrong thing (or nothing, silently) it
   * says so where the operator is already looking — the card's own status line.
   */
  _openCamera(deviceId) {
    if (!Number.isInteger(deviceId)) {
      return;
    }
    const entityId = this._entityForDevice(deviceId);
    if (!entityId) {
      this._setStatus(
        "This tile has no Home Assistant camera entity to open. " +
          "Configure the card with camera entities instead of device_ids."
      );
      return;
    }
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId },
        bubbles: true,
        composed: true,
      })
    );
  }

  _entityForDevice(deviceId) {
    return entityForDevice(this._hass, this._config, deviceId);
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
    // A camera that left the wall takes its host-owned state with it, or the
    // set would silently re-mute it if it ever came back.
    this._pruneHostState(deviceIds);
    const config = {
      serverUrl,
      // This dashboard has nowhere to mount the tile's linked-devices and
      // view-options panels, so the embed must not draw the two discs that
      // open them: without this they were visible on every tile and inert
      // (operator, 2026-09-07). Needs viewer 1.0.461+; an older embed ignores
      // the key and keeps drawing them.
      cellPanels: false,
      ...(grant && grant.token ? { token: grant.token } : {}),
      devices: deviceIds,
      layout: this._layout(deviceIds),
      quality: this._quality(),
      showName: this._config.show_names !== false,
      showBadges: this._config.show_badges !== false,
      muted: this._config.muted !== false,
      paused: false,
      // The seed for a page that is mounting now. It matters after a frame
      // rebuild or a reconnect: without it the wall would come back with every
      // tile audible-by-default and every pause forgotten.
      audioOnIds: [...this._audioOnIds],
      pausedIds: [...this._pausedIds],
      labels: resolveLabels(this._hass, this._config),
      ...(this._activeOnly() ? { activeOnly: true } : {}),
      ...(this._showBoxes() ? { showBoxes: true } : {}),
      // Seeded as well as commanded: a page mounting now renders its first
      // frame from this, and a wall whose borders arrived one command later
      // would flash unlit.
      highlight: this._highlightCommand(),
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

  /** Drop host-owned state for cameras no longer on the wall. */
  _pruneHostState(deviceIds) {
    for (const set of [this._audioOnIds, this._pausedIds]) {
      for (const id of [...set]) {
        if (!deviceIds.includes(id)) {
          set.delete(id);
        }
      }
    }
  }

  /**
   * The wall's column count — DERIVED when the card is scrolling.
   *
   * The embed fits every tile into the box it is handed and never scrolls
   * (`EmbedGridPage` has no `overflow` of its own), so a cap on what is visible
   * can only be the host's. But two column controls that disagree are worse
   * than one: with a cap in force the embed is told to lay ONE row of every
   * camera and the host's scroller decides how much of that row is on screen.
   * `layout` is then not a second opinion, it is simply not consulted.
   */
  _layout(deviceIds) {
    return this._plan(deviceIds).columns;
  }

  /**
   * The layout mode this card is configured for.
   *
   * Reads `layout_mode` when present, and otherwise DERIVES one from the old
   * two-control config so a dashboard written before this option keeps working
   * untouched:
   *
   *  - `max_visible: N` meant "N across, one row, the rest scrolls sideways".
   *    That is `fixed` with N columns and one row — same cameras on screen,
   *    same rest-scrolls, in the direction a wall of rows actually scrolls.
   *  - anything else was a wall that fits, with the column count the operator
   *    pinned (or `auto`).
   */
  _layoutMode() {
    const declared = this._config.layout_mode;
    if (LAYOUT_MODES.includes(declared)) {
      return declared;
    }
    // A card written before `layout_mode` existed still CHOSE an arrangement,
    // in the vocabulary it had. `auto` is the default for a card that chose
    // nothing at all — never for one that did, whose wall must come back the
    // shape the operator left it.
    const legacyCap = clampInt(this._config.max_visible, EMBED_CONTRACT.max_visible, null);
    if (legacyCap !== null) {
      return "fixed";
    }
    const pinned = this._config.columns ?? this._config.layout;
    return pinned === undefined || pinned === null || pinned === "" ? "auto" : "fit";
  }

  /** The wall's geometry for the cameras it is showing, at its measured width. */
  _plan(deviceIds) {
    const mode = this._layoutMode();
    const legacyCap = clampInt(this._config.max_visible, EMBED_CONTRACT.max_visible, null);
    const columns =
      mode === "fixed" && this._config.columns === undefined && legacyCap !== null
        ? legacyCap
        : (this._sessionColumns ?? this._config.columns ?? this._config.layout ?? "auto");
    const rows =
      mode === "fixed" && this._config.rows === undefined && legacyCap !== null
        ? 1
        : this._config.rows;
    return planGrid({
      mode,
      total: Array.isArray(deviceIds) ? deviceIds.length : 0,
      cardWidth: this._cardWidth(),
      columns,
      rows,
      maxRows: this._config.max_rows,
      maxTileWidth: this._config.max_tile_width,
      landscape: window.innerWidth > window.innerHeight,
    });
  }

  /**
   * The card's own width in CSS px, measured.
   *
   * `flow` is a statement about pixels on the glass, so it needs the real
   * number and not a breakpoint guess. `0` before the first layout — `planGrid`
   * treats that as one column and the ResizeObserver restyles the frame the
   * moment a width exists.
   */
  _cardWidth() {
    return this._scroller ? this._scroller.clientWidth : 0;
  }

  /** The cameras this card shows, whatever it was configured with. */
  _deviceIds() {
    return this._hass ? resolveDeviceIds(this._hass, this._config) : [];
  }


  // ── rendering ────────────────────────────────────────────────────────────

  _frameHeight() {
    return Number(this._config.height) > 0
      ? Number(this._config.height)
      : DEFAULT_HEIGHT;
  }

  /**
   * The scroll container — the VIEWPORT onto the wall.
   *
   * Inert unless the plan says the wall is taller than what is shown, so a wall
   * that fits is laid out exactly as it was before any of this existed.
   *
   * Its height is `visibleRows` worth of tiles. The iframe inside is the FULL
   * wall, `totalRows` tall, and the operator scrolls this box down it — the
   * same trick the one-row horizontal cap used, turned through ninety degrees,
   * which is the direction a wall of rows scrolls and the direction a wheel and
   * a thumb already go.
   */
  _scrollerStyle() {
    const plan = this._plan(this._deviceIds());
    if (!plan.scrolls) {
      return "width:100%;";
    }
    const tileHeight = this._tileHeightPx(plan);
    const height = tileHeight > 0 ? `height:${(tileHeight * plan.visibleRows).toFixed(2)}px;` : "";
    return `overflow-y:auto;overflow-x:hidden;width:100%;${height}-webkit-overflow-scrolling:touch;`;
  }

  /**
   * One tile's height in px at the current width, or `0` before layout.
   *
   * Derived from the MEASURED width rather than from the configured maximum: a
   * plan of three columns means the tiles are a third of the card wide, whether
   * or not that reached the cap.
   */
  _tileHeightPx(plan) {
    // The wall is narrower than the scroller by the gutter, so the tile height
    // is derived from the width the FRAME has, not the width the card has —
    // otherwise every scrolling wall is a few pixels too tall and the last row
    // never quite comes into view.
    const width = this._cardWidth() - (plan.scrolls ? SCROLL_GUTTER_PX : 0);
    const columns = plan.columns === "auto" ? 1 : plan.columns;
    if (!(width > 0) || !(columns > 0)) {
      return 0;
    }
    const ratio = ASPECT_RATIOS[this._config.aspect_ratio || DEFAULT_ASPECT] || ASPECT_RATIOS[DEFAULT_ASPECT];
    return width / columns / ratio;
  }

  /**
   * The iframe's own box — the WHOLE wall, of which the scroller shows a part.
   *
   * When the plan scrolls, the frame is `totalRows` tall and the scroller is
   * `visibleRows` tall, so a tile is exactly one row of the visible box. The
   * shape has to follow from the plan rather than from `aspect_ratio` alone,
   * because the embed CENTRES its wall inside whatever box it is given: a box
   * of the wrong shape becomes a band of padding instead of a taller wall.
   * That is also why there are not two controls for one shape here — the cap
   * decides it, and this card refuses to hold that argument twice.
   *
   * When nothing scrolls the old behaviour is untouched: `aspect_ratio` shapes
   * the box, or `none` hands it a pixel height.
   */
  _frameStyle() {
    const plan = this._plan(this._deviceIds());
    const chrome = "border:none;display:block;border-radius:8px;";
    if (this._layoutMode() === "auto") {
      // The wall is exactly its rows tall, so every camera keeps the shape the
      // operator picked while the width is spent on as many columns as stay
      // readable. An aspect ratio over the WHOLE card (what `fit` does) would
      // squash a four-row wall into one camera's worth of height.
      const tileHeight = this._tileHeightPx(plan);
      return tileHeight > 0
        ? `width:100%;height:${(tileHeight * plan.totalRows).toFixed(2)}px;${chrome}`
        : `width:100%;height:${this._frameHeight()}px;${chrome}`;
    }
    if (plan.scrolls) {
      const tileHeight = this._tileHeightPx(plan);
      // The gutter is the only part of the scroller a finger can reach — see
      // SCROLL_GUTTER_PX. The wall gives up those pixels rather than being
      // unscrollable on every phone.
      const width = `width:calc(100% - ${SCROLL_GUTTER_PX}px);`;
      if (tileHeight > 0) {
        return `${width}height:${(tileHeight * plan.totalRows).toFixed(2)}px;${chrome}`;
      }
      // Pre-layout: no measured width yet, so no honest height. The
      // ResizeObserver restyles this the moment there is one.
      return `${width}height:${this._frameHeight()}px;${chrome}`;
    }
    const aspect = this._config.aspect_ratio || DEFAULT_ASPECT;
    if (aspect !== "none" && ASPECT_RATIOS[aspect]) {
      // `aspect-ratio` keeps the wall the right shape on a phone and on a wall
      // display without the operator retyping a pixel height per breakpoint.
      return `width:100%;aspect-ratio:${aspect.replace(":", " / ")};${chrome}`;
    }
    return `width:100%;height:${this._frameHeight()}px;${chrome}`;
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
        this._baseState !== 'absent'
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
      // Re-applied on every render, never only at mount: adding a camera or
      // moving the cap changes the strip's width and shape, and neither may
      // cost a frame rebuild.
      this._iframe.style.cssText = this._frameStyle();
      if (this._scroller) {
        this._scroller.style.cssText = this._scrollerStyle();
      }
      this._sendConfig();
      this._publishWall(false);
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
    // A new page has been pushed nothing yet, and it seeds itself from the
    // handshake config — so the diff starts empty rather than replaying.
    this._publishedWall = {};
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
      this._scroller = null;
      this._status = null;
      this._bar = null;
      return;
    }

    // The frame always sits in a scroller. It is inert until a cap is set, and
    // building it unconditionally keeps the cap out of `_mountFrame`'s key —
    // rebuilding the frame would restart every WebRTC session on the wall.
    const scroller = document.createElement("div");
    scroller.dataset.scroller = "wall";
    scroller.style.cssText = this._scrollerStyle();

    const iframe = document.createElement("iframe");
    iframe.src = frameUrl;
    iframe.allow = "autoplay; fullscreen; microphone";
    iframe.style.cssText = this._frameStyle();
    scroller.appendChild(iframe);
    wrapper.appendChild(scroller);
    this._scroller = scroller;

    const status = document.createElement("div");
    status.style.cssText =
      "position:absolute;left:16px;right:16px;bottom:16px;padding:10px;display:none;" +
      "background:rgba(180,0,0,0.92);color:#fff;font-size:13px;border-radius:6px;z-index:2;";
    wrapper.appendChild(status);

    card.appendChild(wrapper);

    /**
     * The control bar — the viewer's grid bar, under the wall.
     *
     * Built HERE, with the frame, and never again: `_buildCard` runs only when
     * the frame URL changes, so a `set hass` storm cannot close a popover the
     * operator has open. Everything that changes afterwards goes through
     * `sync()`.
     *
     * It sits OUTSIDE the wrapper the status line is absolutely positioned in,
     * so the bar never covers the wall and the wall never covers the bar.
     */
    this._bar = this._config.show_controls === false ? null : buildControlBar(this._barHost());
    if (this._bar) {
      card.appendChild(this._bar.element);
    }
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

  /** The mode the editor is showing, derived for a card written before it. */
  _mode() {
    const declared = this._config.layout_mode;
    if (LAYOUT_MODES.includes(declared)) {
      return declared;
    }
    if (clampInt(this._config.max_visible, EMBED_CONTRACT.max_visible, null) !== null) {
      return "fixed";
    }
    // Same rule as the card's `_layoutMode`: a pinned column count IS a choice,
    // and the editor must show the operator the arrangement their wall is
    // actually running.
    const pinned = this._config.columns ?? this._config.layout;
    return pinned === undefined || pinned === null || pinned === "" ? "auto" : "fit";
  }

  /**
   * Only the fields the chosen arrangement actually uses.
   *
   * This is the whole point of the rewrite: before, four controls were always
   * on screen and two of them explained in their own labels when they were
   * ignored. A control that is not in force is not greyed out here — it is not
   * there, because a setting you cannot see is a setting you cannot
   * misconfigure.
   */
  _modeFields() {
    const config = this._config;
    const mode = this._mode();
    if (mode === "auto") {
      // Nothing to set: the whole promise is that the card decides, from its
      // own measured width. A knob here would be the second authority over the
      // arrangement that the one-control rewrite removed.
      return [];
    }
    if (mode === "flow") {
      return [
        numberField(
          "max_tile_width",
          "Biggest a single camera may get (px)",
          config.max_tile_width ?? DEFAULT_MAX_TILE_WIDTH
        ),
        selectField("max_rows", "Rows visible before it scrolls", String(config.max_rows ?? 2),
          countOptions(EMBED_CONTRACT.max_rows)),
      ];
    }
    if (mode === "fixed") {
      const legacy = clampInt(config.max_visible, EMBED_CONTRACT.max_visible, null);
      return [
        selectField("columns", "Columns", String(config.columns ?? legacy ?? 3),
          countOptions(EMBED_CONTRACT.layout)),
        selectField("rows", "Rows visible before it scrolls",
          String(config.rows ?? (legacy === null ? 2 : 1)), countOptions(EMBED_CONTRACT.rows)),
      ];
    }
    return [
      selectField("columns", "Columns", String(config.columns ?? config.layout ?? "auto"),
        [["auto", "Automatic"], ...countOptions(EMBED_CONTRACT.layout)]),
    ];
  }

  /**
   * The highlight rows, in the viewer's order and nesting.
   *
   * `HighlightTriggerRows.tsx` is the shape: motion · audio Off/Low/Mid/High ·
   * detection, and the class chips + hold only WHILE detection is on. The
   * nesting is not decoration — a trigger offered for a switch that is off is
   * a control that does nothing, and the whole section hangs off the master
   * toggle for the same reason (`GridMenuPanel.tsx`).
   *
   * The audio row is the three NAMED presets, never a dB box: the values are
   * meter rungs (`audio-level.ts`), and a free number would let a dashboard
   * ask for a threshold the meter cannot show.
   */
  _highlightFields() {
    const config = this._config;
    if (config.highlight !== true) {
      return [];
    }
    const rows = [
      checkboxField("highlight_motion", "Motion lights the border",
        config.highlight_motion !== false),
      selectField("highlight_audio", "Audio lights the border", config.highlight_audio || "off", [
        ["off", "Off"],
        ["low", "Low"],
        ["mid", "Medium"],
        ["high", "High"],
      ]),
      checkboxField("highlight_detection", "Detections light the border",
        config.highlight_detection === true),
    ];
    if (config.highlight_detection === true) {
      rows.push(
        checkboxGroup(
          "highlight_detection_classes",
          "Only these classes (none selected means any)",
          config.highlight_detection_classes || [],
          DETECTION_MACRO_CLASSES
        ),
        selectField(
          "highlight_detection_hold",
          "Keep the border lit for",
          String(config.highlight_detection_hold ?? 3),
          DETECTION_HOLD_OPTIONS.map((v) => [String(v), `${v}s`])
        )
      );
    }
    return rows;
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
      selectField("layout_mode", "Arrangement", this._mode(), [
        ["auto", "Automatic — fill the width, keep every camera readable"],
        ["fit", "Fit them all on screen"],
        ["flow", "Cap the camera size, scroll the rest"],
        ["fixed", "Fixed columns and rows, scroll the rest"],
      ]),
      ...this._modeFields(),
      selectField("aspect_ratio", "Camera shape", config.aspect_ratio || DEFAULT_ASPECT, [
        ["16:9", "16:9"],
        ["4:3", "4:3"],
        ["3:2", "3:2"],
        ["1:1", "Square"],
        ...(this._mode() === "fit" ? [["none", "Fixed height (px)"]] : []),
      ]),
      ...(this._mode() === "fit" && (config.aspect_ratio || DEFAULT_ASPECT) === "none"
        ? [numberField("height", "Height in px", config.height ?? DEFAULT_HEIGHT)]
        : []),
      selectField(
        "quality",
        "Stream quality",
        config.quality || "auto",
        EMBED_CONTRACT.quality.map((value) => [value, QUALITY_LABELS[value] || value])
      ),
      checkboxField("show_names", "Show the camera name on each tile", config.show_names !== false),
      checkboxField("show_boxes", "Show detection boxes", config.show_boxes === true),
      checkboxField("active_only", "Only cameras that are currently active", config.active_only === true),
      checkboxField(
        "show_controls",
        "Show the control bar under the wall",
        config.show_controls !== false
      ),
      checkboxField("highlight", "Highlight cameras that are active", config.highlight === true),
      ...this._highlightFields(),
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
    const mode = readText(root, "layout_mode");
    config.layout_mode = LAYOUT_MODES.includes(mode) ? mode : "auto";

    // The legacy pair is REWRITTEN, not carried: leaving `max_visible` behind
    // would let `_layoutMode`'s derivation keep answering for a card whose
    // mode is now explicit, and two authorities for one arrangement is the
    // argument this rewrite exists to end.
    delete config.max_visible;
    delete config.layout;

    const columns = readText(root, "columns");
    if (columns === "" || columns === null || columns === undefined) {
      delete config.columns;
    } else {
      config.columns = columns === "auto" ? "auto" : parseInt(columns, 10);
    }
    setIntOrDelete(config, "rows", readText(root, "rows"));
    setIntOrDelete(config, "max_rows", readText(root, "max_rows"));
    setIntOrDelete(config, "max_tile_width", readText(root, "max_tile_width"));

    config.aspect_ratio = readText(root, "aspect_ratio") || DEFAULT_ASPECT;
    const height = parseInt(readText(root, "height"), 10);
    if (Number.isInteger(height) && height > 0) {
      config.height = height;
    } else {
      delete config.height;
    }
    config.quality = readText(root, "quality") || "auto";
    config.show_names = readBool(root, "show_names");
    setBoolOrDelete(config, "show_boxes", readBool(root, "show_boxes"));
    setBoolOrDelete(config, "active_only", readBool(root, "active_only"));
    // Default TRUE, so only the "no" is worth writing — `setBoolOrDelete` can
    // only ever store `true` and would silently drop the operator's "off".
    setFalseOrDelete(config, "show_controls", readBool(root, "show_controls"));
    setBoolOrDelete(config, "highlight", readBool(root, "highlight"));
    // The triggers are only read while their switch is on: a value left behind
    // for a switch that is off is a setting nothing shows and nothing applies,
    // and the next reader cannot tell it from one in force.
    if (config.highlight === true) {
      setFalseOrDelete(config, "highlight_motion", readBool(root, "highlight_motion"));
      const audio = readText(root, "highlight_audio");
      setOrDelete(config, "highlight_audio", audio === "off" ? "" : audio);
      setBoolOrDelete(config, "highlight_detection", readBool(root, "highlight_detection"));
      if (config.highlight_detection === true) {
        const classes = readChecked(root, "highlight_detection_classes");
        if (classes.length) {
          config.highlight_detection_classes = classes;
        } else {
          delete config.highlight_detection_classes;
        }
        setIntOrDelete(config, "highlight_detection_hold", readText(root, "highlight_detection_hold"));
      } else {
        delete config.highlight_detection_classes;
        delete config.highlight_detection_hold;
      }
    } else {
      for (const key of [
        "highlight_motion",
        "highlight_audio",
        "highlight_detection",
        "highlight_detection_classes",
        "highlight_detection_hold",
      ]) {
        delete config[key];
      }
    }
    setOrDelete(config, "url_base", readText(root, "url_base"));
    this.dispatchEvent(
      new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true })
    );
  }
}

/** Write an integer field, or drop the key when the control is not on screen. */
function setIntOrDelete(config, key, raw) {
  const n = parseInt(raw, 10);
  if (Number.isInteger(n)) {
    config[key] = n;
  } else {
    delete config[key];
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

/** A multi-selection over a fixed vocabulary — `cameraPicker`'s shape without
 *  the entity lookup, read back by the same `readChecked`. */
function checkboxGroup(id, label, selected, values) {
  const box = document.createElement("div");
  box.dataset.picker = id;
  box.style.cssText =
    "display:flex;flex-wrap:wrap;gap:10px;border:1px solid var(--divider-color,#444);" +
    "border-radius:6px;padding:8px;";
  for (const value of values) {
    const row = document.createElement("label");
    row.style.cssText = "display:flex;align-items:center;gap:6px;font-size:14px;";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = value;
    input.checked = selected.includes(value);
    const caption = document.createElement("span");
    caption.textContent = value;
    row.append(input, caption);
    box.appendChild(row);
  }
  return labelled(id, label, box);
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

/** The mirror of {@link setBoolOrDelete} for an option whose DEFAULT is true:
 *  only the operator's "off" is worth a key, and `setBoolOrDelete` cannot say
 *  it — it stores `true` or nothing, so an "off" would vanish on save. */
function setFalseOrDelete(config, key, value) {
  if (value) {
    delete config[key];
  } else {
    config[key] = false;
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
