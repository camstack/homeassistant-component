"""Export membership decides which cameras the integration builds.

Every VALUE now arrives by push, so this is no longer what creates entities in
general — the hub only ever pushes devices it exports. What it still decides
is the live camera, which the push cannot carry, and it remains the place a
wrong `addonId` would import somebody else's selection.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.camstack.api import (
    CamStackForbiddenError,
    CamStackRequestError,
    _unwrap,
)
from custom_components.camstack.const import HA_EXPORT_ADDON_ID

from .conftest import (
    UNRESOLVABLE_ADDON_ERROR,
    make_query_responder,
    setup_integration,
)

# A refusal the hub actually sent on 2026-08-09. Why it sent it is hub-side —
# it turned out to be a scope-enforcement regression there — so this is the
# sentence the operator has to be shown, unedited and uninterpreted.
SCOPE_REFUSAL = (
    "No scope grants view on 'device-export' (system-scope cap). "
    "Have: category:device[view,create], capability:device-manager[view], "
    "capability:device-state[view], capability:pipeline-orchestrator[view,create], "
    "addon:homeassistant-export[view,create]"
)


async def test_membership_is_read_from_the_export_capability(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The hub is asked for the export list, scoped to the HA export addon."""
    await setup_integration(hass, config_entry)

    calls = [call.args for call in mock_client.query.await_args_list]
    exports = [args for args in calls if args[0] == "deviceExport.listExposedDevices"]
    assert exports, "the integration never asked the hub what is exported"
    # Scoped to the addon that owns HA export. Unscoped, a `device-export`
    # collection call merges every exporter's list, so the HomeKit and Alexa
    # selections would silently become Home Assistant's too.
    assert exports[0][1] == {"addonId": HA_EXPORT_ADDON_ID}


