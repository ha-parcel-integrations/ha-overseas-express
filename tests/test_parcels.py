"""Tests for the pure parcel-mapping helpers."""

from datetime import UTC, datetime, timedelta
import logging

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.overseas_express.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    PENDING_CAPABILITIES,
    ParcelStatus,
)
from custom_components.overseas_express.parcels import (
    _EVENT_MAP,
    _STATUS_MAP,
    apply_delivered_filter,
    build_history,
    map_event_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
)

from .payloads import (
    CONTACT_PERSON,
    active_sample,
    created_sample,
    delivered_sample,
    event,
    pickup_sample,
)

# ---------------------------------------------------------------------------
# status maps
# ---------------------------------------------------------------------------


def _parcel_with(status: str, *events: dict) -> dict:
    sample = created_sample()
    sample["shipmentDetails"]["shipmentStatus"] = status
    if events:
        sample["eventDetailList"] = list(events)
    return sample


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Created", ParcelStatus.REGISTERED),
        ("PickupCancelled", ParcelStatus.REGISTERED),
        ("Pickedup", ParcelStatus.IN_TRANSIT),
        ("PickedUp", ParcelStatus.IN_TRANSIT),
        ("Redelivery", ParcelStatus.IN_TRANSIT),
        ("DeliveredToParcelShop", ParcelStatus.AT_PICKUP_POINT),
        ("InLocker", ParcelStatus.AT_PICKUP_POINT),
        ("Delivered", ParcelStatus.DELIVERED),
        ("LockerPickup", ParcelStatus.DELIVERED),
        ("Returned", ParcelStatus.RETURNING),
        ("Recalled", ParcelStatus.RETURNING),
        ("DeliveryFailed", ParcelStatus.PROBLEM),
        ("Cancelled", ParcelStatus.PROBLEM),
    ],
)
def test_shipment_status_map(name, expected):
    assert normalize_parcel(_parcel_with(name))["status"] == expected


def test_band_ordering_traps():
    """Names that read like another band map to their intended status."""
    assert _STATUS_MAP["DeliveredToParcelShop"] is ParcelStatus.AT_PICKUP_POINT
    assert _STATUS_MAP["PartiallyDelivered"] is ParcelStatus.IN_TRANSIT
    assert _STATUS_MAP["DeliveryWasNotDoneInShiftTime"] is ParcelStatus.IN_TRANSIT
    assert _STATUS_MAP["Return"] is ParcelStatus.RETURNING
    assert _STATUS_MAP["PickupCancelled"] is ParcelStatus.REGISTERED
    assert _STATUS_MAP["Cancelled"] is ParcelStatus.PROBLEM
    assert _STATUS_MAP["ConsigneeRefusedDelivery"] is ParcelStatus.PROBLEM
    assert _EVENT_MAP["DEPO"] is ParcelStatus.DELIVERED
    assert _EVENT_MAP["DEPS"] is ParcelStatus.AT_PICKUP_POINT
    assert _EVENT_MAP["RETS"] is ParcelStatus.RETURNING


def test_deliberately_unmapped_names_are_not_in_the_map():
    for name in ("None", "NonAtTask", "InformationOfDelivery", "ConsigneBringToBranch"):
        assert name not in _STATUS_MAP


def test_shipment_status_wins_over_a_delivered_event():
    """An unseen delivered event never promotes a parcel the status says moves on."""
    parcel = normalize_parcel(
        _parcel_with("Loaded", event("DELY", "2026-11-21T10:00:00Z"))
    )
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["delivered"] is False


def test_tour_event_overlays_in_transit_as_out_for_delivery():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["raw_status"] == "Loaded"


def test_tour_overlay_needs_the_tour_to_be_the_newest_event():
    parcel = normalize_parcel(
        _parcel_with(
            "Loaded",
            event("TOUR", "2026-11-21T07:00:00Z"),
            event("ENTY", "2026-11-21T18:00:00Z"),
        )
    )
    assert parcel["status"] == ParcelStatus.IN_TRANSIT


