"""
OpenClaw Bridge — Routes agent executions through OpenClaw Gateway runtime.

OpenClaw provides a real autonomous agent loop (pi-agent-core):
  - LLM thinks → picks tools → executes → observes → loops
  - Real tool execution: bash, browser (CDP), file I/O
  - Streaming: tool events + assistant deltas + lifecycle events
  - Session management with compaction
  - 48-hour timeout capability

This bridge connects to the OpenClaw Gateway via WebSocket RPC and
delegates agent task execution to the OpenClaw runtime, replacing
the simple prompt→response loop in agent_executor.py.

Wire protocol:
  - First frame: connect with auth token
  - Requests:  {type:"req", id, method, params} → {type:"res", id, ok, payload|error}
  - Events:    {type:"event", event, payload}
"""

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import websockets

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────
OPENCLAW_GATEWAY_URL = os.getenv(
    "OPENCLAW_GATEWAY_URL", "ws://openclaw_gateway:18789"
)
OPENCLAW_GATEWAY_TOKEN = os.getenv(
    "OPENCLAW_GATEWAY_TOKEN", ""
)
OPENCLAW_CONNECT_TIMEOUT = int(os.getenv("OPENCLAW_CONNECT_TIMEOUT", "10"))
OPENCLAW_AGENT_TIMEOUT = int(os.getenv("OPENCLAW_AGENT_TIMEOUT", "300"))


@dataclass
class OpenClawResult:
    """Result from an OpenClaw agent execution."""
    success: bool
    output: str
    tool_events: List[Dict[str, Any]] = field(default_factory=list)
    reasoning: str = ""
    run_id: str = ""
    error: Optional[str] = None
    duration_ms: int = 0


