"""Tests for the Overseas Express API client."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.overseas_express.api import (
    OverseasExpressApiClient,
    OverseasExpressApiError,
)

from .payloads import GATEWAY_403_HTML, NOT_FOUND_BODY, created_sample, envelope

CODE = "10000000000001"


def _session_returning(
    status: int, body: object = None, headers: dict | None = None
) -> MagicMock:
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_returns_payload_and_sends_external_id():
    session = _session_returning(200, envelope(created_sample()))
    client = OverseasExpressApiClient(session)

    payload = await client.async_get_parcel(CODE)

    assert payload["shipmentDetails"]["externalId"] == CODE
    kwargs = session.post.call_args.kwargs
    assert kwargs["json"] == {"Platform": 201, "ExternalId": CODE}
    assert kwargs["headers"]["Authorization"] == "Bearer null"
    assert kwargs["allow_redirects"] is False


async def test_get_parcel_leaves_a_missing_external_id_alone():
    sample = created_sample()
    del sample["shipmentDetails"]["externalId"]
    client = OverseasExpressApiClient(_session_returning(200, envelope(sample)))

    payload = await client.async_get_parcel(CODE)

    assert "externalId" not in payload["shipmentDetails"]


async def test_not_found_is_resultcode_400_in_http_200():
    client = OverseasExpressApiClient(_session_returning(200, NOT_FOUND_BODY))
    assert await client.async_get_parcel("99999999999999") is None


async def test_gateway_403_html_is_a_network_error_not_not_found():
    client = OverseasExpressApiClient(_session_returning(403, GATEWAY_403_HTML))
    with pytest.raises(OverseasExpressApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 403
    assert "blocked" in str(err.value)


async def test_unexpected_resultcode_is_a_schema_error_with_one_warning(caplog):
    client = OverseasExpressApiClient(
        _session_returning(200, envelope(None, 500, "boom"))
    )
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            with pytest.raises(OverseasExpressApiError):
                await client.async_get_parcel(CODE)
    warnings = [r for r in caplog.records if "resultCode=500" in r.getMessage()]
    assert len(warnings) == 1
    assert "issues/new?template=unrecognised_status.yml" in warnings[0].getMessage()


async def test_400_with_a_payload_is_a_schema_error():
    client = OverseasExpressApiClient(
        _session_returning(200, envelope(created_sample(), 400))
    )
    with pytest.raises(OverseasExpressApiError):
        await client.async_get_parcel(CODE)


@pytest.mark.parametrize("payload", [None, {}, {"shipmentDetails": None}])
async def test_missing_shipment_details_is_a_schema_error(payload, caplog):
    client = OverseasExpressApiClient(_session_returning(200, envelope(payload)))
    with caplog.at_level(logging.WARNING), pytest.raises(OverseasExpressApiError):
        await client.async_get_parcel(CODE)
    assert "shipmentDetails" in caplog.text


async def test_missing_event_list_warns_once_but_succeeds(caplog):
    sample = created_sample()
    del sample["eventDetailList"]
    client = OverseasExpressApiClient(_session_returning(200, envelope(sample)))
    with caplog.at_level(logging.WARNING):
        assert await client.async_get_parcel(CODE) is not None
        assert await client.async_get_parcel(CODE) is not None
    assert caplog.text.count("eventDetailList") == 1


async def test_429_carries_retry_after():
    client = OverseasExpressApiClient(
        _session_returning(429, None, {"Retry-After": "30"})
    )
    with pytest.raises(OverseasExpressApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 429
    assert err.value.retry_after == 30.0


@pytest.mark.parametrize("header", [None, "Wed, 21 Oct 2026 07:28:00 GMT"])
async def test_429_without_numeric_retry_after(header):
    headers = {"Retry-After": header} if header else {}
    client = OverseasExpressApiClient(_session_returning(429, None, headers))
    with pytest.raises(OverseasExpressApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.retry_after is None


async def test_other_status_is_a_transport_error():
    client = OverseasExpressApiClient(_session_returning(500, {}))
    with pytest.raises(OverseasExpressApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 500


async def test_unparseable_body_raises():
    client = OverseasExpressApiClient(_session_returning(200, "not json"))
    with pytest.raises(OverseasExpressApiError):
        await client.async_get_parcel(CODE)


async def test_non_object_body_raises():
    client = OverseasExpressApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(OverseasExpressApiError):
        await client.async_get_parcel(CODE)


async def test_network_error_propagates():
    session = MagicMock()
    session.post = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = OverseasExpressApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)