def test_tour_overlay_does_not_touch_other_bands():
    parcel = normalize_parcel(
        _parcel_with("Created", event("TOUR", "2026-11-21T07:00:00Z"))
    )
    assert parcel["status"] == ParcelStatus.REGISTERED


def test_tour_overlay_logs_once_for_confirmation(caplog):
    with caplog.at_level(logging.WARNING):
        normalize_parcel(active_sample())
        normalize_parcel(active_sample())
    assert caplog.text.count("newest event TOUR") == 1


def test_event_codes_are_matched_case_insensitively():
    parcel = normalize_parcel(
        _parcel_with("Loaded", event("tour", "2026-11-21T07:00:00Z"))
    )
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY


def test_an_undated_event_never_counts_as_the_newest():
    parcel = normalize_parcel(
        _parcel_with(
            "Loaded",
            event("ENTY", "2026-11-21T07:00:00Z"),
            event("TOUR", "not-a-date"),
        )
    )
    assert parcel["status"] == ParcelStatus.IN_TRANSIT


def test_unseen_event_type_is_logged_once(caplog):
    sample = _parcel_with("Created", event("DDEF", "2026-11-19T08:00:00Z", "Sorted"))
    with caplog.at_level(logging.WARNING):
        normalize_parcel(sample)
        normalize_parcel(sample)
    assert caplog.text.count("event type=Sorted first seen") == 1


def test_seen_event_types_are_not_logged(caplog):
    sample = _parcel_with(
        "Delivered",
        event("DELY", "2026-11-23T09:00:00Z", "Delivered"),
        event("TOUR", "2026-11-23T06:00:00Z", "CourierLeftFromBranch"),
        event("ENTY", "2026-11-23T05:00:00Z", "LegacySystemInfo"),
        event("INIT", "2026-11-20T12:00:00Z", "UnloadAtPickupHub"),
    )
    with caplog.at_level(logging.WARNING):
        normalize_parcel(sample)
    assert "first seen" not in caplog.text


def test_barcode_is_the_entered_code_and_a_different_echo_is_logged(caplog):
    sample = created_sample()
    sample["trackingNumber"] = "1000000000001"
    with caplog.at_level(logging.WARNING):
        parcel = normalize_parcel(sample)
    assert parcel["barcode"] == "1000000000001"
    assert "echoed as a different externalId (len=14)" in caplog.text
    assert sample["shipmentDetails"]["externalId"] not in caplog.text


def test_barcode_falls_back_to_the_echo_without_an_entered_code():
    sample = created_sample()
    assert (
        normalize_parcel(sample)["barcode"] == sample["shipmentDetails"]["externalId"]
    )


def test_unmapped_status_falls_back_to_the_newest_mappable_event(caplog):
    sample = _parcel_with(
        "BrandNewState",
        event("DDEF", "2026-11-19T08:00:00Z"),
        event("ENTY", "2026-11-20T08:00:00Z"),
        event("ZZZZ", "2026-11-21T08:00:00Z"),
    )
    with caplog.at_level(logging.WARNING):
        parcel = normalize_parcel(sample)
        normalize_parcel(sample)
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert caplog.text.count("shipmentStatus=BrandNewState") == 1
    assert caplog.text.count("event code=ZZZZ") == 1
    assert "issues/new?template=unrecognised_status.yml" in caplog.text


@pytest.mark.parametrize(
    "status", ["None", "NonAtTask", "InformationOfDelivery", "ConsigneBringToBranch"]
)
def test_deliberately_unmapped_status_falls_through_to_events(status, caplog):
    sample = _parcel_with(status, event("PICK", "2026-11-20T08:00:00Z"))
    with caplog.at_level(logging.WARNING):
        assert normalize_parcel(sample)["status"] == ParcelStatus.IN_TRANSIT
    assert not caplog.records


