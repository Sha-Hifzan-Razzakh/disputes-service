from __future__ import annotations

import logging

import structlog
from fastapi import FastAPI

from app import __version__
from app.api.errors import install_error_handlers
from app.api.middleware import install_request_context
from app.api.routers import admin, assist, disputes, evidence, reference, resolutions
from app.bootstrap.lifespan import lifespan


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Disputes Service", version=__version__, lifespan=lifespan)
    install_request_context(app)
    install_error_handlers(app)
    for r in (disputes.router, evidence.router, assist.router, resolutions.router, admin.router, reference.router):
        app.include_router(r)
    return app


app = create_app()