class OpenClawBridge:
    """
    WebSocket RPC bridge to OpenClaw Gateway.

    Usage:
        bridge = OpenClawBridge()
        result = await bridge.execute_agent_task(
            message="Search the web for latest AI news and summarize",
            session_key="agent:rg-agent-123",
            system_prompt="You are a research agent...",
        )
    """

    def __init__(
        self,
        gateway_url: str = OPENCLAW_GATEWAY_URL,
        gateway_token: str = OPENCLAW_GATEWAY_TOKEN,
    ):
        self.gateway_url = gateway_url
        self.gateway_token = gateway_token
        self._request_id = 0

    def _next_id(self) -> str:
        self._request_id += 1
        return f"rg-{self._request_id}-{uuid.uuid4().hex[:8]}"

    async def _connect(self) -> websockets.WebSocketClientProtocol:
        """Establish WebSocket connection and perform handshake."""
        ws = await asyncio.wait_for(
            websockets.connect(
                self.gateway_url,
                max_size=10 * 1024 * 1024,  # 10MB max message
                ping_interval=30,
                ping_timeout=10,
            ),
            timeout=OPENCLAW_CONNECT_TIMEOUT,
        )

        # Send connect frame (required first frame)
        connect_msg = {
            "type": "req",
            "id": self._next_id(),
            "method": "connect",
            "params": {
                "auth": {"token": self.gateway_token} if self.gateway_token else {},
                "client": {
                    "name": "rg-agent-engine",
                    "version": "1.0.0",
                    "platform": "linux",
                },
                "deviceId": f"rg-engine-{uuid.uuid4().hex[:12]}",
            },
        }
        await ws.send(json.dumps(connect_msg))

        # Wait for connect response
        resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = json.loads(resp_raw)

        if resp.get("type") == "res" and resp.get("ok"):
            logger.info(
                f"[OpenClaw] Connected to gateway: {self.gateway_url}"
            )
            return ws
        else:
            error = resp.get("error", resp)
            await ws.close()
            raise ConnectionError(
                f"OpenClaw Gateway handshake failed: {error}"
            )

    async def execute_agent_task(
        self,
        message: str,
        session_key: str = "agent:main",
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = OPENCLAW_AGENT_TIMEOUT,
    ) -> OpenClawResult:
        """
        Execute an agent task through the OpenClaw runtime.

        This sends a message to the OpenClaw agent, which then:
        1. Thinks about the task
        2. Picks tools (bash, browser, file I/O, etc.)
        3. Executes tools
        4. Observes results
        5. Loops until task is complete or times out

        Args:
            message: The task/goal for the agent
            session_key: OpenClaw session identifier
            system_prompt: Optional system prompt override
            model: Optional model override (e.g. "openai/gpt-4o")
            timeout: Max seconds to wait for completion

        Returns:
            OpenClawResult with output, tool events, and status
        """
        import time
        start = time.monotonic()

        ws = None
        try:
            ws = await self._connect()

            # Build agent RPC request
            agent_params: Dict[str, Any] = {
                "message": message,
                "sessionKey": session_key,
            }
            if model:
                agent_params["model"] = model

            # Send agent request
            req_id = self._next_id()
            agent_req = {
                "type": "req",
                "id": req_id,
                "method": "agent",
                "params": agent_params,
                "idempotencyKey": f"rg-{uuid.uuid4().hex}",
            }

            logger.info(
                f"[OpenClaw] Sending agent request: {message[:100]!r} "
                f"session={session_key} model={model}"
            )
            await ws.send(json.dumps(agent_req))

            # Collect events until lifecycle end/error
            run_id = ""
            tool_events: List[Dict[str, Any]] = []
            assistant_chunks: List[str] = []
            reasoning_chunks: List[str] = []
            final_status = "unknown"
            error_msg = None

            async for raw_msg in ws:
                elapsed = time.monotonic() - start
                if elapsed > timeout:
                    error_msg = f"Timeout after {timeout}s"
                    break

                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type")

                # Handle response to our agent request
                if msg_type == "res" and msg.get("id") == req_id:
                    if msg.get("ok"):
                        payload = msg.get("payload", {})
                        run_id = payload.get("runId", "")
                        logger.info(f"[OpenClaw] Agent run accepted: {run_id}")
                    else:
                        error_msg = str(msg.get("error", "Agent request rejected"))
                        logger.error(f"[OpenClaw] Agent request failed: {error_msg}")
                        break

                # Handle streaming events
                elif msg_type == "event":
                    event_name = msg.get("event", "")
                    payload = msg.get("payload", {})

                    if event_name == "agent:tool" or "tool" in event_name:
                        tool_events.append(payload)
                        tool_name = payload.get("name", payload.get("tool", "?"))
                        phase = payload.get("phase", "")
                        logger.info(
                            f"[OpenClaw] Tool: {tool_name} phase={phase}"
                        )

                    elif event_name == "agent:assistant" or "assistant" in event_name:
                        delta = payload.get("delta", "")
                        if delta:
                            assistant_chunks.append(delta)

                    elif event_name == "agent:reasoning" or "reasoning" in event_name:
                        delta = payload.get("delta", "")
                        if delta:
                            reasoning_chunks.append(delta)

                    elif event_name == "agent:lifecycle" or "lifecycle" in event_name:
                        phase = payload.get("phase", "")
                        if phase in ("end", "error"):
                            final_status = (
                                "ok" if phase == "end" else "error"
                            )
                            if phase == "error":
                                error_msg = payload.get("error", "Agent error")
                            logger.info(
                                f"[OpenClaw] Lifecycle {phase}: "
                                f"status={final_status}"
                            )
                            break

                    elif event_name == "agent:stream:end" or "stream:end" in event_name:
                        # Some versions emit stream:end instead of lifecycle end
                        final_status = "ok"
                        break

            elapsed_ms = int((time.monotonic() - start) * 1000)
            output = "".join(assistant_chunks).strip()
            reasoning = "".join(reasoning_chunks).strip()

            if not output and error_msg:
                output = f"Error: {error_msg}"

            success = final_status == "ok" and not error_msg

            logger.info(
                f"[OpenClaw] Execution complete: success={success} "
                f"tools_used={len(tool_events)} output_len={len(output)} "
                f"duration={elapsed_ms}ms"
            )

            return OpenClawResult(
                success=success,
                output=output or "Agent completed but produced no output.",
                tool_events=tool_events,
                reasoning=reasoning,
                run_id=run_id,
                error=error_msg,
                duration_ms=elapsed_ms,
            )

        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.error(f"[OpenClaw] Connection timeout after {elapsed_ms}ms")
            return OpenClawResult(
                success=False,
                output="",
                error=f"Connection timeout ({elapsed_ms}ms)",
                duration_ms=elapsed_ms,
            )
        except ConnectionError as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.error(f"[OpenClaw] Connection error: {e}")
            return OpenClawResult(
                success=False,
                output="",
                error=str(e),
                duration_ms=elapsed_ms,
            )
        except Exception as e:
            import time as _t
            elapsed_ms = int((_t.monotonic() - start) * 1000)
            logger.exception(f"[OpenClaw] Unexpected error: {e}")
            return OpenClawResult(
                success=False,
                output="",
                error=f"Unexpected error: {e}",
                duration_ms=elapsed_ms,
            )
        finally:
            if ws and not ws.closed:
                await ws.close()

    async def health_check(self) -> Dict[str, Any]:
        """Check if OpenClaw Gateway is healthy."""
        import httpx

        health_url = self.gateway_url.replace("ws://", "http://").replace(
            "wss://", "https://"
        )
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{health_url}/healthz")
                return resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def send_to_session(
        self,
        message: str,
        session_key: str,
        deliver: bool = False,
    ) -> Dict[str, Any]:
        """
        Send a message to a specific OpenClaw session.
        Useful for agent-to-agent communication.
        """
        ws = None
        try:
            ws = await self._connect()

            req_id = self._next_id()
            send_req = {
                "type": "req",
                "id": req_id,
                "method": "send",
                "params": {
                    "sessionKey": session_key,
                    "message": message,
                    "deliver": deliver,
                },
                "idempotencyKey": f"rg-send-{uuid.uuid4().hex}",
            }
            await ws.send(json.dumps(send_req))

            resp_raw = await asyncio.wait_for(ws.recv(), timeout=30)
            resp = json.loads(resp_raw)

            if resp.get("type") == "res" and resp.get("ok"):
                return {"success": True, "payload": resp.get("payload")}
            else:
                return {"success": False, "error": resp.get("error")}

        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            if ws and not ws.closed:
                await ws.close()


# ── Singleton ───────────────────────────────────────────────────
_bridge: Optional[OpenClawBridge] = None


def get_openclaw_bridge() -> OpenClawBridge:
    """Get or create the global OpenClaw bridge instance."""
    global _bridge
    if _bridge is None:
        _bridge = OpenClawBridge()
        logger.info(
            f"[OpenClaw] Bridge initialized: {OPENCLAW_GATEWAY_URL}"
        )
    return _bridge
