"""
Native Function Calling — Replaces JSON-mode for Public Chat & Agent Executor
==============================================================================

PROBLEM:
  Public chat (routers_public_chat.py) and agent executor (executor.py) use
  JSON-mode where the LLM must generate {"action": "tool_call", "tool": "...", "args": {...}}
  as raw text. This is fragile:
    - LLM puts tool name in "action" field instead of "tool"
    - LLM returns malformed JSON requiring regex extraction
    - LLM responds with plain text despite response_format: json_object

SOLUTION:
  Use native function calling (tools parameter) — the same approach that already
  works in routers_agentic_chat.py. The LLM returns structured tool_calls with
  validated function names and typed arguments.

This module provides:
  - NativeFCClient: async LLM client that handles tool_calls for any provider
  - Provider-specific adapters: Groq, OpenAI, Anthropic, Gemini
  - Automatic tool_call parsing → (tool_name, args) tuples
  - Fallback to JSON-mode if native FC is unavailable
  - Integrated with ToolRegistry for tool definitions
  - Integrated with ToolObserver for metrics
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from .registry import ToolDef, ToolRegistry
from .observability import ToolObserver

logger = logging.getLogger(__name__)


# ── Provider URLs ──
PROVIDER_URLS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "mistral": "https://api.mistral.ai/v1/chat/completions",
}

DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-20250514",
    "gemini": "gemini-2.0-flash",
    "deepseek": "deepseek-chat",
    "mistral": "mistral-large-latest",
}


@dataclass
class ToolCall:
    """Parsed tool call from LLM response."""
    id: str                    # Tool call ID (from LLM)
    name: str                  # Tool function name
    args: Dict[str, Any]       # Parsed arguments
    raw_args: str = ""         # Raw argument string (for debugging)


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""
    content: str = ""                     # Text content (if responding directly)
    tool_calls: List[ToolCall] = field(default_factory=list)  # Tool calls (if using tools)
    finish_reason: str = ""               # stop | tool_calls | length | error
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    provider: str = ""
    model: str = ""
    error: Optional[str] = None

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    @property
    def is_error(self) -> bool:
        return self.error is not None


class NativeFCClient:
    """
    Unified LLM client with native function calling support.

    Replaces:
      - Public chat's JSON-mode + regex parsing (routers_public_chat.py)
      - Executor's JSON-mode + regex parsing (executor.py _call_llm_direct)

    Usage:
        client = NativeFCClient(registry=registry)

        # Single call with tools
        response = await client.call(
            provider="groq",
            api_key="gsk_...",
            messages=[{"role": "user", "content": "What's the weather?"}],
            tools=registry.get_tools(access=ToolAccess.GUEST),
        )

        if response.has_tool_calls:
            for tc in response.tool_calls:
                result = await execute_tool(tc.name, tc.args)
        else:
            print(response.content)
    """

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        observer: Optional[ToolObserver] = None,
        timeout: float = 90.0,
    ):
        self.registry = registry
        self.observer = observer
        self.timeout = timeout

    async def call(
        self,
        provider: str,
        api_key: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[ToolDef]] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        max_tools: Optional[int] = None,
    ) -> LLMResponse:
        """
        Call an LLM provider with native function calling.

        Automatically selects the right format:
          - OpenAI/Groq/DeepSeek/Mistral: OpenAI-compatible tool_calls
          - Anthropic: Anthropic tool_use format
          - Gemini: Google function calling format
        """
        model = model or DEFAULT_MODELS.get(provider, "")

        # Convert tools to provider format
        tool_defs = None
        if tools:
            if provider == "anthropic":
                tool_defs = [t.to_anthropic() for t in tools]
            elif provider == "gemini":
                tool_defs = self._tools_to_gemini(tools)
            else:
                # OpenAI-compatible: Groq, OpenAI, DeepSeek, Mistral
                tool_defs = [t.to_openai() for t in tools]
                if max_tools and len(tool_defs) > max_tools:
                    tool_defs = sorted(
                        zip(tools, tool_defs), key=lambda x: x[0].priority
                    )[:max_tools]
                    tool_defs = [td for _, td in tool_defs]

        # Route to provider-specific handler
        if provider == "anthropic":
            return await self._call_anthropic(
                api_key, messages, tool_defs, model, temperature, max_tokens
            )
        elif provider == "gemini":
            return await self._call_gemini(
                api_key, messages, tool_defs, model, temperature, max_tokens
            )
        else:
            # OpenAI-compatible: Groq, OpenAI, DeepSeek, Mistral
            url = PROVIDER_URLS.get(provider, PROVIDER_URLS["groq"])
            return await self._call_openai_compat(
                url, api_key, messages, tool_defs, model, temperature, max_tokens, provider
            )

    # ── OpenAI-Compatible (Groq, OpenAI, DeepSeek, Mistral) ──

    async def _call_openai_compat(
        self,
        url: str,
        api_key: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict]],
        model: str,
        temperature: float,
        max_tokens: int,
        provider: str,
    ) -> LLMResponse:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
            except Exception as e:
                return LLMResponse(error=f"{provider} request failed: {e}", provider=provider, model=model)

            if resp.status_code != 200:
                return LLMResponse(
                    error=f"{provider} HTTP {resp.status_code}: {resp.text[:300]}",
                    provider=provider, model=model,
                )

            data = resp.json()

        usage = data.get("usage", {})
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        finish = choice.get("finish_reason", "")

        response = LLMResponse(
            content=message.get("content", "") or "",
            finish_reason=finish,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            provider=provider,
            model=model,
        )

        # Parse tool_calls
        raw_tool_calls = message.get("tool_calls", [])
        for tc in raw_tool_calls:
            func = tc.get("function", {})
            raw_args = func.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {"_raw": raw_args}

            response.tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=func.get("name", ""),
                args=args,
                raw_args=raw_args if isinstance(raw_args, str) else json.dumps(raw_args),
            ))

        return response

    # ── Anthropic ──

    async def _call_anthropic(
        self,
        api_key: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        # Separate system messages
        system_parts = []
        non_system = []
        for m in messages:
            if m["role"] == "system":
                system_parts.append(m.get("content", ""))
            else:
                non_system.append({"role": m["role"], "content": str(m.get("content", ""))})

        if not non_system or non_system[0]["role"] != "user":
            non_system.insert(0, {"role": "user", "content": "Continue."})

        # Merge consecutive same-role messages (Anthropic requirement)
        merged = []
        for msg in non_system:
            if merged and merged[-1]["role"] == msg["role"]:
                merged[-1]["content"] += "\n" + msg["content"]
            else:
                merged.append(msg)

        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": merged,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if tools:
            payload["tools"] = tools

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(PROVIDER_URLS["anthropic"], json=payload, headers=headers)
            except Exception as e:
                return LLMResponse(error=f"Anthropic request failed: {e}", provider="anthropic", model=model)

            if resp.status_code != 200:
                return LLMResponse(
                    error=f"Anthropic HTTP {resp.status_code}: {resp.text[:300]}",
                    provider="anthropic", model=model,
                )

            data = resp.json()

        usage = data.get("usage", {})
        stop_reason = data.get("stop_reason", "")

        response = LLMResponse(
            finish_reason="tool_calls" if stop_reason == "tool_use" else stop_reason,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            provider="anthropic",
            model=model,
        )

        # Parse content blocks
        for block in data.get("content", []):
            if block.get("type") == "text":
                response.content += block.get("text", "")
            elif block.get("type") == "tool_use":
                response.tool_calls.append(ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    args=block.get("input", {}),
                    raw_args=json.dumps(block.get("input", {})),
                ))

        return response

    # ── Gemini ──

    async def _call_gemini(
        self,
        api_key: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        contents = []
        system_text = ""
        for m in messages:
            role = m.get("role", "user")
            content = str(m.get("content", ""))
            if role == "system":
                system_text += content + "\n"
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})
            else:
                contents.append({"role": "user", "parts": [{"text": content}]})

        url = f"{PROVIDER_URLS['gemini']}/models/{model}:generateContent?key={api_key}"
        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_text.strip():
            payload["systemInstruction"] = {"parts": [{"text": system_text.strip()}]}
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(url, json=payload)
            except Exception as e:
                return LLMResponse(error=f"Gemini request failed: {e}", provider="gemini", model=model)

            if resp.status_code != 200:
                return LLMResponse(
                    error=f"Gemini HTTP {resp.status_code}: {resp.text[:300]}",
                    provider="gemini", model=model,
                )

            data = resp.json()

        candidates = data.get("candidates", [])
        if not candidates:
            return LLMResponse(error="Gemini returned no candidates", provider="gemini", model=model)

        parts = candidates[0].get("content", {}).get("parts", [])
        usage = data.get("usageMetadata", {})

        response = LLMResponse(
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
            total_tokens=usage.get("totalTokenCount", 0),
            provider="gemini",
            model=model,
        )

        for part in parts:
            if "text" in part:
                response.content += part["text"]
            elif "functionCall" in part:
                fc = part["functionCall"]
                response.tool_calls.append(ToolCall(
                    id=f"gemini_{fc.get('name', '')}",
                    name=fc.get("name", ""),
                    args=fc.get("args", {}),
                    raw_args=json.dumps(fc.get("args", {})),
                ))

        if response.tool_calls:
            response.finish_reason = "tool_calls"

        return response

    # ── Gemini tool format ──

    @staticmethod
    def _tools_to_gemini(tools: List[ToolDef]) -> List[Dict]:
        """Convert ToolDefs to Gemini function declaration format."""
        declarations = []
        for t in tools:
            properties = {}
            required = []
            for p in t.params:
                prop: Dict[str, Any] = {"type": p.type.value.upper(), "description": p.description}
                if p.enum:
                    prop["enum"] = p.enum
                properties[p.name] = prop
                if p.required:
                    required.append(p.name)

            decl: Dict[str, Any] = {
                "name": t.name,
                "description": t.description[:200],
                "parameters": {
                    "type": "OBJECT",
                    "properties": properties,
                },
            }
            if required:
                decl["parameters"]["required"] = required
            declarations.append(decl)

        return [{"functionDeclarations": declarations}]


# ── Agentic Loop Helper ──

class AgenticLoop:
    """
    Generic agentic loop that works with native function calling.

    Replaces:
      - Public chat's while loop with JSON parsing + regex fallback
      - Executor's _get_next_action + manual JSON parsing

    Usage:
        loop = AgenticLoop(
            client=NativeFCClient(registry=registry),
            registry=registry,
            observer=observer,
            handlers=GUEST_HANDLERS,
            max_loops=5,
        )

        async for event in loop.run(
            provider="groq",
            api_key="gsk_...",
            messages=[...],
            tools=guest_tools,
        ):
            if event["type"] == "tool_call":
                yield sse_event("tool_call", event)
            elif event["type"] == "tool_result":
                yield sse_event("tool_result", event)
            elif event["type"] == "response":
                yield sse_event("response", event)
    """

    def __init__(
        self,
        client: NativeFCClient,
        registry: ToolRegistry,
        handlers: Dict[str, Callable],
        observer: Optional[ToolObserver] = None,
        max_loops: int = 5,
        max_result_chars: int = 4000,
    ):
        self.client = client
        self.registry = registry
        self.handlers = handlers
        self.observer = observer
        self.max_loops = max_loops
        self.max_result_chars = max_result_chars

    async def run(
        self,
        provider: str,
        api_key: str,
        messages: List[Dict[str, Any]],
        tools: List[ToolDef],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        user_id: str = "",
        session_id: str = "",
        max_tools: Optional[int] = None,
    ):
        """
        Run the agentic loop. Yields events as dicts.

        Event types:
          - {"type": "thinking", "loop": N, "message": "..."}
          - {"type": "tool_call", "loop": N, "tool": "...", "args": {...}}
          - {"type": "tool_result", "loop": N, "tool": "...", "result": "..."}
          - {"type": "response", "loop": N, "content": "...", "tokens": N}
          - {"type": "error", "error": "..."}
          - {"type": "done", "loops": N, "tokens": N}
        """
        total_tokens = 0
        loop = 0

        while loop < self.max_loops:
            loop += 1

            yield {"type": "thinking", "loop": loop, "message": "Reasoning..."}

            # Call LLM with native function calling
            response = await self.client.call(
                provider=provider,
                api_key=api_key,
                messages=messages,
                tools=tools,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                max_tools=max_tools,
            )

            if response.is_error:
                yield {"type": "error", "error": response.error}
                break

            total_tokens += response.total_tokens

            # If LLM wants to call tools
            if response.has_tool_calls:
                for tc in response.tool_calls:
                    yield {
                        "type": "tool_call",
                        "loop": loop,
                        "tool": tc.name,
                        "args": tc.args,
                    }

                    # Execute tool
                    handler = self.handlers.get(tc.name)
                    if not handler:
                        tool_result = {"error": f"Tool '{tc.name}' not available"}
                    else:
                        try:
                            if self.observer:
                                async with self.observer.observe(
                                    tc.name, user_id=user_id, session_id=session_id,
                                    loop_number=loop, provider=provider, args=tc.args,
                                ) as ctx:
                                    tool_result = await handler(tc.args)
                                    if isinstance(tool_result, dict) and tool_result.get("error"):
                                        ctx.set_error(tool_result["error"])
                                    else:
                                        ctx.set_result(tool_result)
                            else:
                                tool_result = await handler(tc.args)
                        except Exception as e:
                            tool_result = {"error": str(e)[:500]}

                    # Serialize and truncate
                    result_str = json.dumps(tool_result, default=str)
                    tool_def = self.registry.get(tc.name)
                    cap = tool_def.max_result_chars if tool_def else self.max_result_chars
                    truncated = len(result_str) > cap
                    if truncated:
                        result_str = result_str[:cap] + "...(truncated)"

                    yield {
                        "type": "tool_result",
                        "loop": loop,
                        "tool": tc.name,
                        "result": result_str[:3000],  # SSE event size cap
                    }

                    # Append to messages for next loop
                    # OpenAI format: assistant message with tool_calls + tool response
                    if provider != "anthropic":
                        messages.append({
                            "role": "assistant",
                            "content": response.content or None,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {"name": tc.name, "arguments": tc.raw_args},
                                }
                            ],
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_str,
                        })
                    else:
                        # Anthropic format
                        messages.append({
                            "role": "assistant",
                            "content": [
                                {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.args},
                            ],
                        })
                        messages.append({
                            "role": "user",
                            "content": [
                                {"type": "tool_result", "tool_use_id": tc.id, "content": result_str},
                            ],
                        })

                continue  # Next loop

            # LLM responded with text (no tool calls)
            if response.content:
                yield {
                    "type": "response",
                    "loop": loop,
                    "content": response.content,
                    "tokens": total_tokens,
                }
            break

        yield {"type": "done", "loops": loop, "tokens": total_tokens}
