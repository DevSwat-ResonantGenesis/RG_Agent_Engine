"""Shared observability components for production monitoring."""

from .structured_logging import StructuredLogger, LogContext, get_logger
from .slo import SLODefinition, SLOTracker, SLORegistry
from .metrics import MetricsCollector, Counter, Histogram, Gauge
from .tracing import AgentTracer, agent_tracer, init_tracing, traced
from .otel_middleware import (
    setup_opentelemetry,
    OpenTelemetryMiddleware,
    create_custom_span,
    add_span_event,
    set_span_attribute,
    record_exception,
    instrument_sqlalchemy,
)

__all__ = [
    "StructuredLogger",
    "LogContext", 
    "get_logger",
    "SLODefinition",
    "SLOTracker",
    "SLORegistry",
    "MetricsCollector",
    "Counter",
    "Histogram",
    "Gauge",
    "AgentTracer",
    "agent_tracer",
    "init_tracing",
    "traced",
    "setup_opentelemetry",
    "OpenTelemetryMiddleware",
    "create_custom_span",
    "add_span_event",
    "set_span_attribute",
    "record_exception",
    "instrument_sqlalchemy",
]
