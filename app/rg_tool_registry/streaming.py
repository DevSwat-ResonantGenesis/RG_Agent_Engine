"""
Tool Result Streaming
=====================

PROBLEM:
  Currently all tool calls block until complete. Long-running tools like
  Code Visualizer scan (30-120s) and deep research (20-60s) leave the user
  staring at "Thinking..." with no feedback.

SOLUTION:
  StreamableTool wraps any tool handler to emit partial results via an
  async generator. The SSE layer can forward these as `event: tool_progress`
  while the tool continues executing.

Supports:
  - Progress callbacks (percentage, status message)
  - Partial result streaming (intermediate data available before completion)
  - Timeout with graceful cancellation
  - Integration with ToolObserver for latency tracking

Usage:
    # Wrap an existing handler to be streamable:
    streamer = ToolStreamer()

    @streamer.streamable("code_visualizer_scan", estimated_seconds=60)
    async def scan_github(args, progress=None):
        progress(0.1, "Cloning repository...")
        repo = await clone(args["repo_url"])

        progress(0.3, "Running AST analysis...")
        ast = await analyze(repo)

        progress(0.7, "Building dependency graph...")
        graph = build_graph(ast)

        progress(0.9, "Generating report...")
        return {"stats": graph.stats, "nodes": graph.nodes}

    # In SSE stream handler:
    async for event in streamer.execute_streaming("code_visualizer_scan", args):
        if event["type"] == "progress":
            yield sse_event("tool_progress", event)
        elif event["type"] == "result":
            yield sse_event("tool_result", event)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class StreamEvent:
    """Single streaming event from a tool."""
    type: str         # "progress" | "partial" | "result" | "error"
    tool_name: str
    progress: float = 0.0     # 0.0 - 1.0
    message: str = ""
    data: Optional[Dict[str, Any]] = None
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "type": self.type,
            "tool": self.tool_name,
        }
        if self.type == "progress":
            d["progress"] = round(self.progress, 2)
            d["message"] = self.message
            d["elapsed_ms"] = round(self.elapsed_ms, 0)
        elif self.type == "partial":
            d["data"] = self.data
            d["progress"] = round(self.progress, 2)
        elif self.type == "result":
            d["data"] = self.data
            d["elapsed_ms"] = round(self.elapsed_ms, 0)
        elif self.type == "error":
            d["error"] = self.message
            d["elapsed_ms"] = round(self.elapsed_ms, 0)
        return d


class ProgressReporter:
    """
    Passed into streamable tool handlers so they can report progress.

    Usage inside a tool handler:
        async def my_tool(args, progress=None):
            progress(0.2, "Step 1 done...")
            progress(0.5, "Step 2 done...", partial_data={"items_found": 42})
            return final_result
    """

    def __init__(self, tool_name: str, queue: asyncio.Queue, start_time: float):
        self.tool_name = tool_name
        self._queue = queue
        self._start = start_time

    def __call__(
        self,
        progress: float,
        message: str = "",
        partial_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Report progress (non-blocking)."""
        elapsed = (time.monotonic() - self._start) * 1000

        event = StreamEvent(
            type="partial" if partial_data else "progress",
            tool_name=self.tool_name,
            progress=min(max(progress, 0.0), 1.0),
            message=message,
            data=partial_data,
            elapsed_ms=elapsed,
        )

        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            pass  # Drop progress events if queue is full


