---
name: new-tool
description: Use when adding or changing an external action (MCP tool) that the disputes-service resolution workflow may call, such as a new provider operation, so it goes through the governed tool gateway correctly.
---

# Add a tool

The graph never calls a provider directly: only `McpToolGateway`, which is deny-by-default.

1. **Server**: implement the tool in `mcp_servers/<server>.py`. Side-effecting tools take `idempotency_key`,
   replay a known key, and refuse a known key with different arguments (`idempotency_key_reused`). Raise
   `ToolFailure(code, message)` for business errors; transient codes must be in `RETRYABLE_CODES`.
2. **Policy**: add a `ToolRule` in `app/infrastructure/tools/policy.py` (server, name, `side_effect`, required
   output keys). Side-effecting tools need an approval bound to the call: extend `_bound` in `gateway.py` if the
   new tool's arguments carry amounts or evidence.
3. **Pin**: `uv run python scripts/pin_tool_schemas.py --write`; review the hash change like code.
4. **Graph**: tool names come from a fixed map (`resolution.TOOLS`), never from model output.
5. **Tests**: gateway denial cases and a retry-with-same-key case (`tests/integration/test_tool_gateway.py`),
   a malformed-reply case for side effects, and the server's own tests.
6. **Docs/ops**: `docs/tools.md` table; a reconciliation lookup if the tool has side effects (`sweeper.LOOKUP`);
   a row in `ops/concerns.py`.
