"""Tests for the tRPC envelope handling.

These are the parts that talk to the wire, so they are tested against the exact
byte shapes the hub emits rather than through the client's own abstractions.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from custom_components.camstack.api import (
    CamStackApiError,
    CamStackAuth,
    CamStackAuthError,
    CamStackClient,
    CamStackForbiddenError,
    _unwrap,
)

# What the hub actually answered on 2026-08-09 when a linked Home Assistant made
# its first authenticated call. The token was VALID — the grant behind the link
# simply did not cover this capability. Recorded verbatim because the message is
# the fix instruction, and because a fake that answers a bare 403 would not
# prove the reason survives to the operator.
FORBIDDEN_BODY: str = json.dumps(
    {
        "error": {
            "json": {
                "message": (
                    "No scope grants view on 'device-export' (system-scope cap). "
                    "Have: category:device[view,create], "
                    "capability:device-manager[view], capability:device-state[view], "
                    "capability:pipeline-orchestrator[view,create], "
                    "addon:homeassistant-export[view,create]"
                ),
                "code": -32003,
                "data": {
                    "code": "FORBIDDEN",
                    "httpStatus": 403,
                    "path": "deviceExport.listExposedDevices",
                },
            }
        }
    }
)


class _FixedTokenAuth(CamStackAuth):
    """Hands out one token and counts how often it was asked to renew."""

    def __init__(self, token: str = "token") -> None:
        self.token = token
        self.renewals = 0

    async def async_token(self) -> str:
        return self.token

    async def async_renew(self) -> str:
        self.renewals += 1
        return self.token


def test_unwrap_returns_the_json_payload() -> None:
    assert _unwrap("x", '{"result":{"data":{"json":[1,2]}}}') == [1, 2]


def test_unwrap_maps_unauthorized_onto_an_auth_error() -> None:
    body = '{"error":{"json":{"message":"UNAUTHORIZED","code":-32001}}}'
    with pytest.raises(CamStackAuthError):
        _unwrap("x", body)


def test_unwrap_maps_other_errors_onto_an_api_error() -> None:
    body = '{"error":{"json":{"message":"no such device","code":-32603}}}'
    with pytest.raises(CamStackApiError, match="no such device"):
        _unwrap("x", body)


def test_unwrap_rejects_a_non_json_body() -> None:
    with pytest.raises(CamStackApiError):
        _unwrap("x", "<html>502</html>")


async def test_a_rejected_token_is_renewed_exactly_once() -> None:
    """Renew once behind a 401, then let the second failure surface.

    Retrying forever behind a credential that is genuinely dead is how an
    integration hammers a hub and never reaches reauth.
    """
    auth = _FixedTokenAuth()
    session = _AlwaysUnauthorizedSession()
    client = CamStackClient(session, "hub", 4443, auth)

    with pytest.raises(CamStackAuthError):
        await client.query("auth.me")

    assert auth.renewals == 1
    assert session.calls == 2


def test_unwrap_maps_forbidden_onto_a_permission_error() -> None:
    """A refusal is not an expired login, and must not be typed as one.

    `-32003` means the hub verified the token and refused the OPERATION. A
    fresh token cannot change the answer, so calling it an auth error sends
    Home Assistant to reauth — and the operator re-links a link that was
    already valid, forever.
    """
    with pytest.raises(CamStackForbiddenError) as excinfo:
        _unwrap("deviceExport.listExposedDevices", FORBIDDEN_BODY)

    # The hub names the missing grant. That sentence is the whole fix, so it
    # has to survive to whoever reads the error.
    assert "No scope grants view on 'device-export'" in str(excinfo.value)
    assert not isinstance(excinfo.value, CamStackAuthError)


async def test_a_403_is_not_a_credential_problem_and_is_never_renewed() -> None:
    """Renewing behind a 403 replays the same grant and fails identically.

    This is the loop that cost the operator three live sessions: the hub said
    "your grant does not cover this", the component heard "your token is
    stale", Home Assistant said "authentication expired", and re-linking
    produced another token with exactly the same grant.
    """
    auth = _FixedTokenAuth()
    session = _AlwaysForbiddenSession()
    client = CamStackClient(session, "hub", 4443, auth)

    with pytest.raises(CamStackForbiddenError) as excinfo:
        await client.query("deviceExport.listExposedDevices")

    assert auth.renewals == 0
    assert session.calls == 1
    assert "No scope grants view on 'device-export'" in str(excinfo.value)


async def test_a_403_on_an_addon_route_is_not_a_credential_problem_either() -> None:
    """Every HA control entity travels this path, and it has its own gate.

    The hub's addon-route gate answers `403 Token scope mismatch` when the
    link carries no `addon:` grant for the addon being posted to. That is one
    misconfiguration silencing every switch, button and select at once — and
    calling it an expired login would take the whole config entry down with it
    instead of leaving the read-only half working.
    """
    auth = _FixedTokenAuth()
    session = _AlwaysForbiddenSession()
    client = CamStackClient(session, "hub", 4443, auth)

    with pytest.raises(CamStackForbiddenError):
        await client.async_post_addon_route(
            "/addon/homeassistant-export/command", {"command": "reboot"}
        )

    assert auth.renewals == 0
    assert session.calls == 1


class _AlwaysUnauthorizedSession:
    """Answers 401 to everything, and counts."""

    def __init__(self) -> None:
        self.calls = 0

    def request(self, *_args: Any, **_kwargs: object) -> Any:
        self.calls += 1
        return _UnauthorizedResponse()


class _UnauthorizedResponse:
    """A 401."""

    status = 401

    async def __aenter__(self) -> _UnauthorizedResponse:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _AlwaysForbiddenSession:
    """Answers 403 to everything, and counts.

    Both entry points, because the tRPC surface and the addon-route surface
    are separate code paths in the client and the refusal has to be typed the
    same way on each.
    """

    def __init__(self) -> None:
        self.calls = 0

    def request(self, *_args: Any, **_kwargs: object) -> Any:
        self.calls += 1
        return _ForbiddenResponse()

    def post(self, *_args: Any, **_kwargs: object) -> Any:
        self.calls += 1
        return _ForbiddenResponse()


class _ForbiddenResponse:
    """A 403 carrying the hub's explanation in its body."""

    status = 403

    async def __aenter__(self) -> _ForbiddenResponse:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def text(self) -> str:
        return FORBIDDEN_BODY
