/**
 * The grid card's bottom control bar — the viewer's `GridControlBar`, on a
 * Lovelace card.
 *
 * ## Nothing here was invented
 *
 * Every control, its ORDER, and the values it can take are copied from the
 * viewer, which is the operator's source of truth for what a wall's bar
 * contains:
 *
 *   `camstack/src/components/grid/GridControlBar.tsx`
 *     pause/play (all) · audio (combinable multiselect) · quality · layout ·
 *     highlight MENU · active-only · [timeline] · [share] · [edit]
 *   `camstack/src/components/grid/GridMenuPanel.tsx`
 *     the highlight menu: master toggle, then the trigger rows, then the
 *     detector-layer row.
 *   `camstack/src/components/camera/HighlightTriggerRows.tsx`
 *     motion switch · audio Off/Low/Mid/High · detection switch · class chips
 *     · hold.
 *   `camstack/src/components/camera/audio-level.ts`
 *     the audio presets ARE meter rungs (2 / 4 / 6), never a free dB number,
 *     and the hold is a constant the operator does not set.
 *   `camstack/src/store/grid-store.ts`
 *     `GridHighlightSettings` + `GRID_HIGHLIGHT_DEFAULTS`.
 *   `camstack/src/grid/view-options-scope.ts`
 *     WHY the highlight lives on the bar and not on a tile: one border policy
 *     for the wall, because "which camera just lit up" only means something
 *     if every tile lights up for the same reason.
 *
 * The values are MIRRORED rather than imported for the same reason the card's
 * `EMBED_CONTRACT` is: a Lovelace card is plain JavaScript and cannot import
 * the viewer's TypeScript. `tests/test_card_grid_controls.py` diffs this copy
 * against those sources whenever the viewer is checked out beside this repo.
 *
 * ## Where this DELIBERATELY differs from the viewer, and why
 *
 *  1. **It does not float and does not auto-hide.** The viewer's bar lives over
 *     an immersive, full-screen wall and collapses to an edge handle after
 *     4.5 s. A Lovelace card is a box in a scrolling dashboard: a control that
 *     hides itself there is a control the operator cannot find, and the card
 *     owns no space outside its own rectangle. So the bar is a strip under the
 *     wall, always visible.
 *  2. **Its edits are for the session, not the document.** In the viewer the
 *     bar WRITES the grid store, which is the grid's own persisted document.
 *     Here the persisted document is the Lovelace config, and a rendered card
 *     cannot write it — only the config element can, and it has no frame (the
 *     same reason `tileRemove` / `tileResize` are refused). So the bar seeds
 *     itself from the card's config and its presses last until the dashboard
 *     is reloaded. The menu says so, in one line, rather than looking
 *     persistent and not being.
 *  3. **No back / title / share / edit / timeline.** The card has its own
 *     header, Lovelace owns navigation, the share link and the grid builder
 *     are viewer surfaces, and this card mounts no timeline. The viewer's bar
 *     already omits each of these when its host cannot answer for it
 *     (`onShare` / `onEdit` / `onToggleTimeline` are optional props) — this is
 *     that same rule, applied to a host that can answer for none of them.
 *  4. **The detector-layer row is ONE switch.** The viewer's row is a chip set
 *     (objects / discarded / stationary), but only `objects` crosses the embed
 *     bridge at all — `GridViewScreen.tsx:573` sends `showBoxes =
 *     layers.includes('objects')` and the page's schema has no other layer.
 *     A three-chip control here would be two chips that do nothing.
 *  5. **The MIC is shown and refused.** See {@link MIC_UNAVAILABLE_REASON}.
 */

/**
 * dBFS floor of each of the meter's six rungs.
 * `camstack/src/components/camera/audio-level.ts` — AUDIO_LEVEL_THRESHOLDS_DBFS.
 */
export const AUDIO_LEVEL_THRESHOLDS_DBFS = [-60, -53, -46, -39, -32, -24];

