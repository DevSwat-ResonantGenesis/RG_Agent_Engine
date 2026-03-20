"""Lightweight in-memory tool specification — replaces DB ToolDefinition for runtime use."""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ToolSpec:
    """Lightweight in-memory tool spec — replaces DB ToolDefinition for runtime use.

    Used by executor, planner, and other modules that need tool metadata
    without hitting the database. Built from the unified registry
    (rg_tool_registry/builtin_tools.py) at runtime.
    """
    name: str
    description: str = ""
    parameters_schema: Optional[Dict[str, Any]] = None
    handler_type: str = "internal"  # internal | http | webhook
    handler_config: Optional[Dict[str, Any]] = None
    category: str = "general"
    risk_level: str = "low"
    requires_approval: bool = False
    is_active: bool = True
    id: Optional[str] = None
