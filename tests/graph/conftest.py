"""Graph-test fixtures: swap the tool gateway for a fake that dedupes on idempotency keys like the MCP servers."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests.graph.fakes import FakeToolGateway


@pytest.fixture
async def tools(app: Any) -> AsyncIterator[FakeToolGateway]:
    c = app.state.container
    original = c.graph_services
    fake = FakeToolGateway()
    c.graph_services = dataclasses.replace(original, tools=fake)
    yield fake
    c.graph_services = original