/** The rungs the highlight offers, low / mid / high. Same file. */
export const AUDIO_HIGHLIGHT_LEVELS = [2, 4, 6];

/** The highlight's audio hold, seconds — NOT operator-configurable. Same file. */
export const AUDIO_HIGHLIGHT_HOLD_SEC = 5;

const UI_TO_RUNG = { low: 2, mid: 4, high: 6 };

/** dBFS floor of a named preset. `dbfsForAudioUi` in the viewer. */
export function dbfsForAudioUi(ui) {
  const rung = UI_TO_RUNG[ui];
  const floor = AUDIO_LEVEL_THRESHOLDS_DBFS[rung - 1];
  return floor === undefined ? AUDIO_LEVEL_THRESHOLDS_DBFS.at(-1) : floor;
}

/** Map a stored highlight onto the three presets. `audioUiFromStored` in the
 *  viewer — including the legacy `sensor` mode, which reads as `low`. */
export function audioUiFromStored(mode, db) {
  if (mode === "off") return "off";
  if (mode === "sensor") return "low";
  let best = "mid";
  let bestDist = Number.POSITIVE_INFINITY;
  for (const ui of ["low", "mid", "high"]) {
    const dist = Math.abs(db - dbfsForAudioUi(ui));
    if (dist < bestDist) {
      best = ui;
      bestDist = dist;
    }
  }
  return best;
}

/** The detection macro classes the trigger editor offers; EMPTY means any.
 *  `DETECTION_MACRO_CLASSES` in `grid-store.ts`. */
export const DETECTION_MACRO_CLASSES = ["person", "vehicle", "animal", "face", "plate"];

/** The hold options the viewer's `HighlightTriggerRows` offers, in seconds. */
export const DETECTION_HOLD_OPTIONS = [2, 3, 5, 8];

/** `GRID_HIGHLIGHT_DEFAULTS` in `grid-store.ts`, verbatim — minus
 *  `clusterLabel`, which styles the TIMELINE's cluster cards and this card
 *  mounts no timeline (`HighlightTriggerRows.tsx` is explicit that the cluster
 *  row is not one of the trigger rows for exactly this reason). */
export const GRID_HIGHLIGHT_DEFAULTS = Object.freeze({
  motion: true,
  audio: "off",
  audioDb: -30,
  audioHoldSec: 5,
  detection: false,
  detectionHoldSec: 3,
  detectionClasses: [],
});

/** The layout options the viewer's bar offers. `LAYOUT_OPTIONS` in
 *  `GridControlBar.tsx`. */
export const LAYOUT_OPTIONS = ["auto", 2, 3, 4];

/** `QUALITY_BADGE` in `GridControlBar.tsx`, and the words the card already
 *  uses for the same tiers. */
export const QUALITY_BADGE = { auto: "A", high: "H", mid: "M", low: "L" };

/**
 * What the talk control says, per state — and why there are FOUR of them.
 *
 * Talk-back rides on the share token: the hub's `ShareTokenScopeSchema` carries
 * an opt-in `talk` flag, and a `grid-view` token minted with it may call three
 * named methods (`intercom.startTalkSession` / `pushTalkAudio` /
 * `endTalkSession`) — and only for the deviceIds the token already carries. The
 * flag is asked for at MINT time, never granted retroactively, because a share
 * link handed to somebody last week must not acquire the microphone of the
 * house because a feature shipped.
 *
 * Who may ask is the INTEGRATION's option, not this card's: a Lovelace config
 * is editable by anyone who can edit a dashboard and the mint endpoint is open
 * to every authenticated Home Assistant user, so a tick box here alone would
 * mean that editing a dashboard grants you the microphone. The card may only
 * DECLINE.
 *
 * So the control is never hidden and never shown live-but-broken — it names
 * which gate said no, because each has a different fix:
 *
 *   granted    — the token carries it.
 *   off        — the integration's option is off. An operator can turn it on.
 *   declined   — this card asked not to have it (`talk: false`).
 *   unknown    — no mint has answered yet, or the integration predates the
 *                flag. NOT the same as `off` (D315): telling somebody to turn
 *                on an option that does not exist sends them looking in the
 *                wrong place.
 */
