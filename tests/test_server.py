import json

import httpx
from mcp import Client

from mixrank_mcp.client import MixRankClient
from mixrank_mcp.mock import MockMixRankClient
from mixrank_mcp.server import build_server, compact

EXPECTED_TOOLS = {
    "check_connection",
    "find_company",
    "search_companies",
    "get_company",
    "company_changes_since",
    "company_headcount_history",
    "company_employee_breakdown",
    "sdk_overview",
    "apps_using_sdk",
    "validate_email",
}


def payload(result) -> dict:
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def test_compact_drops_empties_and_truncates():
    out = compact({"a": None, "b": [], "c": {"d": None}, "e": "x" * 1000, "f": [None, 1]})
    assert set(out) == {"e", "f"}
    assert len(out["e"]) < 1000
    assert out["f"] == [1]


async def test_lists_all_tools_as_read_only():
    async with Client(build_server(MockMixRankClient())) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.annotations and tool.annotations.read_only_hint, tool.name
        assert tool.description


async def test_mock_mode_end_to_end():
    async with Client(build_server(MockMixRankClient())) as client:
        found = payload(await client.call_tool("find_company", {"url": "stripe.com"}))
        assert found["_mock"] is True
        # The sample record isn't Stripe; the result must say so plainly.
        assert "NOT data about the company you requested" in found["_mock_note"]
        assert found["requested"] == {"url": "stripe.com"}
        company_id = found["match"]["id"]

        changes = payload(await client.call_tool("company_changes_since", {"company_id": company_id, "since": "2026-01-01"}))
        emp = changes["metrics"]["employee_count"]
        assert changes["samples"] > 1
        assert emp["to"] > emp["from"] and emp["change_pct"] > 0

        sdk = payload(await client.call_tool("apps_using_sdk", {"sdk_slug": "facebook", "limit": 3}))
        assert sdk["results"]


async def test_bad_input_is_a_tool_error_not_a_crash():
    async with Client(build_server(MockMixRankClient())) as client:
        res = await client.call_tool("company_changes_since", {"company_id": 1, "since": "last tuesday"})
        assert res.is_error
        assert "YYYY-MM-DD" in res.content[0].text  # the model learns how to fix it
        res = await client.call_tool("find_company", {})
        assert res.is_error


async def test_live_api_error_reaches_agent_without_key():
    key = "sk_live_DO_NOT_LEAK"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"invalid key in {request.url}")

    live = MixRankClient(key, transport=httpx.MockTransport(handler))
    async with Client(build_server(live)) as client:
        res = await client.call_tool("get_company", {"company_id": 42})
    text = " ".join(getattr(c, "text", "") for c in res.content)
    assert res.is_error
    assert key not in text
    assert "MIXRANK_API_KEY" in text  # actionable hint for the agent
