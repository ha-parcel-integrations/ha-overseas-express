"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

The carrier-specific parts are the two status maps and
:func:`normalize_parcel`; the sort contract and the delivered filter are
suite-wide machinery.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-overseas-express/issues/new"
    "?template=unrecognised_status.yml"
)

_REGISTERED = (
    "Created AvisoDataEntryRequired PickupAssigned PickupAssigneNextShiftTime "
    "PackageNotReady PickupNotReady PickupNotPossible PickupWasNotDoneInShiftTime "
    "PickupWasNotDoneDueToHoliday NoPickupAbsent NoPickupAddressKnown "
    "NoPickupNoParcel NotPickupStandart PickupCancelled SenderRefusedPickup "
    "RecipientAddressOutsidePickup InvalidCampaignCode DifferentSenderCode"
)
_IN_TRANSIT = (
    "Pickedup PickedUp PartiallyPickedUp Loaded Unloaded LoadToInventory "
    "UnloadFromInventory DeliveryWasNotDoneInShiftTime "
    "DeliveryWasNotDoneDueToHoliday Redelivery ConsigneeMadeAnAppointment "
    "ConsigneeMadeRedirection ConsigneeDemandedAddressChange "
    "ConsigneeDemandedHubAddressChange ConsigneeDemandedBranchDelivery "
    "CourierTurnedIntoBranchDelivery LockerCompartmentReserved "
    "LockerCompartmentReservationCancelled PartiallyDelivered"
)
_AT_PICKUP_POINT = (
    "DeliveredToParcelShop DeliveredToParcelTerminal DeliveredToCounterLocation "
    "InLocker"
)
_DELIVERED = "Delivered LockerPickup"
_RETURNING = "Returned Return PartiallyReturn Recalled"
_PROBLEM = (
    "DeliveryFailed WrongAddress ConsigneeIsNotKnown ConsigneeWasNotAtTheAddress "
    "ConsigneeRefusedDelivery ConsigneeRefusedToPayShippingFee "
    "ConsigneeDoNotWantParcel ShipmentRefused NoIncomingGoodsDepartment "
    "ExceptionToDelivery CivilDisturbance ProblematicParcel DamagedParcel "
    "InsufficientPackaging TemporaryLost NotLocated PudoLostPickupParcel PudoOff "
    "Cancelled"
)


def _build_map(groups: dict[ParcelStatus, str]) -> dict[str, ParcelStatus]:
    return {name: status for status, names in groups.items() for name in names.split()}


# ``shipmentStatus`` is the name of the carrier's backend enum. ``None``,
# ``NonAtTask``, ``InformationOfDelivery`` and ``ConsigneBringToBranch`` are
# deliberately absent: they say nothing about where the parcel is, so the
# newest mappable event decides instead.
_DELIBERATELY_UNMAPPED = frozenset(
    {"None", "NonAtTask", "InformationOfDelivery", "ConsigneBringToBranch"}
)

_STATUS_MAP: dict[str, ParcelStatus] = _build_map(
    {
        ParcelStatus.REGISTERED: _REGISTERED,
        ParcelStatus.IN_TRANSIT: _IN_TRANSIT,
        ParcelStatus.AT_PICKUP_POINT: _AT_PICKUP_POINT,
        ParcelStatus.DELIVERED: _DELIVERED,
        ParcelStatus.RETURNING: _RETURNING,
        ParcelStatus.PROBLEM: _PROBLEM,
    }
)

# ``legacySystemShortEventCode`` of a history row. Only DDEF has been seen on
# the wire.
_EVENT_MAP: dict[str, ParcelStatus] = _build_map(
    {
        ParcelStatus.REGISTERED: "DDEF INIT PDEF NRDY PABS PADU NPNP",
        ParcelStatus.IN_TRANSIT: (
            "PICK PTOU ENTY STCK RSTO DDSP DCOL SRTF RERT RTGW FRWD RETC"
        ),
        ParcelStatus.OUT_FOR_DELIVERY: "TOUR",
        ParcelStatus.AT_PICKUP_POINT: "DEPS DEPT DERC",
        ParcelStatus.DELIVERED: "DELY DEPO",
        ParcelStatus.RETURNING: "RETS DELR",
        ParcelStatus.PROBLEM: "ADRU TIME EMGY REFU RCVU NIGD ABST FDLY VACA EXPI",
    }
)

