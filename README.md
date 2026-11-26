# mixrank-mcp

An **unofficial** [Model Context Protocol](https://modelcontextprotocol.io) server for the
[MixRank](https://mixrank.com) data API. It lets Claude Code, Codex, Gemini CLI, Cursor and
other MCP clients look up companies, headcount trends, mobile SDK adoption and email validity
directly from MixRank.

> Not affiliated with or endorsed by MixRank. Built from MixRank's public
> [OpenAPI spec](https://mixrank.com/openapi.json) as a proof of concept.

It starts **with no API key**. Without one it runs in **mock mode**: every tool returns the same
fixed sample record built from the examples in MixRank's spec. That lets you try the tool flow,
but the data isn't about the company you asked for (ask for `stripe.com` and you get MixRank's
own sample profile). Mock results say so in `_mock_note`, so the agent tells the user. For real
data, set `MIXRANK_API_KEY`.

## Why

MixRank's own [CrustData comparison](https://mixrank.com/crustdata-alternative/) says to pick
CrustData if "you're wiring data straight into an agent", because CrustData ships an MCP server.
MixRank already has the harder parts for agents: hourly freshness, history back to 2008–2013,
and flat licences instead of per-record credits, which suits agents that re-read the same records.
What's missing is a thin agent-facing layer. This is a sketch of that layer.

## Tools

| Tool | What it does | MixRank endpoint |
|---|---|---|
| `check_connection` | Verify the key, report live vs mock mode | `/echo` |
| `find_company` | Resolve a domain/name/LinkedIn URL to one company + its `company_id` | `/companies/match` |
| `search_companies` | Keyword search over the company directory | `/companies` |
| `get_company` | Company profile | `/companies/{id}` |
| `company_changes_since` | **Headcount and follower change since a date, as absolute and %** | `/companies/{id}/timeseries?since=` |
| `company_headcount_history` | Raw headcount/follower samples, newest first | `/companies/{id}/timeseries` |
| `company_employee_breakdown` | Headcount by employee type | `/companies/{id}/employee-metrics` |
| `sdk_overview` | iOS SDK summary: installs, publishers | `/appstore/sdks/{slug}` |
| `apps_using_sdk` | iOS apps currently shipping an SDK | `/appstore/sdks/{slug}/installs` |
| `validate_email` | Email validity assessment (defaults to `cached` to avoid fresh-check billing) | `/email/validate` |

Design choices for agents:

- **Tools shaped around tasks, not a 1:1 copy of every endpoint.** `company_changes_since`
  answers "is this company growing?" in one call. Otherwise the model would page through
  samples and do arithmetic in its context.
- **Compact responses.** Null and empty fields are removed and long text is truncated, so
  one company record doesn't use up the model's context.
- **Errors the agent can act on.** A 401/403 error says to check the key and endpoint
  permissions, a 404 suggests trying a domain, a 429 says to back off. Bad input (e.g.
  `since="last tuesday"`) comes back as a tool error, not a crash.
- **All tools marked read-only** through MCP tool annotations, so clients can auto-approve them.
- **Billing-aware defaults.** `validate_email` uses `strategy="cached"` unless the user asks
  for a fresh check.

## API key safety

MixRank's API takes the key in the URL path (`https://api.mixrank.com/v2/json/{api_key}/…`).
In agent setups URLs get logged in many places (HTTP debug logs, MCP proxies, tool traces), and
error text is shown to the model, which may repeat it in the chat. This server:

- silences `httpx` request logging, which prints full URLs at INFO level,
- scrubs the key from every error message, including error bodies that echo the URL back,
- only names the endpoint *template* in errors, never the full URL,
- percent-encodes path parameters, so a model-supplied `sdk_slug="../../usage"` can't
  reach a different endpoint.

All four are covered by tests.

## Install

```bash
pipx install git+https://github.com/admiralpunk/mixrank-mcp   # or: pip install .
```

### Claude Code

```bash
# mock mode (no key)
claude mcp add mixrank -- mixrank-mcp

# live
claude mcp add mixrank -e MIXRANK_API_KEY=your_key -- mixrank-mcp
```

### Codex / Gemini CLI / Cursor (any stdio MCP client)

```json
{
  "mcpServers": {
    "mixrank": {
      "command": "mixrank-mcp",
      "env": { "MIXRANK_API_KEY": "your_key" }
    }
  }
}
```

Then ask things like:

- "Find stripe.com in MixRank and tell me how its headcount changed since January."
- "Which top iOS apps ship the Facebook SDK?"
- "Is founders@example.com a valid address? Use cached data only."

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MIXRANK_API_KEY` | (unset → mock mode) | Your MixRank API key |
| `MIXRANK_MOCK` | | Set to `1` to force mock mode even with a key |
| `MIXRANK_BASE_URL` | `https://api.mixrank.com/v2/json` | Override the API base |

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Mock fixtures are generated from MixRank's public spec:

```bash
curl -sS https://mixrank.com/openapi.json -o /tmp/openapi.json
python scripts/build_fixtures.py /tmp/openapi.json
```

## What I'd suggest upstream

Things that would make MixRank work better for agents, roughly in order of effort:

1. **Accept the key in an `Authorization` header** (keep the path form for backward
   compatibility), and offer read-only or scoped keys for agent use.
2. **Expose the hourly change stream through the API**, e.g. a `GET /changes?since=<cursor>`
   feed or webhooks for watched companies. MixRank's own
   [agent-readiness post](https://mixrank.com/blog/ai-data/evaluating-data-api-for-agents/) names
   change streams as one of the three things agents need. Today the hourly diffs reach warehouse
   customers but not API users.
3. **An official hosted MCP server** with a sandbox key, so agent builders can try it the same day.

## License

MIT
