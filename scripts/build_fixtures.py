"""Generate mock-mode fixtures from MixRank's public OpenAPI spec.

The spec at https://mixrank.com/openapi.json attaches `examples` to most response
fields. This script walks the response schema of each endpoint the server uses
and assembles one example response per endpoint. The result is written to
src/mixrank_mcp/fixtures.json, so the server can run (and be demoed) without an
API key.

Usage:
    python scripts/build_fixtures.py path/to/openapi.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Endpoints the MCP server exposes. Keep in sync with server.py.
PATHS = [
    "/echo",
    "/companies",
    "/companies/match",
    "/companies/{company_id}",
    "/companies/{company_id}/timeseries",
    "/companies/{company_id}/employee-metrics",
    "/appstore/sdks/{sdk_slug}",
    "/appstore/sdks/{sdk_slug}/installs",
    "/email/validate",
]

OUT = Path(__file__).resolve().parent.parent / "src" / "mixrank_mcp" / "fixtures.json"


def resolve(spec: dict, node: Any) -> Any:
    while isinstance(node, dict) and "$ref" in node:
        target: Any = spec
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target[part]
        node = target
    return node


def example_for(spec: dict, schema: Any, depth: int = 0) -> Any:
    schema = resolve(spec, schema)
    if not isinstance(schema, dict) or depth > 12:
        return None
    if "examples" in schema and schema["examples"]:
        return schema["examples"][0]
    if "example" in schema:
        return schema["example"]
    if "const" in schema:
        return schema["const"]
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            # Prefer the first object-shaped option (the success shape).
            options = [resolve(spec, s) for s in schema[key]]
            objs = [o for o in options if isinstance(o, dict) and o.get("type") in ("object", None) and "properties" in o]
            return example_for(spec, (objs or options)[0], depth + 1)
    typ = schema.get("type")
    if isinstance(typ, list):
        typ = next((t for t in typ if t != "null"), None)
    if typ == "object" or "properties" in schema:
        return {k: example_for(spec, v, depth + 1) for k, v in schema.get("properties", {}).items()}
    if typ == "array":
        item = example_for(spec, schema.get("items", {}), depth + 1)
        return [] if item is None else [item]
    return None


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    spec = json.loads(Path(sys.argv[1]).read_text())
    fixtures: dict[str, Any] = {}
    for path in PATHS:
        op = spec["paths"][path]["get"]
        content = op["responses"]["200"].get("content", {}).get("application/json", {})
        if content.get("example") is not None:
            fixtures[path] = content["example"]
        elif content.get("examples"):
            fixtures[path] = next(iter(content["examples"].values())).get("value")
        else:
            fixtures[path] = example_for(spec, content.get("schema", {}))
    OUT.write_text(json.dumps(fixtures, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(fixtures)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
