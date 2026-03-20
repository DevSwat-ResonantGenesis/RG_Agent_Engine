"""
Unified Tool Registry
=====================

ONE canonical ToolDef format that replaces:
  - TOOL_DEFS dict       (routers_agentic_chat.py)
  - GUEST_TOOLS dict     (routers_public_chat.py)
  - executor.tool_handlers dict (executor.py)
  - ToolDef[] TypeScript  (toolDefinitions.ts)

Conversion helpers output:
  - OpenAI/Groq native function calling format
  - Anthropic native tool format
  - JSON-mode text prompt (legacy fallback)
  - TypeScript ToolDef[] (for IDE codegen)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set


# ── Enums ──

class ToolCategory(str, Enum):
    SEARCH = "search"
    MEMORY = "memory"
    CODE_ANALYSIS = "code_analysis"
    AGENTS = "agents"
    COMMUNITY = "community"
    MEDIA = "media"
    INTEGRATIONS = "integrations"
    FILESYSTEM = "filesystem"
    GITHUB = "github"
    GIT = "git"
    UTILITIES = "utilities"
    SYSTEM = "system"
    DEVELOPER = "developer"
    PLATFORM_API = "platform_api"
    STATE_PHYSICS = "state_physics"
    NOTEBOOKS = "notebooks"
    DEPLOY = "deploy"
    MCP = "mcp"
    WORKFLOWS = "workflows"
    TERMINAL = "terminal"
    VISUAL = "visual"
    CHECKPOINTS = "checkpoints"
    PLANNING = "planning"
    CUSTOM = "custom"


class ParamType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class ToolAccess(str, Enum):
    """Who can use this tool."""
    REGISTERED = "registered"    # Agentic chat (logged-in users)
    GUEST = "guest"              # Public chat (no auth)
    AGENT = "agent"              # Autonomous agent executor
    IDE = "ide"                  # Resonant IDE (local execution)
    ALL = "all"                  # Available everywhere


# ── Data Classes ──

@dataclass
class ToolParam:
    """Single parameter for a tool."""
    name: str
    type: ParamType = ParamType.STRING
    description: str = ""
    required: bool = False
    default: Any = None
    enum: Optional[List[str]] = None
    items_type: Optional[str] = None  # For array type: element type

    def to_openai_property(self) -> Dict[str, Any]:
        """Convert to OpenAI JSON Schema property."""
        prop: Dict[str, Any] = {
            "type": self.type.value,
            "description": self.description,
        }
        if self.enum:
            prop["enum"] = self.enum
        if self.type == ParamType.ARRAY and self.items_type:
            prop["items"] = {"type": self.items_type}
        if self.default is not None:
            prop["default"] = self.default
        return prop


@dataclass
class ToolDef:
    """
    Canonical tool definition — the ONE format all systems use.

    This replaces:
      - TOOL_DEFS[name] = {"desc": ..., "params": {...}, "handler": ..., "category": ...}
      - GUEST_TOOLS[name] = {"desc": ..., "params": {...}, "category": ...}
      - executor.register_tool_handler(name, handler)
      - { type: 'function', function: { name, description, parameters } }  (TypeScript)
    """
    name: str
    description: str
    category: ToolCategory = ToolCategory.UTILITIES
    params: List[ToolParam] = field(default_factory=list)
    handler: Optional[str] = None  # Handler function name or reference key
    handler_fn: Optional[Callable[..., Coroutine]] = None  # Direct async handler
    access: Set[ToolAccess] = field(default_factory=lambda: {ToolAccess.REGISTERED})
    requires_api_key: Optional[str] = None  # BYOK key name if needed
    priority: int = 50  # 0=highest, 100=lowest — used for Groq tool limit
    max_result_chars: int = 8000  # Per-tool result truncation cap
    streamable: bool = False  # Whether this tool supports streaming partial results

    def to_openai(self) -> Dict[str, Any]:
        """Convert to OpenAI/Groq native function calling format."""
        properties = {}
        required = []
        for p in self.params:
            properties[p.name] = p.to_openai_property()
            if p.required:
                required.append(p.name)

        func_def: Dict[str, Any] = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description[:200],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                },
            },
        }
        if required:
            func_def["function"]["parameters"]["required"] = required
        return func_def

    def to_anthropic(self) -> Dict[str, Any]:
        """Convert to Anthropic native tool format."""
        properties = {}
        required = []
        for p in self.params:
            properties[p.name] = p.to_openai_property()
            if p.required:
                required.append(p.name)

        tool: Dict[str, Any] = {
            "name": self.name,
            "description": self.description[:200],
            "input_schema": {
                "type": "object",
                "properties": properties,
            },
        }
        if required:
            tool["input_schema"]["required"] = required
        return tool

    def to_prompt_text(self) -> str:
        """Convert to inline text for JSON-mode prompt injection (legacy fallback)."""
        params_parts = []
        for p in self.params:
            req = " (required)" if p.required else ""
            params_parts.append(f"{p.name}: {p.type.value}{req} — {p.description}")
        params_str = ", ".join(params_parts) if params_parts else "none"
        return f"  - {self.name}({params_str}): {self.description}"

    def to_typescript(self) -> str:
        """Generate TypeScript ToolDef object literal."""
        props = {}
        required = []
        for p in self.params:
            prop: Dict[str, Any] = {"type": f"'{p.type.value}'"}
            if p.description:
                prop["description"] = f"'{p.description}'"
            props[p.name] = prop
            if p.required:
                required.append(p.name)

        props_str = ", ".join(
            f"{k}: {{ {', '.join(f'{pk}: {pv}' for pk, pv in v.items())} }}"
            for k, v in props.items()
        )
        req_str = str(required) if required else "[]"
        return (
            f"{{ type: F, function: {{ name: '{self.name}', "
            f"description: '{self.description}', "
            f"parameters: {{ type: 'object', properties: {{ {props_str} }}, "
            f"required: {req_str} }} }} }}"
        )

    def to_legacy_tool_defs_entry(self) -> Dict[str, Any]:
        """Convert back to the old TOOL_DEFS dict format (for gradual migration)."""
        params = {}
        for p in self.params:
            req = "(required) " if p.required else ""
            params[p.name] = f"{p.type.value} {req}— {p.description}"
        entry: Dict[str, Any] = {
            "desc": self.description,
            "params": params,
            "category": self.category.value,
        }
        if self.handler:
            entry["handler"] = self.handler
        return entry


# ── Registry ──

class ToolRegistry:
    """
    Central registry holding all tool definitions.

    Usage:
        registry = ToolRegistry()
        registry.register(ToolDef(name="web_search", ...))
        registry.register_bulk(SEARCH_TOOLS)

        # Get tools for a specific system
        guest_tools = registry.get_tools(access=ToolAccess.GUEST)
        agent_tools = registry.get_tools(access=ToolAccess.AGENT)

        # Convert to provider format
        openai_tools = registry.to_openai(tools=guest_tools)
        anthropic_tools = registry.to_anthropic(tools=guest_tools)

        # Get by category
        search_tools = registry.get_by_category(ToolCategory.SEARCH)
    """

    def __init__(self):
        self._tools: Dict[str, ToolDef] = {}
        self._handlers: Dict[str, Callable] = {}

    def register(self, tool: ToolDef) -> None:
        """Register a single tool definition."""
        self._tools[tool.name] = tool
        if tool.handler_fn:
            self._handlers[tool.name] = tool.handler_fn

    def register_bulk(self, tools: List[ToolDef]) -> None:
        """Register multiple tool definitions."""
        for tool in tools:
            self.register(tool)

    def register_handler(self, tool_name: str, handler: Callable) -> None:
        """Register a handler function for a tool (can be done after tool registration)."""
        self._handlers[tool_name] = handler
        if tool_name in self._tools:
            self._tools[tool_name].handler_fn = handler

    def get(self, name: str) -> Optional[ToolDef]:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_handler(self, name: str) -> Optional[Callable]:
        """Get handler function for a tool."""
        return self._handlers.get(name)

    def get_all(self) -> List[ToolDef]:
        """Get all registered tools."""
        return list(self._tools.values())

    def get_tools(
        self,
        access: Optional[ToolAccess] = None,
        categories: Optional[List[ToolCategory]] = None,
        names: Optional[List[str]] = None,
    ) -> List[ToolDef]:
        """Get tools filtered by access level, categories, or explicit names."""
        tools = list(self._tools.values())

        if access:
            tools = [
                t for t in tools
                if access in t.access or ToolAccess.ALL in t.access
            ]

        if categories:
            cat_set = set(categories)
            tools = [t for t in tools if t.category in cat_set]

        if names:
            name_set = set(names)
            tools = [t for t in tools if t.name in name_set]

        return tools

    def get_by_category(self, category: ToolCategory) -> List[ToolDef]:
        """Get all tools in a category."""
        return [t for t in self._tools.values() if t.category == category]

    def get_names(self, access: Optional[ToolAccess] = None) -> List[str]:
        """Get tool names, optionally filtered by access."""
        tools = self.get_tools(access=access) if access else self.get_all()
        return [t.name for t in tools]

    # ── Bulk Converters ──

    def to_openai(
        self,
        tools: Optional[List[ToolDef]] = None,
        max_tools: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Convert tools to OpenAI/Groq native function calling format."""
        items = tools or self.get_all()
        if max_tools and len(items) > max_tools:
            items = sorted(items, key=lambda t: t.priority)[:max_tools]
        return [t.to_openai() for t in items]

    def to_anthropic(
        self,
        tools: Optional[List[ToolDef]] = None,
    ) -> List[Dict[str, Any]]:
        """Convert tools to Anthropic native tool format."""
        items = tools or self.get_all()
        return [t.to_anthropic() for t in items]

    def to_prompt_text(
        self,
        tools: Optional[List[ToolDef]] = None,
    ) -> str:
        """Convert tools to inline prompt text (legacy JSON-mode fallback)."""
        items = tools or self.get_all()
        lines = []
        current_cat = ""
        for t in sorted(items, key=lambda x: x.category.value):
            if t.category.value != current_cat:
                current_cat = t.category.value
                lines.append(f"\n  — {current_cat} —")
            lines.append(t.to_prompt_text())
        return "\n".join(lines)

    def to_typescript(
        self,
        tools: Optional[List[ToolDef]] = None,
    ) -> str:
        """Generate TypeScript array source code."""
        items = tools or self.get_all()
        entries = [t.to_typescript() for t in items]
        return "[\n  " + ",\n  ".join(entries) + "\n]"

    # ── Import from Legacy Formats ──

    @classmethod
    def from_tool_defs_dict(
        cls,
        tool_defs: Dict[str, Dict[str, Any]],
        access: Set[ToolAccess] = None,
    ) -> "ToolRegistry":
        """
        Import from the old TOOL_DEFS dict format used in routers_agentic_chat.py.

        TOOL_DEFS = {
            "web_search": {
                "desc": "Search the web...",
                "params": {"query": "string (required) — search query", ...},
                "handler": "_custom_web_search",
                "category": "search",
            }, ...
        }
        """
        registry = cls()
        default_access = access or {ToolAccess.REGISTERED}

        for name, tdef in tool_defs.items():
            params = []
            for pname, pdesc in tdef.get("params", {}).items():
                ptype = _parse_param_type_from_desc(pdesc)
                required = "(required)" in pdesc.lower() or "required" in pdesc.lower().split("—")[0]
                params.append(ToolParam(
                    name=pname,
                    type=ptype,
                    description=pdesc,
                    required=required,
                ))

            cat_str = tdef.get("category", "utilities")
            try:
                category = ToolCategory(cat_str)
            except ValueError:
                category = ToolCategory.UTILITIES

            registry.register(ToolDef(
                name=name,
                description=tdef.get("desc", ""),
                category=category,
                params=params,
                handler=tdef.get("handler"),
                access=set(default_access),
            ))

        return registry

    @classmethod
    def from_guest_tools_dict(
        cls,
        guest_tools: Dict[str, Dict[str, Any]],
    ) -> "ToolRegistry":
        """Import from GUEST_TOOLS dict format (routers_public_chat.py)."""
        return cls.from_tool_defs_dict(guest_tools, access={ToolAccess.GUEST})

    def merge(self, other: "ToolRegistry") -> None:
        """Merge another registry into this one (other wins on conflicts)."""
        for name, tool in other._tools.items():
            self._tools[name] = tool
        for name, handler in other._handlers.items():
            self._handlers[name] = handler

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __repr__(self) -> str:
        cats = {}
        for t in self._tools.values():
            cats[t.category.value] = cats.get(t.category.value, 0) + 1
        cat_str = ", ".join(f"{k}={v}" for k, v in sorted(cats.items()))
        return f"<ToolRegistry tools={len(self._tools)} [{cat_str}]>"


# ── Helpers ──

def _parse_param_type_from_desc(desc: str) -> ParamType:
    """Parse parameter type from description string like 'string (required) — ...'."""
    d = desc.lower().strip()
    if d.startswith("integer"):
        return ParamType.INTEGER
    if d.startswith("number") or d.startswith("float"):
        return ParamType.NUMBER
    if d.startswith("boolean") or d.startswith("bool"):
        return ParamType.BOOLEAN
    if d.startswith("array") or d.startswith("list"):
        return ParamType.ARRAY
    if d.startswith("object") or d.startswith("dict") or d.startswith("{"):
        return ParamType.OBJECT
    return ParamType.STRING
