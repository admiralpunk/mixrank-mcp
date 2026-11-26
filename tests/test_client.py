import httpx
import pytest

from mixrank_mcp.client import MixRankClient, MixRankError

KEY = "sk_live_SECRET123"


def make(handler) -> MixRankClient:
    return MixRankClient(KEY, transport=httpx.MockTransport(handler))


async def test_key_goes_in_path_and_query_is_cleaned():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = request.url
        return httpx.Response(200, json={"ok": True})

    client = make(handler)
    data = await client.get("/companies/match", params={"url": "stripe.com", "name": None})
    assert data == {"ok": True}
    assert seen["url"].path == f"/v2/json/{KEY}/companies/match"
    assert dict(seen["url"].params) == {"url": "stripe.com"}  # None dropped


async def test_http_error_never_contains_key():
    def handler(request: httpx.Request) -> httpx.Response:
        # Worst case: the API echoes the request URL back in the error body.
        return httpx.Response(403, text=f"forbidden: {request.url}")

    with pytest.raises(MixRankError) as exc:
        await make(handler).get("/usage")
    assert KEY not in str(exc.value)
    assert "***" in str(exc.value)
    assert exc.value.status_code == 403


async def test_network_error_never_contains_key():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"could not connect to {request.url}")

    with pytest.raises(MixRankError) as exc:
        await make(handler).get("/echo")
    assert KEY not in str(exc.value)


async def test_path_params_cannot_traverse_to_other_endpoints():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["raw"] = request.url.raw_path.decode()
        return httpx.Response(200, json={})

    await make(handler).get("/appstore/sdks/{sdk_slug}", {"sdk_slug": "../../usage"})
    assert "/appstore/sdks/..%2F..%2Fusage" in seen["raw"]


def test_empty_key_rejected():
    with pytest.raises(ValueError):
        MixRankClient("  ")
