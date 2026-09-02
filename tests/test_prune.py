"""Pruning devices the hub stopped exporting, and refusing to do it too eagerly.

The push protocol has no removal message, so a camera taken out of the export
just sat in Home Assistant's registry for good. This is the cleanup — and the
half that matters is what it REFUSES: a single successful-but-empty read is
indistinguishable from "the operator removed everything", and acting on one
would take the fleet and its history with it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.helpers import device_registry as dr

from custom_components.camstack.const import DOMAIN, SYNTHETIC_DEVICE_KEYS
from custom_components.camstack.coordinator import CamStackData, CamStackDevice
from custom_components.camstack.prune import StaleDevicePruner


def _device(device_id: int) -> CamStackDevice:
    return CamStackDevice(
        device_id=device_id,
        name=f"cam-{device_id}",
        device_type="camera",
        addon_id="reolink",
        is_camera=True,
        disabled=False,
    )


def _data(*ids: int) -> CamStackData:
    return CamStackData(devices={i: _device(i) for i in ids})


def _registry_entry(key: str, entry_id: str = "e1") -> Any:
    entry = MagicMock()
    entry.id = f"reg-{key}"
    entry.identifiers = {(DOMAIN, key)}
    return entry


class _Registry:
    """Just enough device registry to watch what the pruner asks of it."""

    def __init__(self, entries: list[Any]) -> None:
        self.entries = entries
        self.removed: list[tuple[str, str]] = []

    def async_update_device(self, device_id: str, **kwargs: Any) -> None:
        self.removed.append((device_id, kwargs["remove_config_entry_id"]))


def _pruner(registry: _Registry) -> tuple[StaleDevicePruner, Any]:
    entry = MagicMock()
    entry.entry_id = "e1"
    pruner = StaleDevicePruner(MagicMock(), entry)
    return pruner, entry


def _run(
    pruner: StaleDevicePruner, registry: _Registry, data: CamStackData
) -> list[str]:
    with (
        patch.object(dr, "async_get", return_value=registry),
        patch.object(
            dr, "async_entries_for_config_entry", return_value=registry.entries
        ),
    ):
        return pruner.async_apply(data)


@pytest.mark.asyncio
async def test_one_read_alone_never_removes_anything() -> None:
    """The whole point of the gate.

    A refresh that succeeds can still answer with less than the truth — the
    export addon restarting, a membership not yet loaded. On that answer alone,
    nothing may go.
    """
    registry = _Registry([_registry_entry("camstack-590")])
    pruner, _ = _pruner(registry)

    assert _run(pruner, registry, _data(615)) == []
    assert registry.removed == []


@pytest.mark.asyncio
async def test_two_consecutive_reads_that_agree_do_remove_it() -> None:
    registry = _Registry([_registry_entry("camstack-590")])
    pruner, _ = _pruner(registry)

    _run(pruner, registry, _data(615))
    removed = _run(pruner, registry, _data(615))

    assert removed == ["reg-camstack-590"]
    assert registry.removed == [("reg-camstack-590", "e1")]


@pytest.mark.asyncio
async def test_a_device_that_comes_back_between_the_two_reads_is_spared() -> None:
    """The anomalous answer is forgotten, not banked."""
    registry = _Registry([_registry_entry("camstack-590")])
    pruner, _ = _pruner(registry)

    _run(pruner, registry, _data(615))  # 590 looks gone
    _run(pruner, registry, _data(615, 590))  # ...and is back
    removed = _run(pruner, registry, _data(615, 590))

    assert removed == []
    assert registry.removed == []


@pytest.mark.asyncio
async def test_a_failed_refresh_disarms_the_count() -> None:
    """`async_forget` is what the caller uses when the read did not happen."""
    registry = _Registry([_registry_entry("camstack-590")])
    pruner, _ = _pruner(registry)

    _run(pruner, registry, _data(615))
    pruner.async_forget()
    removed = _run(pruner, registry, _data(615))

    assert removed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("key", sorted(SYNTHETIC_DEVICE_KEYS))
async def test_the_synthetic_devices_are_never_pruned(key: str) -> None:
    """Spare the synthetic devices.

    They are pushed regardless of the membership, so they are never in the
    listing — and deleting one would only have the next push rebuild it.
    """
    registry = _Registry([_registry_entry(key)])
    pruner, _ = _pruner(registry)

    _run(pruner, registry, _data(615))
    removed = _run(pruner, registry, _data(615))

    assert removed == []


@pytest.mark.asyncio
async def test_another_integrations_device_is_not_ours_to_remove() -> None:
    foreign = MagicMock()
    foreign.id = "reg-foreign"
    foreign.identifiers = {("othervendor", "thing-1")}
    registry = _Registry([foreign])
    pruner, _ = _pruner(registry)

    _run(pruner, registry, _data(615))
    removed = _run(pruner, registry, _data(615))

    assert removed == []
