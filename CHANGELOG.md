# Changelog

## Unreleased

### Added

- **Entities carry the hub's static attributes.** A component's `attributes`
  object (JSON scalars — e.g. a consumable's `form`, `kind`, `label`) becomes
  the entity's state attributes, on every platform, and follows a
  re-announcement. Anything that is not an object is ignored.
- **Enum sensors.** `device_class: "enum"` with an `options` list keeps the
  value as text and exposes the options; a value outside them is `unknown`
  instead of freezing the entity. Without a usable list the sensor stays plain
  text, and numeric sensors are unchanged.
- **Cards take every option the viewer's share builders freeze.** The events
  card's `camera_names` (`{<device id>: name}`) becomes the embed's `names=`
  and wins over an entity's Home Assistant name — the only way to name a
  camera given by `device_ids`. The grid card's `audio_on` (device ids) seeds
  the tiles that start audible, exactly as the link's `audio=`, and a saved
  change reaches an open wall. `highlight_motion_color`,
  `highlight_audio_color`, `highlight_detection_color` (hex) and
  `highlight_thickness` (1–8 px) become the border `appearance`, as in the
  link's `hl=`.

### Removed

- **The events card has no order.** Events are newest first, always: the
  `sort` option and its editor control are gone, an old `sort:` key is ignored,
  and saving from the editor drops it. `importance` is not offered as a field.
- The grid card no longer sends Home Assistant names as the embed's `labels`.
  That field is the wall's translated UI words; the names were always ignored
  and tiles were always captioned with the hub's names.

### Fixed

- **Membership cameras come back after a Home Assistant restart.** The
  registry row surviving the restart was treated as "already added", so the
  camera stayed without `camstack_device_id` and the grid and events cards —
  which keep only entities carrying that attribute — went empty. An enabled
  row with nothing running on it is added again. A camera the operator
  disabled stays disabled.

### Changed

- **Card tokens use the hub's one token format** (CamStack server 1.2.606+,
  D892/D895). The share-link routes (`auth.createShareToken`,
  `extendShareToken`, `revokeShareToken`) and the `csv_…` tokens are gone from
  the hub; each card scope (entry, kind, device ids, talk-back) now holds ONE
  token minted by `auth.mintToken` with a lone `share-view` grant.
- Tokens live **90 days** (was 3 hours) and are **extended in place** with
  `auth.extendToken` when a card asks with less than 7 days left.
- After a restart the integration **finds its token** through
  `auth.listTokens({label})` (the label carries a stable digest of the scope)
  instead of minting a duplicate; a row whose secret Home Assistant lost is
  revoked and replaced.
- A card that changes its device set names the set it held (`replaces`), and
  the old token is **revoked** with `auth.revokeToken`. Removing the entry
  revokes all of its tokens; reloading it (an options change) keeps them.
- The relay keeps injecting the token server-side; the browser still sees only
  the grant id.
