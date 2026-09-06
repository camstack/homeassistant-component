/**
 * CamStack sidebar panel.
 *
 * The URL is supplied by the integration, which derives it from the host and
 * port of the config entry. This file never asks for an address and never
 * stores one: a panel pointing somewhere other than the entities is a fault
 * nothing would ever report.
 *
 * ## Why this file PROBES instead of waiting for the iframe
 *
 * The hub serves HTTPS with a certificate signed by its own local root
 * ("CamStack Local CA"). A browser that has not been taught that root refuses
 * the frame — and a refused SUBFRAME is not an error the page can see:
 *   * no interstitial is shown (Chrome only offers "proceed" at top level),
 *   * `about:blank` (or the internal error page) is committed instead,
 *   * and that commit fires the iframe's `load` event.
 * So `load` fires, `loaded` becomes true, and the old deadline that was the
 * only failure signal here never fired. The operator got a white rectangle and
 * not one word of explanation. This is the exact failure mode after the hub
 * reissues its leaf (a new certificate voids the exception the operator had
 * clicked through), which is when the panel "suddenly goes blank".
 *
 * A `fetch()` at the same origin DOES report it: a TLS failure rejects with a
 * TypeError, and a browser that already trusts the hub resolves. That is the
 * signal, and it is the only one available.
 *
 * Add `?debug=1` to the Home Assistant URL to show the resolved address and
 * the probe result.
 */
const LOG = "[camstack-panel]";
const LOAD_TIMEOUT_MS = 12000;
/** Bounded so a hub that accepts the connection and then stalls still reports. */
const PROBE_TIMEOUT_MS = 8000;

/**
 * Ask the browser — not the iframe — whether it will talk to the hub.
 *
 * `no-cors` so the answer never depends on the hub's CORS headers or on the
 * caller being authenticated: an opaque response still means the TLS handshake
 * and the trust check both passed, which is the whole question.
 *
 * Returns "ok" | "unreachable". Deliberately NOT an `export`: this module is
 * also loadable as a classic script, and one `export` keyword would turn that
 * into a syntax error that blanks the panel for a different reason.
 */
async function probeHub(url, fetchImpl = fetch) {
  const controller =
    typeof AbortController === "function" ? new AbortController() : null;
  const timer = controller
    ? setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS)
    : null;
  try {
    await fetchImpl(`${url.replace(/\/$/, "")}/favicon.svg?camstack-probe=1`, {
      mode: "no-cors",
      cache: "no-store",
      credentials: "omit",
      ...(controller ? { signal: controller.signal } : {}),
    });
    return "ok";
  } catch {
    // A rejected no-cors fetch is a transport failure: DNS, refused connection,
    // or — the case this exists for — a certificate the browser will not accept.
    return "unreachable";
  } finally {
    if (timer !== null) {
      clearTimeout(timer);
    }
  }
}

class CamstackPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._loadTimeout = null;
    this._probeToken = 0;
  }

  disconnectedCallback() {
    this._clearTimeout();
    // Invalidates any probe still in flight, so a late answer cannot paint over
    // a panel that has since been re-pointed or torn down.
    this._probeToken += 1;
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
    const proxyBase = config && config.proxy_base;
    if (proxyBase) {
      // Framed THROUGH Home Assistant (`proxy.py`): same origin as this page,
      // so there is no certificate for the browser to refuse and nothing to
      // probe. The hub answers its index under the prefix (`camstack-mount`).
      this._renderRelayedFrame(`${window.location.origin}${proxyBase}/`);
      return;
    }
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

  /** The blocking explanation shown when the browser refuses the hub. */
  _buildUnreachablePanel(url) {
    const box = document.createElement("div");
    box.style.cssText =
      "position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;" +
      "justify-content:center;gap:12px;padding:24px;text-align:center;z-index:10000;" +
      "background:var(--primary-background-color,#111);" +
      "color:var(--primary-text-color,#e1e1e1);" +
      "font-family:var(--paper-font-body1_-_font-family,sans-serif);";

    const title = document.createElement("strong");
    title.style.cssText = "font-size:18px;";
    title.textContent = "This browser will not open the CamStack hub";

    const detail = document.createElement("div");
    detail.style.cssText = "max-width:520px;line-height:1.5;";
    detail.textContent =
      "The hub answers over HTTPS with a certificate signed by its own local " +
      "authority. Until this browser trusts it, the frame is blocked silently — " +
      "which is why this page was blank rather than showing an error.";

    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = `Open ${url} in a new tab, accept the certificate, then reload`;
    link.style.cssText =
      "color:var(--primary-color,#03a9f4);text-decoration:underline;max-width:520px;word-break:break-all;";

    const durable = document.createElement("div");
    durable.style.cssText =
      "max-width:520px;font-size:13px;opacity:0.75;line-height:1.5;";
    durable.textContent =
      "The exception a browser stores is tied to that exact certificate, so it " +
      "is lost every time the hub reissues one. For good: CamStack admin UI → " +
      "Settings → Network → download the CA certificate and install it in this " +
      "device's trust store.";

    box.append(title, detail, link, durable);
    return box;
  }

  _renderRelayedFrame(url) {
    this._clearTimeout();
    this._probeToken += 1;
    const container = document.createElement("div");
    container.style.cssText =
      "position:relative;width:100%;height:100%;background:var(--primary-background-color,#0a0a0a);";
    const iframe = document.createElement("iframe");
    iframe.src = url;
    iframe.allow = "autoplay; fullscreen; microphone";
    iframe.style.cssText =
      "position:absolute;inset:0;width:100%;height:100%;border:none;";
    container.appendChild(iframe);
    this.shadowRoot.replaceChildren(container);
  }

  _renderFrame(url) {
    this._clearTimeout();
    const token = ++this._probeToken;

    const container = document.createElement("div");
    container.style.cssText =
      "position:relative;width:100%;height:100%;background:var(--primary-background-color,#0a0a0a);";

    const iframe = document.createElement("iframe");
    iframe.src = url;
    iframe.allow = "autoplay; fullscreen; microphone";
    iframe.style.cssText =
      "position:absolute;inset:0;width:100%;height:100%;border:none;";
    container.appendChild(iframe);

    const debug = document.createElement("div");
    if (window.location.search.includes("debug=1")) {
      debug.style.cssText =
        "position:absolute;top:8px;left:8px;right:8px;padding:8px;background:rgba(0,0,0,0.85);" +
        "color:#0f0;font-family:monospace;font-size:11px;z-index:9999;border-radius:4px;word-break:break-all;";
      debug.textContent = `${LOG} ${url} probing…`;
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
      "The hub is reachable from this browser, but the page did not come up.";
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
      // NOT proof of success: a certificate-refused subframe commits an error
      // document and fires this too. It only silences the slow-load banner.
      loaded = true;
      this._clearTimeout();
      error.style.display = "none";
    });

    this._loadTimeout = setTimeout(() => {
      this._loadTimeout = null;
      if (!loaded) {
        error.style.display = "block";
      }
    }, LOAD_TIMEOUT_MS);

    this.shadowRoot.replaceChildren(container);

    probeHub(url).then((result) => {
      if (token !== this._probeToken) {
        return;
      }
      if (debug.isConnected) {
        debug.textContent = `${LOG} ${url} — ${result}`;
      }
      if (result === "unreachable") {
        this._clearTimeout();
        container.appendChild(this._buildUnreachablePanel(url));
      }
    });
  }
}

if (!customElements.get("camstack-panel")) {
  customElements.define("camstack-panel", CamstackPanel);
}
