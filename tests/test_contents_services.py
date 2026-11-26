"""Tests for the contents action, the track_parcel field and the store lifecycle."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.overseas_express.const import (
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)
from custom_components.overseas_express.contents import CONTENTS_STORE_MAX_CODES
from custom_components.overseas_express.coordinator import OverseasExpressCoordinator
from custom_components.overseas_express.parcels import normalize_parcel

from .payloads import ACTIVE_CODE, active_sample, delivered_sample

CLIENT = "custom_components.overseas_express.api.OverseasExpressApiClient"
REFRESH = f"{OverseasExpressCoordinator.__module__}.OverseasExpressCoordinator"
UNTRACKED = "EXAMPLE555555"


async def _setup(hass, codes: list[str] | None = None, sample=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: c} for c in codes or []]},
    )
    entry.add_to_hass(hass)
    with patch(
        f"{CLIENT}.async_get_parcel",
        new=AsyncMock(side_effect=lambda _code: (sample or active_sample)()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _call(hass, service: str, data: dict) -> None:
    with patch(
        f"{CLIENT}.async_get_parcel",
        new=AsyncMock(side_effect=lambda _code: active_sample()),
    ):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)
        await hass.async_block_till_done()
        # A refresh requested inside the debouncer's cooldown runs after it.
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done()


def _only_contents(entry: MockConfigEntry) -> str | None:
    return entry.runtime_data.coordinator.data[0]["contents"]


async def test_a_parcel_has_no_contents_without_a_stored_value(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    assert _only_contents(entry) is None


async def test_update_contents_sets_replaces_and_clears(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    data = {CONF_TRACKING_CODE: ACTIVE_CODE}

    await _call(hass, "update_contents", {**data, "contents": " Running  shoes "})
    assert _only_contents(entry) == "Running shoes"

    await _call(hass, "update_contents", {**data, "contents": "Winter socks"})
    assert _only_contents(entry) == "Winter socks"

    await _call(hass, "update_contents", {**data, "contents": "  "})
    assert _only_contents(entry) is None


async def test_update_contents_without_text_clears(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    data = {CONF_TRACKING_CODE: ACTIVE_CODE}
    await _call(hass, "update_contents", {**data, "contents": "Running shoes"})

    await _call(hass, "update_contents", data)

    assert _only_contents(entry) is None


async def test_update_contents_works_for_a_code_that_is_not_tracked(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: UNTRACKED, "contents": "Running shoes"},
    )

    assert entry.runtime_data.coordinator.contents_store.get(UNTRACKED) == (
        "Running shoes"
    )
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_update_contents_never_polls_and_notifies_once_per_change(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    coordinator = entry.runtime_data.coordinator
    data = {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"}
    with (
        patch(f"{REFRESH}.async_request_refresh", new=AsyncMock()) as refresh,
        patch(f"{CLIENT}.async_get_parcel", new=AsyncMock()) as fetch,
        patch.object(
            coordinator,
            "async_update_listeners",
            wraps=coordinator.async_update_listeners,
        ) as listeners,
    ):
        await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
        assert listeners.call_count == 1
        await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
        assert listeners.call_count == 1
        await hass.services.async_call(
            DOMAIN, "update_contents", {CONF_TRACKING_CODE: UNTRACKED}, blocking=True
        )
        assert listeners.call_count == 1
        await hass.services.async_call(
            DOMAIN, "update_contents", {CONF_TRACKING_CODE: ACTIVE_CODE}, blocking=True
        )
        assert listeners.call_count == 2
    refresh.assert_not_called()
    fetch.assert_not_called()
    assert _only_contents(entry) is None


async def test_update_contents_rejects_an_empty_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "update_contents", {CONF_TRACKING_CODE: ""})


async def test_update_contents_needs_a_loaded_entry(hass):
    entry = await _setup(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
        )


async def test_track_parcel_stores_contents_for_a_new_code(hass):
    entry = await _setup(hass)
    await _call(
        hass,
        "track_parcel",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]
    assert _only_contents(entry) == "Running shoes"


async def test_track_parcel_without_contents_is_unchanged(hass):
    entry = await _setup(hass)
    await _call(hass, "track_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert entry.runtime_data.coordinator.contents_store.get(ACTIVE_CODE) is None
    assert _only_contents(entry) is None


async def test_track_parcel_replaces_and_clears_contents_of_a_tracked_code(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    track = {CONF_TRACKING_CODE: ACTIVE_CODE}

    await _call(hass, "track_parcel", {**track, "contents": "Running shoes"})
    assert _only_contents(entry) == "Running shoes"

    await _call(hass, "track_parcel", {**track, "contents": "Winter socks"})
    assert _only_contents(entry) == "Winter socks"

    await _call(hass, "track_parcel", {**track, "contents": ""})
    assert _only_contents(entry) is None
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_track_parcel_on_a_tracked_code_never_polls(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    coordinator = entry.runtime_data.coordinator
    data = {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"}
    with (
        patch(f"{REFRESH}.async_request_refresh", new=AsyncMock()) as refresh,
        patch(f"{CLIENT}.async_get_parcel", new=AsyncMock()) as fetch,
        patch.object(
            coordinator,
            "async_update_listeners",
            wraps=coordinator.async_update_listeners,
        ) as listeners,
    ):
        await hass.services.async_call(DOMAIN, "track_parcel", data, blocking=True)
        await hass.services.async_call(DOMAIN, "track_parcel", data, blocking=True)
    assert listeners.call_count == 1
    refresh.assert_not_called()
    fetch.assert_not_called()
    assert _only_contents(entry) == "Running shoes"


async def test_a_newly_tracked_code_still_refreshes_via_the_options_listener(hass):
    entry = await _setup(hass)
    fetch = AsyncMock(side_effect=lambda _code: active_sample())
    with patch(f"{CLIENT}.async_get_parcel", new=fetch):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
            blocking=True,
        )
        await hass.async_block_till_done()
    fetch.assert_awaited()
    assert _only_contents(entry) == "Running shoes"


async def test_untrack_parcel_drops_the_contents(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert entry.runtime_data.coordinator.contents_store.get(ACTIVE_CODE) is None
    assert entry.options[CONF_PARCELS] == []


async def test_untrack_with_an_unloaded_entry_still_untracks(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})
    assert entry.options[CONF_PARCELS] == []


async def test_the_store_caps_at_200_codes_dropping_the_oldest(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    store = entry.runtime_data.coordinator.contents_store
    for index in range(CONTENTS_STORE_MAX_CODES + 1):
        await hass.services.async_call(
            DOMAIN,
            "update_contents",
            {CONF_TRACKING_CODE: f"CODE{index}", "contents": "Shoes"},
            blocking=True,
        )
    assert store.get("CODE0") is None
    assert store.get("CODE1") == "Shoes"
    assert store.get(f"CODE{CONTENTS_STORE_MAX_CODES}") == "Shoes"


async def test_a_stored_value_survives_a_restart_of_the_entry(hass, hass_storage):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()

    with patch(
        f"{CLIENT}.async_get_parcel", new=AsyncMock(return_value=active_sample())
    ):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert _only_contents(entry) == "Running shoes"


async def test_an_entry_without_a_stored_file_works(hass, hass_storage):
    assert not hass_storage
    entry = await _setup(hass, [ACTIVE_CODE])
    assert _only_contents(entry) is None


async def test_removing_the_entry_deletes_the_stored_file(hass, hass_storage):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()
    key = f"{DOMAIN}.contents.{entry.entry_id}"
    assert key in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_reapply_contents_covers_delivered_parcels(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    coordinator = entry.runtime_data.coordinator
    coordinator.delivered = [normalize_parcel(delivered_sample(ACTIVE_CODE))]

    coordinator.contents_store.async_set(ACTIVE_CODE, "Running shoes")
    coordinator.async_reapply_contents()
    assert coordinator.delivered[0]["contents"] == "Running shoes"

    coordinator.contents_store.async_set(ACTIVE_CODE, None)
    coordinator.async_reapply_contents()
    assert coordinator.delivered[0]["contents"] is None


async def test_a_barcode_less_parcel_keeps_its_contents_after_reapply_and_a_poll(hass):
    def barcode_less() -> dict:
        raw = active_sample()
        raw["trackingNumber"] = None
        raw["shipmentDetails"]["externalId"] = None
        return raw

    entry = await _setup(hass, [ACTIVE_CODE], sample=barcode_less)
    coordinator = entry.runtime_data.coordinator
    assert not coordinator.data[0]["barcode"]

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    assert _only_contents(entry) == "Running shoes"

    coordinator.async_reapply_contents()
    assert _only_contents(entry) == "Running shoes"

    with patch(
        f"{CLIENT}.async_get_parcel",
        new=AsyncMock(side_effect=lambda _c: barcode_less()),
    ):
        await coordinator.async_refresh()
    assert _only_contents(entry) == "Running shoes"
