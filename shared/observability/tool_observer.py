"""
Tool-Level Observability
========================

Unified logging of every tool call across ALL Resonant Genesis systems:
- Which tool was called
- Success / failure
- Latency (ms)
- Token cost (estimated input + output)
- Result size (chars)
- Error messages
- System context (agentic_chat, public_chat, executor, ide)

Provides:
  - In-memory metrics with periodic flush
  - Structured JSON log lines for log aggregation (ELK/Loki/Datadog)
  - Prometheus-compatible counters (optional)
  - Per-tool, per-user, per-session aggregation

Usage:
    observer = ToolObserver(system="agentic_chat")

    # Wrap any tool call:
    async with observer.observe("web_search", user_id="u123", session_id="s456") as ctx:
        result = await handler(args)
        ctx.set_result(result)
        ctx.set_tokens(input_tokens=500, output_tokens=200)

    # Or use decorator:
    @observer.track("web_search")
    async def handle_web_search(args, **kwargs):
        ...

    # Get metrics:
    observer.get_tool_stats("web_search")
    observer.get_summary()
    observer.flush_to_log()
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("rg_tool_observability")


@dataclass
class ToolCallRecord:
    """Single tool call record — written to log and stored in memory."""
    tool_name: str
    system: str                          # agentic_chat | public_chat | executor | ide
    user_id: str = ""
    session_id: str = ""
    agent_id: str = ""                   # For executor: which agent
    timestamp: str = ""                  # ISO 8601
    latency_ms: float = 0.0
    success: bool = False
    error: Optional[str] = None
    result_chars: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    loop_number: int = 0                 # Which loop in multi-loop session
    truncated: bool = False              # Whether result was truncated
    args_summary: str = ""               # First 200 chars of args for debugging
    provider: str = ""                   # LLM provider that requested the tool

    def to_log_dict(self) -> Dict[str, Any]:
        """Structured dict for JSON logging."""
        d = asdict(self)
        d["_type"] = "tool_call"
        return {k: v for k, v in d.items() if v or v == 0}

    def to_log_line(self) -> str:
        """Single-line structured log."""
        status = "OK" if self.success else f"FAIL:{self.error or 'unknown'}"
        return (
            f"[TOOL] {self.system}/{self.tool_name} "
            f"user={self.user_id} session={self.session_id} "
            f"status={status} latency={self.latency_ms:.0f}ms "
            f"result={self.result_chars}chars tokens={self.total_tokens} "
            f"loop={self.loop_number} provider={self.provider}"
        )


@dataclass
class ToolStats:
    """Aggregated stats for a single tool."""
    tool_name: str
    total_calls: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0
    total_result_chars: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    errors: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    last_called: str = ""

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.total_calls if self.total_calls else 0

    @property
    def success_rate(self) -> float:
        return self.success_count / self.total_calls if self.total_calls else 0

    @property
    def avg_result_chars(self) -> float:
        return self.total_result_chars / self.total_calls if self.total_calls else 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "total_calls": self.total_calls,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": round(self.success_rate, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "min_latency_ms": round(self.min_latency_ms, 1) if self.min_latency_ms != float("inf") else 0,
            "max_latency_ms": round(self.max_latency_ms, 1),
            "total_result_chars": self.total_result_chars,
            "total_tokens": self.total_tokens,
            "avg_result_chars": round(self.avg_result_chars, 0),
            "top_errors": dict(sorted(self.errors.items(), key=lambda x: -x[1])[:5]),
            "last_called": self.last_called,
        }


class _ObserveContext:
    """Context manager returned by observer.observe()."""

    def __init__(self, record: ToolCallRecord):
        self.record = record
        self._start = time.monotonic()

    def set_result(self, result: Any) -> None:
        """Set the tool result for size tracking."""
        if isinstance(result, str):
            self.record.result_chars = len(result)
        elif isinstance(result, dict):
            self.record.result_chars = len(json.dumps(result, default=str))
        self.record.success = True

    def set_error(self, error: str) -> None:
        """Mark the call as failed."""
        self.record.success = False
        self.record.error = error[:500]

    def set_tokens(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Set token usage."""
        self.record.input_tokens = input_tokens
        self.record.output_tokens = output_tokens
        self.record.total_tokens = input_tokens + output_tokens

    def set_truncated(self, truncated: bool = True) -> None:
        self.record.truncated = truncated

    def finalize(self) -> None:
        """Called automatically at end of context."""
        self.record.latency_ms = (time.monotonic() - self._start) * 1000
        if not self.record.timestamp:
            self.record.timestamp = datetime.now(timezone.utc).isoformat()


