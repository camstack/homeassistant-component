# Changelog

## Unreleased

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
