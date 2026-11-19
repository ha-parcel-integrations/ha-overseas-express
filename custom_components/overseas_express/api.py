"""Overseas Express public tracking API client.

The tracker's guest lookup is an anonymous JSON POST. The contract the
coordinator relies on:

* ``async_get_parcel`` returns the payload dict on success,
* returns ``None`` when the carrier says the tracking code is unknown (a
  normal, expected state — never an error),
* raises :class:`OverseasExpressApiError` for anything else, with
  ``status_code`` set on a non-200 response and ``retry_after`` set when the
  carrier's own ``Retry-After`` header on a 429 could be parsed as seconds,
* lets ``aiohttp.ClientError`` propagate untouched.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import API_HEADERS, API_PLATFORM, TRACKING_API_URL
from .parcels import warn_once

_LOGGER = logging.getLogger(__name__)

RESULT_OK = 200
RESULT_NOT_FOUND = 400


class OverseasExpressApiError(Exception):
    """Raised when an Overseas Express API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Overseas Express API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class OverseasExpressApiClient:
    """Client for the anonymous Overseas Express guest shipment lookup.

    Every answer is HTTP 200 with a JSON envelope::

        {"resultCode": 200, "resultMessage": null, "payload": {...}}
        {"resultCode": 400, "resultMessage": "...", "payload": null}

    A code the carrier does not know is ``resultCode`` 400 with a null payload.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the payload for a known parcel, or ``None`` when the endpoint
        reports the code as unknown. Any other failure raises
        :class:`OverseasExpressApiError`; network errors propagate as
        ``aiohttp.ClientError``.
        """
        async with self._session.post(
            TRACKING_API_URL,
            json={"Platform": API_PLATFORM, "ExternalId": tracking_code},
            headers=API_HEADERS,
            allow_redirects=False,
        ) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = (
                        float(retry_after_header) if retry_after_header else None
                    )
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds
                raise OverseasExpressApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if response.status == 403:
                # The gateway refuses some networks with an HTML page; that says
                # nothing about the code, so it must never read as not-found.
                raise OverseasExpressApiError(
                    "blocked by the network (HTTP 403)", status_code=403
                )
            if response.status != 200:
                raise OverseasExpressApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            try:
                # content_type=None: the body is JSON whatever the header says.
                body = await response.json(content_type=None)
            except ValueError as err:
                raise OverseasExpressApiError(f"unparseable body ({err})") from err

        if not isinstance(body, dict):
            raise OverseasExpressApiError("unexpected body (not a JSON object)")

        result_code = body.get("resultCode")
        payload = body.get("payload")
        if result_code == RESULT_NOT_FOUND and payload is None:
            return None
        if result_code != RESULT_OK:
            warn_once(
                f"resultCode:{result_code}",
                "unexpected resultCode=%s",
                result_code,
            )
            raise OverseasExpressApiError(f"resultCode {result_code}")

        details = payload.get("shipmentDetails") if isinstance(payload, dict) else None
        if not isinstance(details, dict):
            warn_once(
                "missing:shipmentDetails",
                "response without shipmentDetails (resultCode=%s)",
                result_code,
            )
            raise OverseasExpressApiError("response without shipmentDetails")

        if not isinstance(payload.get("eventDetailList"), list):
            warn_once("missing:eventDetailList", "response without eventDetailList")
        return payload
