"""MCP server exposing MixRank company, headcount, SDK and email data to AI agents."""

from __future__ import annotations

import os
import sys
from datetime import date
from typing import Annotated, Any, Literal, Protocol

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .client import MixRankClient, MixRankError
from .mock import MockMixRankClient

MOCK_NOTE = (
    "MOCK MODE: no MIXRANK_API_KEY is set, so this is a fixed sample record from MixRank's "
    "public API docs, returned whatever you asked for. It is NOT data about the company you "
    "requested. Tell the user this, and set MIXRANK_API_KEY for real results."
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
MAX_LIMIT = 50
MAX_STRING = 400

INSTRUCTIONS = """\
Tools for MixRank's B2B data: companies, headcount history, mobile SDK adoption and
email validation. Typical flow: find_company (by domain is most reliable) to get a
company_id, then get_company / company_changes_since / company_employee_breakdown.
For mobile ecosystems use sdk_overview and apps_using_sdk with an SDK slug such as
"facebook" or "firebase". Responses are compacted: null and empty fields are removed
and long text is truncated. If a result contains "_mock": true, the data is
illustrative sample data, not real MixRank data."""


class Client(Protocol):
    mock: bool

    async def get(self, template: str, path_params: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any: ...


def compact(value: Any) -> Any:
    """Drop nulls/empties and truncate long strings, to save the model's context."""
    if isinstance(value, dict):
        out = {k: compact(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [c for c in (compact(v) for v in value) if c not in (None, "", [], {})]
    if isinstance(value, str) and len(value) > MAX_STRING:
        return value[:MAX_STRING] + "…"
    return value


def _limit(n: int) -> int:
    return max(1, min(MAX_LIMIT, n))


def _check_date(value: str, field: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ToolError(f"{field} must be a date in YYYY-MM-DD format, got {value!r}") from None
    return value


def _pct(old: float | None, new: float | None) -> float | None:
    if old in (None, 0) or new is None:
        return None
    return round((new - old) / old * 100, 2)


def build_server(client: Client) -> MCPServer:
    mcp = MCPServer(name="mixrank", version=__version__, instructions=INSTRUCTIONS)

    def wrap(data: Any) -> dict[str, Any]:
        data = compact(data)
        if not isinstance(data, dict):
            data = {"results": data}
        if client.mock:
            data["_mock"] = True
            data["_mock_note"] = MOCK_NOTE
        return data

    async def call(template: str, path_params: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
        try:
            return await client.get(template, path_params, params)
        except MixRankError as exc:
            hint = ""
            if exc.status_code in (401, 403):
                hint = " Check MIXRANK_API_KEY and that the key's user has permission for this endpoint."
            elif exc.status_code == 404:
                hint = " Nothing matched; try a different identifier (a domain usually matches best)."
            elif exc.status_code == 429:
                hint = " Rate limited; wait before retrying."
            raise ToolError(str(exc) + hint) from None

    @mcp.tool(annotations=READ_ONLY)
    async def check_connection() -> dict[str, Any]:
        """Check that the MixRank API key works. Says whether the server is in mock mode."""
        result = await call("/echo")
        return wrap({"status": result.get("status"), "mode": "mock" if client.mock else "live"})

    @mcp.tool(annotations=READ_ONLY)
    async def find_company(
        url: Annotated[str | None, Field(description="Company domain or website, e.g. 'stripe.com'. Most reliable identifier.")] = None,
        name: Annotated[str | None, Field(description="Company name, e.g. 'Stripe'.")] = None,
        linkedin: Annotated[str | None, Field(description="Company LinkedIn URL.")] = None,
    ) -> dict[str, Any]:
        """Resolve a company to its single best MixRank match and return its profile, including the company_id other tools need."""
        if not (url or name or linkedin):
            raise ToolError("Provide at least one of url, name or linkedin.")
        data = await call("/companies/match", params={"url": url, "name": name, "linkedin": linkedin})
        results = (data or {}).get("results") or []
        requested = {"url": url, "name": name, "linkedin": linkedin}
        if not results:
            return wrap({"requested": requested, "match": None, "message": "No matching company."})
        return wrap({"requested": requested, "match": results[0]})

    @mcp.tool(annotations=READ_ONLY)
    async def search_companies(
        query: Annotated[str, Field(description="Search term, e.g. 'observability'.")],
        limit: Annotated[int, Field(description=f"Max results (1-{MAX_LIMIT}).")] = 10,
        sort_by: Annotated[Literal["rank", "employee_count", "id"], Field(description="Sort order. employee_count sorts largest first.")] = "rank",
    ) -> dict[str, Any]:
        """Search MixRank's company directory by keyword."""
        data = await call("/companies", params={"search": query, "page_size": _limit(limit), "sort_field": sort_by})
        return wrap(data)

    @mcp.tool(annotations=READ_ONLY)
    async def get_company(
        company_id: Annotated[int, Field(description="MixRank company ID (from find_company or search_companies).")],
    ) -> dict[str, Any]:
        """Get a company's profile: headcount, industries, location, LinkedIn data, app counts."""
        return wrap(await call("/companies/{company_id}", {"company_id": company_id}))

    @mcp.tool(annotations=READ_ONLY)
    async def company_changes_since(
        company_id: Annotated[int, Field(description="MixRank company ID.")],
        since: Annotated[str, Field(description="Start date, YYYY-MM-DD (exclusive). e.g. '2026-01-01'.")],
    ) -> dict[str, Any]:
        """What changed at a company since a date: headcount and follower growth, as absolute and % change.

        Answers "is this company growing or shrinking?" in one call instead of the agent
        paging through raw samples and doing arithmetic itself."""
        _check_date(since, "since")
        data = await call(
            "/companies/{company_id}/timeseries",
            {"company_id": company_id},
            {"since": since, "page_size": 500},
        )
        rows = [r for r in (data or {}).get("results") or [] if r.get("date")]
        if not rows:
            return wrap({"company_id": company_id, "since": since, "samples": 0, "message": "No samples after this date."})
        rows.sort(key=lambda r: r["date"])
        first, last = rows[0], rows[-1]
        metrics = {}
        for field in ("employee_count", "linkedin_follower_count", "twitter_follower_count"):
            old, new = first.get(field), last.get(field)
            if old is None and new is None:
                continue
            metrics[field] = {
                "from": old,
                "to": new,
                "change": None if old is None or new is None else new - old,
                "change_pct": _pct(old, new),
            }
        return wrap({
            "company_id": company_id,
            "since": since,
            "window": {"first_sample": first["date"], "last_sample": last["date"]},
            "samples": len(rows),
            "metrics": metrics,
        })

    @mcp.tool(annotations=READ_ONLY)
    async def company_headcount_history(
        company_id: Annotated[int, Field(description="MixRank company ID.")],
        limit: Annotated[int, Field(description=f"Number of samples, newest first (1-{MAX_LIMIT}).")] = 12,
        since: Annotated[str | None, Field(description="Optional start date, YYYY-MM-DD (exclusive).")] = None,
    ) -> dict[str, Any]:
        """Raw headcount and follower samples over time, newest first. Samples aren't guaranteed daily."""
        if since:
            _check_date(since, "since")
        data = await call(
            "/companies/{company_id}/timeseries",
            {"company_id": company_id},
            {"page_size": _limit(limit), "since": since},
        )
        return wrap(data)

    @mcp.tool(annotations=READ_ONLY)
    async def company_employee_breakdown(
        company_id: Annotated[int, Field(description="MixRank company ID.")],
        job_tags: Annotated[str | None, Field(description="Optional comma-separated job tag IDs, e.g. '813,22'. Omit to list available tags with counts.")] = None,
    ) -> dict[str, Any]:
        """Headcount by employee type (function, seniority, education), current and all-time."""
        return wrap(await call("/companies/{company_id}/employee-metrics", {"company_id": company_id}, {"job_tags": job_tags}))

    @mcp.tool(annotations=READ_ONLY)
    async def sdk_overview(
        sdk_slug: Annotated[str, Field(description="SDK slug, e.g. 'facebook', 'firebase', 'stripe'.")],
    ) -> dict[str, Any]:
        """Summary of an iOS SDK: publisher, current install count, number of publishers using it."""
        return wrap(await call("/appstore/sdks/{sdk_slug}", {"sdk_slug": sdk_slug}))

    @mcp.tool(annotations=READ_ONLY)
    async def apps_using_sdk(
        sdk_slug: Annotated[str, Field(description="SDK slug, e.g. 'facebook'.")],
        limit: Annotated[int, Field(description=f"Max apps (1-{MAX_LIMIT}).")] = 10,
        sort_by: Annotated[Literal["rank", "rating_count", "upload_date", "id"], Field(description="Sort order.")] = "rank",
    ) -> dict[str, Any]:
        """iOS apps that currently ship a given SDK (per their latest analysed binary)."""
        return wrap(await call(
            "/appstore/sdks/{sdk_slug}/installs",
            {"sdk_slug": sdk_slug},
            {"page_size": _limit(limit), "sort_field": sort_by},
        ))

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=False, openWorldHint=True))
    async def validate_email(
        email: Annotated[str, Field(description="Email address to assess.")],
        strategy: Annotated[
            Literal["cached", "fetch", "strict", "besteffort"],
            Field(description="'cached' (default) only returns stored results. 'fetch' forces a fresh check. Fresh checks are billed separately."),
        ] = "cached",
    ) -> dict[str, Any]:
        """Assess an email address (validity, disposable, catch-all, MX). Doesn't guarantee deliverability.

        Billing note: MixRank bills cached and fetched assessments separately, so this
        defaults to 'cached'. Only use 'fetch' when the user asks for a fresh check."""
        if "@" not in email or len(email) > 320:
            raise ToolError(f"Not an email address: {email!r}")
        return wrap(await call("/email/validate", params={"email": email, "strategy": strategy}))

    return mcp


def make_client() -> Client:
    key = os.environ.get("MIXRANK_API_KEY", "").strip()
    if os.environ.get("MIXRANK_MOCK") == "1" or not key:
        print("mixrank-mcp: no MIXRANK_API_KEY set, running in mock mode (sample data).", file=sys.stderr)
        return MockMixRankClient()
    return MixRankClient(key, base_url=os.environ.get("MIXRANK_BASE_URL", "https://api.mixrank.com/v2/json"))


def main() -> None:
    build_server(make_client()).run("stdio")


if __name__ == "__main__":
    main()
