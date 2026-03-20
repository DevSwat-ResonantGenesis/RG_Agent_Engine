"""
Registry Builder — creates pre-configured registries for each system.
=====================================================================

Usage:
    from rg_tool_registry.builder import (
        build_registered_registry,   # Agentic chat (~100 tools)
        build_guest_registry,        # Public chat (14 tools)
        build_agent_registry,        # Autonomous agents (~25 tools)
        build_ide_registry,          # Resonant IDE (~61 tools)
        build_full_registry,         # Everything
    )

    guest_registry = build_guest_registry()
    openai_tools = guest_registry.to_openai()
"""

from .registry import ToolRegistry, ToolAccess
from .builtin_tools import (
    SEARCH_TOOLS,
    MEMORY_TOOLS,
    UTILITY_TOOLS,
    CODE_VISUALIZER_TOOLS,
    AGENT_TOOLS,
    MEDIA_TOOLS,
    INTEGRATION_TOOLS,
    COMMUNITY_TOOLS,
    DEVELOPER_TOOLS,
    GITHUB_TOOLS,
    TOOL_MANAGEMENT_TOOLS,
    PLATFORM_API_TOOLS,
    IDE_FILESYSTEM_TOOLS,
)
from .builtin_tools_ide import ALL_IDE_TOOLS

# All built-in tools combined
ALL_BUILTIN_TOOLS = (
    SEARCH_TOOLS +
    MEMORY_TOOLS +
    UTILITY_TOOLS +
    CODE_VISUALIZER_TOOLS +
    AGENT_TOOLS +
    MEDIA_TOOLS +
    INTEGRATION_TOOLS +
    COMMUNITY_TOOLS +
    DEVELOPER_TOOLS +
    GITHUB_TOOLS +
    TOOL_MANAGEMENT_TOOLS +
    PLATFORM_API_TOOLS +
    IDE_FILESYSTEM_TOOLS +
    ALL_IDE_TOOLS
)


def build_full_registry() -> ToolRegistry:
    """Build registry with ALL tools across all systems."""
    reg = ToolRegistry()
    reg.register_bulk(ALL_BUILTIN_TOOLS)
    return reg


def build_registered_registry() -> ToolRegistry:
    """Build registry for agentic chat (registered users)."""
    reg = build_full_registry()
    return _filter_registry(reg, ToolAccess.REGISTERED)


def build_guest_registry() -> ToolRegistry:
    """Build registry for public chat (non-registered guests)."""
    reg = build_full_registry()
    return _filter_registry(reg, ToolAccess.GUEST)


def build_agent_registry() -> ToolRegistry:
    """Build registry for autonomous agent executor."""
    reg = build_full_registry()
    return _filter_registry(reg, ToolAccess.AGENT)


def build_ide_registry() -> ToolRegistry:
    """Build registry for Resonant IDE (local execution)."""
    reg = build_full_registry()
    return _filter_registry(reg, ToolAccess.IDE)


def _filter_registry(source: ToolRegistry, access: ToolAccess) -> ToolRegistry:
    """Create a new registry with only tools that match the given access level."""
    filtered = ToolRegistry()
    for tool in source.get_tools(access=access):
        filtered.register(tool)
    return filtered
