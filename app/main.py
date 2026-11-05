from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import __version__
from app.api.errors import install_error_handlers
from app.api.middleware import install_request_context
from app.api.routers import admin, approvals, assist, disputes, evidence, reference, resolutions
from app.bootstrap.lifespan import lifespan
from app.bootstrap.observability import configure_logging, instrument_http


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Disputes Service", version=__version__, lifespan=lifespan)
    install_request_context(app)
    instrument_http(app)
    install_error_handlers(app)
    for r in (
        disputes.router,
        evidence.router,
        assist.router,
        resolutions.router,
        approvals.router,
        admin.router,
        reference.router,
    ):
        app.include_router(r)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:  # Prometheus pull; fed by the OpenTelemetry PrometheusMetricReader
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
