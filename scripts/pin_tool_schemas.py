"""Print (or --write) the schema pins for app/infrastructure/tools/policy.py from the MCP servers' live schemas.
Run after reviewing a tool contract change; the diff of policy.py is what reviewers approve (T12.4)."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from mcp.client.client import Client

from app.infrastructure.tools.policy import POLICY, schema_hash
from mcp_servers import network_gateway, payments

SERVERS = {"payments": payments.server, "network-gateway": network_gateway.server}


async def live_pins() -> dict[str, str]:
    schemas: dict[str, dict[str, object]] = {}
    for name, srv in SERVERS.items():
        async with Client(srv) as c:
            schemas[name] = {t.name: dict(t.input_schema) for t in (await c.list_tools()).tools}
    return {key: schema_hash(schemas[r.server][r.name]) for key, r in POLICY.items()}  # type: ignore[arg-type]


def main() -> None:
    pins = asyncio.run(live_pins())
    block = "PINNED_SCHEMAS: dict[str, str] = {\n" + "".join(f'    "{k}": "{v}",\n' for k, v in pins.items()) + "}"
    if "--write" in sys.argv:
        p = Path("app/infrastructure/tools/policy.py")
        p.write_text(re.sub(r"PINNED_SCHEMAS: dict\[str, str\] = \{[^}]*\}", block, p.read_text(), flags=re.S))
        print(f"pinned {len(pins)} tools")
    else:
        print(block)


if __name__ == "__main__":
    main()