# The enum has no out-for-delivery value; this event code is the one signal.
OUT_FOR_DELIVERY_EVENT = "TOUR"

# Only this event type has been seen on the wire; anything else is logged once
# so the vocabulary past announcement becomes visible.
_SEEN_EVENT_TYPES = frozenset({"SaveShipment"})

# Keys already warned about, so each unseen value is logged only once per HA
# session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()


def warn_once(key: str, message: str, *args: Any) -> None:
    """Log an unseen shape or code once, with a copy-paste issue link.

    Carries keys and codes only, never values that could be personal.
    """
    if key in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(key)
    _LOGGER.warning(
        "Unrecognised Overseas Express response — help us map it. Open an issue "
        "and paste this line: %s\n  %s",
        NEW_ISSUE_URL,
        message % args,
    )


def map_event_status(code: str | None) -> ParcelStatus | None:
    """Map a history row's event code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` rather than ``unknown`` so a consumer
    can tell "no mapping" from "mapped to unknown", and warn once.
    """
    if not code:
        return None
    mapped = _EVENT_MAP.get(code.upper())
    if mapped is not None:
        return mapped
    warn_once(f"event:{code}", "event code=%s → not mapped", code)
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    The carrier stamps UTC with a varying number of fractional digits, so the
    parse is delegated to Home Assistant's tolerant parser. Naive values are
    treated as UTC so a list always sorts without crashing on a mixed set.
    """
    if not value:
        return None
    try:
        parsed = dt_util.parse_datetime(str(value))
    except ValueError:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _short_code(event: dict) -> str | None:
    """Return the event's short code, uppercased, or ``None``."""
    code = event.get("legacySystemShortEventCode")
    return str(code).upper() if code else None


def _events(raw: dict) -> list[tuple[datetime | None, dict]]:
    """Return ``(parsed time, event)`` pairs, oldest first, unparseable last.

    Status decisions use :func:`_dated_events` instead, so an event without a
    usable time can never count as the newest one.
    """
    events = raw.get("eventDetailList")
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[tuple[None, dict]] = []
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        map_event_status(event.get("legacySystemShortEventCode"))
        event_type = event.get("eventType")
        if event_type and event_type not in _SEEN_EVENT_TYPES:
            warn_once(
                f"eventTypeSeen:{event_type}", "event type=%s first seen", event_type
            )
        parsed = parse_iso(event.get("timeStamp"))
        if parsed is None:
            warn_once("timestamp", "event timeStamp could not be parsed")
            unparseable.append((None, event))
        else:
            parseable.append((parsed, event))
    parseable.sort(key=lambda item: item[0])
    return [*parseable, *unparseable]


def _dated_events(raw: dict) -> list[tuple[datetime, dict]]:
    return [(parsed, event) for parsed, event in _events(raw) if parsed is not None]


def _event_code(event: dict) -> str | None:
    """Return the event's own code, falling back to its event type."""
    code = event.get("legacySystemShortEventCode") or event.get("eventType")
    return str(code) if code else None