async def test_only_exported_devices_are_resolved(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """289 and 290 are in `deviceManager.listAll` and not in the export list."""
    runtime = await setup_integration(hass, config_entry)

    assert set(runtime.coordinator.data.devices) == {615, 585, 291}


async def test_an_empty_export_list_creates_no_camera(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Exporting nothing means importing nothing — never everything.

    The failure mode being guarded is a fallback that reads "no selection" as
    "take them all"; on the operator's cluster that is 132 devices.
    """
    mock_client.query = AsyncMock(side_effect=make_query_responder([]))

    runtime = await setup_integration(hass, config_entry)

    assert runtime.coordinator.data.devices == {}
    assert hass.states.get("camera.videocamera_ingresso") is None


async def test_exporting_one_camera_imports_only_that_camera(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Narrowing the hub's selection narrows what Home Assistant receives."""
    mock_client.query = AsyncMock(
        side_effect=make_query_responder([{"deviceId": "615"}])
    )

    runtime = await setup_integration(hass, config_entry)

    assert set(runtime.coordinator.data.devices) == {615}
    assert hass.states.get("camera.videocamera_ingresso") is not None


async def test_a_string_device_id_still_matches_an_int_one(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The two hub endpoints disagree on the type of a device id.

    `deviceExport.listExposedDevices` sends `"615"`; `deviceManager.listAll`
    sends `615`. Comparing them unconverted matches nothing, which looks
    exactly like an empty selection — a bug that would pass every other test
    in this file while importing nothing on a real hub.
    """
    mock_client.query = AsyncMock(
        side_effect=make_query_responder([{"deviceId": "615"}])
    )

    runtime = await setup_integration(hass, config_entry)

    assert 615 in runtime.coordinator.data.devices


async def test_the_alexa_and_homekit_selections_are_not_imported(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Only the Home Assistant provider's list counts.

    Alexa and HomeKit each export three cameras; Home Assistant exports none.
    The correct outcome is zero cameras. An implementation that reads the
    merged collection imports all three and looks entirely healthy doing it —
    which is indistinguishable, from the operator's side, from the bug this
    whole change exists to fix.
    """
    mock_client.query = AsyncMock(side_effect=make_query_responder([]))

    runtime = await setup_integration(hass, config_entry)

    assert runtime.coordinator.data.devices == {}
    # 615 is exposed to Alexa AND to HomeKit, and to Home Assistant by neither.
    assert 615 not in runtime.coordinator.data.devices


async def test_an_exported_device_from_home_assistant_is_still_refused(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The echo guard outranks the export list.

    Exporting an HA-sourced device back to HA mirrors it across the bridge, so
    a selection that asks for it is refused rather than honoured.
    """
    mock_client.query = AsyncMock(
        side_effect=make_query_responder([{"deviceId": "1027"}])
    )

    runtime = await setup_integration(hass, config_entry)

    assert 1027 not in runtime.coordinator.data.devices


async def test_a_malformed_export_answer_does_not_open_the_gate(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A malformed answer is not an answer, and must not import everything."""

    async def respond(path: str, payload: Any | None = None) -> Any:
        if path == "deviceExport.listExposedDevices":
            return {"unexpected": "shape"}
        return await make_query_responder()(path, payload)

    mock_client.query = AsyncMock(side_effect=respond)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("camera.videocamera_ingresso") is None


async def test_an_unresolvable_pin_fails_setup_with_the_hubs_own_message(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A refused `addonId` must not be flattened into "nothing is exported".

    The hub refuses an unresolvable pin and names every provider that DOES
    exist, rather than quietly answering from one of them. That message is the
    fix instruction, and swallowing it produces the single most expensive
    outcome here: a broken integration that looks exactly like an empty one.

    The refusal recorded in `UNRESOLVABLE_ADDON_ERROR` is the one the live hub
    returns today for the retired `export-ha-mqtt` id.
    """

    async def respond(path: str, payload: Any | None = None) -> Any:
        if path == "deviceExport.listExposedDevices":
            message = UNRESOLVABLE_ADDON_ERROR["json"]["message"]
            raise CamStackRequestError(f"{path}: {message}")
        return await make_query_responder()(path, payload)

    mock_client.query = AsyncMock(side_effect=respond)
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is not None
    reason = config_entry.reason or ""
    assert "has no provider with addonId" in reason
    # The list of valid ids is the actionable half of the message.
    assert "homeassistant-export" in reason
    assert hass.states.get("camera.videocamera_ingresso") is None


async def test_a_refusal_is_reported_verbatim_and_never_diagnosed(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The hub's sentence, plus a neutral pointer. No invented cause.

    Live failure, 2026-08-09: the operator linked Home Assistant, the hub
    minted a token, and the very first authenticated call — this one — came
    back `403 FORBIDDEN`. Home Assistant reported "authentication expired",
    the operator re-linked, and it failed again 25 seconds later. Three
    unrevoked sessions later the hub had still never rejected a credential.

    So: a refusal is a permanent setup error carrying the hub's own sentence,
    and it must NOT raise `ConfigEntryAuthFailed`. Reauth is for a credential
    that stopped working; nothing about this one ever did.

    And the reason STOPS at the hub's sentence. The fix that followed shipped
    a diagnosis instead — "the link is valid, re-linking mints the same grant,
    update CamStack first" — which was false: the refusal came from a
    scope-enforcement regression on the hub. A client cannot see hub-side
    causes, so it must not assert one.
    """

    async def respond(path: str, payload: Any | None = None) -> Any:
        if path == "deviceExport.listExposedDevices":
            raise CamStackForbiddenError(f"{path}: {SCOPE_REFUSAL}")
        return await make_query_responder()(path, payload)

    mock_client.query = AsyncMock(side_effect=respond)
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    reason = config_entry.reason or ""
    # The hub's message, whole and unedited.
    assert SCOPE_REFUSAL in reason
    # Plus a pointer that is true whatever the hub's reason turns out to be.
    assert "The hub refused the request with the message above" in reason
    # And no causal claim about the hub: those go stale and mislead.
    for invented in (
        "re-link",
        "mints",
        "updating CamStack",
        "The link itself is valid",
    ):
        assert invented not in reason
    # No reauth flow: reauth replays the same credential and cannot help.
    assert not [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"].get("source") == SOURCE_REAUTH
    ]


def test_a_forbidden_is_not_a_bad_request() -> None:
    """403 and 400 are both permanent, and they are not the same permanent.

    A bad request is the component's own input being wrong; a forbidden is the
    hub declining a call it understood. They read differently and they are
    fixed differently, so they are typed apart.
    """
    with pytest.raises(CamStackForbiddenError) as excinfo:
        _unwrap(
            "deviceExport.listExposedDevices",
            json.dumps({"error": {"json": {"message": SCOPE_REFUSAL, "code": -32003}}}),
        )
    assert not isinstance(excinfo.value, CamStackRequestError)
    assert "device-export" in str(excinfo.value)


def test_a_bad_request_is_its_own_error() -> None:
    """A 400 from the hub is not a connection problem, and is typed apart.

    Retrying cannot change the answer to a request the hub refused on its
    input; treating it as transient is how it ends up retried forever behind a
    generic "cannot connect".
    """
    with pytest.raises(CamStackRequestError) as excinfo:
        _unwrap(
            "deviceExport.listExposedDevices",
            json.dumps({"error": UNRESOLVABLE_ADDON_ERROR}),
        )
    assert "Valid addonId(s)" in str(excinfo.value)
