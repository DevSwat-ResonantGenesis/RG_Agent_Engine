"""
Context Window Manager
======================
Manages the messages array to stay within model token limits.
Uses tiktoken for OpenAI/Groq, approximation for Anthropic.

Trim strategy (in order):
1. Truncate long tool results (>2000 chars → 2000 + summary)
2. Compress old conversation turns (keep last 6 full, summarize earlier)
3. Drop oldest non-system messages one by one

Always preserves: system message (index 0) + last user message (index -1)
"""

import json
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False
    logger.warning("tiktoken not available — using character-based token estimation")

# Token limits per model (input side, leaving room for output)
MODEL_LIMITS: Dict[str, int] = {
    "gpt-4o": 120_000,
    "gpt-4o-mini": 120_000,
    "gpt-4-turbo": 120_000,
    "o1-mini": 120_000,
    "claude-3-5-sonnet-20241022": 190_000,
    "claude-sonnet-4-20250514": 190_000,
    "claude-3-haiku-20240307": 190_000,
    "claude-3-opus-20240229": 190_000,
    "llama-3.3-70b-versatile": 120_000,
    "llama-3.1-8b-instant": 120_000,
    "mixtral-8x7b-32768": 30_000,
}

OUTPUT_RESERVE = 4096  # Tokens reserved for model response
DEFAULT_LIMIT = 120_000  # For unknown models


class ContextWindowManager:
    """Token-aware context window manager with graceful degradation."""

    def __init__(self, model: str):
        self.model = model
        self.max_tokens = MODEL_LIMITS.get(model, DEFAULT_LIMIT) - OUTPUT_RESERVE
        self._encoder = None

        if _TIKTOKEN_AVAILABLE:
            try:
                self._encoder = tiktoken.encoding_for_model(model)
            except KeyError:
                try:
                    self._encoder = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    pass

    def count_tokens(self, text: str) -> int:
        """Count tokens in a string. Falls back to char/4 estimate."""
        if not text:
            return 0
        if self._encoder:
            try:
                return len(self._encoder.encode(text))
            except Exception:
                pass
        # Fallback: ~4 chars per token (rough but safe)
        return len(text) // 4

    def count_message_tokens(self, msg: Dict) -> int:
        """Count tokens in a single message including role overhead."""
        content = msg.get("content", "")
        if isinstance(content, list):
            # Anthropic-style content blocks
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text", "") or json.dumps(block.get("input", {})))
                else:
                    parts.append(str(block))
            content = " ".join(parts)
        elif content is None:
            content = ""
        return self.count_tokens(str(content)) + 4  # +4 for role/formatting overhead

    def total_tokens(self, messages: List[Dict]) -> int:
        """Total tokens across all messages."""
        return sum(self.count_message_tokens(m) for m in messages)

    def fits(self, messages: List[Dict], tools_tokens: int = 0) -> bool:
        """Check if messages + tools fit in the context window."""
        return self.total_tokens(messages) + tools_tokens < self.max_tokens

    def trim_to_fit(self, messages: List[Dict], tools_tokens: int = 0) -> List[Dict]:
        """Trim messages to fit in context window.

        Preserves: system message (index 0) + last user message (index -1).
        Returns a new list (does not mutate input).
        """
        if self.fits(messages, tools_tokens):
            return list(messages)

        budget = self.max_tokens - tools_tokens
        result = [dict(m) for m in messages]  # Shallow copy

        # Phase 1: Truncate long tool results and tool content
        for i, msg in enumerate(result):
            content = msg.get("content", "")
            role = msg.get("role", "")
            if isinstance(content, str) and len(content) > 2000:
                if role in ("tool", "function") or "Tool result" in content[:50]:
                    result[i] = {**msg, "content": content[:2000] + "\n...(truncated)"}

        if self.total_tokens(result) + tools_tokens <= budget:
            return result

        # Phase 2: Compress old turns, keep last N recent messages intact
        system = result[0] if result and result[0].get("role") == "system" else None
        rest = result[1:] if system else result

        keep_recent = 6
        if len(rest) > keep_recent:
            old = rest[:-keep_recent]
            recent = rest[-keep_recent:]

            # Build compressed summary of old messages
            summary_parts = []
            for msg in old:
                role = msg.get("role", "unknown")
                content = str(msg.get("content", ""))
                if isinstance(content, list):
                    content = str(content)
                snippet = content[:200].replace("\n", " ")
                if role == "user":
                    summary_parts.append(f"User: {snippet}")
                elif role == "assistant":
                    summary_parts.append(f"Assistant: {snippet}")
                elif role in ("tool", "function"):
                    summary_parts.append(f"Tool: {snippet[:100]}")

            compressed = {
                "role": "user",
                "content": (
                    f"[COMPRESSED HISTORY — {len(old)} earlier messages summarized]\n"
                    + "\n".join(summary_parts[-10:])  # Keep last 10 summary lines
                ),
            }

            candidate = ([system] if system else []) + [compressed] + recent
            if self.total_tokens(candidate) + tools_tokens <= budget:
                return candidate

        # Phase 3: Drop oldest non-system messages until it fits
        result = list(messages)
        while len(result) > 2 and self.total_tokens(result) + tools_tokens > budget:
            # Remove the second message (preserve system at 0 and user at -1)
            result.pop(1)

        return result

    def estimate_tools_tokens(self, tools: List[Dict]) -> int:
        """Estimate token cost of tool definitions."""
        if not tools:
            return 0
        return self.count_tokens(json.dumps(tools))
