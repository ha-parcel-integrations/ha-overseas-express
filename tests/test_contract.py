"""The parcel contract's checks, run over every normalised sample."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.overseas_express.const import ParcelStatus
from custom_components.overseas_express.parcels import (
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
)

from .payloads import (
    active_sample,
    created_sample,
    delivered_sample,
    event,
    pickup_sample,
)

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]

ZONE = ZoneInfo("Europe/Zagreb")


def _with_status(name: str) -> dict:
    sample = pickup_sample()
    sample["shipmentDetails"]["shipmentStatus"] = name
    return sample


def _collected() -> dict:
    sample = pickup_sample()
    sample["shipmentDetails"]["shipmentStatus"] = "LockerPickup"
    sample["eventDetailList"] = [
        event("DELY", "2026-11-22T16:00:00Z", "Collected"),
        event("DEPS", "2026-11-21T09:00:00Z"),
    ]
    return sample


def _collected_without_shop_block() -> dict:
    sample = _collected()
    sample["shipmentDetails"]["parcelShopAddress"] = None
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "created": created_sample,
    "at_pickup_point": pickup_sample,
    "collected": _collected,
    "returning": lambda: _with_status("Returned"),
    "problem": lambda: _with_status("DeliveryFailed"),
    "bare": lambda: {"trackingNumber": "10000000000009"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_delivered_at_is_the_delivery_event():
    parcel = normalize_parcel(_collected())
    assert parse_iso(parcel["delivered_at"]) == datetime(
        2026, 11, 22, 16, 0, tzinfo=UTC
    )


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Example Shop Point, Example City"


def test_pickup_survives_collection_through_the_events_alone():
    parcel = normalize_parcel(_collected_without_shop_block())
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None


def test_a_home_delivery_is_not_pickup():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_a_returning_pickup_parcel_stays_pickup_and_undelivered():
    parcel = normalize_parcel(_with_status("Returned"))
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["pickup"] is True


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2099-04-29T13:00:00Z", None, zone=ZONE)
    assert start == "2099-04-29T13:00:00Z"
    stop = datetime.fromisoformat(end)
    assert (stop.hour, stop.minute, stop.second, stop.microsecond) == (23, 59, 59, 0)
    assert stop.utcoffset() == ZONE.utcoffset(datetime(2099, 4, 29))


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2099-04-29", None, zone=ZONE)
    first = datetime.fromisoformat(start)
    last = datetime.fromisoformat(end)
    assert (first.hour, first.minute, first.second) == (0, 0, 0)
    assert (last.hour, last.minute, last.second, last.microsecond) == (23, 59, 59, 0)
    assert first.date() == last.date()


def test_the_window_is_emptied_once_the_parcel_is_done():
    window = ("2099-04-29T00:00:00+02:00", "2099-04-29T23:59:59+02:00")
    assert planned_for_status(ParcelStatus.IN_TRANSIT, *window) == window
    for status in (
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
        ParcelStatus.DELIVERED,
    ):
        assert planned_for_status(status, *window) == (None, None)
