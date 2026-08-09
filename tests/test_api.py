"""Tests for the tRPC envelope handling.

These are the parts that talk to the wire, so they are tested against the exact
byte shapes the hub emits rather than through the client's own abstractions.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.camstack.api import (
    CamStackApiError,
    CamStackAuth,
    CamStackAuthError,
    CamStackClient,
    _unwrap,
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
