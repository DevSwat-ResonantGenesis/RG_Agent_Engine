"""
Structured logging for production observability.
JSON-formatted logs with context propagation.
"""

import json
import logging
import sys
import time
import traceback
from pathlib import Path

# Add shared modules to path
SHARED_PATH = Path(__file__).resolve().parents[2] / "shared"
if str(SHARED_PATH) not in sys.path:
    sys.path.insert(0, str(SHARED_PATH))
from contextvars import ContextVar
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum


class LogLevel(Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class LogContext:
    """Contextual information for structured logs."""
    service: str
    environment: str = "development"
    version: str = "1.0.0"
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    user_id: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


# Context variable for current log context
_log_context: ContextVar[Optional[LogContext]] = ContextVar("log_context", default=None)


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        ctx = _log_context.get()
        
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add context if available
        if ctx:
            log_entry["service"] = ctx.service
            log_entry["environment"] = ctx.environment
            log_entry["version"] = ctx.version
            
            if ctx.trace_id:
                log_entry["trace_id"] = ctx.trace_id
            if ctx.span_id:
                log_entry["span_id"] = ctx.span_id
            if ctx.user_id:
                log_entry["user_id"] = ctx.user_id
            if ctx.request_id:
                log_entry["request_id"] = ctx.request_id
            if ctx.correlation_id:
                log_entry["correlation_id"] = ctx.correlation_id
            if ctx.extra:
                log_entry["context"] = ctx.extra
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "stacktrace": traceback.format_exception(*record.exc_info),
            }
        
        # Add extra fields from record
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)
        
        return json.dumps(log_entry, default=str)


class StructuredLogger:
    """
    Production structured logger with:
    - JSON output format
    - Context propagation
    - Automatic field enrichment
    - Multiple output handlers
    """
    
    def __init__(
        self,
        name: str,
        level: str = "INFO",
        context: Optional[LogContext] = None,
    ):
        self.name = name
        self._logger = logging.getLogger(name)
        self._logger.setLevel(getattr(logging, level.upper()))
        self._context = context
        
        # Remove existing handlers
        self._logger.handlers = []
        
        # Add structured handler
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter())
        self._logger.addHandler(handler)
        
        # Prevent propagation to root logger
        self._logger.propagate = False
    
    def _log(
        self,
        level: int,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
        exc_info: bool = False,
    ) -> None:
        """Internal log method."""
        record = self._logger.makeRecord(
            self.name,
            level,
            "",
            0,
            message,
            (),
            None if not exc_info else sys.exc_info(),
        )
        
        if extra:
            record.extra_fields = extra
        
        self._logger.handle(record)
    
    def debug(self, message: str, **kwargs) -> None:
        self._log(logging.DEBUG, message, kwargs)
    
    def info(self, message: str, **kwargs) -> None:
        self._log(logging.INFO, message, kwargs)
    
    def warning(self, message: str, **kwargs) -> None:
        self._log(logging.WARNING, message, kwargs)
    
    def error(self, message: str, exc_info: bool = False, **kwargs) -> None:
        self._log(logging.ERROR, message, kwargs, exc_info=exc_info)
    
    def critical(self, message: str, exc_info: bool = False, **kwargs) -> None:
        self._log(logging.CRITICAL, message, kwargs, exc_info=exc_info)
    
    def exception(self, message: str, **kwargs) -> None:
        self._log(logging.ERROR, message, kwargs, exc_info=True)
    
    def with_context(self, **kwargs) -> "StructuredLogger":
        """Create a child logger with additional context."""
        new_context = LogContext(
            service=self._context.service if self._context else self.name,
            extra=kwargs,
        )
        return StructuredLogger(self.name, context=new_context)
    
    @staticmethod
    def set_context(ctx: LogContext) -> None:
        """Set the current log context."""
        _log_context.set(ctx)
    
    @staticmethod
    def get_context() -> Optional[LogContext]:
        """Get the current log context."""
        return _log_context.get()
    
    @staticmethod
    def clear_context() -> None:
        """Clear the current log context."""
        _log_context.set(None)


# Logger registry
_loggers: Dict[str, StructuredLogger] = {}


def get_logger(name: str, level: str = "INFO") -> StructuredLogger:
    """Get or create a structured logger."""
    if name not in _loggers:
        _loggers[name] = StructuredLogger(name, level)
    return _loggers[name]


class LoggingMiddleware:
    """FastAPI middleware for request logging."""
    
    def __init__(self, service_name: str, environment: str = "development"):
        self.service_name = service_name
        self.environment = environment
        self.logger = get_logger(f"{service_name}.requests")
    
    async def __call__(self, request, call_next):
        start_time = time.time()
        
        # Extract trace context
        trace_id = request.headers.get("X-Request-ID") or request.headers.get("traceparent", "").split("-")[1] if "-" in request.headers.get("traceparent", "") else None
        user_id = request.headers.get("x-user-id")
        
        # Set log context
        ctx = LogContext(
            service=self.service_name,
            environment=self.environment,
            trace_id=trace_id,
            user_id=user_id,
            request_id=trace_id,
        )
        StructuredLogger.set_context(ctx)
        
        # Log request
        self.logger.info(
            "Request started",
            method=request.method,
            path=request.url.path,
            query=str(request.query_params),
        )
        
        try:
            response = await call_next(request)
            
            duration_ms = (time.time() - start_time) * 1000
            
            # Log response
            self.logger.info(
                "Request completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )
            
            return response
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            
            self.logger.error(
                "Request failed",
                method=request.method,
                path=request.url.path,
                error=str(e),
                duration_ms=round(duration_ms, 2),
                exc_info=True,
            )
            raise
            
        finally:
            StructuredLogger.clear_context()
