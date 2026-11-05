"""T16.2: model timeouts and 429s are retried by the node RetryPolicy; a model that stays down fails the run
cleanly and the thread recovers once the model is back."""

from __future__ import annotations

import dataclasses
import json

import pytest

from app.infrastructure.drafting import LlmResolutionDrafter
from tests.graph.test_resolution_graph import ctx, dispute, sql, start

GOOD = json.dumps(
    {"resolution": "ACCEPT", "amount": "89.50", "rationale": "No proof of delivery.", "citations": ["ledger:delivery"]}
)


class Flaky:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures, self.calls = failures, 0

    async def __call__(self, prompt: str) -> str:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return GOOD


RateLimit = type("RateLimitError", (Exception,), {})


async def test_two_transient_model_failures_then_success(client, auth, app, tools) -> None:
    c = app.state.container
    model = Flaky([TimeoutError(), RateLimit("429")])
    orig = c.graph_services
    c.graph_services = dataclasses.replace(orig, resolution_drafter=LlmResolutionDrafter(model))
    try:
        did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
        v = (await start(client, auth, did)).json()
    finally:
        c.graph_services = orig
    assert model.calls == 3 and v["outcome"] == "AWAITING_APPROVAL" and v["draft"]["resolution"] == "ACCEPT"


async def test_model_down_fails_cleanly_and_recovers(client, auth, app, tools) -> None:
    c = app.state.container
    orig = c.graph_services
    c.graph_services = dataclasses.replace(orig, resolution_drafter=LlmResolutionDrafter(Flaky([TimeoutError()] * 3)))
    try:
        did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
        with pytest.raises(Exception):  # noqa: B017 - the transient error surfaces after the retry budget
            await start(client, auth, did)
    finally:
        c.graph_services = orig
    tid = sql("SELECT thread_id FROM threads WHERE dispute_id = %s", did)[0][0]
    assert sql("SELECT status FROM threads WHERE thread_id = %s", tid) == [("failed",)]
    c.graph_services = dataclasses.replace(orig, resolution_drafter=LlmResolutionDrafter(Flaky([])))
    try:
        out = await c.resolution_runner().recover(tid, ctx(app, user="ana"))
    finally:
        c.graph_services = orig
    assert out["outcome"] == "AWAITING_APPROVAL"
