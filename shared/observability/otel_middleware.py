"""Full OpenTelemetry middleware integration for FastAPI services.

Provides:
- Automatic request tracing
- Span context propagation
- Custom attributes for agent operations
- Metrics collection
- Log correlation
"""

import os
import time
from typing import Callable, Optional

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# OpenTelemetry imports with graceful fallback
try:
    from opentelemetry import trace, metrics
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import Status, StatusCode, SpanKind
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False


class OpenTelemetryMiddleware(BaseHTTPMiddleware):
    """Middleware for OpenTelemetry request tracing."""

    def __init__(self, app, service_name: str = "unknown"):
        super().__init__(app)
        self.service_name = service_name
        self.tracer = trace.get_tracer(service_name) if OTEL_AVAILABLE else None

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not OTEL_AVAILABLE or not self.tracer:
            return await call_next(request)

        # Extract trace context from headers
        propagator = TraceContextTextMapPropagator()
        ctx = propagator.extract(carrier=dict(request.headers))

        with self.tracer.start_as_current_span(
            f"{request.method} {request.url.path}",
            context=ctx,
            kind=SpanKind.SERVER,
            attributes={
                "http.method": request.method,
                "http.url": str(request.url),
                "http.route": request.url.path,
                "http.host": request.headers.get("host", ""),
                "http.user_agent": request.headers.get("user-agent", ""),
                "service.name": self.service_name,
            },
        ) as span:
            # Add user context if available
            user_id = request.headers.get("x-user-id")
            if user_id:
                span.set_attribute("user.id", user_id)

            # Add agent context if available
            agent_id = request.headers.get("x-agent-id")
            if agent_id:
                span.set_attribute("agent.id", agent_id)

            session_id = request.headers.get("x-session-id")
            if session_id:
                span.set_attribute("agent.session_id", session_id)

            start_time = time.perf_counter()

            try:
                response = await call_next(request)

                # Record response attributes
                span.set_attribute("http.status_code", response.status_code)

                if response.status_code >= 400:
                    span.set_status(Status(StatusCode.ERROR))
                else:
                    span.set_status(Status(StatusCode.OK))

                return response

            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise

            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000
                span.set_attribute("http.duration_ms", duration_ms)


def setup_opentelemetry(
    app: FastAPI,
    service_name: str,
    otlp_endpoint: Optional[str] = None,
    enable_metrics: bool = True,
    enable_auto_instrumentation: bool = True,
) -> bool:
    """
    Set up full OpenTelemetry integration for a FastAPI app.
    
    Args:
        app: FastAPI application instance
        service_name: Name of the service for tracing
        otlp_endpoint: OTLP collector endpoint (default from env)
        enable_metrics: Enable metrics collection
        enable_auto_instrumentation: Enable auto-instrumentation for libraries
        
    Returns:
        True if setup successful, False otherwise
    """
    if not OTEL_AVAILABLE:
        print(f"[{service_name}] OpenTelemetry not available, skipping setup")
        return False

    otlp_endpoint = otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    # Create resource
    resource = Resource.create({
        "service.name": service_name,
        "service.version": os.getenv("SERVICE_VERSION", "1.0.0"),
        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
        "service.namespace": "resonantgenesis",
    })

    # Set up tracing
    tracer_provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    trace.set_tracer_provider(tracer_provider)

    # Set up metrics
    if enable_metrics and otlp_endpoint:
        try:
            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=otlp_endpoint),
                export_interval_millis=60000,
            )
            meter_provider = MeterProvider(
                resource=resource,
                metric_readers=[metric_reader],
            )
            metrics.set_meter_provider(meter_provider)
        except Exception as e:
            print(f"[{service_name}] Failed to set up metrics: {e}")

    # Auto-instrumentation
    if enable_auto_instrumentation:
        try:
            FastAPIInstrumentor.instrument_app(app)
        except Exception:
            pass

        try:
            HTTPXClientInstrumentor().instrument()
        except Exception:
            pass

        try:
            RedisInstrumentor().instrument()
        except Exception:
            pass

    # Add custom middleware
    app.add_middleware(OpenTelemetryMiddleware, service_name=service_name)

    print(f"[{service_name}] OpenTelemetry initialized")
    return True


def instrument_sqlalchemy(engine):
    """Instrument SQLAlchemy engine for tracing."""
    if not OTEL_AVAILABLE:
        return

    try:
        SQLAlchemyInstrumentor().instrument(engine=engine)
    except Exception:
        pass


def create_custom_span(
    name: str,
    attributes: Optional[dict] = None,
    kind: SpanKind = SpanKind.INTERNAL,
):
    """Create a custom span for tracing."""
    if not OTEL_AVAILABLE:
        from contextlib import nullcontext
        return nullcontext()

    tracer = trace.get_tracer(__name__)
    return tracer.start_as_current_span(
        name,
        kind=kind,
        attributes=attributes or {},
    )


def add_span_event(name: str, attributes: Optional[dict] = None):
    """Add an event to the current span."""
    if not OTEL_AVAILABLE:
        return

    span = trace.get_current_span()
    if span:
        span.add_event(name, attributes=attributes or {})


def set_span_attribute(key: str, value):
    """Set an attribute on the current span."""
    if not OTEL_AVAILABLE:
        return

    span = trace.get_current_span()
    if span:
        span.set_attribute(key, value)


def record_exception(exception: Exception):
    """Record an exception on the current span."""
    if not OTEL_AVAILABLE:
        return

    span = trace.get_current_span()
    if span:
        span.record_exception(exception)
        span.set_status(Status(StatusCode.ERROR, str(exception)))
