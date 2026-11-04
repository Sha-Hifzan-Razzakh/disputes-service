"""Node instrumentation (T08.6, T13.2): one span, one duration sample and start/end log lines per node run, all
carrying graph, node and thread_id. Uses only the vendor-neutral OpenTelemetry API; bootstrap wires exporters."""

from __future__ import annotations

import functools
import inspect
import time
from collections.abc import Callable
from typing import Any

import structlog
from opentelemetry import metrics, trace

_tracer = trace.get_tracer("disputes.graph")
_meter = metrics.get_meter("disputes.graph")
_node_ms = _meter.create_histogram("graph_node_duration", unit="ms", description="Node run time")
_node_errors = _meter.create_counter("graph_node_errors", description="Node runs that raised")
_outcomes = _meter.create_counter("resolution_outcomes", description="Terminal outcomes of resolution runs")
log = structlog.get_logger("graph")


def _thread_id(kwargs: dict[str, Any]) -> str | None:
    rt = kwargs.get("runtime")
    info = getattr(rt, "execution_info", None)
    return getattr(info, "thread_id", None)


def instrument(graph: str, node: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a node without changing what LangGraph sees: signature, annotations (Command destinations) and
    injected parameters are preserved through functools.wraps (__wrapped__)."""

    def _start(kwargs: dict[str, Any]) -> tuple[Any, dict[str, Any], float]:
        attrs = {"graph": graph, "node": node, "thread_id": _thread_id(kwargs) or ""}
        span = _tracer.start_as_current_span(f"{graph}.{node}", attributes=attrs)
        return span, attrs, time.perf_counter()

    def _end(attrs: dict[str, Any], t0: float, result: Any, error: BaseException | None) -> None:
        ms = (time.perf_counter() - t0) * 1000
        _node_ms.record(ms, {"graph": graph, "node": node})
        if error is not None and type(error).__name__ not in {"GraphInterrupt", "ParentCommand"}:
            _node_errors.add(1, {"graph": graph, "node": node, "error": type(error).__name__})
            log.error("node.error", **attrs, ms=round(ms, 1), error=type(error).__name__, detail=str(error)[:300])
            return
        update = result if isinstance(result, dict) else getattr(result, "update", None)  # dict or Command
        keys = sorted(update) if isinstance(update, dict) else []
        if isinstance(update, dict) and update.get("outcome") and graph == "resolution":
            _outcomes.add(1, {"outcome": str(update["outcome"])})
        log.info("node.end", **attrs, ms=round(ms, 1), wrote=keys, goto=getattr(result, "goto", None) or None)

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            cm, attrs, t0 = _start(kwargs)
            with cm:
                try:
                    result = await fn(*args, **kwargs)
                except BaseException as e:
                    _end(attrs, t0, None, e)
                    raise
            _end(attrs, t0, result, None)
            return result

        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        cm, attrs, t0 = _start(kwargs)
        with cm:
            try:
                result = fn(*args, **kwargs)
            except BaseException as e:
                _end(attrs, t0, None, e)
                raise
        _end(attrs, t0, result, None)
        return result

    return sync_wrapper