def test_unknown_with_one_warning_when_nothing_maps(caplog):
    sample = _parcel_with("BrandNewState", event("ZZZZ", "2026-11-21T08:00:00Z"))
    with caplog.at_level(logging.WARNING):
        assert normalize_parcel(sample)["status"] == ParcelStatus.UNKNOWN
        assert normalize_parcel(sample)["status"] == ParcelStatus.UNKNOWN
    assert caplog.text.count("shipmentStatus=BrandNewState") == 1


@pytest.mark.parametrize("status", [None, "None"])
def test_unknown_when_status_is_absent_and_no_events(status, caplog):
    sample = _parcel_with(status)
    sample["eventDetailList"] = []
    with caplog.at_level(logging.WARNING):
        assert normalize_parcel(sample)["status"] == ParcelStatus.UNKNOWN
    assert "no mappable event" in caplog.text


def test_event_code_falls_back_to_event_type_without_a_code(caplog):
    sample = _parcel_with(
        "Created", event(None, "2026-11-19T08:00:00Z", "NewType", description=None)
    )
    with caplog.at_level(logging.WARNING):
        history = build_history(sample)
    assert history[0]["raw_status"] == "NewType"
    assert history[0]["status"] is None
    assert "eventType=NewType" in caplog.text


def test_map_event_status():
    assert map_event_status(None) is None
    assert map_event_status("ddef") == ParcelStatus.REGISTERED
    assert map_event_status("QQQQ") is None


# ---------------------------------------------------------------------------
# timestamps and history
# ---------------------------------------------------------------------------


def test_parse_iso_handles_variable_fractions_naive_and_garbage():
    two = parse_iso("2026-11-19T08:45:50.02Z")
    three = parse_iso("2026-11-19T08:45:50.029Z")
    assert two.tzinfo is not None
    assert two < three
    assert parse_iso("2026-11-19T08:45:50").tzinfo == UTC
    assert parse_iso("not-a-date") is None
    assert parse_iso("2026-13-45T99:99:99Z") is None
    assert parse_iso(None) is None


def test_history_raw_status_is_the_description_else_the_code():
    sample = _parcel_with(
        "Created",
        event("DDEF", "2026-11-19T08:00:00Z", description="Zaprimljena najava"),
        event("TOUR", "2026-11-20T08:00:00Z", description=""),
    )
    history = build_history(sample)
    assert [h["raw_status"] for h in history] == ["Zaprimljena najava", "TOUR"]


def test_history_is_sorted_oldest_first():
    history = build_history(delivered_sample())
    assert [h["status"] for h in history] == [
        ParcelStatus.REGISTERED,
        ParcelStatus.OUT_FOR_DELIVERY,
        ParcelStatus.DELIVERED,
    ]
    assert history[0]["timestamp"] == "2026-11-19T08:45:50.029000+00:00"


def test_history_keeps_the_newest_twenty():
    sample = created_sample()
    sample["eventDetailList"] = [
        event("ENTY", f"2026-11-{day:02d}T10:00:00Z") for day in range(1, 26)
    ]
    history = build_history(sample)
    assert len(history) == 20
    assert history[0]["timestamp"].startswith("2026-11-06")
    assert history[-1]["timestamp"].startswith("2026-11-25")


def test_history_handles_missing_malformed_and_unparseable(caplog):
    sample = created_sample()
    sample["eventDetailList"] = [
        "not-a-dict",
        {"legacySystemShortEventCode": "ENTY"},
        event("ENTY", "not-a-date"),
        event("DDEF", "2026-11-19T08:00:00Z"),
    ]
    with caplog.at_level(logging.WARNING):
        history = build_history(sample)
    assert [h["timestamp"] for h in history] == [
        "2026-11-19T08:00:00+00:00",
        "not-a-date",
    ]
    assert "timeStamp could not be parsed" in caplog.text
    sample["eventDetailList"] = None
    assert build_history(sample) == []


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

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


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    assert CAPABILITIES <= KNOWN_CAPABILITIES
    assert PENDING_CAPABILITIES <= KNOWN_CAPABILITIES


