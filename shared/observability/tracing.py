"""OpenTelemetry tracing for autonomous agent pipeline.

Provides distributed tracing across all agent operations:
- Step execution
- Tool calls
- LLM requests
- Safety checks
- Plan revisions
- Verification decisions
"""

import functools
import os
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Optional

# OpenTelemetry imports
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.trace import Status, StatusCode, SpanKind
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None


class AgentTracer:
    """
    OpenTelemetry tracer for autonomous agent operations.
    
    Traces:
    - Agent sessions
    - Execution loops
    - Individual steps
    - Tool calls
    - LLM requests
    - Safety checks
    - Verifications
    - Plan revisions
    """

    def __init__(
        self,
        service_name: str = "agent-engine",
        otlp_endpoint: Optional[str] = None,
        enable_console: bool = False,
    ):
        self.service_name = service_name
        self.enabled = OTEL_AVAILABLE
        self.tracer = None
        self.propagator = None

        if not self.enabled:
            return

        # Configure resource
        resource = Resource.create({
            "service.name": service_name,
            "service.version": os.getenv("SERVICE_VERSION", "1.0.0"),
            "deployment.environment": os.getenv("ENVIRONMENT", "development"),
        })

        # Create tracer provider
        provider = TracerProvider(resource=resource)

        # Add OTLP exporter if endpoint provided
        otlp_endpoint = otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if otlp_endpoint:
            otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

        # Add console exporter for debugging
        if enable_console or os.getenv("OTEL_CONSOLE_EXPORT", "").lower() == "true":
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        # Set global tracer provider
        trace.set_tracer_provider(provider)

        # Get tracer
        self.tracer = trace.get_tracer(service_name)
        self.propagator = TraceContextTextMapPropagator()

        # Instrument httpx for automatic HTTP tracing
        try:
            HTTPXClientInstrumentor().instrument()
        except Exception:
            pass

    def get_tracer(self):
        """Get the OpenTelemetry tracer."""
        return self.tracer

    @asynccontextmanager
    async def trace_session(
        self,
        session_id: str,
        agent_id: str,
        goal: str,
        user_id: Optional[str] = None,
    ):
        """Trace an entire agent session."""
        if not self.enabled or not self.tracer:
            yield None
            return

        with self.tracer.start_as_current_span(
            "agent.session",
            kind=SpanKind.SERVER,
            attributes={
                "agent.session_id": session_id,
                "agent.agent_id": agent_id,
                "agent.goal": goal[:500],
                "agent.user_id": user_id or "anonymous",
            },
        ) as span:
            try:
                yield span
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise

    @asynccontextmanager
    async def trace_loop_iteration(
        self,
        session_id: str,
        iteration: int,
    ):
        """Trace a single loop iteration."""
        if not self.enabled or not self.tracer:
            yield None
            return

        with self.tracer.start_as_current_span(
            "agent.loop_iteration",
            attributes={
                "agent.session_id": session_id,
                "agent.iteration": iteration,
            },
        ) as span:
            try:
                yield span
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise

    @asynccontextmanager
    async def trace_step(
        self,
        session_id: str,
        step_number: int,
        step_type: str,
    ):
        """Trace a single step execution."""
        if not self.enabled or not self.tracer:
            yield None
            return

        with self.tracer.start_as_current_span(
            f"agent.step.{step_type}",
            attributes={
                "agent.session_id": session_id,
                "agent.step_number": step_number,
                "agent.step_type": step_type,
            },
        ) as span:
            try:
                yield span
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise

    @asynccontextmanager
    async def trace_tool_call(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
    ):
        """Trace a tool call."""
        if not self.enabled or not self.tracer:
            yield None
            return

        with self.tracer.start_as_current_span(
            f"agent.tool.{tool_name}",
            kind=SpanKind.CLIENT,
            attributes={
                "agent.tool_name": tool_name,
                "agent.tool_input_keys": ",".join(tool_input.keys()),
            },
        ) as span:
            try:
                yield span
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise

    @asynccontextmanager
    async def trace_llm_request(
        self,
        model: str,
        prompt_type: str,
        token_count: Optional[int] = None,
    ):
        """Trace an LLM request."""
        if not self.enabled or not self.tracer:
            yield None
            return

        with self.tracer.start_as_current_span(
            "agent.llm_request",
            kind=SpanKind.CLIENT,
            attributes={
                "llm.model": model,
                "llm.prompt_type": prompt_type,
                "llm.token_count": token_count or 0,
            },
        ) as span:
            try:
                yield span
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise

    @asynccontextmanager
    async def trace_safety_check(
        self,
        action_type: str,
    ):
        """Trace a safety check."""
        if not self.enabled or not self.tracer:
            yield None
            return

        with self.tracer.start_as_current_span(
            "agent.safety_check",
            attributes={
                "agent.action_type": action_type,
            },
        ) as span:
            try:
                yield span
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise

    @asynccontextmanager
    async def trace_verification(
        self,
        step_number: int,
        verification_type: str,
    ):
        """Trace a verification check."""
        if not self.enabled or not self.tracer:
            yield None
            return

        with self.tracer.start_as_current_span(
            "agent.verification",
            attributes={
                "agent.step_number": step_number,
                "agent.verification_type": verification_type,
            },
        ) as span:
            try:
                yield span
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise

    @asynccontextmanager
    async def trace_plan_revision(
        self,
        session_id: str,
        revision_count: int,
        reason: str,
    ):
        """Trace a plan revision."""
        if not self.enabled or not self.tracer:
            yield None
            return

        with self.tracer.start_as_current_span(
            "agent.plan_revision",
            attributes={
                "agent.session_id": session_id,
                "agent.revision_count": revision_count,
                "agent.revision_reason": reason[:200],
            },
        ) as span:
            try:
                yield span
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        """Add an event to the current span."""
        if not self.enabled:
            return

        span = trace.get_current_span()
        if span:
            span.add_event(name, attributes=attributes or {})

    def set_attribute(self, key: str, value: Any):
        """Set an attribute on the current span."""
        if not self.enabled:
            return

        span = trace.get_current_span()
        if span:
            span.set_attribute(key, value)

    def record_error(self, error: Exception):
        """Record an error on the current span."""
        if not self.enabled:
            return

        span = trace.get_current_span()
        if span:
            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR, str(error)))


def traced(span_name: Optional[str] = None):
    """Decorator to trace async functions."""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if not OTEL_AVAILABLE or not agent_tracer.tracer:
                return await func(*args, **kwargs)

            name = span_name or f"{func.__module__}.{func.__name__}"
            with agent_tracer.tracer.start_as_current_span(name):
                return await func(*args, **kwargs)
        return wrapper
    return decorator


# Global tracer instance
agent_tracer = AgentTracer()


def init_tracing(
    service_name: str = "agent-engine",
    otlp_endpoint: Optional[str] = None,
    enable_console: bool = False,
):
    """Initialize tracing for the service."""
    global agent_tracer
    agent_tracer = AgentTracer(
        service_name=service_name,
        otlp_endpoint=otlp_endpoint,
        enable_console=enable_console,
    )
    return agent_tracer
