/**
 * Will this browser talk to the hub? Shared by the Lovelace cards.
 *
 * The hub serves HTTPS with a certificate signed by its own local root
 * ("CamStack Local CA"). A browser that has not been taught that root refuses
 * the frame — and a refused SUBFRAME is not an error a page can see: no
 * interstitial (browsers only offer "proceed" at top level), an error document
 * is committed in its place, and that commit even fires the iframe's `load`
 * event. A card built on `load` alone shows a white rectangle and not one word
 * of explanation, in the dashboard AND in the card picker's preview.
 *
 * A `fetch()` at the hub's origin DOES report it: a TLS failure rejects with a
 * TypeError, a browser that already trusts the hub resolves. `no-cors`, so the
 * answer never depends on the hub's CORS headers or on being logged in — an
 * opaque response still means the handshake and the trust check both passed.
 *
 * The sidebar panel carries its own copy of this probe: it must stay loadable
 * as a classic script, and one `export` keyword would blank it.
 */
const PROBE_TIMEOUT_MS = 8000;

/** Returns "ok" | "unreachable". Bounded, so a hub that accepts and stalls still answers. */
export async function probeHub(url, fetchImpl = fetch) {
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

/** The explanation a card shows in place of a frame the browser refused. */
export function buildUnreachableNotice(url) {
  const box = document.createElement("div");
  box.style.cssText =
    "display:flex;flex-direction:column;gap:10px;padding:16px;line-height:1.5;" +
    "color:var(--primary-text-color);font-size:14px;";

  const title = document.createElement("strong");
  title.textContent = "This browser will not open the CamStack hub";

  const detail = document.createElement("div");
  detail.textContent =
    "The hub answers over HTTPS with a certificate signed by its own local " +
    "authority. Until this browser trusts it, the frame is blocked silently — " +
    "which is why this card was blank rather than showing an error.";

  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = `Open ${url} in a new tab, accept the certificate, then reload`;
  link.style.cssText =
    "color:var(--primary-color);text-decoration:underline;word-break:break-all;";

  const durable = document.createElement("div");
  durable.style.cssText = "font-size:12px;opacity:0.75;";
  durable.textContent =
    "The exception a browser stores is tied to that exact certificate, so it " +
    "is lost every time the hub reissues one. For good: CamStack admin UI → " +
    "Settings → Network → download the CA certificate and install it in this " +
    "device's trust store. The Home Assistant apps need the certificate in the " +
    "device's trust store; they offer no exception to click through.";

  box.append(title, detail, link, durable);
  return box;
}
