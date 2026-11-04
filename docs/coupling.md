# Coupling map — v1 (T03.4)

Import graph of `app/` on main@2462be0, then the target contracts from `pyproject.toml` run against it
(`uv run lint-imports`). The contracts were written first (stage 4 target) so the report lists exactly what
the refactor must remove. pydeps is not installed in this environment; the table below is the same graph
in text (`rg -n '^from app|^\s+from app' app`).

## Module edges (v1)

| From | To | Why | Target verdict |
|---|---|---|---|
| `orchestration.assist` | `bootstrap.container.get_container` | nodes fetch store, drafter, settings from a module global | ✗ service locator inside graph code → `Runtime[Ctx]` |
| `orchestration.assist` | `domain.reason_codes` | classify | ✓ |
| `orchestration.assist` | `application.ports.PolicyHit` (function-local import) | rebuild hits from state dicts | ✓ (move to module level) |
| `api.deps` | `infrastructure.repositories` | constructs `SqlDisputeRepository` etc. per request | ✗ API knows adapters → container factories |
| `api.routers.reference` | `infrastructure.repositories.SqlLedger` | transaction lookup | ✗ same |
| `api.deps`, `api.routers.reference` | `bootstrap.container.Container` | typed access to the container | ✓ (api sits above bootstrap) |
| `application.assist` | (none) — holds `graph: Any` | invokes the graph | ✗ hidden dependency on orchestration; cannot pass `Ctx` without importing upward → move runner to `orchestration` |
| `bootstrap.container` | `infrastructure.*`, `psycopg_pool`, `sqlalchemy` | composition root | ✓ |
| `bootstrap.lifespan` | `orchestration.assist`, `langgraph.checkpoint.postgres` | compile graph | ✓ |
| `infrastructure.*` | `application.ports`, `domain.*` | implements ports | ✓ |

Indirect vendor reach from graph code: `orchestration → bootstrap.container → psycopg, psycopg_pool,
sqlalchemy, infrastructure.drafting → langchain_anthropic`.

## `lint-imports` against v1

```text
Layers: api -> bootstrap -> (orchestration | infrastructure) -> application -> domain BROKEN
Graph code never touches vendors, drivers or the container BROKEN
API never builds adapters itself BROKEN
Use cases and domain stay framework-free KEPT

Contracts: 1 kept, 3 broken.


Layers: api -> bootstrap -> (orchestration | infrastructure) -> application -> 
domain
--------------------------------------------------------------------------------
-----

app.orchestration is not allowed to import app.bootstrap:

- app.orchestration.assist -> app.bootstrap.container (l.14)


Graph code never touches vendors, drivers or the container
----------------------------------------------------------

app.orchestration is not allowed to import langchain_anthropic:

-   app.orchestration.assist -> app.bootstrap.container (l.14)
    app.bootstrap.container -> app.infrastructure.drafting (l.14)
    app.infrastructure.drafting -> langchain_anthropic (l.55)


app.orchestration is not allowed to import psycopg:

-   app.orchestration.assist -> app.bootstrap.container (l.14)
    app.bootstrap.container -> psycopg (l.8)


app.orchestration is not allowed to import psycopg_pool:

-   app.orchestration.assist -> app.bootstrap.container (l.14)
    app.bootstrap.container -> psycopg_pool (l.9)


app.orchestration is not allowed to import sqlalchemy:

-   app.orchestration.assist -> app.bootstrap.container (l.14)
    app.bootstrap.container -> sqlalchemy (l.10)


API never builds adapters itself
--------------------------------

app.api is not allowed to import app.infrastructure:

-   app.api.deps -> app.infrastructure.repositories (l.17)

-   app.api.routers.reference -> app.infrastructure.repositories (l.14)


```
