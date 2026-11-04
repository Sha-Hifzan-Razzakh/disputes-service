"""Telemetry wiring (stage 13): JSON logs with standard fields and redaction, OpenTelemetry traces and metrics.

Graph, gateway and API code use only the OpenTelemetry API; this module chooses exporters:
- traces: none | console | otlp (APP_OTEL_EXPORTER) — vendor-neutral OTLP/HTTP to any collector (T13.3)
- metrics: Prometheus pull at /metrics (T13.6)
- logs: structlog JSON with request_id, thread_id, trace_id/span_id; SENSITIVE keys and card/email patterns
  masked before rendering (T13.5, T13.7)
"""

from __future__ import annotations

import logging
import re
from collections.abc import MutableMapping
from typing import Any

import structlog
from opentelemetry import metrics, trace
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor, SpanExporter

from app.bootstrap.settings import Settings
from app.orchestration.state import SENSITIVE

REDACTED = "[REDACTED]"
EXTRA_SENSITIVE = {"note", "rationale", "authorization", "api_key", "token", "password", "jwt_secret"}
_PAN = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _mask(value: Any) -> Any:
    if isinstance(value, str):
        return _EMAIL.sub("[EMAIL]", _PAN.sub("[PAN]", value))
    if isinstance(value, dict):
        return {k: REDACTED if k in SENSITIVE or k in EXTRA_SENSITIVE else _mask(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_mask(v) for v in value]
    return value


def redact(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for k in list(event):
        if k in SENSITIVE or k in EXTRA_SENSITIVE:
            event[k] = REDACTED
        elif k != "event":
            event[k] = _mask(event[k])
    return event


def add_trace_ids(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event["trace_id"] = format(ctx.trace_id, "032x")
        event["span_id"] = format(ctx.span_id, "016x")
    return event


def configure_logging(level: int = logging.INFO) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", key="ts"),
            add_trace_ids,
            redact,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=False,
    )


_configured: dict[str, Any] = {}


def setup_telemetry(s: Settings, span_exporter: SpanExporter | None = None) -> dict[str, Any]:
    """Install global tracer and meter providers once per process (OTel refuses to replace them)."""
    if _configured:
        return _configured
    resource = Resource.create({"service.name": s.service_name, "deployment.environment": s.env})
    tp = TracerProvider(resource=resource)
    if span_exporter is not None:
        tp.add_span_processor(SimpleSpanProcessor(span_exporter))
    elif s.otel_exporter == "console":
        tp.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif s.otel_exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{s.otel_endpoint}/v1/traces")))
    trace.set_tracer_provider(tp)
    reader = PrometheusMetricReader()
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    _configured.update(tracer_provider=tp, metric_reader=reader)
    return _configured


def instrument_http(app: Any) -> None:
    """HTTP server spans and metrics (T13.4). Must run in create_app, before the middleware stack is built."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")


def instrument_db(engine: Any) -> None:
    """DB spans (T13.4); the engine exists only once the container is built. MCP calls get spans in the gateway."""
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
