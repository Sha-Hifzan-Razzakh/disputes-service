# ADR-0002: Schema style per boundary

- Status: Accepted · Date: 2026-11-04 · Stage: 4–5 (T05.1)

## Context
Data crosses five kinds of boundary: HTTP, graph state (checkpointed), run context, ports, and tool calls. Each
has different needs: validation of untrusted input, cheap serialisation that later releases can read, or
in-process typing only.

## Decision
| Boundary | Style | Why |
|---|---|---|
| HTTP request/response | Pydantic v2 models (`app/api/schemas.py`) | Untrusted input; OpenAPI |
| Graph state, input, output | `TypedDict` with JSON-safe values only; money as 2-dp strings | Checkpoints must load under `LANGGRAPH_STRICT_MSGPACK` and in later releases without importing our classes; no validation cost per step |
| Run context (`Ctx`) | frozen `dataclass` | Not persisted; holds service handles |
| Ports | frozen `dataclass` data shapes + `Protocol` interfaces | No framework in `application`/`domain` (import contract) |
| Tool calls | JSON Schema from the MCP server, pinned by schema hash in the gateway | Contract owned by the tool side |
| Interrupt payload / resume value | `TypedDict` with a `v` field | Versioned so later fields are additive |

## Consequences
Nodes convert dataclasses to dicts at the edge (`case` snapshot, `Evidence`). Validation of state happens in
tests (ownership test, golden runs), not at runtime. A Pydantic state would buy runtime checks but would
put class paths into checkpoints and slow every super-step.
