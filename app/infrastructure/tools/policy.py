"""Tool policy (T12.7): deny by default. Only tools listed here can be called, each with its server, kind and the
output keys the gateway requires. Input schemas are pinned by hash (T12.4): if a server changes a tool's schema,
the gateway refuses that tool until the pin is updated in review."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolRule:
    server: str
    name: str
    side_effect: bool  # needs an approved approval and an idempotency key
    output_keys: frozenset[str]


POLICY: dict[str, ToolRule] = {
    "payments.issue_refund": ToolRule("payments", "issue_refund", True, frozenset({"external_ref", "status"})),
    "payments.get_refund": ToolRule("payments", "get_refund", False, frozenset({"found"})),
    "payments.get_transaction": ToolRule("payments", "get_transaction", False, frozenset({"ref", "amount"})),
    "network.submit_representment": ToolRule(
        "network-gateway", "submit_representment", True, frozenset({"external_ref", "status"})
    ),
    "network.get_submission": ToolRule("network-gateway", "get_submission", False, frozenset({"found"})),
}

# sha256 of the canonical JSON input schema each tool had when it was reviewed (scripts/pin_tool_schemas.py).
PINNED_SCHEMAS: dict[str, str] = {
    "payments.issue_refund": "ae298034c8eb428cf0da2e9b91427eb9cab1ec7543efa5fba21eefa498f0e212",
    "payments.get_refund": "cda8ad28e61d93d516bcc4a6f6c52b6e838c3808e5ce71545a25027f816a4f7d",
    "payments.get_transaction": "4b611ef47620bba7ca33710f270ba586328fc1bf21b53ed86242a2741aa2f47d",
    "network.submit_representment": "46b0b3b62b54f2d2143384d4013c623927ec6e61d21dc32ef779df6de03fc953",
    "network.get_submission": "8b50b1b1b7eb3df65007be53940592f4fba3fc085515866f19d3139f5f2ecc2d",
}


def schema_hash(schema: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def pin_mismatches(live: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, str]:
    """`live` = {server: {tool_name: input_schema}}. Returns {policy tool: reason} for every tool that is missing on
    its server or whose schema hash differs from the pin."""
    problems: dict[str, str] = {}
    for key, rule in POLICY.items():
        if rule.server not in live:
            continue  # server not checked yet (not connected); checked on first successful connect
        schema = live[rule.server].get(rule.name)
        if schema is None:
            problems[key] = f"{rule.server} does not expose {rule.name}"
        elif PINNED_SCHEMAS.get(key) != schema_hash(schema):
            problems[key] = "input schema differs from the reviewed pin"
    return problems