def test_a_capability_is_never_both_populated_and_pending():
    assert not CAPABILITIES & PENDING_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    delivered = normalize_parcel(delivered_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)
    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None
    for field in ("weight", "dimensions"):
        if field not in CAPABILITIES:
            assert delivered[field] is None
    if "delivery_window" not in CAPABILITIES:
        assert delivered["planned_from"] is None
        assert delivered["planned_to"] is None


def test_normalize_created_parcel():
    parcel = normalize_parcel(created_sample())
    assert parcel["carrier"] == "Overseas Express"
    assert parcel["barcode"] == "10000000000001"
    assert parcel["sender"] == "Example Shop"
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.REGISTERED
    assert parcel["raw_status"] == "Created"
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None
    assert parcel["url"] == "https://inet.overseas.hr/auth?trackingid=10000000000001"
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None


def test_normalize_delivered_parcel_takes_delivered_at_from_the_delivered_event():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-11-21T13:12:42.500000+00:00"


def test_delivered_without_a_delivered_event_has_no_delivered_at():
    sample = _parcel_with("Delivered", event("ENTY", "2026-11-21T10:00:00Z"))
    parcel = normalize_parcel(sample)
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is None


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 3


def test_sender_with_asterisks_or_blank_is_dropped():
    for name in ("Ex*** Shop", "  ", None, 5):
        sample = created_sample()
        sample["shipmentDetails"]["shipper"]["name"] = name
        assert normalize_parcel(sample)["sender"] is None
    sample = created_sample()
    sample["shipmentDetails"]["shipper"] = None
    assert normalize_parcel(sample)["sender"] is None


def test_parcel_shop_address_gives_pickup_and_point(caplog):
    with caplog.at_level(logging.WARNING):
        parcel = normalize_parcel(pickup_sample())
        normalize_parcel(pickup_sample())
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Example Shop Point, Example City"
    assert caplog.text.count("parcelShopAddress seen, keys=['city', 'name']") == 1


def test_parcel_shop_address_of_unseen_shape_is_guarded(caplog):
    sample = created_sample()
    sample["shipmentDetails"]["parcelShopAddress"] = {"id": "1"}
    with caplog.at_level(logging.WARNING):
        parcel = normalize_parcel(sample)
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None
    sample["shipmentDetails"]["parcelShopAddress"] = ["odd"]
    assert normalize_parcel(sample)["pickup_point"] is None
    assert "parcelShopAddress of unexpected type" in caplog.text


def test_parcel_shop_address_string_is_no_pickup_signal(caplog):
    sample = delivered_sample()
    sample["shipmentDetails"]["parcelShopAddress"] = "Example Street 1, 10000, City"
    with caplog.at_level(logging.WARNING):
        parcel = normalize_parcel(sample)
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None
    assert "parcelShopAddress" not in caplog.text


def test_pending_placeholder_is_unknown_and_silent(caplog):
    """A tracked-but-unknown code still yields a full parcel dict."""
    with caplog.at_level(logging.WARNING):
        parcel = normalize_parcel({"trackingNumber": "10000000000009"})
    assert parcel["barcode"] == "10000000000009"
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["history"] is None
    assert not caplog.records


def test_raw_is_the_full_record_without_the_contact_person():
    raw = created_sample()
    parcel = normalize_parcel(raw)
    consignee = parcel["raw"]["shipmentDetails"]["consignee"]
    assert "contactPerson" not in consignee
    assert CONTACT_PERSON not in str(parcel["raw"])
    assert consignee["name"] == "Example Consignee"
    assert parcel["raw"]["eventDetailList"] == raw["eventDetailList"]
    # the cached record is not mutated
    assert raw["shipmentDetails"]["consignee"]["contactPerson"] == CONTACT_PERSON


def test_raw_is_returned_as_is_without_a_consignee():
    raw = created_sample()
    del raw["shipmentDetails"]["consignee"]
    assert normalize_parcel(raw)["raw"] is raw


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(UTC)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels


def test_normalize_without_any_code_has_no_barcode_or_url():
    parcel = normalize_parcel({})
    assert parcel["barcode"] is None
    assert parcel["url"] is None
