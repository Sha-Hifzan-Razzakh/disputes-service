"""Smoke suite (T25.1): runs against a deployed service, never against the test app.

    SMOKE_BASE_URL=http://localhost:8080 SMOKE_JWT_SECRET=... uv run pytest -m smoke tests/smoke

SMOKE_BASE_URL_B points at a second replica (deploy.yml port-forwards two different pods) so the approval
flow starts on one instance and resumes on the other (T25.3). It defaults to SMOKE_BASE_URL.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import httpx
import jwt
import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/smoke/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.smoke)


def _client(url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=url, timeout=30) as c:
        yield c


@pytest.fixture(scope="session")
def base_a() -> str:
    url = os.environ.get("SMOKE_BASE_URL")
    if not url:
        pytest.skip("SMOKE_BASE_URL not set: smoke tests run against a deployment")
    return url


@pytest.fixture(scope="session")
def a(base_a: str) -> Iterator[httpx.Client]:
    yield from _client(base_a)


@pytest.fixture(scope="session")
def b(base_a: str) -> Iterator[httpx.Client]:
    yield from _client(os.environ.get("SMOKE_BASE_URL_B") or base_a)


@pytest.fixture(scope="session")
def token() -> Callable[..., dict[str, str]]:
    secret = os.environ["SMOKE_JWT_SECRET"]

    def make(user: str = "smoke-ana", role: str = "analyst", tenant: str = "northwind") -> dict[str, str]:
        return {"Authorization": "Bearer " + jwt.encode({"sub": user, "tenant": tenant, "role": role}, secret, "HS256")}

    return make
