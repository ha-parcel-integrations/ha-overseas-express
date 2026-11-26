"""Tests for Overseas Express diagnostics."""

from datetime import timedelta
from unittest.mock import MagicMock

from homeassistant.components.diagnostics import async_redact_data

from custom_components.overseas_express.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
)
from custom_components.overseas_express.parcels import normalize_parcel

from .payloads import CONTACT_PERSON, created_sample


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "10000000000001"}]}
    entry.runtime_data.coordinator.current_tier_minutes = 15
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=15)
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "10000000000001",
            "sender": "Example Shop",
            "receiver": "Jane Doe",
            "contents": "Running shoes",
            "status": "out_for_delivery",
            "raw": {
                "trackingNumber": "10000000000001",
                "consignee": {"contactPerson": "Jane Doe", "city": "Zagreb"},
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.delivered_codes = set()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {
        "incoming_active": 1,
        "delivered": 0,
        "skipped_from_fetch": 0,
    }
    assert result["polling"] == {
        "tier_minutes": 15,
        "update_interval_seconds": 900.0,
        "suspended": False,
    }
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["receiver"] == "**REDACTED**"
    assert result["incoming"][0]["contents"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["consignee"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "out_for_delivery"


async def test_diagnostics_redacts_every_code_copy_and_person_key(hass):
    """The entered code, its copies and every shipper/consignee key are gone."""
    sample = created_sample()
    sample["shipmentDetails"]["consignee"]["contactPerson"] = CONTACT_PERSON
    parcel = normalize_parcel(sample)
    # the contact person never reaches raw; put it back to prove redaction too
    parcel["raw"]["shipmentDetails"]["consignee"]["contactPerson"] = CONTACT_PERSON
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "10000000000001"}]}
    entry.runtime_data.coordinator.current_tier_minutes = 45
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=45)
    entry.runtime_data.coordinator.data = [parcel]
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.delivered_codes = set()

    dump = str(await async_get_config_entry_diagnostics(hass, entry))

    for leaked in (
        "10000000000001",
        "2000000000001",
        "300000000000000000000001",
        "4000001",
        "5000001",
        "000000000000000000000001",
        "Placeholder",
        "Example Shop",
        "00000",
        "p*****",
        "+385",
        "1.5",
    ):
        assert leaked not in dump, leaked
    # depot names, event codes and services stay
    assert "Example Depot" in dump
    assert "DDEF" in dump
    assert "Standard" in dump


async def test_diagnostics_redacts_nested_person_keys(hass):
    """Each listed key is redacted wherever it nests, even with a new parent."""
    keys = [
        "name", "contactPerson", "phone", "gsm", "email", "street", "addressText",
        "geocodedAddressText", "houseNumber", "doorNumber", "city", "zipCode",
        "latitude", "longitude", "location", "title", "shipmentId", "externalId",
        "id", "integrationCode", "integrationCode1", "integrationCode2",
        "integrationCode3", "internationalCode", "legacySystemCargoId",
        "originalShipmentId", "legacySystemShortBarcode", "barcode",
    ]  # fmt: skip
    data = {"future_block": [{"deeper": dict.fromkeys(keys, "secret")}]}
    assert "secret" not in str(async_redact_data(data, TO_REDACT))


async def test_diagnostics_reports_suspended_polling(hass):
    """update_interval None (Section 2.1's full stop) must be visible, not just absent."""
    entry = MagicMock()
    entry.options = {"parcels": []}
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"] == {
        "tier_minutes": None,
        "update_interval_seconds": None,
        "suspended": True,
    }
