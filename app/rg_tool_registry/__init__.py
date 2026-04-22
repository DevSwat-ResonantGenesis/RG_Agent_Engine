"""
RG Tool Registry — Agent Engine (Lite)
=======================================

Retained modules (still used by executor.py and routers.py):
  - registry.py     → ToolDef dataclass + ToolRegistry
  - observability.py → Tool-level timing/logging
  - builtin_tools.py → build_registry() for tool definitions
  - api_catalog.py   → Platform API service catalog
"""

from .registry import ToolDef, ToolParam, ToolRegistry, ToolCategory
from .observability import ToolObserver, ToolCallRecord
from .builtin_tools import build_registry

__all__ = [
    "ToolDef",
    "ToolParam",
    "ToolRegistry",
    "ToolCategory",
    "ToolObserver",
    "ToolCallRecord",
    "build_registry",
]