class ToolStreamer:
    """
    Manages streamable tool execution.

    Register tools with @streamer.streamable() decorator,
    then use execute_streaming() to get async event generator.
    """

    def __init__(self, default_timeout: float = 120.0, queue_size: int = 100):
        self._handlers: Dict[str, Callable] = {}
        self._configs: Dict[str, Dict[str, Any]] = {}
        self.default_timeout = default_timeout
        self.queue_size = queue_size

    def streamable(
        self,
        tool_name: str,
        estimated_seconds: float = 30.0,
        timeout: Optional[float] = None,
    ):
        """
        Decorator to register a streamable tool handler.

        The handler receives an extra `progress` keyword argument:
            async def handler(args, progress=None):
                progress(0.5, "Halfway done...")
                return result
        """
        def decorator(fn: Callable):
            self._handlers[tool_name] = fn
            self._configs[tool_name] = {
                "estimated_seconds": estimated_seconds,
                "timeout": timeout or self.default_timeout,
            }
            return fn
        return decorator

    def register(
        self,
        tool_name: str,
        handler: Callable,
        estimated_seconds: float = 30.0,
        timeout: Optional[float] = None,
    ) -> None:
        """Register a streamable handler programmatically."""
        self._handlers[tool_name] = handler
        self._configs[tool_name] = {
            "estimated_seconds": estimated_seconds,
            "timeout": timeout or self.default_timeout,
        }

    def is_streamable(self, tool_name: str) -> bool:
        """Check if a tool supports streaming."""
        return tool_name in self._handlers

    async def execute_streaming(
        self,
        tool_name: str,
        args: Dict[str, Any],
        **kwargs,
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Execute a streamable tool and yield progress events.

        Usage:
            async for event in streamer.execute_streaming("cv_scan", {"repo_url": "..."}):
                if event.type == "progress":
                    print(f"{event.progress*100:.0f}% - {event.message}")
                elif event.type == "result":
                    final = event.data
        """
        handler = self._handlers.get(tool_name)
        if not handler:
            yield StreamEvent(
                type="error", tool_name=tool_name,
                message=f"Tool '{tool_name}' is not registered as streamable",
            )
            return

        config = self._configs.get(tool_name, {})
        timeout = config.get("timeout", self.default_timeout)

        queue: asyncio.Queue = asyncio.Queue(maxsize=self.queue_size)
        start = time.monotonic()
        reporter = ProgressReporter(tool_name, queue, start)

        # Yield initial progress
        yield StreamEvent(
            type="progress", tool_name=tool_name,
            progress=0.0, message="Starting...",
            elapsed_ms=0,
        )

        # Run handler in background task
        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, str] = {}

        async def _run():
            try:
                result = await handler(args, progress=reporter, **kwargs)
                result_holder["data"] = result
            except Exception as e:
                error_holder["error"] = str(e)[:500]

        task = asyncio.create_task(_run())

        # Stream events while task is running
        try:
            while not task.done():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield event
                except asyncio.TimeoutError:
                    # No progress event in 1s — check if task done
                    elapsed = (time.monotonic() - start) * 1000
                    if elapsed > timeout * 1000:
                        task.cancel()
                        yield StreamEvent(
                            type="error", tool_name=tool_name,
                            message=f"Tool timed out after {timeout}s",
                            elapsed_ms=elapsed,
                        )
                        return

            # Drain remaining events from queue
            while not queue.empty():
                try:
                    event = queue.get_nowait()
                    yield event
                except asyncio.QueueFull:
                    break

            # Get task result
            elapsed = (time.monotonic() - start) * 1000

            if error_holder:
                yield StreamEvent(
                    type="error", tool_name=tool_name,
                    message=error_holder["error"],
                    elapsed_ms=elapsed,
                )
            elif result_holder:
                yield StreamEvent(
                    type="result", tool_name=tool_name,
                    progress=1.0,
                    data=result_holder["data"],
                    elapsed_ms=elapsed,
                )
            else:
                yield StreamEvent(
                    type="error", tool_name=tool_name,
                    message="Tool completed without result",
                    elapsed_ms=elapsed,
                )

        except asyncio.CancelledError:
            task.cancel()
            raise

    async def execute_or_block(
        self,
        tool_name: str,
        args: Dict[str, Any],
        fallback_handler: Optional[Callable] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Execute a tool — streaming if available, blocking otherwise.

        Returns the final result dict. Progress events are logged but not yielded.
        Useful when you just want the result but want the timeout/cancellation benefits.
        """
        if self.is_streamable(tool_name):
            final_result = None
            async for event in self.execute_streaming(tool_name, args, **kwargs):
                if event.type == "result":
                    final_result = event.data
                elif event.type == "error":
                    return {"error": event.message}
                elif event.type == "progress":
                    logger.debug(f"[STREAM] {tool_name}: {event.progress*100:.0f}% {event.message}")
            return final_result or {"error": "No result from streaming execution"}

        # Not streamable — use fallback
        if fallback_handler:
            return await fallback_handler(args, **kwargs)

        return {"error": f"Tool '{tool_name}' has no handler"}


# ── SSE Integration Helper ──

def stream_events_to_sse(events: AsyncGenerator[StreamEvent, None]):
    """
    Convert StreamEvent generator to SSE format strings.

    Usage in FastAPI:
        async def _stream():
            async for sse_line in stream_events_to_sse(
                streamer.execute_streaming("cv_scan", args)
            ):
                yield sse_line

        return StreamingResponse(_stream(), media_type="text/event-stream")
    """
    async def _gen():
        async for event in events:
            data = json.dumps(event.to_dict())
            yield f"event: tool_{event.type}\ndata: {data}\n\n"
    return _gen()
