"""Sample Overseas Express API payloads shared by the test modules.

Built from the shape of one real parcel with every personal, address and code
value replaced by a placeholder. Keep them in one module rather than inline in
each test — when the payload shape changes there is then exactly one place to
fix.
"""

from __future__ import annotations

ACTIVE_CODE = "10000000000001"
DELIVERED_CODE = "10000000000002"
CONTACT_PERSON = "Placeholder Person"


def event(
    code: str | None,
    timestamp: str,
    event_type: str = "SaveShipment",
    hub: str = "Example Depot",
    description: str | None = "Example event text",
) -> dict:
    """One entry of the carrier's own event timeline."""
    return {
        "timeStamp": timestamp,
        "legacySystemShortEventCode": code,
        "description": description,
        "eventType": event_type,
        "eventLocation": {
            "lat": 0.0,
            "lon": 0.0,
            "hubId": "10",
            "city": None,
            "country": "191",
            "zipCode": None,
        },
        "hubName": hub,
    }


def _address() -> dict:
    return {
        "addressId": "999",
        "street": "Placeholder Street",
        "addressText": "Placeholder Street 1",
        "geocodedAddressText": "Placeholder Street 1",
        "houseNumber": "1",
        "doorNumber": "2",
        "country": "HR",
        "city": "Placeholder City",
        "zipCode": "00000",
        "latitude": 1.5,
        "longitude": 2.5,
        "location": {"lat": 1.5, "lon": 2.5},
        "title": "Placeholder",
    }


def created_sample(code: str = ACTIVE_CODE) -> dict:
    """The ``payload`` of a freshly announced parcel (``Created``)."""
    return {
        "shipmentId": code,
        "eventDetailList": [event("DDEF", "2026-11-19T08:45:50.029Z")],
        "shipmentDetails": {
            "id": "000000000000000000000001",
            "shipmentId": code,
            "externalId": code,
            "createdAt": "2026-11-19T08:45:50.02Z",
            "shipper": {
                "name": "Example Shop",
                "phone": None,
                "gsm": None,
                "email": None,
                "address": _address(),
            },
            "consignee": {
                "name": "Example Consignee",
                "phone": "+385******",
                "gsm": "+385******",
                "email": "p*****@example.test",
                "addresses": [_address()],
                "currentAddress": _address(),
                "contactPerson": CONTACT_PERSON,
            },
            "integrationCode": "2000000000001",
            "integrationCode1": "2000000000001",
            "integrationCode2": None,
            "integrationCode3": "300000000000000000000001",
            "internationalCode": "2000000000001",
            "shipmentStatus": "Created",
            "services": [
                {
                    "serviceType": 24,
                    "serviceName": "Standard",
                    "servicePrice": 0.0,
                    "legacySystemServiceId": "1",
                    "currency": "HUF",
                }
            ],
            "legacySystemCargoId": "4000001",
            "parcelCount": 1,
            "shipmentType": "Standard",
            "parcelShopAddress": None,
            "isExport": False,
            "isImport": True,
            "originalShipmentId": "5000001",
            "parcels": [
                {
                    "barcode": f"00000{code}",
                    "legacySystemShortBarcode": f"S{code}",
                }
            ],
        },
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A synthetic delivered parcel: ``Delivered`` plus a ``DELY`` event."""
    sample = created_sample(code)
    sample["shipmentDetails"]["shipmentStatus"] = "Delivered"
    sample["eventDetailList"] = [
        event("DELY", "2026-11-21T13:12:42.5Z", "Delivery"),
        event("TOUR", "2026-11-21T07:46:00Z", "Tour"),
        event("DDEF", "2026-11-19T08:45:50.029Z"),
    ]
    return sample


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An in-transit parcel whose newest event is the delivery tour."""
    sample = created_sample(code)
    sample["shipmentDetails"]["shipmentStatus"] = "Loaded"
    sample["eventDetailList"] = [
        event("TOUR", "2026-11-21T07:46:00Z", "Tour"),
        event("ENTY", "2026-11-20T15:52:17.12Z", "Entry"),
        event("DDEF", "2026-11-19T08:45:50.029Z"),
    ]
    return sample


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel waiting at a pickup point."""
    sample = created_sample(code)
    sample["shipmentDetails"]["shipmentStatus"] = "DeliveredToParcelShop"
    sample["shipmentDetails"]["parcelShopAddress"] = {
        "name": "Example Shop Point",
        "city": "Example City",
    }
    sample["eventDetailList"] = [event("DEPS", "2026-11-21T09:00:00Z")]
    return sample


def envelope(payload: dict | None, result_code: int = 200, message=None) -> dict:
    """The API's response envelope around a ``payload``."""
    return {"resultCode": result_code, "resultMessage": message, "payload": payload}


NOT_FOUND_BODY = envelope(None, 400, "Barkodovi nisu pronađeni")
GATEWAY_403_HTML = "<html><body><h1>403 Forbidden</h1></body></html>"