export const TALK_REASONS = {
  granted:
    "Talk-back is enabled for this card. Press the talk button on a camera to " +
    "speak through it.",
  off:
    "Talk-back is off for this CamStack integration. Turn on \u201cAllow " +
    "talk-back\u201d in the integration\u2019s options (Settings \u2192 " +
    "Devices & services \u2192 CamStack \u2192 Configure). It then applies to " +
    "every Home Assistant user who can see this card.",
  declined:
    "This card asked for a viewing token without talk-back (talk: false). " +
    "Remove that from the card\u2019s settings to use it.",
  unknown:
    "Talk-back has not been answered for yet \u2014 the card is still getting " +
    "its viewing token, or this CamStack integration predates the setting.",
};

/** The state a card is in when it has no answer about talk-back. */
export const TALK_UNKNOWN = "unknown";

/**
 * Resolve the talk state from the two facts the card has: what it ASKED for,
 * and what the mint ANSWERED. Pure, so the guard can hold it to its table.
 *
 * `granted === null` means the question has not been answered — never folded
 * into `false`, which is the D315 mistake of making not-yet-known look like
 * not-installed.
 */
export function talkState(asked, granted) {
  if (granted === null || granted === undefined) return TALK_UNKNOWN;
  if (granted === true) return "granted";
  return asked ? "off" : "declined";
}

/** Fill any missing highlight field from the defaults —
 *  `withHighlightDefaults` in `grid-store.ts`. */
export function withHighlightDefaults(patch) {
  return { ...GRID_HIGHLIGHT_DEFAULTS, ...(patch || {}) };
}

/**
 * The card's own config keys → the viewer's highlight settings.
 *
 * The card config is this surface's persisted document, so it is the
 * equivalent of the viewer's grid store entry. `highlight_audio` is the NAMED
 * preset the operator picked, mapped to a rung here exactly as
 * `HighlightTriggerRows` maps it — the card never stores a dB number, because
 * a second way to say the same thing is how two surfaces start disagreeing.
 */
export function highlightFromConfig(config) {
  const audioUi = ["off", "low", "mid", "high"].includes(config.highlight_audio)
    ? config.highlight_audio
    : "off";
  const classes = Array.isArray(config.highlight_detection_classes)
    ? config.highlight_detection_classes.filter((c) => DETECTION_MACRO_CLASSES.includes(c))
    : [];
  const hold = DETECTION_HOLD_OPTIONS.includes(config.highlight_detection_hold)
    ? config.highlight_detection_hold
    : GRID_HIGHLIGHT_DEFAULTS.detectionHoldSec;
  return withHighlightDefaults({
    motion: config.highlight_motion !== false,
    ...(audioUi === "off"
      ? { audio: "off" }
      : {
          audio: "level",
          audioDb: dbfsForAudioUi(audioUi),
          audioHoldSec: AUDIO_HIGHLIGHT_HOLD_SEC,
        }),
    detection: config.highlight_detection === true,
    detectionClasses: classes,
    detectionHoldSec: hold,
  });
}

// ── the bar ────────────────────────────────────────────────────────────────

const BAR_CSS =
  "display:flex;align-items:center;gap:4px;flex-wrap:wrap;position:relative;" +
  "padding:6px 8px;border-top:1px solid var(--divider-color);";

const BUTTON_CSS =
  "display:inline-flex;align-items:center;gap:4px;min-height:34px;padding:0 10px;" +
  "border:none;border-radius:6px;background:transparent;cursor:pointer;" +
  "color:var(--primary-text-color);font:inherit;font-size:13px;";

