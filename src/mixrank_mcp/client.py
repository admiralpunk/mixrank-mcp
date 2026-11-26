"""Thin async client for the MixRank v2 REST API.

MixRank authenticates by putting the API key in the URL path:

    https://api.mixrank.com/v2/json/{api_key}/companies/match?url=stripe.com

In a normal ETL job that's fine. In an agent setup, URLs are logged in many
places: HTTP client debug logs, MCP proxy logs, tool-call traces, and error
messages the model reads back and may repeat in the chat. So this client keeps
the key out of everything except the outgoing request:

* httpx's request logging is silenced (it logs full URLs at INFO level).
* Every error message is scrubbed of the key before it is raised.
* Errors only ever mention the endpoint *template*, never the full URL.
* Path parameters are percent-encoded, so a model-supplied value like
  "../usage" can't reach a different endpoint.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_BASE_URL = "https://api.mixrank.com/v2/json"

# httpx logs "HTTP Request: GET https://.../{api_key}/..." at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class MixRankError(Exception):
    """An API or network failure, with the API key already scrubbed out."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class MixRankClient:
    mock = False

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not api_key or not api_key.strip():
            raise ValueError("MixRank API key is empty")
        self._key = api_key.strip()
        self._base = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "mixrank-mcp (unofficial)"},
        )

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "***").replace(quote(self._key, safe=""), "***")

    async def get(self, template: str, path_params: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
        """GET an endpoint by its spec template, e.g. "/companies/{company_id}"."""
        path = template
        for name, value in (path_params or {}).items():
            path = path.replace("{" + name + "}", quote(str(value), safe=""))
        query = {k: v for k, v in (params or {}).items() if v is not None}
        url = f"{self._base}/{quote(self._key, safe='')}{path}"

        try:
            resp = await self._http.get(url, params=query)
        except httpx.HTTPError as exc:
            raise MixRankError(self._scrub(f"Network error calling MixRank {template}: {type(exc).__name__}: {exc}")) from None

        if resp.status_code >= 400:
            detail = self._scrub(resp.text[:300].strip())
            raise MixRankError(
                f"MixRank {template} returned HTTP {resp.status_code}" + (f": {detail}" if detail else ""),
                status_code=resp.status_code,
            )
        try:
            return resp.json()
        except ValueError:
            raise MixRankError(f"MixRank {template} returned a non-JSON response") from None

    async def aclose(self) -> None:
        await self._http.aclose()
