"""Stage 10 / G5: threads written by earlier releases keep working.

Each committed assist fixture is loaded into a fresh thread for a real dispute (state written as if the old
graph's last node had just run), then one more question is asked through the v1 endpoint."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

Auth = Callable[..., dict[str, str]]
FIXTURES = sorted(Path("tests/fixtures/checkpoints").glob("*_assist.json"))


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f.stem for f in FIXTURES])
async def test_assist_fixture_resumes_with_history_and_upgraded_schema(
    fixture: Path, client: httpx.AsyncClient, auth: Auth, app: Any
) -> None:
    doc = json.loads(fixture.read_text())  # noqa: ASYNC240 - small test fixture
    body = {
        "network": "VISA",
        "reason_code": "10.4",
        "transaction_ref": "NW-1002",
        "amount": "10.00",
        "currency": "USD",
    }
    did = (await client.post("/v1/disputes", json=body, headers=auth())).json()["id"]  # fresh thread per fixture
    graph = app.state.container.assist_graph
    cfg = {"configurable": {"thread_id": f"assist:{did}"}}
    old = doc["values"]
    await graph.aupdate_state(cfg, old, as_node="draft_recommendation")
    before = await graph.aget_state(cfg)
    assert before.values.get("schema_version", 1) == int(doc["state_schema"].rsplit("v", 1)[1])

    r = await client.post(f"/v1/disputes/{did}/assist", json={"question": "Still contest?"}, headers=auth())
    assert r.status_code == 200, r.text
    assert r.json()["turns"] == len(old["history"]) + 1
    assert r.headers["Deprecation"].startswith("@") and "successor-version" in r.headers["Link"]
    after = await graph.aget_state(cfg)
    assert after.values["schema_version"] == 2
    assert after.values["history"][: len(old["history"])] == old["history"]  # history kept, in order
    assert all(e["source"] in {"policy", "ledger", "merchant"} for e in after.values["evidence"])


RESOLUTION_FIXTURES = sorted(Path("tests/fixtures/checkpoints").glob("*_resolution_*.json"))


@pytest.mark.parametrize("fixture", RESOLUTION_FIXTURES, ids=[f.stem for f in RESOLUTION_FIXTURES])
def test_paused_resolution_fixture_keys_are_still_understood(fixture: Path) -> None:
    """A thread paused at approval by an earlier release resumes into current code: every state key it carries
    must still be declared, and its schema version must not be newer than ours (upgrade hooks go only forward)."""
    import typing

    from app.orchestration import state as S

    doc = json.loads(fixture.read_text())
    keys = {k for k in doc["values"] if not k.startswith(("branch:", "__"))}
    assert keys <= set(typing.get_type_hints(S.ResolutionState)), keys - set(typing.get_type_hints(S.ResolutionState))
    assert doc["values"]["schema_version"] <= S.SCHEMA_VERSION["resolution"]
    assert doc["values"]["approval"]["status"] == "pending"
