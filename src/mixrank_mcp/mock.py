"""Mock client: serves example responses built from MixRank's public OpenAPI spec.

Used when no MIXRANK_API_KEY is set (or MIXRANK_MOCK=1), so anyone can install
the server and try every tool in Claude Code / Codex / Gemini CLI without an
account. Data is illustrative, not real.
"""

from __future__ import annotations

import copy
import json
from datetime import date, timedelta
from importlib import resources
from typing import Any


def _load_fixtures() -> dict[str, Any]:
    return json.loads(resources.files("mixrank_mcp").joinpath("fixtures.json").read_text())


class MockMixRankClient:
    mock = True

    def __init__(self) -> None:
        self._fixtures = _load_fixtures()

    async def get(self, template: str, path_params: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
        if template not in self._fixtures:
            raise KeyError(f"no mock fixture for {template}")
        data = copy.deepcopy(self._fixtures[template])
        params = params or {}
        if template == "/companies/{company_id}/timeseries":
            data = _synthetic_timeseries(data, since=params.get("since"))
        if template == "/email/validate" and params.get("email"):
            data["email"] = data["normalized"] = params["email"]
        return data

    async def aclose(self) -> None:
        return None


def _synthetic_timeseries(data: dict[str, Any], since: str | None) -> dict[str, Any]:
    """The spec example has one sample; expand it into ~monthly history so
    trend tools have something to compute. Newest first, like the real API."""
    seed = data["results"][0]
    newest = date.fromisoformat(seed["date"])
    rows = []
    for i in range(12):
        day = newest - timedelta(days=30 * i)
        row = dict(seed)
        row["date"] = day.isoformat()
        row["employee_count"] = round(seed["employee_count"] * (1 - 0.01 * i))
        row["linkedin_follower_count"] = round(seed["linkedin_follower_count"] * (1 - 0.015 * i))
        rows.append(row)
    if since:
        rows = [r for r in rows if r["date"] > since]
    data["results"] = rows
    data["total"] = len(rows)
    return data
