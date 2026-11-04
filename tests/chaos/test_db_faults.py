"""T16.4: DB connection resets inside write steps are retried without duplicate rows; added latency is tolerated."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from tests.fakes import TRANSIENT, FailOnce
from tests.graph.test_resolution_graph import dispute, sql, start


async def test_connection_reset_during_approval_request_creates_one_row(client, auth, app, tools) -> None:
    c = app.state.container
    orig = c.graph_services
    c.graph_services = dataclasses.replace(
        orig,
        threads=FailOnce(orig.threads, "set_status", TRANSIENT, when={}, times=1),
        audit=FailOnce(orig.audit, "record", TRANSIENT, times=1),
    )
    try:
        did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
        v = (await start(client, auth, did)).json()
    finally:
        c.graph_services = orig
    assert v["outcome"] == "AWAITING_APPROVAL"
    assert sql("SELECT count(*) FROM approvals WHERE dispute_id = %s", did) == [(1,)]


class Slow:
    def __init__(self, inner: Any, delay: float) -> None:
        self.inner, self.delay = inner, delay

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.inner, name)
        if not asyncio.iscoroutinefunction(target):
            return target

        async def slow(*a: Any, **kw: Any) -> Any:
            await asyncio.sleep(self.delay)
            return await target(*a, **kw)

        return slow


async def test_db_latency_is_tolerated(client, auth, app, tools) -> None:
    c = app.state.container
    orig = c.graph_services
    c.graph_services = dataclasses.replace(
        orig, cases=Slow(orig.cases, 0.2), approvals=Slow(orig.approvals, 0.2), audit=Slow(orig.audit, 0.05)
    )
    try:
        did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
        r = await start(client, auth, did)
    finally:
        c.graph_services = orig
    assert r.status_code == 202