const PANEL_CSS =
  "position:absolute;bottom:100%;left:8px;right:8px;max-height:340px;overflow:auto;" +
  "margin-bottom:4px;padding:8px;border-radius:8px;z-index:3;" +
  "background:var(--card-background-color,#fff);color:var(--primary-text-color);" +
  "box-shadow:0 4px 18px rgba(0,0,0,0.3);";

const ROW_CSS =
  "display:flex;align-items:center;gap:8px;width:100%;min-height:34px;padding:2px 6px;" +
  "border:none;background:transparent;color:inherit;font:inherit;font-size:13px;" +
  "text-align:left;cursor:pointer;";

function button(action, label, { active = false, badge = "", disabled = false, title = "" } = {}) {
  const el = document.createElement("button");
  el.type = "button";
  el.dataset.action = action;
  el.style.cssText = BUTTON_CSS;
  el.textContent = badge ? `${label} ${badge}` : label;
  el.setAttribute("aria-label", label);
  if (title) el.title = title;
  el.disabled = disabled;
  if (disabled) el.style.opacity = "0.5";
  if (active) el.style.color = "var(--primary-color)";
  return el;
}

function row(key, label, { selected = false, disabled = false, title = "" } = {}) {
  const el = document.createElement("button");
  el.type = "button";
  el.dataset.row = key;
  el.style.cssText = ROW_CSS;
  el.textContent = `${selected ? "✓ " : "   "}${label}`;
  if (title) el.title = title;
  el.disabled = disabled;
  if (disabled) el.style.opacity = "0.5";
  return el;
}

function note(text) {
  const el = document.createElement("div");
  el.style.cssText =
    "padding:4px 6px;font-size:11px;line-height:1.35;color:var(--secondary-text-color);";
  el.textContent = text;
  return el;
}

function heading(text) {
  const el = document.createElement("div");
  el.style.cssText =
    "padding:8px 6px 2px;font-size:11px;font-weight:600;color:var(--secondary-text-color);";
  el.textContent = text;
  return el;
}

function toggleRow(key, label, value, onChange) {
  const wrap = document.createElement("label");
  wrap.style.cssText = ROW_CSS + "cursor:pointer;";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.dataset.row = key;
  input.checked = value;
  input.addEventListener("change", () => onChange(input.checked));
  const text = document.createElement("span");
  text.textContent = label;
  wrap.append(input, text);
  return wrap;
}

function selectRow(key, label, value, options, onChange) {
  const wrap = document.createElement("label");
  wrap.style.cssText = ROW_CSS;
  const text = document.createElement("span");
  text.style.cssText = "flex:1;";
  text.textContent = label;
  const select = document.createElement("select");
  select.dataset.row = key;
  select.style.cssText = "font:inherit;font-size:13px;";
  for (const [optionValue, optionLabel] of options) {
    const option = document.createElement("option");
    option.value = String(optionValue);
    option.textContent = optionLabel;
    if (String(optionValue) === String(value)) option.selected = true;
    select.appendChild(option);
  }
  select.addEventListener("change", () => onChange(select.value));
  wrap.append(text, select);
  return wrap;
}

/**
 * Build the bar once and return a handle.
 *
 * Built ONCE on purpose: `set hass` re-enters the card's render several times a
 * second, and a bar rebuilt there would close an open popover under the
 * operator's finger (and lose the focus ring). `sync()` re-reads state into the
 * existing nodes; the open popover is re-rendered only when a press changed
 * something inside it.
 *
 * `host` supplies the state and receives the intents. Every one of them ends in
 * an embed command — nothing here keeps a store of its own.
 */