class ToolObserver:
    """
    Central tool observability engine.

    Create one per system:
        chat_observer = ToolObserver(system="agentic_chat")
        public_observer = ToolObserver(system="public_chat")
        agent_observer = ToolObserver(system="executor")
        ide_observer = ToolObserver(system="ide")

    Or share a single global observer across all systems.
    """

    def __init__(
        self,
        system: str = "unknown",
        log_every_call: bool = True,
        max_records: int = 10000,
    ):
        self.system = system
        self.log_every_call = log_every_call
        self.max_records = max_records
        self._records: List[ToolCallRecord] = []
        self._stats: Dict[str, ToolStats] = defaultdict(lambda: ToolStats(tool_name=""))
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def observe(
        self,
        tool_name: str,
        user_id: str = "",
        session_id: str = "",
        agent_id: str = "",
        loop_number: int = 0,
        provider: str = "",
        args: Optional[Dict] = None,
    ):
        """
        Async context manager that tracks a single tool call.

        Usage:
            async with observer.observe("web_search", user_id="u1") as ctx:
                result = await do_search(query)
                ctx.set_result(result)
        """
        record = ToolCallRecord(
            tool_name=tool_name,
            system=self.system,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            loop_number=loop_number,
            provider=provider,
            args_summary=json.dumps(args, default=str)[:200] if args else "",
        )
        ctx = _ObserveContext(record)

        try:
            yield ctx
        except Exception as e:
            ctx.set_error(str(e))
            raise
        finally:
            ctx.finalize()
            await self._record(record)

    def track(self, tool_name: str):
        """
        Decorator that auto-tracks a tool handler function.

        Usage:
            @observer.track("web_search")
            async def handle_web_search(args, **kwargs):
                return await do_search(args["query"])
        """
        def decorator(fn: Callable):
            @wraps(fn)
            async def wrapper(*args, **kwargs):
                user_id = kwargs.get("user_id", "")
                session_id = kwargs.get("session_id", "")
                agent_id = kwargs.get("agent_id", "")

                async with self.observe(
                    tool_name=tool_name,
                    user_id=user_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    args=args[0] if args and isinstance(args[0], dict) else None,
                ) as ctx:
                    result = await fn(*args, **kwargs)
                    if isinstance(result, dict) and result.get("error"):
                        ctx.set_error(result["error"])
                    else:
                        ctx.set_result(result)
                    return result
            return wrapper
        return decorator

    async def _record(self, record: ToolCallRecord) -> None:
        """Store record and update stats."""
        async with self._lock:
            # Store record (ring buffer)
            self._records.append(record)
            if len(self._records) > self.max_records:
                self._records = self._records[-self.max_records:]

            # Update stats
            stats = self._stats[record.tool_name]
            stats.tool_name = record.tool_name
            stats.total_calls += 1
            if record.success:
                stats.success_count += 1
            else:
                stats.failure_count += 1
                if record.error:
                    error_key = record.error[:100]
                    stats.errors[error_key] += 1

            stats.total_latency_ms += record.latency_ms
            stats.min_latency_ms = min(stats.min_latency_ms, record.latency_ms)
            stats.max_latency_ms = max(stats.max_latency_ms, record.latency_ms)
            stats.total_result_chars += record.result_chars
            stats.total_input_tokens += record.input_tokens
            stats.total_output_tokens += record.output_tokens
            stats.total_tokens += record.total_tokens
            stats.last_called = record.timestamp

        # Log immediately if enabled
        if self.log_every_call:
            logger.info(record.to_log_line())

    # ── Query Methods ──

    def get_tool_stats(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Get aggregated stats for a specific tool."""
        stats = self._stats.get(tool_name)
        return stats.to_dict() if stats else None

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all tool usage."""
        total_calls = sum(s.total_calls for s in self._stats.values())
        total_failures = sum(s.failure_count for s in self._stats.values())
        total_tokens = sum(s.total_tokens for s in self._stats.values())

        tools_by_calls = sorted(
            self._stats.values(),
            key=lambda s: s.total_calls,
            reverse=True,
        )

        tools_by_latency = sorted(
            [s for s in self._stats.values() if s.total_calls > 0],
            key=lambda s: s.avg_latency_ms,
            reverse=True,
        )

        tools_by_failures = sorted(
            [s for s in self._stats.values() if s.failure_count > 0],
            key=lambda s: s.failure_count,
            reverse=True,
        )

        return {
            "system": self.system,
            "total_calls": total_calls,
            "total_failures": total_failures,
            "overall_success_rate": round(
                (total_calls - total_failures) / total_calls, 3
            ) if total_calls else 0,
            "total_tokens": total_tokens,
            "unique_tools_used": len(self._stats),
            "records_stored": len(self._records),
            "top_tools_by_calls": [
                {"name": s.tool_name, "calls": s.total_calls}
                for s in tools_by_calls[:10]
            ],
            "slowest_tools": [
                {"name": s.tool_name, "avg_ms": round(s.avg_latency_ms, 1)}
                for s in tools_by_latency[:10]
            ],
            "most_failing_tools": [
                {"name": s.tool_name, "failures": s.failure_count, "rate": round(1 - s.success_rate, 3)}
                for s in tools_by_failures[:10]
            ],
        }

    def get_all_stats(self) -> List[Dict[str, Any]]:
        """Get stats for all tools."""
        return [s.to_dict() for s in sorted(
            self._stats.values(), key=lambda s: -s.total_calls
        )]

    def get_recent_records(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get most recent tool call records."""
        return [r.to_log_dict() for r in self._records[-limit:]]

    def get_records_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all tool calls for a specific session."""
        return [r.to_log_dict() for r in self._records if r.session_id == session_id]

    def get_records_for_user(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent tool calls for a specific user."""
        records = [r for r in self._records if r.user_id == user_id]
        return [r.to_log_dict() for r in records[-limit:]]

    def reset(self) -> None:
        """Clear all records and stats."""
        self._records.clear()
        self._stats.clear()

    def flush_to_log(self) -> int:
        """Write all pending records to log and clear."""
        count = len(self._records)
        for record in self._records:
            logger.info(json.dumps(record.to_log_dict()))
        self._records.clear()
        return count


# ── Global singleton (optional convenience) ──
_global_observers: Dict[str, ToolObserver] = {}


def get_observer(system: str = "default") -> ToolObserver:
    """Get or create a global observer for a system."""
    if system not in _global_observers:
        _global_observers[system] = ToolObserver(system=system)
    return _global_observers[system]