def build_history(raw: dict, *, max_events: int = HISTORY_MAX_EVENTS) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. Sorted oldest → newest and capped to the
    most recent ``max_events``.
    """
    entries = []
    for parsed, event in _events(raw):
        code = _event_code(event)
        if not event.get("legacySystemShortEventCode") and event.get("eventType"):
            warn_once(
                f"eventType:{code}",
                "event without a code, eventType=%s",
                code,
            )
        timestamp = parsed.isoformat() if parsed else event.get("timeStamp")
        if not timestamp:
            continue
        entries.append(
            {
                "timestamp": timestamp,
                "status": map_event_status(event.get("legacySystemShortEventCode")),
                "raw_status": event.get("description") or code,
            }
        )
    return entries[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


def _current_status(raw: dict, details: dict) -> ParcelStatus:
    """Map ``shipmentStatus``, else the newest mappable event, else unknown."""
    shipment_status = details.get("shipmentStatus")
    name = shipment_status if isinstance(shipment_status, str) else ""
    mapped = _STATUS_MAP.get(name)
    events = _dated_events(raw)

    if mapped is None:
        if name and name not in _DELIBERATELY_UNMAPPED:
            warn_once(f"status:{name}", "shipmentStatus=%s → not mapped", name)
        for _, event in reversed(events):
            fallback = _EVENT_MAP.get(_short_code(event) or "")
            if fallback is not None:
                return fallback
        if not name or name in _DELIBERATELY_UNMAPPED:
            warn_once(
                f"status:{name}",
                "shipmentStatus=%s and no mappable event → reported as 'unknown'",
                name or "(missing)",
            )
        return ParcelStatus.UNKNOWN

    if mapped is ParcelStatus.IN_TRANSIT and events:
        if _short_code(events[-1][1]) == OUT_FOR_DELIVERY_EVENT:
            warn_once(
                "overlay:TOUR",
                "shipmentStatus=%s with newest event TOUR → reported as "
                "out_for_delivery; please confirm",
                name,
            )
            return ParcelStatus.OUT_FOR_DELIVERY
    return mapped


def _pickup_point(address: Any) -> tuple[bool, str | None]:
    """Return ``(route ends at a point, "name, city")`` from ``parcelShopAddress``."""
    if not address:
        return False, None
    if not isinstance(address, dict):
        warn_once("parcelShopAddress:type", "parcelShopAddress of unexpected type")
        return True, None
    warn_once(
        "parcelShopAddress",
        "first parcelShopAddress seen, keys=%s",
        sorted(address),
    )
    parts = [str(address[key]) for key in ("name", "city") if address.get(key)]
    return True, ", ".join(parts) or None


def _clean_raw(raw: dict) -> dict:
    """Return the record without the consignee's contact person.

    ``contactPerson`` is an unmasked personal name; everything else stays.
    """
    details = raw.get("shipmentDetails")
    if not isinstance(details, dict):
        return raw
    consignee = details.get("consignee")
    if not isinstance(consignee, dict) or "contactPerson" not in consignee:
        return raw
    cleaned_consignee = {k: v for k, v in consignee.items() if k != "contactPerson"}
    return {**raw, "shipmentDetails": {**details, "consignee": cleaned_consignee}}


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order. A key the carrier does not
    expose is ``None``, never omitted. ``raw`` is the full record minus the
    consignee's contact person.

    A ``raw`` without ``shipmentDetails`` is the coordinator's placeholder for
    a code the carrier does not know (yet): it reports ``unknown`` silently.
    """
    found = raw.get("shipmentDetails")
    placeholder = not isinstance(found, dict)
    details: dict = found if isinstance(found, dict) else {}

    barcode = raw.get("trackingNumber") or details.get("externalId")
    echoed = details.get("externalId")
    if barcode and echoed and str(echoed) != str(barcode):
        warn_once(
            "echo-mismatch",
            "entered code (len=%d) echoed as a different externalId (len=%d)",
            len(str(barcode)),
            len(str(echoed)),
        )
    status = ParcelStatus.UNKNOWN if placeholder else _current_status(raw, details)
    delivered = status is ParcelStatus.DELIVERED

    delivered_at = None
    if delivered:
        for parsed, event in reversed(_dated_events(raw)):
            if _EVENT_MAP.get(_short_code(event) or "") is ParcelStatus.DELIVERED:
                delivered_at = parsed.isoformat()
                break

    shipper = details.get("shipper")
    sender = shipper.get("name") if isinstance(shipper, dict) else None
    if not isinstance(sender, str) or not sender.strip() or "*" in sender:
        sender = None

    route_flag, point_name = _pickup_point(details.get("parcelShopAddress"))
    pickup = is_pickup(
        route_flag,
        status,
        (map_event_status(_short_code(event)) for _, event in _dated_events(raw)),
    )
    planned_from, planned_to = planned_for_status(status, None, None)

    return {
        "carrier": "Overseas Express",
        "barcode": barcode,
        "sender": sender,
        "receiver": None,
        "status": status,
        "raw_status": details.get("shipmentStatus") or None,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": pickup,
        "pickup_point": point_name if pickup else None,
        "url": tracking_url(barcode),
        "weight": None,
        "dimensions": None,
        "history": build_history(raw) if include_history else None,
        "raw": _clean_raw(raw),
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