export function buildControlBar(host) {
  const bar = document.createElement("div");
  bar.dataset.bar = "controls";
  bar.style.cssText = BAR_CSS;

  const panel = document.createElement("div");
  panel.style.cssText = PANEL_CSS;
  panel.hidden = true;

  let open = null;

  const controls = new Map();

  const openPanel = (key) => {
    open = open === key ? null : key;
    renderPanel();
    sync();
  };

  function renderPanel() {
    if (open === null) {
      panel.hidden = true;
      panel.replaceChildren();
      return;
    }
    panel.hidden = false;
    panel.dataset.panel = open;
    panel.replaceChildren(...PANELS[open]());
  }

  /** Re-draw the open popover after a press changed what it shows. */
  const refresh = () => {
    renderPanel();
    sync();
  };

  const PANELS = {
    /**
     * The combinable audio multiselect, lead row first — the viewer's
     * `GridControlBar` audio panel, control for control.
     */
    audio: () => {
      const cameras = host.cameras();
      if (!cameras.length) return [note("—")];
      const anyOn = cameras.some((c) => host.audioOn(c.id));
      return [
        row("audio-all", anyOn ? "Mute all" : "Unmute all"),
        ...cameras.map((c) => row(`audio-${c.id}`, c.name, { selected: host.audioOn(c.id) })),
      ];
    },
    /**
     * Every camera, and what talk-back is doing on this card.
     *
     * The rows are never hidden and never shown as live sessions: talking is
     * the TILE's button (the embed owns the audio path), and this panel is the
     * one place that can say whether the credential behind that button carries
     * talk-back at all. Refused rows carry the gate that refused them.
     */
    mic: () => {
      const state = host.talkState();
      const reason = TALK_REASONS[state];
      const refused = state !== "granted";
      return [
        note(reason),
        ...host.cameras().map((c) =>
          row(`mic-${c.id}`, c.name, { disabled: refused, title: reason })
        ),
      ];
    },
    quality: () =>
      host.qualityTiers().map((tier) =>
        row(`quality-${tier}`, host.qualityLabel(tier), { selected: host.quality() === tier })
      ),
    layout: () => {
      const reason = host.layoutReason();
      if (reason) return [note(reason)];
      return host
        .layoutOptions()
        .map((value) =>
          row(`layout-${value}`, value === "auto" ? "Auto" : String(value), {
            selected: String(host.columns()) === String(value),
          })
        );
    },
    /**
     * The viewer's `GridMenuPanel`: the master switch, then — only while it is
     * on — the trigger rows that belong to it, then the detector-box row.
     *
     * The triggers are nested under the switch deliberately: in the viewer they
     * are one section, because a border and the reasons it lights must not
     * drift apart (they did once, and the trigger rows "did nothing").
     */
    highlight: () => {
      const h = host.highlight();
      const on = host.highlightOn();
      const nodes = [
        heading("Detection"),
        toggleRow("highlight-master", "Highlight active cameras", on, () => {
          host.setHighlightOn(!on);
          refresh();
        }),
      ];
      if (on) {
        nodes.push(
          toggleRow("highlight-motion", "Motion", h.motion, (value) => {
            host.patchHighlight({ motion: value });
            refresh();
          }),
          selectRow(
            "highlight-audio",
            "Audio",
            audioUiFromStored(h.audio, h.audioDb),
            [
              ["off", "Off"],
              ["low", "Low"],
              ["mid", "Medium"],
              ["high", "High"],
            ],
            (ui) => {
              host.patchHighlight(
                ui === "off"
                  ? { audio: "off" }
                  : {
                      audio: "level",
                      audioDb: dbfsForAudioUi(ui),
                      audioHoldSec: AUDIO_HIGHLIGHT_HOLD_SEC,
                    }
              );
              refresh();
            }
          ),
          toggleRow("highlight-detection", "Detection", h.detection, (value) => {
            host.patchHighlight({ detection: value });
            refresh();
          })
        );
        if (h.detection) {
          for (const macro of DETECTION_MACRO_CLASSES) {
            nodes.push(
              row(`highlight-class-${macro}`, macro, {
                selected: h.detectionClasses.includes(macro),
                title: "No class selected means any class",
              })
            );
          }
          nodes.push(
            selectRow(
              "highlight-hold",
              "Keep the border lit for",
              h.detectionHoldSec,
              DETECTION_HOLD_OPTIONS.map((v) => [v, `${v}s`]),
              (value) => {
                host.patchHighlight({ detectionHoldSec: Number(value) });
                refresh();
              }
            )
          );
        }
      }
      nodes.push(
        toggleRow("highlight-boxes", "Detection boxes", host.showBoxes(), (value) => {
          host.setShowBoxes(value);
          refresh();
        }),
        note(
          "These settings apply to this wall until the dashboard is reloaded. " +
            "The saved values live in this card's settings."
        )
      );
      return nodes;
    },
  };

  /** One press inside the popover, routed by its row key. */
  panel.addEventListener("click", (event) => {
    const target = event.target.closest("[data-row]");
    if (!target || target.disabled || target.tagName !== "BUTTON") {
      return;
    }
    const key = target.dataset.row;
    if (key === "audio-all") {
      host.toggleAllAudio();
      refresh();
      return;
    }
    if (key.startsWith("audio-")) {
      host.toggleAudio(Number(key.slice("audio-".length)));
      refresh();
      return;
    }
    if (key.startsWith("quality-")) {
      host.setQuality(key.slice("quality-".length));
      openPanel(open);
      return;
    }
    if (key.startsWith("layout-")) {
      const raw = key.slice("layout-".length);
      host.setColumns(raw === "auto" ? "auto" : Number(raw));
      openPanel(open);
      return;
    }
    if (key.startsWith("highlight-class-")) {
      host.toggleHighlightClass(key.slice("highlight-class-".length));
      refresh();
    }
  });

  // The buttons, in the viewer's order.
  const definitions = [
    ["pause", () => (host.anyPaused() ? "Play all" : "Pause all"), () => host.toggleAllPaused()],
    ["audio", () => "Audio", () => openPanel("audio")],
    ["mic", () => "Talk", () => openPanel("mic")],
    ["quality", () => "Quality", () => openPanel("quality")],
    ["layout", () => "Layout", () => openPanel("layout")],
    ["highlight", () => "Highlight", () => openPanel("highlight")],
    ["activeOnly", () => "Active only", () => host.toggleActiveOnly()],
  ];

  for (const [key, label, press] of definitions) {
    const el = button(key, label());
    el.addEventListener("click", () => {
      press();
      // A direct action closes any open popover, as the viewer's bar does.
      if (!["audio", "mic", "quality", "layout", "highlight"].includes(key)) {
        open = null;
        renderPanel();
      }
      sync();
    });
    controls.set(key, { el, label });
    bar.appendChild(el);
  }
  bar.appendChild(panel);

  /**
   * Re-read the host's state into the existing nodes. Cheap, and it must stay
   * cheap: the card calls it on every render.
   */
  function sync() {
    for (const [key, { el, label }] of controls) {
      el.textContent = label();
      el.setAttribute("aria-label", label());
      const active =
        (key === "activeOnly" && host.activeOnly()) ||
        (key === "highlight" && host.highlightOn()) ||
        (key === "audio" && host.audioCount() > 0) ||
        open === key;
      el.style.color = active ? "var(--primary-color)" : "var(--primary-text-color)";
    }
    const audio = controls.get("audio");
    if (audio) {
      const count = host.audioCount();
      audio.el.textContent = count > 0 ? `Audio ${count}` : "Audio";
    }
    const quality = controls.get("quality");
    if (quality) {
      quality.el.textContent = `Quality ${QUALITY_BADGE[host.quality()] || host.quality()}`;
    }
    const mic = controls.get("mic");
    if (mic) {
      // Never drawn `active` — this card holds no mic session of its own; the
      // tile's button does. Always explained, whichever state it is in.
      const state = host.talkState();
      mic.el.title = TALK_REASONS[state];
      mic.el.style.opacity = state === "granted" ? "1" : "0.6";
    }
    const layout = controls.get("layout");
    if (layout) {
      const reason = host.layoutReason();
      layout.el.title = reason || "";
      layout.el.style.opacity = reason ? "0.6" : "1";
    }
  }

  sync();
  return { element: bar, sync };
}
