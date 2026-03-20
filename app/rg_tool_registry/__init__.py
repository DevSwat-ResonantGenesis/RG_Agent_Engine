"""
RG Unified Tool Registry & Observability Module
================================================

Single canonical tool definition format for ALL Resonant Genesis systems:
- Agentic Chat (registered users)
- Public Chat (guests)
- Agent Executor (autonomous agents)
- Resonant IDE (local Electron)

Replaces 4 incompatible formats:
  TOOL_DEFS dict, GUEST_TOOLS dict, executor.tool_handlers dict, ToolDef[] TypeScript

Provides:
  - registry.py     → ToolDef dataclass + ToolRegistry + format converters
  - observability.py → Tool-level timing, success/fail, latency, token cost logging
  - native_fc.py     → Native function calling for public chat & agent executor
  - streaming.py     → Tool result streaming for long-running tools
"""

from .registry import ToolDef, ToolParam, ToolRegistry, ToolCategory
from .observability import ToolObserver, ToolCallRecord

__version__ = "1.0.0"

__all__ = [
    "ToolDef",
    "ToolParam",
    "ToolRegistry",
    "ToolCategory",
    "ToolObserver",
    "ToolCallRecord",
]
