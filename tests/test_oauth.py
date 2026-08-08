"""Tests for OAuth onboarding.

The hub-side half of this ships with the CamStack server, not with an addon,
so a hub that predates it answers 404 to the discovery probe. Every test below
is about one of the two things that go wrong in practice: a hub that cannot do
this yet, and a token exchange that quietly verifies TLS against a self-signed
certificate.
"""

from __future__ import annotations

import base64
import hashlib
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.camstack.api import CamStackConnectionError
from custom_components.camstack.const import (
    CONF_VERIFY_SSL,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.camstack.oauth import (
    CamStackOAuth2Implementation,
    async_probe_oauth,
    implementation_domain,
)

HUB_INPUT = {CONF_HOST: "192.168.1.9", CONF_PORT: 4443, CONF_VERIFY_SSL: False}
DISCOVERY_URL = "https://192.168.1.9:4443/api/oauth2/integrations"
TOKEN_URL = "https://192.168.1.9:4443/api/oauth2/token"

SUPPORTED = {
    "integrations": [
        {
            "integrationId": "export-alexa",
            "displayName": "Alexa Smart Home",
            "requiresPkce": False,
        },
        {
            "integrationId": "homeassistant",
            "displayName": "Home Assistant",
            "requiresPkce": True,
        },
    ]
}


# ── Discovery ────────────────────────────────────────────────────────────────


async def test_probe_reports_support(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(DISCOVERY_URL, json=SUPPORTED)
    assert await async_probe_oauth(hass, "192.168.1.9", 4443, verify_ssl=False) is True


async def test_probe_reports_an_old_hub_rather_than_failing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    # This is the case the whole probe exists for: a hub released before the
    # feature. It must read as "not yet", never as "broken".
    aioclient_mock.get(DISCOVERY_URL, status=404)
    assert await async_probe_oauth(hass, "192.168.1.9", 4443, verify_ssl=False) is False


async def test_probe_reports_a_hub_that_knows_other_integrations_only(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        DISCOVERY_URL,
        json={
            "integrations": [{"integrationId": "export-alexa", "requiresPkce": False}]
        },
    )
    assert await async_probe_oauth(hass, "192.168.1.9", 4443, verify_ssl=False) is False


async def test_probe_reports_html_from_a_proxy_as_unsupported(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(DISCOVERY_URL, text="<html>404</html>")
    assert await async_probe_oauth(hass, "192.168.1.9", 4443, verify_ssl=False) is False


async def test_probe_raises_when_the_hub_is_unreachable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    # An unreachable hub is a DIFFERENT problem from an old one, and offering
    # the manual path here would hide a wrong address.
    aioclient_mock.get(DISCOVERY_URL, exc=TimeoutError())
    with pytest.raises(CamStackConnectionError):
        await async_probe_oauth(hass, "192.168.1.9", 4443, verify_ssl=False)


# ── The implementation ───────────────────────────────────────────────────────


def _challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


async def test_the_authorize_data_carries_a_matching_s256_challenge(
    hass: HomeAssistant,
) -> None:
    impl = CamStackOAuth2Implementation(hass, "192.168.1.9", 4443, verify_ssl=False)
    data = impl.extra_authorize_data

    assert data["integration"] == "homeassistant"
    assert data["code_challenge_method"] == "S256"
    # The hub recomputes exactly this. If the pair does not match, linking
    # fails at the very last step with an opaque invalid_grant.
    assert data["code_challenge"] == _challenge_for(impl._code_verifier)


async def test_each_authorization_gets_its_own_verifier(hass: HomeAssistant) -> None:
    impl = CamStackOAuth2Implementation(hass, "192.168.1.9", 4443, verify_ssl=False)
    first = impl.extra_authorize_data["code_challenge"]
    second = impl.extra_authorize_data["code_challenge"]
    assert first != second


async def test_the_token_exchange_sends_the_verifier_and_no_secret(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    impl = CamStackOAuth2Implementation(hass, "192.168.1.9", 4443, verify_ssl=False)
    verifier = impl.extra_authorize_data and impl._code_verifier
    aioclient_mock.post(
        TOKEN_URL,
        json={
            "access_token": "a",
            "refresh_token": "r",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    )

    token = await impl.async_resolve_external_data(
        {"code": "the-code", "state": {"redirect_uri": "https://example.com/cb"}}
    )

    assert token["access_token"] == "a"
    sent = dict(aioclient_mock.mock_calls[0][2])
    assert sent["code_verifier"] == verifier
    assert sent["client_id"] == "homeassistant"
    # A published client cannot keep a secret, and sending an empty one would
    # be a value that looks like a credential to whoever reads the traffic.
    assert "client_secret" not in sent


async def test_the_token_exchange_honours_verify_ssl(hass: HomeAssistant) -> None:
    """The hub's certificate is self-signed by default.

    Home Assistant's own implementation reaches for a VERIFYING session. If
    that is what runs, linking fails on a stock hub while every other call in
    this integration succeeds — the worst possible failure to diagnose.
    """
    seen: dict[str, Any] = {}

    def _record(_hass: HomeAssistant, verify_ssl: bool = True) -> Any:
        seen["verify_ssl"] = verify_ssl
        raise AssertionError("stop here — the session choice is what is under test")

    impl = CamStackOAuth2Implementation(hass, "192.168.1.9", 4443, verify_ssl=False)
    impl.extra_authorize_data  # noqa: B018 — mint a verifier
    with (
        patch("custom_components.camstack.oauth.async_get_clientsession", _record),
        pytest.raises(AssertionError),
    ):
        await impl.async_resolve_external_data(
            {"code": "c", "state": {"redirect_uri": "https://example.com/cb"}}
        )

    assert seen["verify_ssl"] is False


async def test_the_implementation_key_is_derived_from_the_address() -> None:
    # It is written into the entry as `auth_implementation` and looked up again
    # after a restart, so it cannot be random and two hubs cannot collide.
    assert implementation_domain("192.168.1.9", 4443) == "192.168.1.9:4443"
    assert implementation_domain("10.0.0.2", 4443) != implementation_domain(
        "192.168.1.9", 4443
    )


# ── The flow ─────────────────────────────────────────────────────────────────


async def test_the_menu_offers_both_ways_in(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"oauth", "manual"}


async def test_an_old_hub_is_told_apart_from_a_broken_one(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(DISCOVERY_URL, status=404)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "oauth"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], HUB_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "oauth_unsupported"}


async def test_an_unreachable_hub_says_so(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(DISCOVERY_URL, exc=TimeoutError())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "oauth"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], HUB_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.usefixtures("current_request_with_host")
async def test_the_full_link_stores_the_token_beside_the_address(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Walk the whole round trip and check what lands in the entry.

    A token with no address is a token nobody can use: the panel, the card and
    the API client all read host/port from the same entry.
    """
    # Registers Home Assistant's own /auth/external/callback view — the piece
    # this flow deliberately does NOT reimplement.
    assert await async_setup_component(hass, "auth", {})
    aioclient_mock.get(DISCOVERY_URL, json=SUPPORTED)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "oauth"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], HUB_INPUT
    )

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert "integration=homeassistant" in result["url"]
    assert "code_challenge_method=S256" in result["url"]
    assert "client_secret" not in result["url"]

    state = config_entry_oauth2_flow._encode_jwt(
        hass,
        {
            "flow_id": result["flow_id"],
            "redirect_uri": "https://example.com/auth/external/callback",
        },
    )
    client = await hass_client_no_auth()
    response = await client.get(f"/auth/external/callback?code=the-code&state={state}")
    assert response.status == 200

    aioclient_mock.post(
        TOKEN_URL,
        json={
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    )
    with patch(
        "custom_components.camstack.async_setup_entry", return_value=True
    ) as setup:
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data["token"]["access_token"] == "access-1"
    assert data["auth_implementation"] == "192.168.1.9:4443"
    assert data[CONF_HOST] == "192.168.1.9"
    assert data[CONF_PORT] == 4443
    assert data[CONF_VERIFY_SSL] is False
    # No password anywhere near the entry — that is the point of the exercise.
    assert "password" not in data
    assert len(setup.mock_calls) == 1


async def test_a_linked_entry_authenticates_without_a_password() -> None:
    """`OAuth2Auth` returns whatever Home Assistant has refreshed."""
    from custom_components.camstack.oauth import OAuth2Auth

    session = AsyncMock()
    session.token = {"access_token": "access-9"}
    auth = OAuth2Auth(session)

    assert await auth.async_token() == "access-9"
    session.async_ensure_token_valid.assert_awaited()


async def test_a_linked_entry_loads_after_a_restart(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """Set an OAuth entry up with NOTHING registered beforehand.

    Home Assistant's implementation registry is in-memory, so after a restart
    it is empty. The entry has to be able to rebuild its own implementation
    from what it stored, or every linked hub comes back dead on boot with an
    unhelpful `missing_configuration`.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="CamStack (192.168.1.9)",
        unique_id="192.168.1.9:4443",
        version=CONFIG_ENTRY_VERSION,
        data={
            CONF_HOST: "192.168.1.9",
            CONF_PORT: 4443,
            CONF_VERIFY_SSL: False,
            "auth_implementation": "192.168.1.9:4443",
            "token": {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_at": time.time() + 3600,
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
