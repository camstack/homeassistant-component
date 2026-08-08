"""Tests for the tRPC envelope handling.

These are the parts that talk to the wire, so they are tested against the exact
byte shapes the hub emits rather than through the client's own abstractions.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.camstack.api import (
    CamStackApiError,
    CamStackAuthError,
    CamStackClient,
    _parse_sse_line,
    _unwrap,
)


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


def test_sse_line_yields_the_inner_json_envelope() -> None:
    line = (
        b'data: {"json":{"category":"device.state-changed",'
        b'"data":{"deviceId":615,"capName":"motion","slice":{"detected":true}}},'
        b'"meta":{"v":1}}'
    )
    event = _parse_sse_line(line)
    assert event is not None
    assert event["category"] == "device.state-changed"
    assert event["data"]["deviceId"] == 615


@pytest.mark.parametrize(
    "line",
    [b"event: connected", b"", b"\n", b"data: ", b"data: not-json"],
)
def test_sse_framing_lines_are_discarded(line: bytes) -> None:
    assert _parse_sse_line(line) is None


async def test_the_event_stream_refuses_compression() -> None:
    """Refuse compression on the event stream.

    The hub gzips `text/event-stream` when a client asks it to, and the
    compressor only flushes when its buffer fills. Measured against the live
    hub: an 8-second filtered subscription delivered 0 events under the
    default `gzip` and 56 under `identity`. Losing this header does not fail
    loudly — it makes motion arrive late, or never.
    """
    session = _RecordingSession()
    client = CamStackClient(session, "hub", 4443, "u", "p")
    client._token = "token"

    async for _event in client.subscribe_events("device.state-changed"):
        pass

    assert session.headers["accept-encoding"] == "identity"


class _RecordingSession:
    """Minimal aiohttp stand-in that records the headers of one GET."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def get(self, _url: str, *, headers: dict[str, str], **_kwargs: object) -> Any:
        self.headers = headers
        return _EmptyStreamResponse()


class _EmptyStreamResponse:
    """A 200 response whose body ends immediately."""

    status = 200

    @property
    def content(self) -> Any:
        async def empty() -> Any:
            return
            yield b""  # pragma: no cover

        return empty()

    async def __aenter__(self) -> _EmptyStreamResponse:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False
