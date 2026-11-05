# Tools and external capabilities (stage 12)

## Inventory (T12.1)

| Tool | Server | Kind | Input (pinned schema) | Output keys required | Called by |
|---|---|---|---|---|---|
| `payments.issue_refund` | payments | side effect, idempotent on key, `dry_run` | tenant_id, transaction_ref, amount, currency, dispute_id, idempotency_key, dry_run | external_ref, status | `execute` (ACCEPT/PARTIAL) |
| `payments.get_refund` | payments | lookup | tenant_id, idempotency_key | found | sweeper reconciliation |
| `payments.get_transaction` | payments | lookup | tenant_id, transaction_ref | ref, amount | ops tooling |
| `network.submit_representment` | network-gateway | side effect, idempotent on key, `dry_run` | tenant_id, dispute_id, network, reason_code, evidence_ids, rationale, idempotency_key, dry_run | external_ref, status | `execute` (CONTEST) |
| `network.get_submission` | network-gateway | lookup | tenant_id, idempotency_key | found | sweeper reconciliation |

Business errors come back as `<code>: <message>` (`refund_exceeds_capture`, `currency_mismatch`, `not_found`,
`missing_evidence`, `forbidden`, …); `temporarily_unavailable` and `rate_limited` are the only retryable codes.

## Servers (T12.2, T12.3)

`mcp_servers/payments.py` and `mcp_servers/network_gateway.py` — MCP SDK 2.x `MCPServer`, streamable HTTP
(stateless, JSON responses), bearer token per server (`MCP_PAYMENTS_TOKEN`, `MCP_NETWORK_TOKEN`), tenant binding
on every key (a key used by one tenant is `forbidden` for another), failure injection for chaos tests. They
simulate the payments ledger (seeded from `seeds/data.py`) and the card-network gateway.

## Gateway pipeline (T12.4–T12.11) — `app/infrastructure/tools/gateway.py`

1. **Policy** — deny by default (`policy.POLICY`); unknown tool → `denied`.
2. **Pins** — on every connect and reconnect (each session generation) the live input schemas are compared with
   `PINNED_SCHEMAS`; nothing is sent to a server whose session has not been checked. A changed tool is disabled (`tool_disabled` alert) until `scripts/pin_tool_schemas.py --write` is reviewed.
3. **Tenant** — caller tenant comes from `Ctx`; arguments carrying another tenant → `denied`; the gateway injects
   `tenant_id` itself.
4. **Side-effect guard** — idempotency key present and the approval row is `approved` or `auto` for this tenant,
   **and bound to this exact call**: same dispute, tool, idempotency key, amount/currency and evidence as the
   approved summary (review R4). Providers also refuse a known key with different arguments (`idempotency_key_reused`).
5. **Rate limit** — token bucket per tenant per tool (`APP_TOOL_RATE_PER_S`, `APP_TOOL_BURST`).
6. **Call** — one long-lived MCP session per server, owned by one task (opened and closed there, so shutdown and
   reconnects never leak it); calls never connect implicitly; timeout `APP_TOOL_TIMEOUT_S`; transport errors and retryable
   codes retried up to `APP_TOOL_MAX_ATTEMPTS` with backoff and the **same key**.
7. **Output** — must be an object with the required keys; strings truncated to 2 000 chars, lists to 100 items.
8. **Log** — one `tool_calls` row per attempt (status ok / error / retryable_error / denied, latency, sha256 of the
   arguments, request id) + `tool_calls_total` / `tool_call_duration` metrics + a `tool <name>` span.

A provider outage never blocks startup: the connection opens lazily and failures surface as retryable
(`test_unreachable_server_degrades_to_retryable_and_recovers_on_next_call`). `APP_TOOLS_ENABLED=false` swaps the
gateway for the kill switch.

## Exercised by hand over HTTP (T12.12)

Both servers under uvicorn with tokens, `scripts/smoke_tools.py` using the production connection code:

```text
payments: tools ['get_refund', 'get_transaction', 'issue_refund']
network-gateway: tools ['get_submission', 'submit_representment']
pin mismatches: none
get_transaction: (False, {'ref': 'NW-1003', 'amount': '89.50', 'currency': 'USD', 'refunded': '0'})
issue_refund dry_run: (False, {'dry_run': True, 'would_refund': '10.00', 'currency': 'USD'})
over-refund: (True, 'Error executing tool issue_refund: refund_exceeds_capture: 0 + 500.00 > 89.50')
representment w/o evidence: (True, 'Error executing tool submit_representment: missing_evidence: a representment needs at least one evidence item')
wrong token: refused, ToolTransportError
wrong token call: payments: cannot connect (ExceptionGroup)
```

Then the whole service with `APP_TOOLS_ENABLED=true` against those servers (dev database):

```text
start: 202 AWAITING_APPROVAL ACCEPT 89.50 role analyst
queue for ben: [('361e7490', 'analyst')]
decision: 200 REFUNDED {'status': 'succeeded', 'external_ref': 'rf_000001', 'attempts': 1, 'error': None}
dispute: RESOLVED_ACCEPTED
second decision: 409 approval_decided
history steps: ['__start__', 'load_case', 'classify_reason', 'gather_evidence', 'draft_resolution', 'verify',
                'prepare_action', 'request_approval', 'approve', 'execute', 'apply_resolution', 'notify']
metrics: resolution_outcomes_total{outcome="REFUNDED"} 1.0, tool_calls_total{status="ok",tool="payments.issue_refund"} 1.0
```
