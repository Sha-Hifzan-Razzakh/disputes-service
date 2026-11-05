"""T26: the migrations and the ORM models describe the same schema, and new threads carry their graph version."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app import __version__
from app.infrastructure.db.models import Base
from app.orchestration.state import SCHEMA_VERSION
from tests.conftest import BASE_DSN, TEST_DB
from tests.graph.test_resolution_graph import dispute, sql, start


def test_migrations_and_models_do_not_drift(app) -> None:
    engine = create_engine(f"{BASE_DSN.replace('postgresql://', 'postgresql+psycopg://')}/{TEST_DB}")
    with engine.connect() as conn:
        ours = set(Base.metadata.tables)

        def include(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
            table = obj if type_ == "table" else getattr(obj, "table", None)
            return getattr(table, "name", name) in ours  # LangGraph's checkpoint_* tables are not ours

        ctx = MigrationContext.configure(
            conn, opts={"compare_server_default": True, "compare_type": True, "include_object": include}
        )
        assert compare_metadata(ctx, Base.metadata) == []
    engine.dispose()


async def test_new_threads_are_stamped_with_release_and_state_schema(client, auth, tools) -> None:
    did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
    tid = (await start(client, auth, did)).json()["thread_id"]
    assert sql("SELECT graph_version FROM threads WHERE thread_id = %s", tid) == [
        (f"{__version__}+s{SCHEMA_VERSION['resolution']}",)
    ]
