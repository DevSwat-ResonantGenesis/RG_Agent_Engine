"""
Distributed request tracing with OpenTelemetry-compatible context propagation.
"""

import uuid
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from contextvars import ContextVar
from datetime import datetime


# Context variable for current trace
_current_trace: ContextVar[Optional["TraceContext"]] = ContextVar("current_trace", default=None)


@dataclass
class SpanContext:
    """Individual span within a trace."""
    span_id: str
    name: str
    parent_span_id: Optional[str]
    start_time: float
    end_time: Optional[float] = None
    status: str = "ok"
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    
    @property
    def duration_ms(self) -> Optional[float]:
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return None


@dataclass
class TraceContext:
    """
    Distributed trace context for request tracking.
    Compatible with W3C Trace Context and OpenTelemetry.
    """
    trace_id: str
    root_span_id: str
    service_name: str
    start_time: float
    spans: List[SpanContext] = field(default_factory=list)
    baggage: Dict[str, str] = field(default_factory=dict)
    
    # W3C Trace Context headers
    TRACEPARENT_HEADER = "traceparent"
    TRACESTATE_HEADER = "tracestate"
    REQUEST_ID_HEADER = "X-Request-ID"
    
    @classmethod
    def generate_id(cls, length: int = 32) -> str:
        """Generate a random trace/span ID."""
        return uuid.uuid4().hex[:length]
    
    @classmethod
    def create(cls, service_name: str) -> "TraceContext":
        """Create a new trace context."""
        trace_id = cls.generate_id(32)
        span_id = cls.generate_id(16)
        now = time.time()
        
        ctx = cls(
            trace_id=trace_id,
            root_span_id=span_id,
            service_name=service_name,
            start_time=now,
        )
        
        # Create root span
        root_span = SpanContext(
            span_id=span_id,
            name=f"{service_name}.root",
            parent_span_id=None,
            start_time=now,
        )
        ctx.spans.append(root_span)
        
        return ctx
    
    @classmethod
    def from_headers(cls, headers: Dict[str, str], service_name: str) -> "TraceContext":
        """Extract trace context from HTTP headers."""
        traceparent = headers.get(cls.TRACEPARENT_HEADER)
        request_id = headers.get(cls.REQUEST_ID_HEADER)
        
        if traceparent:
            # Parse W3C traceparent: version-trace_id-parent_id-flags
            parts = traceparent.split("-")
            if len(parts) >= 4:
                trace_id = parts[1]
                parent_span_id = parts[2]
                
                ctx = cls(
                    trace_id=trace_id,
                    root_span_id=cls.generate_id(16),
                    service_name=service_name,
                    start_time=time.time(),
                )
                
                # Create span with parent
                span = SpanContext(
                    span_id=ctx.root_span_id,
                    name=f"{service_name}.request",
                    parent_span_id=parent_span_id,
                    start_time=time.time(),
                )
                ctx.spans.append(span)
                
                return ctx
        
        if request_id:
            # Use request ID as trace ID
            ctx = cls(
                trace_id=request_id,
                root_span_id=cls.generate_id(16),
                service_name=service_name,
                start_time=time.time(),
            )
            span = SpanContext(
                span_id=ctx.root_span_id,
                name=f"{service_name}.request",
                parent_span_id=None,
                start_time=time.time(),
            )
            ctx.spans.append(span)
            return ctx
        
        # Create new trace
        return cls.create(service_name)
    
    def to_headers(self) -> Dict[str, str]:
        """Export trace context to HTTP headers."""
        current_span = self.spans[-1] if self.spans else None
        span_id = current_span.span_id if current_span else self.root_span_id
        
        # W3C traceparent format: version-trace_id-span_id-flags
        traceparent = f"00-{self.trace_id}-{span_id}-01"
        
        headers = {
            self.TRACEPARENT_HEADER: traceparent,
            self.REQUEST_ID_HEADER: self.trace_id,
        }
        
        if self.baggage:
            # Encode baggage
            baggage_str = ",".join(f"{k}={v}" for k, v in self.baggage.items())
            headers["baggage"] = baggage_str
        
        return headers
    
    def start_span(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> SpanContext:
        """Start a new span."""
        parent = self.spans[-1] if self.spans else None
        
        span = SpanContext(
            span_id=self.generate_id(16),
            name=name,
            parent_span_id=parent.span_id if parent else None,
            start_time=time.time(),
            attributes=attributes or {},
        )
        self.spans.append(span)
        return span
    
    def end_span(self, span: SpanContext, status: str = "ok") -> None:
        """End a span."""
        span.end_time = time.time()
        span.status = status
    
    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """Add an event to the current span."""
        if self.spans:
            self.spans[-1].events.append({
                "name": name,
                "timestamp": time.time(),
                "attributes": attributes or {},
            })
    
    def set_attribute(self, key: str, value: Any) -> None:
        """Set an attribute on the current span."""
        if self.spans:
            self.spans[-1].attributes[key] = value
    
    def set_baggage(self, key: str, value: str) -> None:
        """Set baggage item (propagated to downstream services)."""
        self.baggage[key] = value
    
    def to_dict(self) -> Dict[str, Any]:
        """Export trace as dictionary for logging/storage."""
        return {
            "trace_id": self.trace_id,
            "service": self.service_name,
            "start_time": datetime.fromtimestamp(self.start_time).isoformat(),
            "spans": [
                {
                    "span_id": s.span_id,
                    "name": s.name,
                    "parent_span_id": s.parent_span_id,
                    "duration_ms": s.duration_ms,
                    "status": s.status,
                    "attributes": s.attributes,
                    "events": s.events,
                }
                for s in self.spans
            ],
            "baggage": self.baggage,
        }


class RequestTracer:
    """
    Request tracing manager with:
    - Automatic context propagation
    - Span management
    - Metrics collection
    - Export to various backends
    """
    
    def __init__(self, service_name: str):
        self.service_name = service_name
        self._traces: Dict[str, TraceContext] = {}
        self._max_traces = 1000
    
    def start_trace(self, headers: Optional[Dict[str, str]] = None) -> TraceContext:
        """Start a new trace or continue from headers."""
        if headers:
            ctx = TraceContext.from_headers(headers, self.service_name)
        else:
            ctx = TraceContext.create(self.service_name)
        
        # Store trace
        self._traces[ctx.trace_id] = ctx
        
        # Cleanup old traces
        if len(self._traces) > self._max_traces:
            oldest = sorted(self._traces.values(), key=lambda t: t.start_time)[:100]
            for t in oldest:
                del self._traces[t.trace_id]
        
        # Set context variable
        _current_trace.set(ctx)
        
        return ctx
    
    def get_current_trace(self) -> Optional[TraceContext]:
        """Get the current trace context."""
        return _current_trace.get()
    
    def end_trace(self, ctx: TraceContext) -> None:
        """End a trace and all open spans."""
        for span in ctx.spans:
            if span.end_time is None:
                span.end_time = time.time()
        
        _current_trace.set(None)
    
    def inject_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        """Inject trace context into outgoing request headers."""
        ctx = self.get_current_trace()
        if ctx:
            headers.update(ctx.to_headers())
        return headers
    
    def get_trace(self, trace_id: str) -> Optional[TraceContext]:
        """Get a trace by ID."""
        return self._traces.get(trace_id)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get tracing statistics."""
        return {
            "active_traces": len(self._traces),
            "service_name": self.service_name,
        }


# Middleware helper
class TracingMiddleware:
    """FastAPI middleware for automatic request tracing."""
    
    def __init__(self, tracer: RequestTracer):
        self.tracer = tracer
    
    async def __call__(self, request, call_next):
        # Extract headers
        headers = dict(request.headers)
        
        # Start trace
        ctx = self.tracer.start_trace(headers)
        
        # Add request attributes
        ctx.set_attribute("http.method", request.method)
        ctx.set_attribute("http.url", str(request.url))
        ctx.set_attribute("http.route", request.url.path)
        
        try:
            response = await call_next(request)
            
            # Add response attributes
            ctx.set_attribute("http.status_code", response.status_code)
            
            # Add trace headers to response
            for key, value in ctx.to_headers().items():
                response.headers[key] = value
            
            return response
            
        except Exception as e:
            ctx.set_attribute("error", True)
            ctx.set_attribute("error.message", str(e))
            ctx.add_event("exception", {"type": type(e).__name__, "message": str(e)})
            raise
            
        finally:
            self.tracer.end_trace(ctx)
