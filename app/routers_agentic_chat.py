"""
AGENTIC CHAT — Cascade-style tool-calling loop with SSE streaming.

Uses prompt-based tool calling (JSON output) for model compatibility.
Wires ALL ResonantGenesis platform tools and skills:
  - Executor handlers (web_search, memory, rabbit, gmail, slack, figma, etc.)
  - Code Visualizer / AST Analysis (direct HTTP to rg_ast_analysis)
  - Agents OS (direct HTTP to agent_engine_service itself)
  - Media generation (image, audio, music, video)
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from .rg_tool_registry import ToolRegistry, ToolObserver, ToolCallRecord
from .rg_tool_registry.registry import ToolAccess, ToolCategory
from .rg_tool_registry.builtin_tools import build_registry, ALL_TOOLS
from .rg_tool_registry.observability import ToolObserver as _ToolObserver

# ── Unified Tool Registry (single source of truth for ALL tools) ──
_registry = build_registry()
_agentic_observer = _ToolObserver(system="agentic_chat")

router = APIRouter(prefix="/agentic-chat", tags=["agentic-chat"])

# ── Provider config sourced from rg_llm (single source of truth) ──
from rg_llm import UnifiedLLMClient, LLMRequest
from rg_llm.providers import BUILTIN_PROVIDERS
from rg_llm.keys import resolve_api_key

_llm_client = UnifiedLLMClient(
    fallback_order=["openai", "anthropic", "google", "groq"],
)

# Local constants derived from rg_llm (used by _call_llm_with_tools)
def _get_key(pid: str) -> str:
    return resolve_api_key(BUILTIN_PROVIDERS[pid]) or ""

GROQ_API_KEY = _get_key("groq")
GROQ_API_URL = BUILTIN_PROVIDERS["groq"].base_url + "/chat/completions"
OPENAI_API_KEY = _get_key("openai")
OPENAI_API_URL = BUILTIN_PROVIDERS["openai"].base_url + "/chat/completions"
ANTHROPIC_API_KEY = _get_key("anthropic")
ANTHROPIC_API_URL = BUILTIN_PROVIDERS["anthropic"].base_url + "/messages"
GEMINI_API_KEY = _get_key("google")
GEMINI_API_URL = BUILTIN_PROVIDERS["google"].base_url

PROVIDER_MODELS = {
    "openai": BUILTIN_PROVIDERS["openai"].default_model,
    "groq": BUILTIN_PROVIDERS["groq"].default_model,
    "anthropic": BUILTIN_PROVIDERS["anthropic"].default_model,
    "gemini": BUILTIN_PROVIDERS["google"].default_model,
}
PROVIDER_URLS = {
    "openai": OPENAI_API_URL,
    "groq": GROQ_API_URL,
}
PROVIDER_KEYS = {
    "openai": OPENAI_API_KEY,
    "groq": GROQ_API_KEY,
    "anthropic": ANTHROPIC_API_KEY,
    "gemini": GEMINI_API_KEY,
}
# Fallback order: best quality first
PROVIDER_FALLBACK_ORDER = ["openai", "anthropic", "gemini", "groq"]
# Groq's Llama struggles with many native tools — limit and prioritize
GROQ_MAX_TOOLS = 30
GROQ_PRIORITY_CATEGORIES = [
    "agents", "memory", "search", "orchestrator", "code_visualizer",
    "utilities", "media", "community", "integrations",
]

DEFAULT_MODEL = "gpt-4o"

# ── Runtime Intelligence: Context Window Management ──
# Token limits per provider (leave headroom for response + tools)
PROVIDER_MAX_CONTEXT = {
    "openai": 120000,
    "anthropic": 180000,
    "groq": 120000,
    "gemini": 1000000,
}
# Budget allocation (% of max context)
BUDGET_SYSTEM = 0.15       # 15% for system prompt + memories
BUDGET_HISTORY = 0.55      # 55% for conversation history
BUDGET_TOOLS = 0.10        # 10% for tool definitions
BUDGET_RESPONSE = 0.20     # 20% reserved for LLM response


def _estimate_tokens(text: str) -> int:
    """Fast token estimate: ~4 chars per token for English."""
    if not text:
        return 0
    return len(text) // 4


def _trim_message_content(content: str, max_tokens: int) -> str:
    """Trim a message to fit within token budget."""
    max_chars = max_tokens * 4
    if len(content) <= max_chars:
        return content
    return content[:max_chars - 20] + "\n...[trimmed]"


async def _summarize_old_history(messages: list, provider: str, api_key: str) -> str:
    """Summarize old conversation turns into a compact context block."""
    if not messages:
        return ""
    # Build a text block from old messages
    text_parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(b.get("text", str(b)) for b in content if isinstance(b, dict))
        if content:
            text_parts.append(f"{role}: {content[:300]}")
    combined = "\n".join(text_parts)
    if _estimate_tokens(combined) < 200:
        return combined  # Short enough, no need to summarize

    # Use LLM to summarize (quick, low-token call)
    try:
        summary_messages = [
            {"role": "system", "content": "Summarize this conversation history in 3-5 concise bullet points. Keep key facts, decisions, and context. Be brief."},
            {"role": "user", "content": combined[:8000]},
        ]
        url = PROVIDER_URLS.get(provider if provider != "anthropic" else "openai", GROQ_API_URL)
        model = "llama-3.3-70b-versatile" if provider == "groq" else PROVIDER_MODELS.get(provider, "gpt-4o")
        if provider == "anthropic":
            # Use Groq for summary to avoid Anthropic complexity
            url = GROQ_API_URL
            model = "llama-3.3-70b-versatile"
            api_key = GROQ_API_KEY or api_key

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json={"model": model, "messages": summary_messages, "max_tokens": 500, "temperature": 0.1},
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                summary = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if summary:
                    return f"[Summary of earlier conversation]\n{summary}"
    except Exception as e:
        logger.warning(f"[Runtime] Summary failed: {e}")

    # Fallback: just take first/last messages
    fallback = []
    for m in messages[:2] + messages[-2:]:
        content = m.get("content", "")
        if isinstance(content, str) and content:
            fallback.append(f"{m.get('role', 'user')}: {content[:200]}")
    return "[Earlier conversation context]\n" + "\n".join(fallback)


def _build_context_window(system: str, history: list, user_message: str, provider: str) -> list:
    """Intelligent context window management — fits everything within token budget."""
    max_ctx = PROVIDER_MAX_CONTEXT.get(provider, 120000)
    system_budget = int(max_ctx * BUDGET_SYSTEM)
    history_budget = int(max_ctx * BUDGET_HISTORY)

    messages = []

    # 1. System prompt (trim if needed)
    system_tokens = _estimate_tokens(system)
    if system_tokens > system_budget:
        system = _trim_message_content(system, system_budget)
    messages.append({"role": "system", "content": system})

    # 2. Conversation history — fit within budget
    user_msg_tokens = _estimate_tokens(user_message)
    remaining = history_budget

    if history:
        # Calculate total history tokens
        hist_entries = []
        for msg in history:
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(b.get("text", str(b)) for b in content if isinstance(b, dict))
            tokens = _estimate_tokens(content or "")
            hist_entries.append((msg, tokens))

        total_hist_tokens = sum(t for _, t in hist_entries)

        if total_hist_tokens <= remaining:
            # Everything fits
            for msg, _ in hist_entries:
                messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        else:
            # Need to trim: keep recent turns, mark old ones for summarization
            # Walk backwards from most recent, accumulate until budget
            kept = []
            used = 0
            for msg, tokens in reversed(hist_entries):
                if used + tokens <= remaining * 0.8:  # 80% for recent, 20% for summary
                    kept.insert(0, msg)
                    used += tokens
                else:
                    break

            # Old messages that didn't fit — will be summarized
            old_count = len(hist_entries) - len(kept)
            if old_count > 0:
                old_msgs = [m for m, _ in hist_entries[:old_count]]
                # Inline summary (fast, no LLM call — just compress)
                summary_parts = []
                for m in old_msgs[:6]:
                    c = m.get("content", "")
                    if isinstance(c, str) and c.strip():
                        summary_parts.append(f"- {m.get('role', 'user')}: {c[:150]}")
                if summary_parts:
                    summary_text = "[Earlier in this conversation]\n" + "\n".join(summary_parts)
                    messages.append({"role": "system", "content": summary_text})

            for msg in kept:
                messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

    # 3. Current user message (always last, always full)
    messages.append({"role": "user", "content": user_message})

    return messages

CV_SERVICE_URL = os.getenv("AST_ANALYSIS_SERVICE_URL") or os.getenv("CODE_VISUALIZER_SERVICE_URL", "http://rg_ast_analysis:8000")
MEMORY_SERVICE_URL = os.getenv("MEMORY_SERVICE_URL", "http://memory_service:8000")
AGENT_ENGINE_URL = "http://localhost:8000"  # self — agent_engine_service
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth_service:8000")
STATE_PHYSICS_URL = os.getenv("STATE_PHYSICS_URL", "http://rg_users_invarients_sim:8091")

from .platform_api_tools import platform_api_search, platform_api_call
from .runtime.context_manager import ContextWindowManager
from .runtime.smart_memory import filter_and_rank_memories, format_memories_for_prompt

# Tool selection constants (registry handles priority-based selection)
MAX_TOOLS_DEFAULT = 50  # OpenAI/Anthropic handle many tools well
MAX_TOOLS_GROQ = 30

_INTERNAL_SERVICE_KEY = os.getenv("AUTH_INTERNAL_SERVICE_KEY") or os.getenv("INTERNAL_SERVICE_KEY") or ""


async def _fetch_user_byok_keys(user_id: str) -> Dict[str, str]:
    """Fetch user's BYOK API keys from auth service (encrypted storage)."""
    if not user_id or user_id == "anonymous":
        return {}
    try:
        url = f"{AUTH_SERVICE_URL.rstrip('/')}/auth/internal/user-api-keys/{user_id}"
        headers = {"x-user-id": user_id}
        if _INTERNAL_SERVICE_KEY:
            headers["x-internal-service-key"] = _INTERNAL_SERVICE_KEY
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                keys = {}
                # Normalize provider names: auth stores 'google' but we use 'gemini'
                _byok_alias = {"google": "gemini", "chatgpt": "openai", "claude": "anthropic"}
                for entry in data.get("keys", []):
                    prov = entry.get("provider")
                    key = entry.get("api_key")
                    if prov and key:
                        normalized = _byok_alias.get(prov.lower(), prov.lower())
                        keys[normalized] = key
                if keys:
                    logger.info(f"[BYOK] Loaded {len(keys)} keys for {user_id}: {list(keys.keys())}")
                return keys
            else:
                logger.warning(f"[BYOK] Auth returned {resp.status_code} for {user_id}")
                return {}
    except Exception as e:
        logger.warning(f"[BYOK] Failed to fetch keys for {user_id}: {e}")
        return {}


# ── DB for persistent conversations ──
from sqlalchemy import text as sa_text
from .db import engine as _db_engine

_DDL_DONE = False

async def _ensure_tables():
    """Create agentic chat tables if they don't exist (runs once)."""
    global _DDL_DONE
    if _DDL_DONE:
        return
    # Pre-migration: fix user_id column type if old table exists with UUID
    try:
        async with _db_engine.begin() as conn:
            await conn.execute(sa_text(
                "ALTER TABLE agentic_custom_tools ALTER COLUMN user_id TYPE TEXT"
            ))
    except Exception:
        pass  # Table doesn't exist yet or already TEXT

    try:
        async with _db_engine.begin() as conn:
            await conn.execute(sa_text("""
                CREATE TABLE IF NOT EXISTS agentic_chat_conversations (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL,
                    title TEXT DEFAULT 'New conversation',
                    model TEXT DEFAULT 'llama-3.3-70b-versatile',
                    message_count INTEGER DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(sa_text("""
                CREATE INDEX IF NOT EXISTS idx_acc_user_id ON agentic_chat_conversations(user_id)
            """))
            await conn.execute(sa_text("""
                CREATE INDEX IF NOT EXISTS idx_acc_updated ON agentic_chat_conversations(updated_at DESC)
            """))
            await conn.execute(sa_text("""
                CREATE TABLE IF NOT EXISTS agentic_chat_messages (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    conversation_id UUID NOT NULL REFERENCES agentic_chat_conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tool_calls JSONB DEFAULT '[]'::jsonb,
                    tool_results JSONB DEFAULT '[]'::jsonb,
                    tokens_used INTEGER DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(sa_text("""
                CREATE INDEX IF NOT EXISTS idx_acm_conv_id ON agentic_chat_messages(conversation_id)
            """))
            # Dynamic custom tools created by the AI assistant at runtime
            await conn.execute(sa_text("""
                CREATE TABLE IF NOT EXISTS agentic_custom_tools (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    category TEXT DEFAULT 'custom',
                    parameters JSONB DEFAULT '{}'::jsonb,
                    http_method TEXT DEFAULT 'GET',
                    endpoint_url TEXT NOT NULL,
                    request_body_template JSONB DEFAULT NULL,
                    headers_template JSONB DEFAULT '{}'::jsonb,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(user_id, tool_name)
                )
            """))
            await conn.execute(sa_text("""
                CREATE INDEX IF NOT EXISTS idx_act_user_id ON agentic_custom_tools(user_id)
            """))
        _DDL_DONE = True
        print("[AGENTIC_CHAT] DB tables ready", flush=True)
    except Exception as e:
        print(f"[AGENTIC_CHAT] DDL error (non-fatal): {e}", flush=True)
        _DDL_DONE = True  # Don't retry on every request


# Cache for user custom tools (user_id -> {tool_name: tool_def})
_custom_tools_cache: Dict[str, Dict[str, Any]] = {}
_custom_tools_cache_ts: Dict[str, float] = {}
CUSTOM_TOOLS_CACHE_TTL = 60  # seconds

_executor = None


def _get_executor():
    global _executor
    if _executor is None:
        from .executor import AgentExecutor
        _executor = AgentExecutor()
    return _executor


# ── Tool definitions — generated from Unified Tool Registry ──
# TOOL_DEFS is backward-compatible dict built from the canonical registry.
# The registry (_registry) is the single source of truth.
TOOL_DEFS = {t.name: t.to_legacy_tool_defs_entry() for t in _registry.get_tools(access=ToolAccess.REGISTERED)}
logger.info(f"[ToolRegistry] Loaded {len(TOOL_DEFS)} tools from unified registry")

# --- OLD TOOL_DEFS DICT REMOVED (was ~894 lines) ---
# All tool definitions now live in rg_tool_registry/builtin_tools.py
# To add/edit tools, modify builtin_tools.py and the registry auto-generates everything.
_TOOL_DEFS_REMOVED = True  # marker

# ── Skill panel ID → individual tool names mapping ──
# The frontend skills panel sends skill IDs like "agents_os", "memory_library" etc.
# but TOOL_DEFS uses individual tool names. This mapping expands them.
SKILL_TO_TOOLS = {
    "agents_os": ["agents_list", "agents_create", "agents_start", "agents_stop", "agents_delete",
                   "agents_status", "agents_sessions", "agents_session_steps", "agents_session_trace",
                   "agents_metrics", "agents_session_detail", "agents_session_cancel",
                   "agents_update", "agents_available_tools", "agents_templates", "agents_versions",
                   "present_options", "workspace_snapshot", "schedule_agent", "run_snapshot",
                   "list_workspace_tools", "agent_snapshot", "run_agent", "session_log"],
    "memory_library": ["memory_read", "memory_write", "memory_search", "memory_stats",
                        "hash_sphere_search", "hash_sphere_anchor", "hash_sphere_list_anchors",
                        "hash_sphere_hash", "hash_sphere_resonance"],
    "memory_search": ["memory_read", "memory_search", "memory_stats", "hash_sphere_search"],
    "code_visualizer": ["code_visualizer_scan", "code_visualizer_trace", "code_visualizer_functions",
                         "code_visualizer_governance", "code_visualizer_list", "code_visualizer_full_analysis",
                         "code_visualizer_report", "code_visualizer_graph", "code_visualizer_pipeline",
                         "code_visualizer_filter", "code_visualizer_by_type", "code_visualizer_compare",
                         "code_visualizer_delete"],
    "image_generation": ["generate_image", "generate_audio", "generate_music"],
    "ide_workspace": ["file_read", "file_write", "file_edit", "file_list", "file_delete",
                       "execute_code", "http_request"],
    "rabbit_post": ["create_rabbit_community", "list_rabbit_communities", "get_rabbit_community",
                     "create_rabbit_post", "list_rabbit_posts", "search_rabbit_posts",
                     "get_rabbit_post", "delete_rabbit_post",
                     "create_rabbit_comment", "list_rabbit_comments", "delete_rabbit_comment",
                     "rabbit_vote"],
    "state_physics": ["sp_state", "sp_reset", "sp_nodes", "sp_metrics", "sp_identity",
                       "sp_simulate", "sp_galaxy", "sp_demo", "sp_asymmetry",
                       "sp_physics_config", "sp_entropy_config", "sp_entropy_toggle",
                       "sp_entropy_perturbation", "sp_agent_spawn", "sp_agent_step",
                       "sp_agent_kill", "sp_agents_spawn", "sp_agents_kill_all",
                       "sp_experiment", "sp_memory_cost", "sp_metrics_record"],
    # These skill IDs already match tool names directly:
    "web_search": ["web_search", "fetch_url", "read_webpage", "read_many_pages", "reddit_search",
                    "image_search", "news_search", "places_search", "youtube_search",
                    "deep_research", "wikipedia", "weather", "stock_crypto", "generate_chart",
                    "visualize"],
    "google_drive": ["google_drive"],
    "google_calendar": ["google_calendar"],
    "figma": ["figma"],
    "sigma": ["sigma"],
    "platform_api": ["platform_api_search", "platform_api_call"],
}


def _expand_skill_ids(enabled_tools: List[str]) -> List[str]:
    """Expand frontend skill panel IDs to individual tool names via registry."""
    expanded = set()
    all_names = _registry.get_names(access=ToolAccess.REGISTERED)
    all_names_set = set(all_names)
    for sid in enabled_tools:
        if sid in SKILL_TO_TOOLS:
            expanded.update(SKILL_TO_TOOLS[sid])
        if sid in all_names_set:
            expanded.add(sid)
    # Always include system tools + platform API gateway
    expanded.update(["get_current_time", "get_system_info", "platform_api_search", "platform_api_call"])
    # Include github/git tools if any tools are enabled
    if expanded:
        for tid in all_names:
            if tid.startswith("github_") or tid.startswith("git_"):
                expanded.add(tid)
    return list(expanded) if expanded else all_names


# ── Custom tool handlers (Code Visualizer & Agents OS) ──

async def _custom_cv_scan(args: dict, ctx: dict) -> dict:
    """Scan a GitHub repo via rg_ast_analysis service — returns rich analysis."""
    url = (args.get("repo_url") or args.get("github_url") or args.get("url") or "").strip()
    if not url:
        return {"error": "Missing repo_url parameter — provide a GitHub URL like https://github.com/owner/repo"}
    github_token = await _fetch_user_key(ctx.get("user_id", ""), "github")
    headers = {
        "x-user-id": ctx.get("user_id", ""),
        "x-user-role": ctx.get("user_role", "user"),
        "x-is-superuser": "true" if ctx.get("is_superuser") else "false",
    }
    if github_token:
        headers["x-github-token"] = github_token
    parts = url.rstrip("/").split("/")
    project_name = parts[-1] if parts else "repo"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            payload = {"repo_url": url, "project_name": project_name}
            if github_token:
                payload["token"] = github_token
            resp = await client.post(
                f"{CV_SERVICE_URL}/api/v1/scan/github",
                json=payload,
                headers=headers,
            )
            if resp.status_code != 200:
                return {"error": f"CV service error {resp.status_code}: {resp.text[:300]}"}
            data = resp.json()
            analysis_id = data.get("analysis_id") or data.get("id", "")
            analysis = data.get("analysis", {})
            stats = analysis.get("stats", data.get("stats", {}))
            nodes = analysis.get("nodes", [])
            services = [n for n in nodes if n.get("type") == "service"]
            endpoints = [n for n in nodes if n.get("type") == "endpoint"]
            functions = [n for n in nodes if n.get("type") == "function"]
            imports = [n for n in nodes if n.get("type") == "import"]
            result = {
                "success": True,
                "analysis_id": analysis_id,
                "repo": url,
                "project_name": project_name,
                "stats": {
                    "total_files": stats.get("total_files", 0),
                    "total_services": stats.get("total_services", len(services)),
                    "total_functions": stats.get("total_functions", len(functions)),
                    "total_endpoints": stats.get("total_endpoints", len(endpoints)),
                    "total_connections": stats.get("total_connections", 0),
                    "broken_connections": stats.get("broken_connections", 0),
                    "total_imports": len(imports),
                },
                "services": [{"name": s.get("label", s.get("id", "")), "file": s.get("file", "")} for s in services[:20]],
                "top_endpoints": [
                    {"method": e.get("method", ""), "route": e.get("route", e.get("path", "")), "service": e.get("service", "")}
                    for e in endpoints[:15]
                ],
                "sample_functions": [
                    {"name": f.get("label", f.get("id", "")), "file": f.get("file", "")}
                    for f in functions[:20]
                ],
            }
            if stats.get("total_functions", len(functions)) == 0 and len(nodes) > 0:
                result["note"] = f"Analysis found {len(nodes)} nodes total. Use code_visualizer_report for full breakdown."
            return result
    except Exception as e:
        return {"error": f"Code Visualizer scan failed: {str(e)[:300]}"}


async def _custom_cv_trace(args: dict, ctx: dict) -> dict:
    """Trace execution flow in an analyzed codebase."""
    query = args.get("query", "")
    analysis_id = args.get("analysis_id", "")
    if not analysis_id:
        return {"error": "Missing analysis_id — run code_visualizer_scan first"}
    max_depth = int(args.get("max_depth", 10))
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{CV_SERVICE_URL}/api/analysis/{analysis_id}/trace",
                json={"start_node": query, "max_depth": max_depth},
                headers=headers,
            )
            if resp.status_code != 200:
                return {"error": f"CV trace error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": f"Trace failed: {str(e)[:300]}"}


async def _custom_cv_functions(args: dict, ctx: dict) -> dict:
    """List functions or endpoints from an analysis."""
    analysis_id = args.get("analysis_id", "")
    list_type = args.get("type", "functions")
    if not analysis_id:
        return {"error": "Missing analysis_id"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            endpoint = "functions" if list_type == "functions" else "endpoints"
            resp = await client.get(
                f"{CV_SERVICE_URL}/api/analysis/{analysis_id}/{endpoint}",
                headers=headers,
            )
            if resp.status_code != 200:
                return {"error": f"CV {endpoint} error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_cv_governance(args: dict, ctx: dict) -> dict:
    """Run governance checks on an analysis."""
    analysis_id = args.get("analysis_id", "")
    if not analysis_id:
        return {"error": "Missing analysis_id"}
    drift_threshold = float(args.get("drift_threshold", 20.0))
    headers = {
        "x-user-id": ctx.get("user_id", ""),
        "x-user-role": ctx.get("user_role", "user"),
        "x-is-superuser": "true" if ctx.get("is_superuser") else "false",
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{CV_SERVICE_URL}/api/analysis/{analysis_id}/governance",
                json={"drift_threshold": drift_threshold},
                headers=headers,
            )
            if resp.status_code != 200:
                return {"error": f"Governance error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_cv_list(args: dict, ctx: dict) -> dict:
    """List user's previous analyses."""
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{CV_SERVICE_URL}/api/analyses", headers=headers)
            if resp.status_code != 200:
                return {"error": f"CV list error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_memory_search(args: dict, ctx: dict) -> dict:
    """Deep search through memories."""
    query = args.get("query", "")
    if not query:
        return {"error": "Missing query"}
    limit = int(args.get("limit", 10))
    headers = {"x-user-id": ctx.get("user_id", "")}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{MEMORY_SERVICE_URL}/memory/search",
                json={"query": query, "limit": limit, "user_id": ctx.get("user_id", "")},
                headers=headers,
            )
            if resp.status_code != 200:
                return {"error": f"Memory search error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_memory_stats(args: dict, ctx: dict) -> dict:
    """Get memory statistics."""
    headers = {"x-user-id": ctx.get("user_id", "")}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{MEMORY_SERVICE_URL}/memory/stats",
                headers=headers,
                params={"user_id": ctx.get("user_id", "")},
            )
            if resp.status_code != 200:
                return {"error": f"Memory stats error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_hs_search(args: dict, ctx: dict) -> dict:
    """Search Hash Sphere anchors."""
    query = args.get("query", "")
    if not query:
        return {"error": "Missing query"}
    limit = int(args.get("limit", 10))
    headers = {"x-user-id": ctx.get("user_id", "")}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{MEMORY_SERVICE_URL}/memory/hash-sphere/search",
                json={"query": query, "limit": limit, "user_id": ctx.get("user_id", "")},
                headers=headers,
            )
            if resp.status_code != 200:
                return {"error": f"Hash Sphere search error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_hs_anchor(args: dict, ctx: dict) -> dict:
    """Create a Hash Sphere anchor."""
    content_val = args.get("content", "")
    if not content_val:
        return {"error": "Missing content to anchor"}
    label = args.get("label", "")
    metadata = args.get("metadata", {})
    headers = {"x-user-id": ctx.get("user_id", "")}
    payload = {
        "content": content_val,
        "user_id": ctx.get("user_id", ""),
    }
    if label:
        payload["label"] = label
    if metadata and isinstance(metadata, dict):
        payload["metadata"] = metadata
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{MEMORY_SERVICE_URL}/memory/hash-sphere/anchors",
                json=payload,
                headers=headers,
            )
            if resp.status_code != 200:
                return {"error": f"Anchor creation error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_hs_list_anchors(args: dict, ctx: dict) -> dict:
    """List user's Hash Sphere anchors."""
    limit = int(args.get("limit", 20))
    headers = {"x-user-id": ctx.get("user_id", "")}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{MEMORY_SERVICE_URL}/memory/hash-sphere/anchors",
                headers=headers,
                params={"user_id": ctx.get("user_id", ""), "limit": limit},
            )
            if resp.status_code != 200:
                return {"error": f"List anchors error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_hs_hash(args: dict, ctx: dict) -> dict:
    """Generate a Hash Sphere hash."""
    content_val = args.get("content", "")
    if not content_val:
        return {"error": "Missing content"}
    headers = {"x-user-id": ctx.get("user_id", "")}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{MEMORY_SERVICE_URL}/memory/hash-sphere/hash",
                json={"content": content_val},
                headers=headers,
            )
            if resp.status_code != 200:
                return {"error": f"Hash error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_hs_resonance(args: dict, ctx: dict) -> dict:
    """Check resonance between two contents."""
    a = args.get("content_a", "")
    b = args.get("content_b", "")
    if not a or not b:
        return {"error": "Both content_a and content_b required"}
    headers = {"x-user-id": ctx.get("user_id", "")}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{MEMORY_SERVICE_URL}/memory/hash-sphere/resonance",
                json={"content_a": a, "content_b": b},
                headers=headers,
            )
            if resp.status_code != 200:
                return {"error": f"Resonance error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_cv_full_analysis(args: dict, ctx: dict) -> dict:
    """Complete analysis pipeline: scan + report + functions + trace + governance."""
    url = (args.get("repo_url") or args.get("github_url") or args.get("url") or "").strip()
    if not url:
        return {"error": "Missing repo_url"}
    trace_entry = args.get("trace_entry", "")
    result = {"repo": url, "steps": []}

    # Step 1: Scan
    scan_result = await _custom_cv_scan({"repo_url": url}, ctx)
    result["steps"].append({"step": "scan", "success": scan_result.get("success", False)})
    if not scan_result.get("success"):
        result["error"] = scan_result.get("error", "Scan failed")
        return result
    analysis_id = scan_result.get("analysis_id", "")
    result["analysis_id"] = analysis_id
    result["scan"] = scan_result

    headers = {
        "x-user-id": ctx.get("user_id", ""),
        "x-user-role": ctx.get("user_role", "user"),
        "x-is-superuser": "true" if ctx.get("is_superuser") else "false",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        # Step 2: Full report (graph structure)
        try:
            resp = await client.get(f"{CV_SERVICE_URL}/api/analysis/{analysis_id}/graph-structure", headers=headers)
            if resp.status_code == 200:
                graph = resp.json()
                result["graph"] = {
                    "files": len(graph.get("files", [])),
                    "modules": len(graph.get("modules", [])),
                    "import_edges": len(graph.get("import_edges", graph.get("edges", []))),
                }
                result["steps"].append({"step": "graph", "success": True})
            else:
                result["steps"].append({"step": "graph", "success": False, "error": resp.text[:200]})
        except Exception as e:
            result["steps"].append({"step": "graph", "success": False, "error": str(e)[:200]})

        # Step 3: Functions list
        try:
            resp = await client.get(f"{CV_SERVICE_URL}/api/analysis/{analysis_id}/functions", headers=headers)
            if resp.status_code == 200:
                funcs = resp.json().get("functions", [])
                result["functions"] = {
                    "total": len(funcs),
                    "sample": [{"name": f.get("label", f.get("id", "")), "file": f.get("file", "")} for f in funcs[:30]],
                }
                result["steps"].append({"step": "functions", "success": True})
            else:
                result["steps"].append({"step": "functions", "success": False})
        except Exception as e:
            result["steps"].append({"step": "functions", "success": False, "error": str(e)[:200]})

        # Step 4: Trace pipeline
        try:
            trace_payload = {"start_node": trace_entry or "", "max_depth": 30}
            resp = await client.post(
                f"{CV_SERVICE_URL}/api/analysis/{analysis_id}/full-pipeline",
                json=trace_payload,
                headers=headers,
            )
            if resp.status_code == 200:
                trace_data = resp.json()
                trace_nodes = trace_data.get("trace", trace_data.get("nodes", []))
                if isinstance(trace_nodes, list):
                    result["pipeline"] = {
                        "depth": len(trace_nodes),
                        "nodes": [{"id": n.get("id", ""), "type": n.get("type", ""), "label": n.get("label", "")} for n in trace_nodes[:30]],
                    }
                else:
                    result["pipeline"] = trace_data
                result["steps"].append({"step": "pipeline", "success": True})
            else:
                result["steps"].append({"step": "pipeline", "success": False, "error": resp.text[:200]})
        except Exception as e:
            result["steps"].append({"step": "pipeline", "success": False, "error": str(e)[:200]})

        # Step 5: Governance check
        try:
            resp = await client.post(
                f"{CV_SERVICE_URL}/api/analysis/{analysis_id}/governance",
                json={"drift_threshold": 20.0},
                headers=headers,
            )
            if resp.status_code == 200:
                gov = resp.json()
                result["governance"] = {
                    "health_score": gov.get("governance", {}).get("health_score", gov.get("health_score")),
                    "live_nodes": gov.get("live_count"),
                    "invalid_nodes": gov.get("invalid_count"),
                    "issues": gov.get("governance", {}).get("issues", [])[:10],
                    "credits_deducted": gov.get("credits_deducted", 0),
                }
                result["steps"].append({"step": "governance", "success": True})
            else:
                result["steps"].append({"step": "governance", "success": False, "error": resp.text[:200]})
        except Exception as e:
            result["steps"].append({"step": "governance", "success": False, "error": str(e)[:200]})

    result["success"] = True
    return result


async def _custom_cv_report(args: dict, ctx: dict) -> dict:
    """Get full analysis report."""
    analysis_id = args.get("analysis_id", "")
    if not analysis_id:
        return {"error": "Missing analysis_id"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{CV_SERVICE_URL}/api/analysis/{analysis_id}", headers=headers)
            if resp.status_code != 200:
                return {"error": f"CV report error {resp.status_code}: {resp.text[:300]}"}
            data = resp.json()
            nodes = data.get("nodes") or []
            connections = data.get("connections") or []
            return {
                "analysis_id": analysis_id,
                "total_nodes": len(nodes),
                "total_connections": len(connections),
                "node_types": {t: sum(1 for n in nodes if n.get("type") == t) for t in set(n.get("type", "unknown") for n in nodes)},
                "files": list(set(n.get("file", "") for n in nodes if n.get("file")))[:50],
                "sample_nodes": nodes[:20],
            }
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_cv_graph(args: dict, ctx: dict) -> dict:
    """Get dependency graph structure."""
    analysis_id = args.get("analysis_id", "")
    if not analysis_id:
        return {"error": "Missing analysis_id"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{CV_SERVICE_URL}/api/analysis/{analysis_id}/graph-structure", headers=headers)
            if resp.status_code != 200:
                return {"error": f"CV graph error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_cv_pipeline(args: dict, ctx: dict) -> dict:
    """Full end-to-end pipeline trace (deep, max_depth=50)."""
    analysis_id = args.get("analysis_id", "")
    if not analysis_id:
        return {"error": "Missing analysis_id"}
    start_node = args.get("start_node", "")
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{CV_SERVICE_URL}/api/analysis/{analysis_id}/full-pipeline",
                json={"start_node": start_node, "max_depth": 50},
                headers=headers,
            )
            if resp.status_code != 200:
                return {"error": f"CV pipeline error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_cv_filter(args: dict, ctx: dict) -> dict:
    """Filter analysis by file path, node type, or keyword."""
    analysis_id = args.get("analysis_id", "")
    if not analysis_id:
        return {"error": "Missing analysis_id"}
    payload = {}
    if args.get("file_path"):
        payload["file_path"] = args["file_path"]
    if args.get("node_type"):
        payload["node_type"] = args["node_type"]
    if args.get("keyword"):
        payload["keyword"] = args["keyword"]
    if not payload:
        return {"error": "Provide at least one of: file_path, node_type, keyword"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{CV_SERVICE_URL}/api/analysis/{analysis_id}/filter",
                json=payload,
                headers=headers,
            )
            if resp.status_code != 200:
                return {"error": f"CV filter error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_cv_by_type(args: dict, ctx: dict) -> dict:
    """Get all nodes of a specific type."""
    analysis_id = args.get("analysis_id", "")
    node_type = args.get("node_type", "function")
    if not analysis_id:
        return {"error": "Missing analysis_id"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{CV_SERVICE_URL}/api/analysis/{analysis_id}/by-type/{node_type}",
                headers=headers,
            )
            if resp.status_code != 200:
                return {"error": f"CV by-type error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_cv_compare(args: dict, ctx: dict) -> dict:
    """Compare two analyses."""
    id_a = args.get("analysis_id_a", "")
    id_b = args.get("analysis_id_b", "")
    if not id_a or not id_b:
        return {"error": "Both analysis_id_a and analysis_id_b are required"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{CV_SERVICE_URL}/api/compare-by-analysis",
                json={"analysis_id_a": id_a, "analysis_id_b": id_b},
                headers=headers,
            )
            if resp.status_code != 200:
                return {"error": f"CV compare error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_cv_delete(args: dict, ctx: dict) -> dict:
    """Delete a saved analysis."""
    analysis_id = args.get("analysis_id", "")
    if not analysis_id:
        return {"error": "Missing analysis_id"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(
                f"{CV_SERVICE_URL}/api/v1/analyses/{analysis_id}",
                headers=headers,
            )
            if resp.status_code not in (200, 204):
                return {"error": f"CV delete error {resp.status_code}: {resp.text[:300]}"}
            return {"success": True, "message": f"Analysis {analysis_id} deleted"}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _local_file_read(args: dict, context: dict) -> dict:
    """Placeholder — actual execution happens in Electron client via IPC."""
    return {"_local_tool": True, "tool": "file_read", "args": args, "message": "This tool executes locally in the Resonant IDE desktop app."}

async def _local_file_write(args: dict, context: dict) -> dict:
    return {"_local_tool": True, "tool": "file_write", "args": args, "message": "This tool executes locally in the Resonant IDE desktop app."}

async def _local_file_edit(args: dict, context: dict) -> dict:
    return {"_local_tool": True, "tool": "file_edit", "args": args, "message": "This tool executes locally in the Resonant IDE desktop app."}

async def _local_file_list(args: dict, context: dict) -> dict:
    return {"_local_tool": True, "tool": "file_list", "args": args, "message": "This tool executes locally in the Resonant IDE desktop app."}

async def _local_file_delete(args: dict, context: dict) -> dict:
    return {"_local_tool": True, "tool": "file_delete", "args": args, "message": "This tool executes locally in the Resonant IDE desktop app."}


async def _custom_agents_list(args: dict, ctx: dict) -> dict:
    """List user's agents."""
    headers = {
        "x-user-id": ctx.get("user_id", ""),
        "x-user-role": ctx.get("user_role", "user"),
        "x-is-superuser": "true" if ctx.get("is_superuser") else "false",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(f"{AGENT_ENGINE_URL}/agents/", headers=headers, params={"limit": 20})
            if resp.status_code != 200:
                return {"error": f"Agents list error {resp.status_code}: {resp.text[:300]}"}
            data = resp.json()
            agents = data if isinstance(data, list) else data.get("agents", data.get("items", []))
            return {
                "agents": [{"id": a.get("id"), "name": a.get("name"), "status": a.get("status"), "goal": (a.get("goal") or "")[:100]} for a in agents[:20]],
                "count": len(agents),
            }
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_create(args: dict, ctx: dict) -> dict:
    """Create a new agent."""
    name = args.get("name", "").strip()
    goal = args.get("goal", "").strip()
    if not name or not goal:
        return {"error": "Both 'name' and 'goal' are required"}
    tools_str = args.get("tools", "")
    tools = [t.strip() for t in tools_str.split(",") if t.strip()] if tools_str else ["web_search", "memory.read"]
    headers = {
        "x-user-id": ctx.get("user_id", ""),
        "x-user-role": ctx.get("user_role", "user"),
        "x-is-superuser": "true" if ctx.get("is_superuser") else "false",
        "x-unlimited-credits": "true" if ctx.get("unlimited_credits") else "false",
    }
    payload = {"name": name, "goal": goal, "tools": tools, "model": "groq/llama-3.3-70b-versatile"}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.post(f"{AGENT_ENGINE_URL}/agents/", json=payload, headers=headers)
            if resp.status_code not in (200, 201):
                return {"error": f"Create agent error {resp.status_code}: {resp.text[:300]}"}
            data = resp.json()
            return {
                "success": True,
                "agent_id": data.get("id"),
                "name": data.get("name"),
                "status": data.get("status"),
                "panel_url": f"/agents?agent={data.get('id')}",
            }
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_start(args: dict, ctx: dict) -> dict:
    """Start an agent by creating a new session with a goal."""
    agent_id = args.get("agent_id") or args.get("agent_name", "")
    goal = args.get("goal") or args.get("message") or "Execute your configured task autonomously"
    if not agent_id:
        return {"error": "Provide agent_id or agent_name"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user"),
               "x-org-id": ctx.get("org_id", "")}
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            # The correct endpoint is POST /agents/{id}/sessions with a goal
            resp = await client.post(
                f"{AGENT_ENGINE_URL}/agents/{agent_id}/sessions",
                json={"goal": goal, "context": {}},
                headers=headers,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {"success": True, "message": f"Agent {agent_id} started", "session": data}
            return {"error": f"Start error {resp.status_code}: {resp.text[:300]}"}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_stop(args: dict, ctx: dict) -> dict:
    """Stop an agent by cancelling its running sessions."""
    agent_id = args.get("agent_id") or args.get("agent_name", "")
    session_id = args.get("session_id", "")
    if not agent_id and not session_id:
        return {"error": "Provide agent_id, agent_name, or session_id"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            if session_id:
                # Cancel specific session
                resp = await client.post(f"{AGENT_ENGINE_URL}/agents/sessions/{session_id}/cancel", headers=headers)
                if resp.status_code == 200:
                    return {"success": True, "message": f"Session {session_id} cancelled"}
                return {"error": f"Cancel error {resp.status_code}: {resp.text[:300]}"}
            else:
                # Find running sessions for this agent and cancel them
                resp = await client.get(f"{AGENT_ENGINE_URL}/agents/{agent_id}/sessions", headers=headers)
                if resp.status_code != 200:
                    return {"error": f"Could not get sessions: {resp.status_code}: {resp.text[:300]}"}
                sessions = resp.json() if isinstance(resp.json(), list) else resp.json().get("sessions", resp.json().get("items", []))
                cancelled = []
                for s in sessions:
                    if s.get("status") in ("running", "pending"):
                        sid = s.get("id")
                        cr = await client.post(f"{AGENT_ENGINE_URL}/agents/sessions/{sid}/cancel", headers=headers)
                        if cr.status_code == 200:
                            cancelled.append(sid)
                if cancelled:
                    return {"success": True, "message": f"Cancelled {len(cancelled)} session(s)", "cancelled_sessions": cancelled}
                return {"info": "No running sessions found for this agent"}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_delete(args: dict, ctx: dict) -> dict:
    """Delete an agent."""
    agent_id = args.get("agent_id") or args.get("agent_name", "")
    if not agent_id:
        return {"error": "Provide agent_id or agent_name"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.delete(f"{AGENT_ENGINE_URL}/agents/{agent_id}", headers=headers)
            if resp.status_code not in (200, 204):
                return {"error": f"Delete error {resp.status_code}: {resp.text[:300]}"}
            return {"success": True, "message": f"Agent {agent_id} deleted"}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_status(args: dict, ctx: dict) -> dict:
    """Get detailed status of an agent by ID or name."""
    agent_id = args.get("agent_id") or args.get("agent_name", "")
    if not agent_id:
        return {"error": "Provide agent_id or agent_name"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(f"{AGENT_ENGINE_URL}/agents/{agent_id}", headers=headers)
            if resp.status_code != 200:
                return {"error": f"Status error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_sessions(args: dict, ctx: dict) -> dict:
    """List sessions for an agent — shows status, goal, loop count, tokens used."""
    agent_id = args.get("agent_id") or args.get("agent_name", "")
    status_filter = args.get("status", "")
    limit = int(args.get("limit", 20))
    if not agent_id:
        return {"error": "Provide agent_id or agent_name"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            params = {"limit": limit}
            if status_filter:
                params["status_filter"] = status_filter
            resp = await client.get(f"{AGENT_ENGINE_URL}/agents/{agent_id}/sessions",
                                    params=params, headers=headers)
            if resp.status_code != 200:
                return {"error": f"Sessions error {resp.status_code}: {resp.text[:300]}"}
            return {"sessions": resp.json()}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_session_steps(args: dict, ctx: dict) -> dict:
    """Get execution steps for a session — tool calls, reasoning, outputs, timing."""
    session_id = args.get("session_id", "")
    if not session_id:
        return {"error": "Provide session_id"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(f"{AGENT_ENGINE_URL}/agents/sessions/{session_id}/steps",
                                    headers=headers)
            if resp.status_code != 200:
                return {"error": f"Steps error {resp.status_code}: {resp.text[:300]}"}
            return {"steps": resp.json()}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_session_trace(args: dict, ctx: dict) -> dict:
    """Get full execution trace for a session — LangSmith-level detail with waterfall, cost, safety flags."""
    session_id = args.get("session_id", "")
    if not session_id:
        return {"error": "Provide session_id"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(f"{AGENT_ENGINE_URL}/agents/sessions/{session_id}/trace",
                                    headers=headers)
            if resp.status_code != 200:
                return {"error": f"Trace error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_metrics(args: dict, ctx: dict) -> dict:
    """Get agent run metrics — or platform-wide metrics if no agent_id given."""
    agent_id = args.get("agent_id") or args.get("agent_name", "")
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            if agent_id:
                resp = await client.get(f"{AGENT_ENGINE_URL}/agents/{agent_id}/metrics", headers=headers)
            else:
                resp = await client.get(f"{AGENT_ENGINE_URL}/agents/metrics/summary", headers=headers)
            if resp.status_code != 200:
                return {"error": f"Metrics error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_session_detail(args: dict, ctx: dict) -> dict:
    """Get detailed info for a single session by session_id — status, goal, loops, tokens, output, error."""
    session_id = args.get("session_id", "")
    if not session_id:
        return {"error": "Provide session_id"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(f"{AGENT_ENGINE_URL}/agents/sessions/{session_id}", headers=headers)
            if resp.status_code != 200:
                return {"error": f"Session detail error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_session_cancel(args: dict, ctx: dict) -> dict:
    """Cancel a specific session by session_id."""
    session_id = args.get("session_id", "")
    if not session_id:
        return {"error": "Provide session_id"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.post(f"{AGENT_ENGINE_URL}/agents/sessions/{session_id}/cancel", headers=headers)
            if resp.status_code == 200:
                return {"success": True, "message": f"Session {session_id} cancelled", "data": resp.json()}
            return {"error": f"Cancel error {resp.status_code}: {resp.text[:300]}"}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_update(args: dict, ctx: dict) -> dict:
    """Update/edit an agent — change name, goal, model, tools, system_prompt, etc."""
    agent_id = args.get("agent_id", "")
    if not agent_id:
        return {"error": "Provide agent_id"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user"),
               "Content-Type": "application/json"}
    patch_body = {}
    for key in ("name", "description", "goal", "system_prompt", "model", "tools",
                "allowed_actions", "blocked_actions", "temperature", "max_tokens", "is_active"):
        if key in args:
            patch_body[key] = args[key]
    if not patch_body:
        return {"error": "Provide at least one field to update (name, goal, model, tools, system_prompt, etc.)"}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.patch(f"{AGENT_ENGINE_URL}/agents/{agent_id}", json=patch_body, headers=headers)
            if resp.status_code == 200:
                return {"success": True, "agent": resp.json()}
            return {"error": f"Update error {resp.status_code}: {resp.text[:300]}"}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_available_tools(args: dict, ctx: dict) -> dict:
    """List all tools that agents can use — tool names, descriptions, categories."""
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(f"{AGENT_ENGINE_URL}/agents/available-tools", headers=headers)
            if resp.status_code != 200:
                return {"error": f"Tools error {resp.status_code}: {resp.text[:300]}"}
            return {"tools": resp.json()}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_templates(args: dict, ctx: dict) -> dict:
    """List available agent templates — pre-configured agents you can instantiate."""
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(f"{AGENT_ENGINE_URL}/agents/templates", headers=headers)
            if resp.status_code != 200:
                return {"error": f"Templates error {resp.status_code}: {resp.text[:300]}"}
            return {"templates": resp.json()}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_agents_versions(args: dict, ctx: dict) -> dict:
    """Get version history for an agent — shows config changes over time."""
    agent_id = args.get("agent_id", "")
    if not agent_id:
        return {"error": "Provide agent_id"}
    headers = {"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(f"{AGENT_ENGINE_URL}/agents/{agent_id}/versions", headers=headers)
            if resp.status_code != 200:
                return {"error": f"Versions error {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:300]}


# ── State Physics Handlers ──
# All handlers call state_physics_service internal endpoints at /api/*

def _sp_headers(ctx: dict) -> dict:
    """Build headers for State Physics requests with user context."""
    return {
        "x-user-id": ctx.get("user_id", "anonymous"),
        "x-org-id": ctx.get("org_id", ""),
        "x-user-role": ctx.get("user_role", "user"),
    }


async def _custom_sp_state(args: dict, ctx: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(f"{STATE_PHYSICS_URL}/api/state", headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_reset(args: dict, ctx: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/reset", headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_nodes(args: dict, ctx: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(f"{STATE_PHYSICS_URL}/api/nodes", headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_metrics(args: dict, ctx: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(f"{STATE_PHYSICS_URL}/api/metrics", headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_identity(args: dict, ctx: dict) -> dict:
    dsid = args.get("dsid") or args.get("id") or args.get("name", "")
    if not dsid:
        return {"error": "Provide dsid (unique identity ID)"}
    body = {
        "dsid": dsid,
        "node_type": args.get("node_type", "user"),
        "trust": float(args.get("trust", 0.5)),
        "value": float(args.get("value", 0)),
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/identity", json=body, headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_simulate(args: dict, ctx: dict) -> dict:
    steps = int(args.get("steps", 1))
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/simulate", json={"steps": steps}, headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_galaxy(args: dict, ctx: dict) -> dict:
    body = {
        "num_users": int(args.get("num_users", 500)),
        "num_transactions": int(args.get("num_transactions", 1500)),
        "num_services": int(args.get("num_services", 10)),
        "enable_agent": bool(args.get("enable_agent", True)),
        "enable_entropy": bool(args.get("enable_entropy", True)),
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/galaxy", json=body, headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_demo(args: dict, ctx: dict) -> dict:
    params = {
        "num_users": int(args.get("num_users", 30)),
        "num_transactions": int(args.get("num_transactions", 80)),
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/demo", params=params, headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_asymmetry(args: dict, ctx: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(f"{STATE_PHYSICS_URL}/api/asymmetry", headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_physics_config(args: dict, ctx: dict) -> dict:
    body = {}
    for k in ("gravity_constant", "repulsion_constant", "spring_constant", "damping"):
        if k in args and args[k] is not None:
            body[k] = float(args[k])
    if not body:
        return {"error": "Provide at least one: gravity_constant, repulsion_constant, spring_constant, damping"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/physics/config", json=body, headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_entropy_config(args: dict, ctx: dict) -> dict:
    body = {}
    for k in ("position_noise", "velocity_noise", "trust_decay", "value_decay", "activity_probability"):
        if k in args and args[k] is not None:
            body[k] = float(args[k])
    if not body:
        return {"error": "Provide at least one entropy parameter"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/entropy/config", json=body, headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_entropy_toggle(args: dict, ctx: dict) -> dict:
    enabled = args.get("enabled", True)
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/entropy/toggle", params={"enabled": str(enabled).lower()}, headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_entropy_perturbation(args: dict, ctx: dict) -> dict:
    magnitude = float(args.get("magnitude", 1.0))
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/entropy/perturbation", params={"magnitude": magnitude}, headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_agent_spawn(args: dict, ctx: dict) -> dict:
    budget = float(args.get("budget", 5000))
    action_prob = float(args.get("action_probability", 0.3))
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/agent/spawn", params={"budget": budget, "action_probability": action_prob}, headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_agent_step(args: dict, ctx: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/agent/step", headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_agent_kill(args: dict, ctx: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/agent/kill", headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_agents_spawn(args: dict, ctx: dict) -> dict:
    count = int(args.get("count", 3))
    budget = float(args.get("budget", 1000))
    action_prob = float(args.get("action_probability", 0.3))
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/agents/spawn", params={"count": count, "budget": budget, "action_probability": action_prob}, headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_agents_kill_all(args: dict, ctx: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/agents/kill_all", headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_experiment(args: dict, ctx: dict) -> dict:
    experiment = args.get("experiment", "")
    if not experiment:
        return {"error": "Provide experiment name: zero_agent, stress_test, or long_run"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/experiment/setup", params={"experiment": experiment}, headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_memory_cost(args: dict, ctx: dict) -> dict:
    cost_multiplier = float(args.get("cost_multiplier", 1.0))
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/memory/cost", params={"cost_multiplier": cost_multiplier}, headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_sp_metrics_record(args: dict, ctx: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{STATE_PHYSICS_URL}/api/metrics/record", headers=_sp_headers(ctx))
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
    except Exception as e:
        return {"error": str(e)[:300]}


# ── Dynamic Tool Management Handlers ──
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:8000")


async def _load_user_custom_tools(user_id: str) -> Dict[str, Any]:
    """Load custom tools from DB for a user (with cache)."""
    import time
    now = time.time()
    cached_ts = _custom_tools_cache_ts.get(user_id, 0)
    if user_id in _custom_tools_cache and (now - cached_ts) < CUSTOM_TOOLS_CACHE_TTL:
        return _custom_tools_cache[user_id]

    tools = {}
    try:
        async with _db_engine.begin() as conn:
            rows = await conn.execute(sa_text(
                "SELECT tool_name, description, category, parameters, http_method, "
                "endpoint_url, request_body_template, headers_template "
                "FROM agentic_custom_tools WHERE user_id = :uid AND is_active = TRUE"
            ), {"uid": user_id})
            for row in rows:
                tname = row[0]
                tools[tname] = {
                    "desc": row[1],
                    "category": row[2] or "custom",
                    "params": row[3] if isinstance(row[3], dict) else {},
                    "handler": f"_dynamic_custom_tool:{tname}",
                    "_http_method": row[4] or "GET",
                    "_endpoint_url": row[5],
                    "_request_body_template": row[6],
                    "_headers_template": row[7] if isinstance(row[7], dict) else {},
                }
    except Exception as e:
        print(f"[AGENTIC_CHAT] Failed to load custom tools for {user_id}: {e}", flush=True)

    _custom_tools_cache[user_id] = tools
    _custom_tools_cache_ts[user_id] = now
    return tools


def _invalidate_custom_tools_cache(user_id: str):
    """Clear cached custom tools for a user so next request reloads from DB."""
    _custom_tools_cache.pop(user_id, None)
    _custom_tools_cache_ts.pop(user_id, None)


async def _custom_create_tool(args: dict, ctx: dict) -> dict:
    """Create a new custom tool stored in the DB."""
    user_id = ctx.get("user_id", "")
    if not user_id:
        return {"error": "Authentication required to create tools"}

    tool_name = (args.get("tool_name") or "").strip().lower().replace(" ", "_").replace("-", "_")
    description = (args.get("description") or "").strip()
    endpoint_url = (args.get("endpoint_url") or "").strip()

    if not tool_name:
        return {"error": "tool_name is required (snake_case, e.g. 'get_weather')"}
    if not description:
        return {"error": "description is required — tells the AI when to use this tool"}
    if not endpoint_url:
        return {"error": "endpoint_url is required — the API endpoint this tool calls"}
    if tool_name in TOOL_DEFS:
        return {"error": f"Tool '{tool_name}' already exists as a built-in tool. Choose a different name."}

    parameters = args.get("parameters", {})
    if isinstance(parameters, str):
        try:
            import json as _json
            parameters = _json.loads(parameters)
        except Exception:
            parameters = {}

    http_method = (args.get("http_method") or "GET").upper()
    if http_method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        http_method = "GET"

    request_body = args.get("request_body")
    if isinstance(request_body, str):
        try:
            import json as _json
            request_body = _json.loads(request_body)
        except Exception:
            request_body = None

    category = (args.get("category") or "custom").strip()

    try:
        async with _db_engine.begin() as conn:
            await conn.execute(sa_text("""
                INSERT INTO agentic_custom_tools (user_id, tool_name, description, category,
                    parameters, http_method, endpoint_url, request_body_template)
                VALUES (:uid, :name, :desc, :cat, CAST(:params AS jsonb), :method, :url, CAST(:body AS jsonb))
                ON CONFLICT (user_id, tool_name) DO UPDATE SET
                    description = EXCLUDED.description,
                    category = EXCLUDED.category,
                    parameters = EXCLUDED.parameters,
                    http_method = EXCLUDED.http_method,
                    endpoint_url = EXCLUDED.endpoint_url,
                    request_body_template = EXCLUDED.request_body_template,
                    is_active = TRUE,
                    updated_at = NOW()
            """), {
                "uid": user_id, "name": tool_name, "desc": description,
                "cat": category,
                "params": json.dumps(parameters) if not isinstance(parameters, str) else parameters,
                "method": http_method, "url": endpoint_url,
                "body": json.dumps(request_body) if request_body else None,
            })
        _invalidate_custom_tools_cache(user_id)
        return {
            "success": True,
            "message": f"Tool '{tool_name}' created successfully! I can now use it in this and future conversations.",
            "tool": {
                "name": tool_name,
                "description": description,
                "category": category,
                "parameters": parameters,
                "http_method": http_method,
                "endpoint_url": endpoint_url,
                "request_body": request_body,
            }
        }
    except Exception as e:
        return {"error": f"Failed to create tool: {str(e)[:300]}"}


async def _custom_list_tools(args: dict, ctx: dict) -> dict:
    """List all custom tools for the user."""
    user_id = ctx.get("user_id", "")
    if not user_id:
        return {"error": "Authentication required"}

    try:
        tools = []
        async with _db_engine.begin() as conn:
            rows = await conn.execute(sa_text(
                "SELECT tool_name, description, category, parameters, http_method, "
                "endpoint_url, request_body_template, created_at, is_active "
                "FROM agentic_custom_tools WHERE user_id = :uid ORDER BY created_at DESC"
            ), {"uid": user_id})
            for row in rows:
                tools.append({
                    "name": row[0],
                    "description": row[1],
                    "category": row[2],
                    "parameters": row[3],
                    "http_method": row[4],
                    "endpoint_url": row[5],
                    "request_body": row[6],
                    "created_at": str(row[7]) if row[7] else None,
                    "is_active": row[8],
                })
        return {"tools": tools, "count": len(tools)}
    except Exception as e:
        return {"error": f"Failed to list tools: {str(e)[:300]}"}


async def _custom_delete_tool(args: dict, ctx: dict) -> dict:
    """Delete a custom tool by name."""
    user_id = ctx.get("user_id", "")
    tool_name = (args.get("tool_name") or "").strip()
    if not user_id:
        return {"error": "Authentication required"}
    if not tool_name:
        return {"error": "tool_name is required"}

    try:
        async with _db_engine.begin() as conn:
            result = await conn.execute(sa_text(
                "DELETE FROM agentic_custom_tools WHERE user_id = :uid AND tool_name = :name"
            ), {"uid": user_id, "name": tool_name})
            if result.rowcount == 0:
                return {"error": f"Tool '{tool_name}' not found"}
        _invalidate_custom_tools_cache(user_id)
        return {"success": True, "message": f"Tool '{tool_name}' deleted."}
    except Exception as e:
        return {"error": f"Failed to delete tool: {str(e)[:300]}"}


async def _custom_update_tool(args: dict, ctx: dict) -> dict:
    """Update an existing custom tool."""
    user_id = ctx.get("user_id", "")
    tool_name = (args.get("tool_name") or "").strip()
    if not user_id:
        return {"error": "Authentication required"}
    if not tool_name:
        return {"error": "tool_name is required"}

    updates = []
    params: Dict[str, Any] = {"uid": user_id, "name": tool_name}

    if "description" in args and args["description"]:
        updates.append("description = :desc")
        params["desc"] = args["description"]
    if "parameters" in args and args["parameters"]:
        updates.append("parameters = CAST(:params AS jsonb)")
        p = args["parameters"]
        params["params"] = json.dumps(p) if not isinstance(p, str) else p
    if "http_method" in args and args["http_method"]:
        updates.append("http_method = :method")
        params["method"] = args["http_method"].upper()
    if "endpoint_url" in args and args["endpoint_url"]:
        updates.append("endpoint_url = :url")
        params["url"] = args["endpoint_url"]
    if "request_body" in args:
        updates.append("request_body_template = CAST(:body AS jsonb)")
        b = args["request_body"]
        params["body"] = json.dumps(b) if b and not isinstance(b, str) else b

    if not updates:
        return {"error": "Provide at least one field to update"}

    updates.append("updated_at = NOW()")
    set_clause = ", ".join(updates)

    try:
        async with _db_engine.begin() as conn:
            result = await conn.execute(sa_text(
                f"UPDATE agentic_custom_tools SET {set_clause} WHERE user_id = :uid AND tool_name = :name"
            ), params)
            if result.rowcount == 0:
                return {"error": f"Tool '{tool_name}' not found"}
        _invalidate_custom_tools_cache(user_id)
        return {"success": True, "message": f"Tool '{tool_name}' updated."}
    except Exception as e:
        return {"error": f"Failed to update tool: {str(e)[:300]}"}


async def _execute_dynamic_custom_tool(tool_name: str, args: dict, ctx: dict) -> dict:
    """Execute a user-created custom tool by making the configured HTTP request."""
    user_id = ctx.get("user_id", "")
    user_tools = await _load_user_custom_tools(user_id)
    tool_def = user_tools.get(tool_name)
    if not tool_def:
        return {"error": f"Custom tool '{tool_name}' not found or inactive"}

    method = tool_def.get("_http_method", "GET").upper()
    url_template = tool_def.get("_endpoint_url", "")
    body_template = tool_def.get("_request_body_template")

    # Parameter substitution in URL: replace {{param_name}} with actual values
    url = url_template
    for k, v in args.items():
        url = url.replace("{{" + k + "}}", str(v))

    # If URL is a relative path, prepend gateway URL
    if url.startswith("/"):
        url = f"{GATEWAY_URL}{url}"

    # Parameter substitution in body template
    req_body = None
    if body_template and isinstance(body_template, dict):
        import copy
        req_body = copy.deepcopy(body_template)
        _substitute_params(req_body, args)
    elif method in ("POST", "PUT", "PATCH") and args:
        # If no body template, send all args as JSON body
        req_body = {k: v for k, v in args.items()}

    headers = {
        "x-user-id": user_id,
        "x-org-id": ctx.get("org_id", ""),
        "x-user-role": ctx.get("user_role", "user"),
    }
    # Merge any custom headers from template
    extra_headers = tool_def.get("_headers_template", {})
    if isinstance(extra_headers, dict):
        headers.update(extra_headers)

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.request(
                method, url,
                json=req_body if req_body else None,
                params=args if method == "GET" and not req_body else None,
                headers=headers,
            )
            try:
                data = resp.json()
            except Exception:
                data = {"response_text": resp.text[:1000]}
            if resp.status_code >= 400:
                return {"error": f"HTTP {resp.status_code}", "details": data}
            return {"success": True, "data": data}
    except Exception as e:
        return {"error": f"Request failed: {str(e)[:300]}"}


def _substitute_params(obj: Any, params: dict):
    """Recursively replace {{param_name}} in a dict/list with actual values."""
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            v = obj[k]
            if isinstance(v, str):
                for pk, pv in params.items():
                    v = v.replace("{{" + pk + "}}", str(pv))
                obj[k] = v
            elif isinstance(v, (dict, list)):
                _substitute_params(v, params)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str):
                for pk, pv in params.items():
                    item = item.replace("{{" + pk + "}}", str(pv))
                obj[i] = item
            elif isinstance(item, (dict, list)):
                _substitute_params(item, params)


# ── Web Search Handler (Tavily) ──

async def _custom_web_search(args: dict, ctx: dict) -> dict:
    """Search the web using Tavily API with key rotation."""
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}

    max_results = min(int(args.get("max_results", 5)), 10)

    tavily_raw = os.getenv("TAVILY_API_KEY", "")
    tavily_keys = [k.strip() for k in tavily_raw.split(",") if k.strip()]
    if not tavily_keys:
        return {"error": "Web search API not configured."}

    data = None
    last_error = ""
    for key in tavily_keys:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post("https://api.tavily.com/search", json={
                    "api_key": key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "advanced",
                    "include_answer": True,
                    "include_raw_content": False,
                })
                if resp.status_code == 200:
                    data = resp.json()
                    break
                last_error = f"HTTP {resp.status_code}"
        except Exception as e:
            last_error = str(e)[:200]

    if data is None:
        return {"error": f"Web search failed: {last_error}"}

    results = []
    for item in data.get("results", []):
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", "")[:400],
            "published_date": item.get("published_date"),
        })

    out = {"query": query, "results": results, "count": len(results)}
    if data.get("answer"):
        out["ai_summary"] = data["answer"]
    return out


# ── Web Browsing / Scraping Handlers ──
_WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _extract_page_content(html: str, url: str, max_length: int = 15000, extract_links: bool = True) -> dict:
    """Extract clean structured content from HTML using BeautifulSoup."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # Fallback: basic regex strip
        import re
        from html import unescape
        text = re.sub(r"(?is)<(script|style|nav|footer|header).*?>.*?</\1>", " ", html)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return {"url": url, "title": "", "content": text[:max_length], "links": []}

    try:
        import lxml  # noqa: F401
        parser = "lxml"
    except ImportError:
        parser = "html.parser"
    soup = BeautifulSoup(html, parser)

    # Remove noise elements
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside",
                               "iframe", "noscript", "svg", "form"]):
        tag.decompose()

    # Extract title
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Extract main content (prefer article/main tags)
    main = soup.find("article") or soup.find("main") or soup.find(role="main") or soup.body or soup

    # Build structured text with headings
    parts = []
    for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th", "pre", "code", "blockquote"]):
        text = el.get_text(separator=" ", strip=True)
        if not text or len(text) < 3:
            continue
        tag = el.name
        if tag in ("h1", "h2", "h3", "h4"):
            level = int(tag[1])
            parts.append(f"\n{'#' * level} {text}\n")
        elif tag == "li":
            parts.append(f"  • {text}")
        elif tag == "blockquote":
            parts.append(f"> {text}")
        elif tag in ("pre", "code"):
            parts.append(f"```\n{text}\n```")
        else:
            parts.append(text)

    content = "\n".join(parts).strip()
    if not content:
        # Fallback to all text
        content = main.get_text(separator="\n", strip=True)

    # Extract links
    links = []
    if extract_links:
        for a in (main.find_all("a", href=True) if main else []):
            href = a["href"]
            link_text = a.get_text(strip=True)
            if href.startswith("http") and link_text and len(link_text) > 2:
                links.append({"text": link_text[:100], "url": href})
            if len(links) >= 20:
                break

    return {
        "url": url,
        "title": title,
        "content": content[:max_length],
        "content_length": len(content),
        "links": links,
    }


async def _fetch_and_extract(url: str, max_length: int = 15000, extract_links: bool = True) -> dict:
    """Fetch a URL and extract clean content. Used by both read_webpage and read_many_pages."""
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=_WEB_HEADERS) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                return {"url": url, "error": f"HTTP {resp.status_code}"}
            ct = (resp.headers.get("content-type") or "").lower()
            if "json" in ct:
                try:
                    return {"url": url, "content": json.dumps(resp.json(), indent=2)[:max_length], "content_type": "json"}
                except Exception:
                    pass
            html = resp.text
            if len(html) > 2_000_000:
                html = html[:2_000_000]
            return _extract_page_content(html, url, max_length, extract_links)
    except Exception as e:
        return {"url": url, "error": str(e)[:300]}


async def _custom_read_webpage(args: dict, ctx: dict) -> dict:
    """Read a single webpage and extract structured content."""
    url = (args.get("url") or "").strip()
    if not url:
        return {"error": "url is required"}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    max_length = int(args.get("max_length", 15000))
    extract_links = args.get("extract_links", True)
    if isinstance(extract_links, str):
        extract_links = extract_links.lower() not in ("false", "0", "no")

    return await _fetch_and_extract(url, max_length, extract_links)


async def _custom_read_many_pages(args: dict, ctx: dict) -> dict:
    """Read multiple web pages in parallel."""
    urls = args.get("urls", [])
    if isinstance(urls, str):
        # Try to parse as JSON array or comma-separated
        try:
            urls = json.loads(urls)
        except Exception:
            urls = [u.strip() for u in urls.split(",") if u.strip()]

    if not urls or not isinstance(urls, list):
        return {"error": "urls is required — provide a list of URLs"}
    if len(urls) > 5:
        urls = urls[:5]  # Cap at 5 to avoid abuse

    max_length = int(args.get("max_length_per_page", 8000))

    # Normalize URLs
    clean_urls = []
    for u in urls:
        u = str(u).strip()
        if not u.startswith(("http://", "https://")):
            u = "https://" + u
        clean_urls.append(u)

    # Fetch all in parallel
    import asyncio
    results = await asyncio.gather(*[_fetch_and_extract(u, max_length, False) for u in clean_urls])

    return {
        "pages": list(results),
        "total": len(results),
        "succeeded": sum(1 for r in results if "error" not in r),
        "failed": sum(1 for r in results if "error" in r),
    }


async def _custom_reddit_search(args: dict, ctx: dict) -> dict:
    """Search Reddit using Tavily API with include_domains filter."""
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}

    subreddit = (args.get("subreddit") or "").strip()
    limit = min(int(args.get("limit", 10)), 25)
    import re

    # Use Tavily API with include_domains=reddit.com
    tavily_raw = os.getenv("TAVILY_API_KEY", "")
    tavily_keys = [k.strip() for k in tavily_raw.split(",") if k.strip()]
    if not tavily_keys:
        return {"error": "Search API not configured. Contact platform admin."}

    search_query = f"{query} reddit"
    if subreddit:
        search_query = f"{query} r/{subreddit} reddit"

    data = None
    last_error = ""
    for key in tavily_keys:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post("https://api.tavily.com/search", json={
                    "api_key": key,
                    "query": search_query,
                    "max_results": limit,
                    "search_depth": "basic",
                    "include_domains": ["reddit.com"],
                    "include_answer": True,
                    "include_raw_content": False,
                })
                if resp.status_code == 200:
                    data = resp.json()
                    break
                last_error = f"HTTP {resp.status_code}"
        except Exception as e:
            last_error = str(e)[:200]

    if data is None:
        return {"error": f"All search API keys failed. Last error: {last_error}"}

    try:
        posts = []
        for item in data.get("results", []):
            url = item.get("url", "")
            title = item.get("title", "").replace(" : r/", " | r/").replace(" - Reddit", "").strip()
            snippet = item.get("content", "")[:500]
            sr_match = re.search(r"reddit\.com/r/(\w+)", url)
            sr_name = sr_match.group(1) if sr_match else ""
            posts.append({
                "title": title,
                "subreddit": sr_name,
                "url": url,
                "snippet": snippet,
            })

        result = {
            "query": query,
            "subreddit": subreddit or "all",
            "results": posts,
            "count": len(posts),
        }
        if data.get("answer"):
            result["ai_summary"] = data["answer"]
        return result

    except Exception as e:
        return {"error": f"Reddit search failed: {str(e)[:300]}"}


GITHUB_API = "https://api.github.com"
ED_SERVICE_URL = os.getenv("ED_SERVICE_URL", "http://ed_service:8000")


async def _custom_get_current_time(args: dict, ctx: dict) -> dict:
    """Get current date/time/timezone."""
    from datetime import datetime, timezone as tz
    try:
        import zoneinfo
        tzname = args.get("timezone", "UTC")
        try:
            zi = zoneinfo.ZoneInfo(tzname)
        except Exception:
            zi = tz.utc
            tzname = "UTC"
        now = datetime.now(zi)
    except ImportError:
        now = datetime.now(tz.utc)
        tzname = "UTC"
    return {
        "datetime": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "day_of_week": now.strftime("%A"),
        "timezone": tzname,
        "unix_timestamp": int(now.timestamp()),
    }


async def _custom_get_system_info(args: dict, ctx: dict) -> dict:
    """Get platform system info."""
    return {
        "platform": "Resonant Genesis",
        "version": "2026.3",
        "tools_available": len(TOOL_DEFS),
        "tool_categories": list(set(t.get("category", "") for t in TOOL_DEFS.values())),
        "user_id": ctx.get("user_id", "anonymous"),
        "user_role": ctx.get("user_role", "user"),
        "is_superuser": ctx.get("is_superuser", False),
    }


async def _custom_github_create_repo(args: dict, ctx: dict) -> dict:
    """Create a GitHub repository."""
    token = await _fetch_user_key(ctx.get("user_id", ""), "github")
    if not token:
        return {"error": "No GitHub token configured. Add your GitHub Personal Access Token in Settings > API Keys."}
    name = args.get("name", "").strip()
    if not name:
        return {"error": "Missing 'name' parameter"}
    payload = {"name": name, "auto_init": True}
    if args.get("description"):
        payload["description"] = args["description"]
    if args.get("private"):
        payload["private"] = True
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{GITHUB_API}/user/repos", json=payload, headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
            if resp.status_code not in (200, 201):
                return {"error": f"GitHub API {resp.status_code}: {resp.text[:300]}"}
            data = resp.json()
            return {"success": True, "full_name": data.get("full_name"), "url": data.get("html_url"), "clone_url": data.get("clone_url"), "private": data.get("private")}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_github_list_repos(args: dict, ctx: dict) -> dict:
    """List GitHub repos."""
    token = await _fetch_user_key(ctx.get("user_id", ""), "github")
    if not token:
        return {"error": "No GitHub token configured."}
    owner = args.get("owner", "")
    limit = int(args.get("limit", 30))
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            url = f"{GITHUB_API}/users/{owner}/repos?per_page={limit}&sort=updated" if owner else f"{GITHUB_API}/user/repos?per_page={limit}&sort=updated"
            resp = await client.get(url, headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
            if resp.status_code != 200:
                return {"error": f"GitHub API {resp.status_code}: {resp.text[:300]}"}
            repos = resp.json()
            return {"repos": [{"name": r.get("name"), "full_name": r.get("full_name"), "url": r.get("html_url"), "private": r.get("private"), "language": r.get("language"), "updated_at": r.get("updated_at")} for r in repos[:limit]], "count": len(repos)}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_github_list_files(args: dict, ctx: dict) -> dict:
    """List files in a GitHub repo directory."""
    token = await _fetch_user_key(ctx.get("user_id", ""), "github")
    if not token:
        return {"error": "No GitHub token configured."}
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    path = args.get("path", "")
    ref = args.get("ref", "main")
    if not owner or not repo:
        return {"error": "Both 'owner' and 'repo' are required"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}?ref={ref}", headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
            if resp.status_code != 200:
                return {"error": f"GitHub API {resp.status_code}: {resp.text[:300]}"}
            items = resp.json()
            if isinstance(items, list):
                return {"files": [{"name": i.get("name"), "type": i.get("type"), "size": i.get("size"), "path": i.get("path")} for i in items]}
            return {"file": {"name": items.get("name"), "type": items.get("type"), "size": items.get("size"), "path": items.get("path")}}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_github_download_file(args: dict, ctx: dict) -> dict:
    """Download/read a file from GitHub."""
    token = await _fetch_user_key(ctx.get("user_id", ""), "github")
    if not token:
        return {"error": "No GitHub token configured."}
    owner, repo, path = args.get("owner", ""), args.get("repo", ""), args.get("path", "")
    ref = args.get("ref", "main")
    if not owner or not repo or not path:
        return {"error": "'owner', 'repo', and 'path' are required"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}?ref={ref}", headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
            if resp.status_code != 200:
                return {"error": f"GitHub API {resp.status_code}: {resp.text[:300]}"}
            data = resp.json()
            import base64
            content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace") if data.get("encoding") == "base64" else data.get("content", "")
            return {"path": path, "sha": data.get("sha"), "size": data.get("size"), "content": content[:10000]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_github_upload_file(args: dict, ctx: dict) -> dict:
    """Upload/create/update a file on GitHub."""
    token = await _fetch_user_key(ctx.get("user_id", ""), "github")
    if not token:
        return {"error": "No GitHub token configured."}
    owner, repo, path = args.get("owner", ""), args.get("repo", ""), args.get("path", "")
    content = args.get("content", "")
    message = args.get("message", "Update file")
    branch = args.get("branch", "main")
    sha = args.get("sha")
    if not owner or not repo or not path or not content:
        return {"error": "'owner', 'repo', 'path', and 'content' are required"}
    import base64
    payload = {"message": message, "content": base64.b64encode(content.encode()).decode(), "branch": branch}
    if sha:
        payload["sha"] = sha
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.put(f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}", json=payload, headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
            if resp.status_code not in (200, 201):
                return {"error": f"GitHub API {resp.status_code}: {resp.text[:300]}"}
            data = resp.json()
            return {"success": True, "path": path, "sha": data.get("content", {}).get("sha"), "commit_sha": data.get("commit", {}).get("sha")}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_github_commits(args: dict, ctx: dict) -> dict:
    """Get commits from a GitHub repo."""
    token = await _fetch_user_key(ctx.get("user_id", ""), "github")
    if not token:
        return {"error": "No GitHub token configured."}
    owner, repo = args.get("owner", ""), args.get("repo", "")
    sha = args.get("sha", "")
    limit = int(args.get("limit", 10))
    if not owner or not repo:
        return {"error": "'owner' and 'repo' are required"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if sha:
                resp = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}/commits/{sha}", headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
                if resp.status_code != 200:
                    return {"error": f"GitHub API {resp.status_code}: {resp.text[:300]}"}
                c = resp.json()
                return {"commit": {"sha": c.get("sha"), "message": c.get("commit", {}).get("message"), "author": c.get("commit", {}).get("author", {}).get("name"), "date": c.get("commit", {}).get("author", {}).get("date"), "files_changed": len(c.get("files", []))}}
            else:
                resp = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}/commits?per_page={limit}", headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
                if resp.status_code != 200:
                    return {"error": f"GitHub API {resp.status_code}: {resp.text[:300]}"}
                commits = resp.json()
                return {"commits": [{"sha": c.get("sha")[:8], "message": c.get("commit", {}).get("message", "")[:100], "author": c.get("commit", {}).get("author", {}).get("name"), "date": c.get("commit", {}).get("author", {}).get("date")} for c in commits[:limit]]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_github_pull_request(args: dict, ctx: dict) -> dict:
    """Create or list pull requests."""
    token = await _fetch_user_key(ctx.get("user_id", ""), "github")
    if not token:
        return {"error": "No GitHub token configured."}
    owner, repo = args.get("owner", ""), args.get("repo", "")
    action = args.get("action", "list")
    if not owner or not repo:
        return {"error": "'owner' and 'repo' are required"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if action == "create":
                payload = {"title": args.get("title", ""), "body": args.get("body", ""), "head": args.get("head", ""), "base": args.get("base", "main")}
                resp = await client.post(f"{GITHUB_API}/repos/{owner}/{repo}/pulls", json=payload, headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
                if resp.status_code not in (200, 201):
                    return {"error": f"GitHub API {resp.status_code}: {resp.text[:300]}"}
                pr = resp.json()
                return {"success": True, "number": pr.get("number"), "url": pr.get("html_url"), "title": pr.get("title")}
            else:
                state = args.get("state", "open")
                resp = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}/pulls?state={state}&per_page=20", headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
                if resp.status_code != 200:
                    return {"error": f"GitHub API {resp.status_code}: {resp.text[:300]}"}
                prs = resp.json()
                return {"pull_requests": [{"number": p.get("number"), "title": p.get("title"), "state": p.get("state"), "user": p.get("user", {}).get("login"), "url": p.get("html_url")} for p in prs[:20]]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_github_issue(args: dict, ctx: dict) -> dict:
    """Create or list issues."""
    token = await _fetch_user_key(ctx.get("user_id", ""), "github")
    if not token:
        return {"error": "No GitHub token configured."}
    owner, repo = args.get("owner", ""), args.get("repo", "")
    action = args.get("action", "list")
    if not owner or not repo:
        return {"error": "'owner' and 'repo' are required"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if action == "create":
                payload = {"title": args.get("title", ""), "body": args.get("body", "")}
                labels = args.get("labels", "")
                if labels:
                    payload["labels"] = [l.strip() for l in labels.split(",") if l.strip()]
                resp = await client.post(f"{GITHUB_API}/repos/{owner}/{repo}/issues", json=payload, headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
                if resp.status_code not in (200, 201):
                    return {"error": f"GitHub API {resp.status_code}: {resp.text[:300]}"}
                issue = resp.json()
                return {"success": True, "number": issue.get("number"), "url": issue.get("html_url"), "title": issue.get("title")}
            else:
                state = args.get("state", "open")
                resp = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}/issues?state={state}&per_page=20", headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
                if resp.status_code != 200:
                    return {"error": f"GitHub API {resp.status_code}: {resp.text[:300]}"}
                issues = resp.json()
                return {"issues": [{"number": i.get("number"), "title": i.get("title"), "state": i.get("state"), "user": i.get("user", {}).get("login"), "labels": [l.get("name") for l in i.get("labels", [])]} for i in issues[:20]]}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_github_comment(args: dict, ctx: dict) -> dict:
    """Comment on a GitHub issue/PR."""
    token = await _fetch_user_key(ctx.get("user_id", ""), "github")
    if not token:
        return {"error": "No GitHub token configured."}
    owner, repo = args.get("owner", ""), args.get("repo", "")
    issue_number = args.get("issue_number")
    body = args.get("body", "")
    if not owner or not repo or not issue_number or not body:
        return {"error": "'owner', 'repo', 'issue_number', and 'body' are required"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}/comments", json={"body": body}, headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
            if resp.status_code not in (200, 201):
                return {"error": f"GitHub API {resp.status_code}: {resp.text[:300]}"}
            return {"success": True, "url": resp.json().get("html_url")}
    except Exception as e:
        return {"error": str(e)[:300]}


async def _custom_git_proxy(args: dict, ctx: dict) -> dict:
    """Proxy git operations to ed_service."""
    # Determine which git tool was called based on args context
    tool_name = args.pop("_tool_name", "git_status")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{ED_SERVICE_URL}/tools/execute",
                json={"tool": tool_name, "args": args},
                headers={"x-user-id": ctx.get("user_id", ""), "x-user-role": ctx.get("user_role", "user")},
            )
            if resp.status_code != 200:
                return {"error": f"ED service {resp.status_code}: {resp.text[:300]}"}
            return resp.json()
    except Exception as e:
        return {"info": f"Git operation '{tool_name}' — ed_service not available: {str(e)[:200]}. Use GitHub API tools instead."}


async def _fetch_user_key(user_id: str, provider: str) -> str:
    """Fetch a user's API key from auth_service (same as executor._get_user_api_key)."""
    if not user_id or user_id == "anonymous":
        return ""
    import os as _os
    auth_url = _os.getenv("AUTH_URL", _os.getenv("AUTH_SERVICE_URL", "http://auth_service:8000"))
    internal_key = _os.getenv("AUTH_INTERNAL_SERVICE_KEY") or _os.getenv("INTERNAL_SERVICE_KEY", "")
    headers = {}
    if internal_key:
        headers["x-internal-service-key"] = internal_key
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{auth_url}/auth/internal/user-api-keys/{user_id}?provider={provider}",
                headers=headers,
            )
            if resp.status_code == 200:
                for entry in resp.json().get("keys", []):
                    if entry.get("provider") == provider and entry.get("api_key"):
                        return entry["api_key"]
    except Exception as e:
        logger.warning(f"Failed to fetch {provider} key for {user_id}: {e}")
    return ""


RABBIT_SERVICE_URL = os.getenv("RABBIT_SERVICE_URL", os.getenv("GATEWAY_RABBIT_URL", "http://rabbit_api_service:8000"))


async def _custom_rabbit_create_community(args: dict, ctx: dict) -> dict:
    """Create a new Rabbit community."""
    slug = (args.get("slug") or "").strip()
    name = (args.get("name") or "").strip()
    description = args.get("description") or ""
    if not slug or not name:
        return {"error": "Both 'slug' (url-friendly id) and 'name' are required."}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{RABBIT_SERVICE_URL}/rabbit/communities",
                json={"slug": slug, "name": name, "description": description},
                headers={"x-user-id": ctx.get("user_id", "anonymous")},
            )
            if resp.status_code in (200, 201):
                return resp.json()
            return {"error": f"Failed ({resp.status_code}): {resp.text[:500]}"}
    except Exception as e:
        return {"error": str(e)[:500]}


async def _custom_rabbit_list_communities(args: dict, ctx: dict) -> dict:
    """List all Rabbit communities."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{RABBIT_SERVICE_URL}/rabbit/communities",
                headers={"x-user-id": ctx.get("user_id", "anonymous")},
            )
            return resp.json() if resp.status_code == 200 else {"error": resp.text[:500]}
    except Exception as e:
        return {"error": str(e)[:500]}


async def _custom_rabbit_get_community(args: dict, ctx: dict) -> dict:
    """Get a community by slug."""
    slug = (args.get("slug") or args.get("community_slug") or "").strip()
    if not slug:
        return {"error": "Missing 'slug' parameter."}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{RABBIT_SERVICE_URL}/rabbit/communities/{slug}",
                headers={"x-user-id": ctx.get("user_id", "anonymous")},
            )
            return resp.json() if resp.status_code == 200 else {"error": resp.text[:500]}
    except Exception as e:
        return {"error": str(e)[:500]}


async def _custom_rabbit_create_post(args: dict, ctx: dict) -> dict:
    """Create a post in a Rabbit community."""
    title = (args.get("title") or "").strip()
    body = args.get("body") or ""
    community_slug = (args.get("community_slug") or args.get("slug") or "").strip()
    image_url = args.get("image_url")
    if not title:
        return {"error": "Missing 'title' parameter."}
    payload = {"title": title, "body": body, "community_slug": community_slug}
    if image_url:
        payload["image_url"] = image_url
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{RABBIT_SERVICE_URL}/rabbit/posts",
                json=payload,
                headers={"x-user-id": ctx.get("user_id", "anonymous")},
            )
            if resp.status_code in (200, 201):
                return resp.json()
            return {"error": f"Failed ({resp.status_code}): {resp.text[:500]}"}
    except Exception as e:
        return {"error": str(e)[:500]}


async def _custom_rabbit_list_posts(args: dict, ctx: dict) -> dict:
    """List posts — global feed or filtered by community slug."""
    slug = (args.get("community_slug") or args.get("slug") or "").strip()
    limit = int(args.get("limit", 20))
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if slug:
                url = f"{RABBIT_SERVICE_URL}/rabbit/communities/{slug}/posts"
            else:
                url = f"{RABBIT_SERVICE_URL}/rabbit/posts"
            resp = await client.get(url, params={"limit": limit},
                                    headers={"x-user-id": ctx.get("user_id", "anonymous")})
            return resp.json() if resp.status_code == 200 else {"error": resp.text[:500]}
    except Exception as e:
        return {"error": str(e)[:500]}


async def _custom_rabbit_search_posts(args: dict, ctx: dict) -> dict:
    """Search Rabbit posts by keyword."""
    q = (args.get("query") or args.get("q") or "").strip()
    if not q:
        return {"error": "Missing 'query' parameter."}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{RABBIT_SERVICE_URL}/rabbit/posts/search",
                params={"q": q, "limit": int(args.get("limit", 20))},
                headers={"x-user-id": ctx.get("user_id", "anonymous")},
            )
            return resp.json() if resp.status_code == 200 else {"error": resp.text[:500]}
    except Exception as e:
        return {"error": str(e)[:500]}


async def _custom_rabbit_get_post(args: dict, ctx: dict) -> dict:
    """Get a specific post by ID."""
    post_id = args.get("post_id") or args.get("id")
    if not post_id:
        return {"error": "Missing 'post_id' parameter."}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{RABBIT_SERVICE_URL}/rabbit/posts/{post_id}",
                headers={"x-user-id": ctx.get("user_id", "anonymous")},
            )
            return resp.json() if resp.status_code == 200 else {"error": resp.text[:500]}
    except Exception as e:
        return {"error": str(e)[:500]}


async def _custom_rabbit_delete_post(args: dict, ctx: dict) -> dict:
    """Delete a post (soft delete, owner only)."""
    post_id = args.get("post_id") or args.get("id")
    if not post_id:
        return {"error": "Missing 'post_id' parameter."}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(
                f"{RABBIT_SERVICE_URL}/rabbit/posts/{post_id}",
                headers={"x-user-id": ctx.get("user_id", "anonymous")},
            )
            return resp.json() if resp.status_code == 200 else {"error": resp.text[:500]}
    except Exception as e:
        return {"error": str(e)[:500]}


async def _custom_rabbit_create_comment(args: dict, ctx: dict) -> dict:
    """Create a comment on a post."""
    post_id = args.get("post_id")
    body = (args.get("body") or args.get("text") or args.get("content") or "").strip()
    parent_comment_id = args.get("parent_comment_id")
    if not post_id or not body:
        return {"error": "Both 'post_id' and 'body' are required."}
    payload = {"body": body}
    if parent_comment_id:
        payload["parent_comment_id"] = int(parent_comment_id)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{RABBIT_SERVICE_URL}/rabbit/posts/{post_id}/comments",
                json=payload,
                headers={"x-user-id": ctx.get("user_id", "anonymous")},
            )
            if resp.status_code in (200, 201):
                return resp.json()
            return {"error": f"Failed ({resp.status_code}): {resp.text[:500]}"}
    except Exception as e:
        return {"error": str(e)[:500]}


async def _custom_rabbit_list_comments(args: dict, ctx: dict) -> dict:
    """List comments on a post."""
    post_id = args.get("post_id")
    if not post_id:
        return {"error": "Missing 'post_id' parameter."}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{RABBIT_SERVICE_URL}/rabbit/posts/{post_id}/comments",
                headers={"x-user-id": ctx.get("user_id", "anonymous")},
            )
            return resp.json() if resp.status_code == 200 else {"error": resp.text[:500]}
    except Exception as e:
        return {"error": str(e)[:500]}


async def _custom_rabbit_delete_comment(args: dict, ctx: dict) -> dict:
    """Delete a comment (owner only)."""
    comment_id = args.get("comment_id") or args.get("id")
    if not comment_id:
        return {"error": "Missing 'comment_id' parameter."}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(
                f"{RABBIT_SERVICE_URL}/rabbit/comments/{comment_id}",
                headers={"x-user-id": ctx.get("user_id", "anonymous")},
            )
            return resp.json() if resp.status_code == 200 else {"error": resp.text[:500]}
    except Exception as e:
        return {"error": str(e)[:500]}


async def _custom_rabbit_vote(args: dict, ctx: dict) -> dict:
    """Upvote or downvote a post or comment."""
    target_type = (args.get("target_type") or "post").strip()
    target_id = args.get("target_id") or args.get("post_id") or args.get("comment_id")
    value = args.get("value", 1)
    if not target_id:
        return {"error": "Missing 'target_id' (the post or comment ID)."}
    if target_type not in ("post", "comment"):
        return {"error": "target_type must be 'post' or 'comment'."}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.put(
                f"{RABBIT_SERVICE_URL}/rabbit/votes",
                json={"target_type": target_type, "target_id": int(target_id), "value": int(value)},
                headers={"x-user-id": ctx.get("user_id", "anonymous")},
            )
            return resp.json() if resp.status_code == 200 else {"error": resp.text[:500]}
    except Exception as e:
        return {"error": str(e)[:500]}


# ── Weather Handler ──

async def _custom_weather(args: dict, ctx: dict) -> dict:
    """Get weather for any location using wttr.in (free, no API key)."""
    location = (args.get("location") or "").strip()
    if not location:
        return {"error": "location is required"}
    units = (args.get("units") or "metric").strip().lower()
    unit_param = "m" if units == "metric" else "u"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://wttr.in/{location}",
                params={"format": "j1"},
                headers={"User-Agent": "ResonantGenesis/1.0"},
            )
            if resp.status_code != 200:
                return {"error": f"Weather service returned {resp.status_code}"}
            data = resp.json()
            current = data.get("current_condition", [{}])[0]
            area = data.get("nearest_area", [{}])[0]
            forecast = data.get("weather", [])
            city = area.get("areaName", [{}])[0].get("value", location)
            country = area.get("country", [{}])[0].get("value", "")
            result = {
                "location": f"{city}, {country}",
                "current": {
                    "temp_c": current.get("temp_C"),
                    "temp_f": current.get("temp_F"),
                    "feels_like_c": current.get("FeelsLikeC"),
                    "condition": current.get("weatherDesc", [{}])[0].get("value", ""),
                    "humidity": current.get("humidity"),
                    "wind_kmph": current.get("windspeedKmph"),
                    "wind_dir": current.get("winddir16Point"),
                    "uv_index": current.get("uvIndex"),
                    "visibility_km": current.get("visibility"),
                    "cloud_cover": current.get("cloudcover"),
                },
                "forecast": [],
            }
            for day in forecast[:3]:
                result["forecast"].append({
                    "date": day.get("date"),
                    "max_c": day.get("maxtempC"),
                    "min_c": day.get("mintempC"),
                    "max_f": day.get("maxtempF"),
                    "min_f": day.get("mintempF"),
                    "condition": day.get("hourly", [{}])[4].get("weatherDesc", [{}])[0].get("value", "") if day.get("hourly") else "",
                    "chance_of_rain": day.get("hourly", [{}])[4].get("chanceofrain", "") if day.get("hourly") else "",
                })
            return result
    except Exception as e:
        return {"error": f"Weather lookup failed: {str(e)[:300]}"}


# ── Image Search Handler (SerpAPI) ──

async def _custom_image_search(args: dict, ctx: dict) -> dict:
    """Search for images using SerpAPI Google Images."""
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    limit = min(int(args.get("limit", 8)), 20)
    serpapi_key = os.getenv("SERPAPI_KEY", "")
    if not serpapi_key:
        return {"error": "Image search not configured."}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get("https://serpapi.com/search.json", params={
                "engine": "google_images",
                "q": query,
                "num": limit,
                "api_key": serpapi_key,
            })
            if resp.status_code != 200:
                return {"error": f"Image search failed: HTTP {resp.status_code}"}
            data = resp.json()
            images = []
            for img in data.get("images_results", [])[:limit]:
                images.append({
                    "title": img.get("title", ""),
                    "url": img.get("original", img.get("link", "")),
                    "thumbnail": img.get("thumbnail", ""),
                    "source": img.get("source", ""),
                    "width": img.get("original_width"),
                    "height": img.get("original_height"),
                })
            return {"query": query, "images": images, "count": len(images)}
    except Exception as e:
        return {"error": f"Image search failed: {str(e)[:300]}"}


# ── News Search Handler (Tavily news mode) ──

async def _custom_news_search(args: dict, ctx: dict) -> dict:
    """Search latest news using Tavily API with topic=news."""
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    max_results = min(int(args.get("max_results", 5)), 10)
    tavily_raw = os.getenv("TAVILY_API_KEY", "")
    tavily_keys = [k.strip() for k in tavily_raw.split(",") if k.strip()]
    if not tavily_keys:
        return {"error": "News search not configured."}
    for key in tavily_keys:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post("https://api.tavily.com/search", json={
                    "api_key": key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "advanced",
                    "topic": "news",
                    "include_answer": True,
                })
                if resp.status_code == 200:
                    data = resp.json()
                    articles = []
                    for r in data.get("results", [])[:max_results]:
                        articles.append({
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "snippet": r.get("content", "")[:400],
                            "published_date": r.get("published_date"),
                            "source": r.get("url", "").split("/")[2] if "/" in r.get("url", "") else "",
                        })
                    out = {"query": query, "articles": articles, "count": len(articles)}
                    if data.get("answer"):
                        out["ai_summary"] = data["answer"]
                    return out
        except Exception as e:
            logger.warning(f"[NEWS] Tavily key failed: {e}")
    return {"error": "News search temporarily unavailable."}


# ── Places Search Handler (SerpAPI Google Maps) ──

async def _custom_places_search(args: dict, ctx: dict) -> dict:
    """Search for businesses, restaurants, locations using SerpAPI Google Maps."""
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    location = (args.get("location") or "").strip()
    limit = min(int(args.get("limit", 5)), 20)
    serpapi_key = os.getenv("SERPAPI_KEY", "")
    if not serpapi_key:
        return {"error": "Places search not configured."}
    try:
        params = {
            "engine": "google_maps",
            "q": query,
            "api_key": serpapi_key,
            "type": "search",
        }
        if location:
            params["ll"] = ""
            params["q"] = f"{query} in {location}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get("https://serpapi.com/search.json", params=params)
            if resp.status_code != 200:
                return {"error": f"Places search failed: HTTP {resp.status_code}"}
            data = resp.json()
            places = []
            for p in data.get("local_results", [])[:limit]:
                places.append({
                    "name": p.get("title", ""),
                    "address": p.get("address", ""),
                    "rating": p.get("rating"),
                    "reviews": p.get("reviews"),
                    "phone": p.get("phone", ""),
                    "type": p.get("type", ""),
                    "hours": p.get("hours", ""),
                    "website": p.get("website", ""),
                    "gps": p.get("gps_coordinates", {}),
                    "thumbnail": p.get("thumbnail", ""),
                })
            return {"query": query, "location": location or "auto", "places": places, "count": len(places)}
    except Exception as e:
        return {"error": f"Places search failed: {str(e)[:300]}"}


# ── YouTube Search Handler (SerpAPI) ──

async def _custom_youtube_search(args: dict, ctx: dict) -> dict:
    """Search YouTube videos using SerpAPI."""
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    limit = min(int(args.get("limit", 5)), 15)
    serpapi_key = os.getenv("SERPAPI_KEY", "")
    if not serpapi_key:
        return {"error": "YouTube search not configured."}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get("https://serpapi.com/search.json", params={
                "engine": "youtube",
                "search_query": query,
                "api_key": serpapi_key,
            })
            if resp.status_code != 200:
                return {"error": f"YouTube search failed: HTTP {resp.status_code}"}
            data = resp.json()
            videos = []
            for v in data.get("video_results", [])[:limit]:
                videos.append({
                    "title": v.get("title", ""),
                    "url": v.get("link", ""),
                    "channel": v.get("channel", {}).get("name", ""),
                    "views": v.get("views"),
                    "published": v.get("published_date", ""),
                    "duration": v.get("length", ""),
                    "thumbnail": v.get("thumbnail", {}).get("static", ""),
                    "description": v.get("description", "")[:200],
                })
            return {"query": query, "videos": videos, "count": len(videos)}
    except Exception as e:
        return {"error": f"YouTube search failed: {str(e)[:300]}"}


# ── Stock/Crypto Handler (Yahoo Finance via public endpoint) ──

async def _custom_stock_crypto(args: dict, ctx: dict) -> dict:
    """Get stock or crypto prices using Yahoo Finance public API."""
    symbol = (args.get("symbol") or "").strip().upper()
    if not symbol:
        return {"error": "symbol is required (e.g. AAPL, BTC-USD, ETH-USD, TSLA)"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"interval": "1d", "range": "5d"},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code != 200:
                return {"error": f"Could not find symbol '{symbol}'. Try formats like AAPL, MSFT, BTC-USD, ETH-USD."}
            data = resp.json()
            meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
            indicators = data.get("chart", {}).get("result", [{}])[0].get("indicators", {})
            closes = indicators.get("quote", [{}])[0].get("close", [])
            timestamps = data.get("chart", {}).get("result", [{}])[0].get("timestamp", [])
            price = meta.get("regularMarketPrice", 0)
            prev_close = meta.get("previousClose") or meta.get("chartPreviousClose", 0)
            change = round(price - prev_close, 2) if prev_close else 0
            change_pct = round((change / prev_close) * 100, 2) if prev_close else 0
            history = []
            for i, ts in enumerate(timestamps[-5:]):
                if i < len(closes) and closes[-(5-i)] is not None:
                    from datetime import datetime
                    history.append({
                        "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
                        "close": round(closes[-(5-i)], 2),
                    })
            return {
                "symbol": symbol,
                "name": meta.get("shortName", meta.get("symbol", symbol)),
                "currency": meta.get("currency", "USD"),
                "exchange": meta.get("exchangeName", ""),
                "price": round(price, 2),
                "previous_close": round(prev_close, 2) if prev_close else None,
                "change": change,
                "change_percent": change_pct,
                "market_state": meta.get("marketState", ""),
                "day_high": meta.get("regularMarketDayHigh"),
                "day_low": meta.get("regularMarketDayLow"),
                "52w_high": meta.get("fiftyTwoWeekHigh"),
                "52w_low": meta.get("fiftyTwoWeekLow"),
                "history_5d": history,
            }
    except Exception as e:
        return {"error": f"Stock/crypto lookup failed: {str(e)[:300]}"}


# ── Deep Research Handler (Perplexity API) ──

async def _custom_deep_research(args: dict, ctx: dict) -> dict:
    """Perform deep multi-source research using Perplexity API."""
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    detail = (args.get("detail") or "detailed").strip()
    pplx_key = os.getenv("PERPLEXITY_API_KEY", "")
    if not pplx_key:
        return {"error": "Deep research not configured."}
    try:
        system_msg = "You are a research assistant. Provide comprehensive, well-sourced answers with citations. Be thorough and detailed."
        if detail == "brief":
            system_msg = "You are a research assistant. Provide concise, well-sourced answers with key citations."
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                json={
                    "model": "sonar",
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": query},
                    ],
                    "max_tokens": 4000,
                },
                headers={
                    "Authorization": f"Bearer {pplx_key}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code != 200:
                return {"error": f"Research API error: HTTP {resp.status_code}: {resp.text[:300]}"}
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            citations = data.get("citations", [])
            return {
                "query": query,
                "research": content,
                "citations": citations[:20],
                "model": data.get("model", "sonar"),
                "tokens_used": data.get("usage", {}).get("total_tokens", 0),
            }
    except Exception as e:
        return {"error": f"Deep research failed: {str(e)[:300]}"}


# ── Chart Generation Handler (QuickChart.io) ──

async def _custom_generate_chart(args: dict, ctx: dict) -> dict:
    """Generate a chart image URL using QuickChart.io (free)."""
    chart_type = (args.get("type") or "bar").strip().lower()
    labels = args.get("labels", [])
    datasets = args.get("datasets", [])
    title = (args.get("title") or "").strip()

    if not labels or not datasets:
        return {"error": "Both 'labels' (array of strings) and 'datasets' (array of {{label, data}}) are required. Example: labels=['Jan','Feb'], datasets=[{{label:'Sales', data:[10,20]}}]"}

    chart_config = {
        "type": chart_type,
        "data": {
            "labels": labels,
            "datasets": datasets,
        },
    }
    if title:
        chart_config["options"] = {"plugins": {"title": {"display": True, "text": title}}}
    try:
        import json as _json
        chart_json = _json.dumps(chart_config)
        import urllib.parse
        encoded = urllib.parse.quote(chart_json)
        chart_url = f"https://quickchart.io/chart?c={encoded}&w=600&h=400&bkg=white"
        if len(chart_url) > 8000:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post("https://quickchart.io/chart/create", json={
                    "chart": chart_config,
                    "width": 600,
                    "height": 400,
                    "backgroundColor": "white",
                })
                if resp.status_code == 200:
                    chart_url = resp.json().get("url", chart_url)
        return {
            "chart_url": chart_url,
            "type": chart_type,
            "title": title,
            "note": "Open the chart_url to view the chart image. You can embed it in markdown: ![chart](url)",
        }
    except Exception as e:
        return {"error": f"Chart generation failed: {str(e)[:300]}"}


# ── Send Email Handler (SendGrid) ──

async def _custom_send_email(args: dict, ctx: dict) -> dict:
    """Send an email using SendGrid API."""
    to_email = (args.get("to") or "").strip()
    subject = (args.get("subject") or "").strip()
    body = (args.get("body") or "").strip()
    if not to_email or not subject or not body:
        return {"error": "to, subject, and body are all required"}
    sendgrid_key = os.getenv("AUTH_SENDGRID_API_KEY", "")
    if not sendgrid_key:
        return {"error": "Email sending not configured."}
    from_email = os.getenv("AUTH_SMTP_USER", "noreply@resonantgenesis.com")
    user_id = ctx.get("user_id", "anonymous")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                json={
                    "personalizations": [{"to": [{"email": to_email}]}],
                    "from": {"email": from_email, "name": "Resonant Assistant"},
                    "subject": subject,
                    "content": [{"type": "text/html", "value": body}],
                },
                headers={
                    "Authorization": f"Bearer {sendgrid_key}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code in (200, 201, 202):
                return {"success": True, "to": to_email, "subject": subject, "message": "Email sent successfully!"}
            return {"error": f"SendGrid error: HTTP {resp.status_code}: {resp.text[:300]}"}
    except Exception as e:
        return {"error": f"Email send failed: {str(e)[:300]}"}


# ── Wikipedia Handler ──

async def _custom_wikipedia(args: dict, ctx: dict) -> dict:
    """Search and read Wikipedia articles."""
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    action = (args.get("action") or "summary").strip()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if action == "search":
                resp = await client.get("https://en.wikipedia.org/w/api.php", params={
                    "action": "opensearch",
                    "search": query,
                    "limit": 10,
                    "format": "json",
                })
                if resp.status_code == 200:
                    data = resp.json()
                    results = []
                    titles = data[1] if len(data) > 1 else []
                    descs = data[2] if len(data) > 2 else []
                    urls = data[3] if len(data) > 3 else []
                    for i, t in enumerate(titles):
                        results.append({
                            "title": t,
                            "description": descs[i] if i < len(descs) else "",
                            "url": urls[i] if i < len(urls) else "",
                        })
                    return {"query": query, "results": results, "count": len(results)}
            else:
                resp = await client.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{query.replace(' ', '_')}")
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "title": data.get("title", ""),
                        "extract": data.get("extract", ""),
                        "url": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
                        "thumbnail": data.get("thumbnail", {}).get("source", ""),
                        "description": data.get("description", ""),
                    }
                elif resp.status_code == 404:
                    search_resp = await client.get("https://en.wikipedia.org/w/api.php", params={
                        "action": "opensearch", "search": query, "limit": 5, "format": "json",
                    })
                    if search_resp.status_code == 200:
                        suggestions = search_resp.json()[1] if len(search_resp.json()) > 1 else []
                        return {"error": f"Article '{query}' not found.", "suggestions": suggestions}
                    return {"error": f"Article '{query}' not found."}
            return {"error": f"Wikipedia API error: HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": f"Wikipedia lookup failed: {str(e)[:300]}"}


# ── Visualize Handler (SVG diagram generation via LLM) ──

_SVG_SYSTEM_PROMPT = """You are an SVG diagram generator. You ONLY output valid SVG markup — no explanation, no markdown, no code fences.

Rules:
- Output ONLY the <svg>...</svg> element. Nothing else.
- Use viewBox for responsive sizing, e.g. viewBox="0 0 800 500"
- Use clean, modern design: rounded rects, soft colors, clear labels
- Color palette: use professional colors (#3b82f6 blue, #10b981 green, #f59e0b amber, #ef4444 red, #8b5cf6 purple, #06b6d4 cyan, #64748b slate)
- Text: font-family="system-ui, -apple-system, sans-serif", white or dark text for contrast
- For flowcharts: use rounded rectangles connected by lines/arrows with arrowhead markers
- For architecture: use layered boxes with labeled connections
- For sequence diagrams: use vertical lifelines with horizontal arrows
- For mindmaps: use centered topic with radiating branches
- For comparisons: use side-by-side columns or tables
- Always include a <defs> section for arrow markers if using arrows
- Max width 800px, max height 600px via viewBox
- Make text readable (min 12px equivalent)"""

async def _custom_visualize(args: dict, ctx: dict) -> dict:
    """Generate an SVG diagram using LLM."""
    description = (args.get("description") or args.get("prompt") or "").strip()
    diagram_type = (args.get("type") or "auto").strip().lower()
    if not description:
        return {"error": "description is required — describe what you want to visualize"}

    groq_key = os.getenv("GROQ_API_KEY", "").split(",")[0].strip()
    if not groq_key:
        return {"error": "Visualization service not configured."}

    user_prompt = f"Generate an SVG {diagram_type} diagram for: {description}"
    if diagram_type == "auto":
        user_prompt = f"Generate an SVG diagram (choose the best type — flowchart, architecture, mindmap, sequence, comparison, etc.) for: {description}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": _SVG_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4096,
                },
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                return {"error": f"SVG generation failed: HTTP {resp.status_code}"}
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Extract SVG from response (strip any markdown/text wrapping)
            import re as _re
            svg_match = _re.search(r'(<svg[\s\S]*?</svg>)', content, _re.IGNORECASE)
            if not svg_match:
                return {"error": "Failed to generate valid SVG. Try a more specific description.", "raw": content[:500]}
            svg_code = svg_match.group(1)

            # Basic sanitization: remove script tags and event handlers
            svg_code = _re.sub(r'<script[\s\S]*?</script>', '', svg_code, flags=_re.IGNORECASE)
            svg_code = _re.sub(r'\bon\w+\s*=\s*["\'][^"\']*["\']', '', svg_code)

            return {
                "svg": svg_code,
                "type": diagram_type,
                "description": description,
                "note": "Embed this SVG directly in your markdown response. The frontend renders inline SVG.",
            }
    except Exception as e:
        return {"error": f"Visualization failed: {str(e)[:300]}"}


# ── Orchestrator Tools ──

async def _custom_present_options(args: dict, ctx: dict) -> dict:
    """Present interactive options/choices to the user as clickable cards.
    The frontend renders these as buttons the user can click to respond."""
    title = (args.get("title") or args.get("question") or "").strip()
    options = args.get("options", [])
    if not title:
        return {"error": "title/question is required"}
    if not options or not isinstance(options, list):
        return {"error": "options must be a non-empty list of choices"}

    # Normalize options: accept strings or {label, description, value} dicts
    normalized = []
    for i, opt in enumerate(options):
        if isinstance(opt, str):
            normalized.append({"label": opt, "value": opt, "description": ""})
        elif isinstance(opt, dict):
            normalized.append({
                "label": opt.get("label", opt.get("text", f"Option {i+1}")),
                "value": opt.get("value", opt.get("label", opt.get("text", f"option_{i+1}"))),
                "description": opt.get("description", opt.get("desc", "")),
                "icon": opt.get("icon", ""),
            })
        else:
            normalized.append({"label": str(opt), "value": str(opt), "description": ""})

    return {
        "_type": "present_options",
        "title": title,
        "options": normalized[:8],  # Max 8 options
        "allow_custom": args.get("allow_custom", True),
        "note": "The frontend will render these as clickable cards. The user's selection will be sent as their next message.",
    }


async def _custom_workspace_snapshot(args: dict, ctx: dict) -> dict:
    """Get a full overview of the user's workspace — all agents, their status, recent runs, and summary stats."""
    headers = {
        "x-user-id": ctx.get("user_id", ""),
        "x-user-role": ctx.get("user_role", "user"),
        "x-is-superuser": "true" if ctx.get("is_superuser") else "false",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            # Fetch all agents
            resp = await client.get(f"{AGENT_ENGINE_URL}/agents/", headers=headers, params={"limit": 50})
            if resp.status_code != 200:
                return {"error": f"Failed to fetch agents: {resp.status_code}"}
            data = resp.json()
            agents = data if isinstance(data, list) else data.get("agents", data.get("items", []))

            agent_summaries = []
            active_count = 0
            total_sessions = 0

            for a in agents[:30]:
                agent_id = a.get("id", "")
                is_active = a.get("is_active") or a.get("status") == "active"
                if is_active:
                    active_count += 1

                summary = {
                    "id": agent_id,
                    "name": a.get("name", "Unnamed"),
                    "goal": (a.get("goal") or "")[:120],
                    "status": "active" if is_active else a.get("status", "inactive"),
                    "model": a.get("model", ""),
                    "tools_count": len(a.get("tools", [])),
                    "created": a.get("created_at", "")[:10],
                }

                # Fetch recent sessions for this agent (lightweight)
                try:
                    sess_resp = await client.get(
                        f"{AGENT_ENGINE_URL}/agents/{agent_id}/sessions",
                        headers=headers, params={"limit": 3}
                    )
                    if sess_resp.status_code == 200:
                        sessions = sess_resp.json() if isinstance(sess_resp.json(), list) else sess_resp.json().get("sessions", sess_resp.json().get("items", []))
                        total_sessions += len(sessions)
                        summary["recent_runs"] = [
                            {
                                "id": s.get("id", "")[:8],
                                "status": s.get("status", ""),
                                "goal": (s.get("goal") or "")[:60],
                                "loops": s.get("loop_count", 0),
                            }
                            for s in sessions[:3]
                        ]
                except Exception:
                    pass

                agent_summaries.append(summary)

            return {
                "_type": "workspace_snapshot",
                "agents": agent_summaries,
                "stats": {
                    "total_agents": len(agents),
                    "active_agents": active_count,
                    "total_recent_sessions": total_sessions,
                },
                "user_id": ctx.get("user_id", ""),
            }
    except Exception as e:
        return {"error": f"Workspace snapshot failed: {str(e)[:300]}"}


async def _custom_schedule_agent(args: dict, ctx: dict) -> dict:
    """Set a recurring schedule (trigger) for an agent to run automatically."""
    agent_id = args.get("agent_id", "").strip()
    agent_name = args.get("agent_name", "").strip()
    schedule = args.get("schedule", "").strip()
    goal = args.get("goal", "").strip()

    if not agent_id and not agent_name:
        return {"error": "Provide agent_id or agent_name"}
    if not schedule:
        return {"error": "schedule is required — e.g. 'every 1h', 'every 6h', 'daily', 'weekly', 'cron: 0 */2 * * *'"}

    # Parse schedule to cron expression
    cron_expr = ""
    schedule_lower = schedule.lower().strip()
    if schedule_lower.startswith("cron:"):
        cron_expr = schedule_lower.replace("cron:", "").strip()
    elif "every 1h" in schedule_lower or "hourly" in schedule_lower or "every hour" in schedule_lower:
        cron_expr = "0 * * * *"
    elif "every 2h" in schedule_lower:
        cron_expr = "0 */2 * * *"
    elif "every 4h" in schedule_lower:
        cron_expr = "0 */4 * * *"
    elif "every 6h" in schedule_lower:
        cron_expr = "0 */6 * * *"
    elif "every 12h" in schedule_lower:
        cron_expr = "0 */12 * * *"
    elif "daily" in schedule_lower or "every day" in schedule_lower:
        cron_expr = "0 9 * * *"
    elif "weekly" in schedule_lower or "every week" in schedule_lower:
        cron_expr = "0 9 * * 1"
    elif "every 30m" in schedule_lower or "every 30 min" in schedule_lower:
        cron_expr = "*/30 * * * *"
    elif "every 15m" in schedule_lower or "every 15 min" in schedule_lower:
        cron_expr = "*/15 * * * *"
    else:
        return {"error": f"Could not parse schedule '{schedule}'. Use: 'hourly', 'every 2h', 'daily', 'weekly', 'every 30m', or 'cron: <expression>'"}

    headers = {
        "x-user-id": ctx.get("user_id", ""),
        "x-user-role": ctx.get("user_role", "user"),
    }

    # If agent_name provided, resolve to ID
    if not agent_id and agent_name:
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(f"{AGENT_ENGINE_URL}/agents/", headers=headers, params={"limit": 50})
                if resp.status_code == 200:
                    data = resp.json()
                    agents = data if isinstance(data, list) else data.get("agents", data.get("items", []))
                    for a in agents:
                        if (a.get("name") or "").lower() == agent_name.lower():
                            agent_id = a.get("id", "")
                            break
                if not agent_id:
                    return {"error": f"Agent '{agent_name}' not found"}
        except Exception as e:
            return {"error": f"Failed to resolve agent name: {str(e)[:200]}"}

    # Set trigger via API
    trigger_payload = {
        "name": goal[:80] if goal else f"Schedule: {schedule}",
        "trigger_type": "cron",
        "cron_expression": cron_expr,
        "goal": goal or f"Run agent on schedule: {schedule}",
        "enabled": True,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.post(
                f"{AGENT_ENGINE_URL}/agents/{agent_id}/triggers",
                json=trigger_payload,
                headers=headers,
            )
            if resp.status_code in (200, 201):
                return {
                    "success": True,
                    "agent_id": agent_id,
                    "schedule": schedule,
                    "cron": cron_expr,
                    "message": f"Agent scheduled: {schedule} (cron: {cron_expr})",
                }
            # If triggers endpoint doesn't exist, store as agent metadata
            elif resp.status_code == 404:
                # Fall back to updating agent description with schedule info
                patch_resp = await client.patch(
                    f"{AGENT_ENGINE_URL}/agents/{agent_id}",
                    json={"description": f"[SCHEDULED: {cron_expr}] {goal or ''}".strip()},
                    headers=headers,
                )
                return {
                    "success": True,
                    "agent_id": agent_id,
                    "schedule": schedule,
                    "cron": cron_expr,
                    "message": f"Schedule saved to agent config: {schedule} (cron: {cron_expr}). Note: Automatic execution requires the scheduler service.",
                    "note": "The schedule is stored. Set up a cron job or scheduler to execute POST /agents/{agent_id}/sessions at this interval.",
                }
            return {"error": f"Schedule error {resp.status_code}: {resp.text[:300]}"}
    except Exception as e:
        return {"error": f"Schedule failed: {str(e)[:300]}"}


async def _custom_run_snapshot(args: dict, ctx: dict) -> dict:
    """Get detailed snapshot of a specific agent run (session) — steps, tool calls, output, timing, errors."""
    session_id = args.get("session_id", "").strip()
    if not session_id:
        return {"error": "session_id is required"}

    headers = {
        "x-user-id": ctx.get("user_id", ""),
        "x-user-role": ctx.get("user_role", "user"),
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            # Get session detail
            detail_resp = await client.get(f"{AGENT_ENGINE_URL}/agents/sessions/{session_id}", headers=headers)
            if detail_resp.status_code != 200:
                return {"error": f"Session not found: {detail_resp.status_code}"}
            session = detail_resp.json()

            # Get steps
            steps_resp = await client.get(f"{AGENT_ENGINE_URL}/agents/sessions/{session_id}/steps", headers=headers)
            steps = []
            if steps_resp.status_code == 200:
                steps_data = steps_resp.json()
                raw_steps = steps_data if isinstance(steps_data, list) else steps_data.get("steps", steps_data.get("items", []))
                for s in raw_steps:
                    steps.append({
                        "step": s.get("step_number", 0),
                        "tool": s.get("tool_name", s.get("action", "")),
                        "reasoning": (s.get("reasoning") or "")[:150],
                        "output": (str(s.get("output") or s.get("result", ""))[:200]),
                        "status": s.get("status", ""),
                        "duration_ms": s.get("duration_ms", 0),
                    })

            return {
                "_type": "run_snapshot",
                "session_id": session_id,
                "agent_id": session.get("agent_id", ""),
                "status": session.get("status", ""),
                "goal": session.get("goal", ""),
                "loops": session.get("loop_count", 0),
                "tokens_used": session.get("total_tokens", 0),
                "final_output": (str(session.get("final_output") or session.get("output", ""))[:500]),
                "error": session.get("error_message", ""),
                "started_at": session.get("created_at", ""),
                "steps": steps[:20],
                "total_steps": len(steps),
            }
    except Exception as e:
        return {"error": f"Run snapshot failed: {str(e)[:300]}"}


async def _custom_list_workspace_tools(args: dict, ctx: dict) -> dict:
    """List all available tools in the workspace, grouped by category."""
    try:
        categories = {}
        for tid, tdef in TOOL_DEFS.items():
            cat = tdef.get("category", "general")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append({
                "name": tid,
                "description": tdef["desc"][:120],
            })

        # Also load user's custom tools
        user_id = ctx.get("user_id", "")
        custom = {}
        if user_id and user_id != "anonymous":
            custom = await _load_user_custom_tools(user_id)
        if custom:
            categories["custom (user-created)"] = [
                {"name": tid, "description": tdef.get("desc", "")[:120]}
                for tid, tdef in custom.items()
            ]

        total = sum(len(v) for v in categories.values())
        return {
            "_type": "workspace_tools",
            "total_tools": total,
            "categories": {cat: {"count": len(tools), "tools": tools} for cat, tools in categories.items()},
        }
    except Exception as e:
        return {"error": f"Failed to list tools: {str(e)[:300]}"}


async def _custom_agent_snapshot(args: dict, ctx: dict) -> dict:
    """Get full agent configuration — tools, instructions, runs, schedule."""
    agent_id = args.get("agent_id", "")
    agent_name = args.get("agent_name", "")
    user_id = ctx.get("user_id", "anonymous")
    headers = {"x-user-id": user_id, "x-user-role": ctx.get("user_role", "user")}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Find agent by name if no ID
            if not agent_id and agent_name:
                resp = await client.get(f"{AGENT_ENGINE_URL}/agents/", headers=headers)
                if resp.status_code == 200:
                    agents = resp.json() if isinstance(resp.json(), list) else resp.json().get("agents", [])
                    for a in agents:
                        if agent_name.lower() in (a.get("name") or "").lower():
                            agent_id = a.get("id", "")
                            break
                if not agent_id:
                    return {"error": f"No agent found matching '{agent_name}'"}

            # Get agent details
            resp = await client.get(f"{AGENT_ENGINE_URL}/agents/{agent_id}", headers=headers)
            if resp.status_code != 200:
                return {"error": f"Agent not found: {resp.status_code}"}
            agent = resp.json()

            # Get recent sessions
            sess_resp = await client.get(f"{AGENT_ENGINE_URL}/agents/{agent_id}/sessions?limit=5", headers=headers)
            sessions = []
            if sess_resp.status_code == 200:
                sess_data = sess_resp.json()
                raw = sess_data if isinstance(sess_data, list) else sess_data.get("sessions", [])
                for s in raw[:5]:
                    sessions.append({
                        "id": s.get("id", ""),
                        "status": s.get("status", ""),
                        "loops": s.get("loop_count", 0),
                        "goal": (s.get("goal") or "")[:100],
                        "created_at": s.get("created_at", ""),
                    })

            return {
                "_type": "agent_snapshot",
                "id": agent.get("id", ""),
                "name": agent.get("name", ""),
                "status": agent.get("status", ""),
                "goal": agent.get("goal", ""),
                "instructions": (agent.get("instructions") or "")[:500],
                "model": agent.get("model", ""),
                "tools": agent.get("tools", []),
                "schedule": agent.get("schedule", agent.get("trigger", None)),
                "created_at": agent.get("created_at", ""),
                "updated_at": agent.get("updated_at", ""),
                "recent_runs": sessions,
                "total_runs": len(sessions),
            }
    except Exception as e:
        return {"error": f"Agent snapshot failed: {str(e)[:300]}"}


async def _custom_run_agent(args: dict, ctx: dict) -> dict:
    """Directly run/start an agent by name or ID."""
    agent_id = args.get("agent_id", "")
    agent_name = args.get("agent_name", "")
    goal = args.get("goal", "")
    user_id = ctx.get("user_id", "anonymous")
    headers = {"x-user-id": user_id, "x-user-role": ctx.get("user_role", "user")}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Find agent by name if no ID
            if not agent_id and agent_name:
                resp = await client.get(f"{AGENT_ENGINE_URL}/agents/", headers=headers)
                if resp.status_code == 200:
                    agents = resp.json() if isinstance(resp.json(), list) else resp.json().get("agents", [])
                    for a in agents:
                        if agent_name.lower() in (a.get("name") or "").lower():
                            agent_id = a.get("id", "")
                            break
                if not agent_id:
                    return {"error": f"No agent found matching '{agent_name}'"}

            # Start the agent
            payload = {}
            if goal:
                payload["goal"] = goal
            resp = await client.post(f"{AGENT_ENGINE_URL}/agents/{agent_id}/start", json=payload, headers=headers)
            if resp.status_code not in (200, 201, 202):
                return {"error": f"Failed to start agent: {resp.status_code} — {resp.text[:200]}"}
            data = resp.json()
            return {
                "_type": "agent_run_started",
                "agent_id": agent_id,
                "session_id": data.get("session_id", data.get("id", "")),
                "status": data.get("status", "started"),
                "message": f"Agent started successfully. Use run_snapshot with the session_id to check progress.",
            }
    except Exception as e:
        return {"error": f"Run agent failed: {str(e)[:300]}"}


async def _custom_session_log(args: dict, ctx: dict) -> dict:
    """Return the current session's tool call log, loop count, and stats.
    This is populated by the streaming loop via ctx['_session_tracker']."""
    tracker = ctx.get("_session_tracker", {})
    return {
        "_type": "session_log",
        "tools_called": tracker.get("tools_called", []),
        "total_tool_calls": tracker.get("total_tool_calls", 0),
        "total_loops": tracker.get("total_loops", 0),
        "total_tokens": tracker.get("total_tokens", 0),
        "elapsed_seconds": tracker.get("elapsed_seconds", 0),
        "skills_used": tracker.get("skills_used", []),
    }


CUSTOM_HANDLERS = {
    "_custom_memory_search": _custom_memory_search,
    "_custom_memory_stats": _custom_memory_stats,
    "_custom_hs_search": _custom_hs_search,
    "_custom_hs_anchor": _custom_hs_anchor,
    "_custom_hs_list_anchors": _custom_hs_list_anchors,
    "_custom_hs_hash": _custom_hs_hash,
    "_custom_hs_resonance": _custom_hs_resonance,
    "_custom_cv_scan": _custom_cv_scan,
    "_custom_cv_full_analysis": _custom_cv_full_analysis,
    "_custom_cv_trace": _custom_cv_trace,
    "_custom_cv_functions": _custom_cv_functions,
    "_custom_cv_governance": _custom_cv_governance,
    "_custom_cv_list": _custom_cv_list,
    "_custom_cv_report": _custom_cv_report,
    "_custom_cv_graph": _custom_cv_graph,
    "_custom_cv_pipeline": _custom_cv_pipeline,
    "_custom_cv_filter": _custom_cv_filter,
    "_custom_cv_by_type": _custom_cv_by_type,
    "_custom_cv_compare": _custom_cv_compare,
    "_custom_cv_delete": _custom_cv_delete,
    "_local_file_read": _local_file_read,
    "_local_file_write": _local_file_write,
    "_local_file_edit": _local_file_edit,
    "_local_file_list": _local_file_list,
    "_local_file_delete": _local_file_delete,
    "_custom_agents_list": _custom_agents_list,
    "_custom_agents_create": _custom_agents_create,
    "_custom_agents_start": _custom_agents_start,
    "_custom_agents_stop": _custom_agents_stop,
    "_custom_agents_delete": _custom_agents_delete,
    "_custom_agents_status": _custom_agents_status,
    "_custom_agents_sessions": _custom_agents_sessions,
    "_custom_agents_session_steps": _custom_agents_session_steps,
    "_custom_agents_session_trace": _custom_agents_session_trace,
    "_custom_agents_metrics": _custom_agents_metrics,
    "_custom_agents_session_detail": _custom_agents_session_detail,
    "_custom_agents_session_cancel": _custom_agents_session_cancel,
    "_custom_agents_update": _custom_agents_update,
    "_custom_agents_available_tools": _custom_agents_available_tools,
    "_custom_agents_templates": _custom_agents_templates,
    "_custom_agents_versions": _custom_agents_versions,
    # State Physics tools
    "_custom_sp_state": _custom_sp_state,
    "_custom_sp_reset": _custom_sp_reset,
    "_custom_sp_nodes": _custom_sp_nodes,
    "_custom_sp_metrics": _custom_sp_metrics,
    "_custom_sp_identity": _custom_sp_identity,
    "_custom_sp_simulate": _custom_sp_simulate,
    "_custom_sp_galaxy": _custom_sp_galaxy,
    "_custom_sp_demo": _custom_sp_demo,
    "_custom_sp_asymmetry": _custom_sp_asymmetry,
    "_custom_sp_physics_config": _custom_sp_physics_config,
    "_custom_sp_entropy_config": _custom_sp_entropy_config,
    "_custom_sp_entropy_toggle": _custom_sp_entropy_toggle,
    "_custom_sp_entropy_perturbation": _custom_sp_entropy_perturbation,
    "_custom_sp_agent_spawn": _custom_sp_agent_spawn,
    "_custom_sp_agent_step": _custom_sp_agent_step,
    "_custom_sp_agent_kill": _custom_sp_agent_kill,
    "_custom_sp_agents_spawn": _custom_sp_agents_spawn,
    "_custom_sp_agents_kill_all": _custom_sp_agents_kill_all,
    "_custom_sp_experiment": _custom_sp_experiment,
    "_custom_sp_memory_cost": _custom_sp_memory_cost,
    "_custom_sp_metrics_record": _custom_sp_metrics_record,
    # Web search & browsing
    "_custom_web_search": _custom_web_search,
    "_custom_read_webpage": _custom_read_webpage,
    "_custom_read_many_pages": _custom_read_many_pages,
    "_custom_reddit_search": _custom_reddit_search,
    # Dynamic tool management
    "_custom_create_tool": _custom_create_tool,
    "_custom_list_tools": _custom_list_tools,
    "_custom_delete_tool": _custom_delete_tool,
    "_custom_update_tool": _custom_update_tool,
    # System tools
    "_custom_get_current_time": _custom_get_current_time,
    "_custom_get_system_info": _custom_get_system_info,
    # GitHub API tools
    "_custom_github_create_repo": _custom_github_create_repo,
    "_custom_github_list_repos": _custom_github_list_repos,
    "_custom_github_list_files": _custom_github_list_files,
    "_custom_github_download_file": _custom_github_download_file,
    "_custom_github_upload_file": _custom_github_upload_file,
    "_custom_github_commits": _custom_github_commits,
    "_custom_github_pull_request": _custom_github_pull_request,
    "_custom_github_issue": _custom_github_issue,
    "_custom_github_comment": _custom_github_comment,
    # Git proxy tools
    "_custom_git_proxy": _custom_git_proxy,
    # Platform API tools (access all ~433 endpoints)
    "_custom_platform_api_search": platform_api_search,
    "_custom_platform_api_call": platform_api_call,
    # Rabbit (Community Forum) tools
    "_custom_rabbit_create_community": _custom_rabbit_create_community,
    "_custom_rabbit_list_communities": _custom_rabbit_list_communities,
    "_custom_rabbit_get_community": _custom_rabbit_get_community,
    "_custom_rabbit_create_post": _custom_rabbit_create_post,
    "_custom_rabbit_list_posts": _custom_rabbit_list_posts,
    "_custom_rabbit_search_posts": _custom_rabbit_search_posts,
    "_custom_rabbit_get_post": _custom_rabbit_get_post,
    "_custom_rabbit_delete_post": _custom_rabbit_delete_post,
    "_custom_rabbit_create_comment": _custom_rabbit_create_comment,
    "_custom_rabbit_list_comments": _custom_rabbit_list_comments,
    "_custom_rabbit_delete_comment": _custom_rabbit_delete_comment,
    "_custom_rabbit_vote": _custom_rabbit_vote,
    # New tools: weather, search, utilities
    "_custom_weather": _custom_weather,
    "_custom_image_search": _custom_image_search,
    "_custom_news_search": _custom_news_search,
    "_custom_places_search": _custom_places_search,
    "_custom_youtube_search": _custom_youtube_search,
    "_custom_stock_crypto": _custom_stock_crypto,
    "_custom_deep_research": _custom_deep_research,
    "_custom_generate_chart": _custom_generate_chart,
    "_custom_send_email": _custom_send_email,
    "_custom_wikipedia": _custom_wikipedia,
    "_custom_visualize": _custom_visualize,
    # Orchestrator tools
    "_custom_present_options": _custom_present_options,
    "_custom_workspace_snapshot": _custom_workspace_snapshot,
    "_custom_schedule_agent": _custom_schedule_agent,
    "_custom_run_snapshot": _custom_run_snapshot,
    "_custom_list_workspace_tools": _custom_list_workspace_tools,
    "_custom_agent_snapshot": _custom_agent_snapshot,
    "_custom_run_agent": _custom_run_agent,
    "_custom_session_log": _custom_session_log,
}


def _build_native_tools(enabled: List[str], custom_tools: Dict[str, Any] = None) -> List[dict]:
    """Convert enabled tools to OpenAI/Groq native function calling format via registry."""
    tools = _registry.to_openai(
        tools=[t for t in _registry.get_all() if t.name in enabled]
    )
    if custom_tools:
        for tid, tdef in custom_tools.items():
            properties = {}
            for pname, pdesc in tdef.get("params", {}).items():
                properties[pname] = {"type": "string", "description": pdesc}
            tools.append({
                "type": "function",
                "function": {
                    "name": tid,
                    "description": tdef.get("desc", "")[:200],
                    "parameters": {"type": "object", "properties": properties},
                },
            })
    return tools


def _limit_tools_for_groq(tools: List[dict]) -> List[dict]:
    """Sort tools by registry priority and cap at GROQ_MAX_TOOLS."""
    if len(tools) <= GROQ_MAX_TOOLS:
        return tools

    def _priority(t):
        name = t.get("function", {}).get("name", "")
        tdef = _registry.get(name)
        return tdef.priority if tdef else 99
    sorted_tools = sorted(tools, key=_priority)
    return sorted_tools[:GROQ_MAX_TOOLS]


def _build_anthropic_tools(openai_tools: List[dict]) -> List[dict]:
    """Convert OpenAI-format tools to Anthropic format."""
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        }
        for t in openai_tools
    ]


async def _call_llm_json_mode(
    client: httpx.AsyncClient, api_key: str, messages: list,
    tools_prompt: str, temperature: float = 0.3,
) -> dict:
    """Fallback: JSON-mode prompt-based tool calling via UnifiedLLMClient.
    Used when native tool calling fails with tool_use_failed."""
    json_system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
    json_system += f"\n\nAVAILABLE TOOLS:\n{tools_prompt}\n\n"
    json_system += (
        "RESPONSE FORMAT — respond with valid JSON only.\n"
        'To use a tool: {{"action": "tool_call", "tool": "<tool_name>", "args": {{...}}, "reasoning": "<why>"}}\n'
        'To respond: {{"action": "respond", "content": "<markdown response>"}}'
    )
    json_messages = [{"role": "system", "content": json_system}] + messages[1:]
    try:
        response = await _llm_client.complete(LLMRequest(
            messages=json_messages,
            provider="groq",
            temperature=temperature,
            max_tokens=4096,
            response_format={"type": "json_object"},
        ))
        usage = response.usage
        content_str = response.content or ""
    except Exception as e:
        return {"error": f"JSON-mode LLM call failed: {e}"}

    parsed = None
    try:
        parsed = json.loads(content_str)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content_str, flags=re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    if not parsed:
        return {"text": content_str or "No response.", "tool_calls": [], "usage": usage, "json_mode": True}
    action = parsed.get("action", "respond")
    if action == "tool_call":
        tool_name = parsed.get("tool", "")
        tool_args = parsed.get("args", {})
        return {
            "text": parsed.get("reasoning", ""),
            "tool_calls": [{"id": f"json_{tool_name}", "name": tool_name, "arguments": json.dumps(tool_args)}],
            "usage": usage,
            "json_mode": True,
        }
    else:
        return {"text": parsed.get("content", content_str), "tool_calls": [], "usage": usage, "json_mode": True}


def _resolve_provider(preferred: str = None, user_api_keys: Dict[str, str] = None) -> tuple:
    """Resolve provider, model, and API key. Returns (provider, model, api_key).
    Respects user preference, falls back through PROVIDER_FALLBACK_ORDER."""
    # Normalize provider aliases
    alias_map = {"chatgpt": "openai", "gpt": "openai", "claude": "anthropic", "google": "gemini"}
    normalized = alias_map.get((preferred or "").lower(), (preferred or "").lower())
    user_keys = user_api_keys or {}

    # Try preferred provider first
    if normalized and normalized in PROVIDER_MODELS:
        key = user_keys.get(normalized) or PROVIDER_KEYS.get(normalized, "")
        if key:
            return normalized, PROVIDER_MODELS[normalized], key

    # Fallback chain: best quality first
    for prov in PROVIDER_FALLBACK_ORDER:
        key = user_keys.get(prov) or PROVIDER_KEYS.get(prov, "")
        if key:
            return prov, PROVIDER_MODELS[prov], key

    # Last resort: Groq
    return "groq", PROVIDER_MODELS["groq"], GROQ_API_KEY


async def _call_llm_with_tools(
    client: httpx.AsyncClient,
    provider: str,
    model: str,
    api_key: str,
    messages: list,
    tools: list,
    temperature: float = 0.3,
) -> dict:
    """Call LLM with native tool calling. Returns unified result dict.
    Result: {text, tool_calls: [{id, name, arguments}], usage, error}"""

    if provider in ("openai", "groq"):
        url = PROVIDER_URLS.get(provider, GROQ_API_URL)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 4096,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        resp = await client.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=90.0,
        )
        if resp.status_code != 200:
            return {"error": f"LLM error {resp.status_code}: {resp.text[:400]}"}

        data = resp.json()
        msg = data.get("choices", [{}])[0].get("message", {})
        text = msg.get("content") or ""
        raw_tool_calls = msg.get("tool_calls") or []
        tool_calls = []
        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            tool_calls.append({
                "id": tc.get("id", f"call_{len(tool_calls)}"),
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments", "{}"),
            })
        return {
            "text": text,
            "tool_calls": tool_calls,
            "usage": data.get("usage", {}),
            "raw_message": msg,
        }

    elif provider == "anthropic":
        # Separate system message (Anthropic requires it as a top-level field)
        system_parts = []
        non_system = []
        for m in messages:
            if m["role"] == "system":
                c = m.get("content", "")
                system_parts.append(c if isinstance(c, str) else str(c))
            elif m.get("role") == "tool":
                # Convert OpenAI tool-result to Anthropic tool_result format
                tc_id = m.get("tool_call_id", "unknown")
                non_system.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tc_id, "content": str(m.get("content", ""))}],
                })
            else:
                role = m.get("role", "user")
                content = m.get("content")
                # Sanitize assistant messages — strip OpenAI keys, ensure valid content
                if role == "assistant":
                    tc = m.get("tool_calls")
                    if isinstance(content, list):
                        # Already content blocks — keep only valid types
                        valid_blocks = []
                        for blk in content:
                            if isinstance(blk, dict) and blk.get("type") in ("text", "tool_use"):
                                valid_blocks.append(blk)
                        if not valid_blocks:
                            content = str(content)
                        else:
                            content = valid_blocks
                    elif content is None or content == "":
                        # OpenAI allows null content with tool_calls; Anthropic needs text
                        if tc:
                            # Reconstruct as Anthropic tool_use blocks
                            blocks = []
                            for t in tc:
                                fn = t.get("function", t) if isinstance(t, dict) else {}
                                try:
                                    inp = json.loads(fn.get("arguments", "{}"))
                                except (json.JSONDecodeError, TypeError):
                                    inp = {}
                                blocks.append({
                                    "type": "tool_use",
                                    "id": t.get("id", f"call_{len(blocks)}"),
                                    "name": fn.get("name", t.get("name", "unknown")),
                                    "input": inp,
                                })
                            content = blocks if blocks else "..."
                        else:
                            content = "..."
                    else:
                        content = str(content)
                else:
                    # User messages — ensure string content
                    if isinstance(content, list):
                        # Could be valid Anthropic content blocks (tool_result)
                        pass
                    elif content is None:
                        content = ""
                    else:
                        content = str(content)
                clean = {"role": role, "content": content}
                non_system.append(clean)

        # Ensure messages alternate user/assistant (Anthropic requirement)
        # Merge consecutive same-role messages
        merged = []
        for msg in non_system:
            if merged and merged[-1]["role"] == msg["role"]:
                prev_c = merged[-1]["content"]
                cur_c = msg["content"]
                if isinstance(prev_c, str) and isinstance(cur_c, str):
                    merged[-1]["content"] = prev_c + "\n" + cur_c
                elif isinstance(prev_c, list) and isinstance(cur_c, list):
                    merged[-1]["content"] = prev_c + cur_c
                elif isinstance(prev_c, str) and isinstance(cur_c, list):
                    merged[-1]["content"] = [{"type": "text", "text": prev_c}] + cur_c
                elif isinstance(prev_c, list) and isinstance(cur_c, str):
                    merged[-1]["content"] = prev_c + [{"type": "text", "text": cur_c}]
            else:
                merged.append(msg)
        non_system = merged

        # Anthropic requires first message to be user role
        if non_system and non_system[0]["role"] != "user":
            non_system.insert(0, {"role": "user", "content": "Continue."})

        anthropic_tools = _build_anthropic_tools(tools) if tools else []
        payload = {
            "model": model,
            "max_tokens": 4096,
            "temperature": temperature,
            "messages": non_system,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if anthropic_tools:
            payload["tools"] = anthropic_tools

        resp = await client.post(
            ANTHROPIC_API_URL, json=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=90.0,
        )
        if resp.status_code != 200:
            return {"error": f"Anthropic error {resp.status_code}: {resp.text[:400]}"}

        data = resp.json()
        text = ""
        tool_calls = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", f"call_{len(tool_calls)}"),
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {})),
                })
        return {
            "text": text,
            "tool_calls": tool_calls,
            "usage": data.get("usage", {}),
        }

    elif provider == "gemini":
        # Google Gemini API format
        url = f"{GEMINI_API_URL}/models/{model}:generateContent?key={api_key}"

        # Convert messages to Gemini format (role: user/model, parts)
        contents = []
        system_text = ""
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", str(b)) for b in content if isinstance(b, dict)
                )
            elif content is None:
                content = ""
            else:
                content = str(content)

            if role == "system":
                system_text += content + "\n"
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})
            elif role == "tool":
                # Tool result → functionResponse
                contents.append({
                    "role": "function",
                    "parts": [{"functionResponse": {
                        "name": m.get("name", m.get("tool_call_id", "unknown")),
                        "response": {"result": content[:4000]},
                    }}],
                })
            else:
                contents.append({"role": "user", "parts": [{"text": content}]})

        # Build Gemini tool declarations
        gemini_tools = []
        if tools:
            func_decls = []
            for t in tools:
                fn = t.get("function", {})
                params = fn.get("parameters", {})
                # Gemini doesn't accept empty properties
                if not params.get("properties"):
                    params = {"type": "object", "properties": {"query": {"type": "string", "description": "input"}}}
                func_decls.append({
                    "name": fn.get("name", ""),
                    "description": fn.get("description", "")[:200],
                    "parameters": params,
                })
            gemini_tools = [{"function_declarations": func_decls}]

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": 4096,
            },
        }
        if system_text.strip():
            payload["systemInstruction"] = {"parts": [{"text": system_text.strip()}]}
        if gemini_tools:
            payload["tools"] = gemini_tools

        resp = await client.post(url, json=payload, timeout=90.0)
        if resp.status_code != 200:
            return {"error": f"Gemini error {resp.status_code}: {resp.text[:400]}"}

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return {"error": "Gemini returned no candidates"}

        parts = candidates[0].get("content", {}).get("parts", [])
        text = ""
        tool_calls = []
        for part in parts:
            if "text" in part:
                text += part["text"]
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append({
                    "id": f"gemini_call_{len(tool_calls)}",
                    "name": fc.get("name", ""),
                    "arguments": json.dumps(fc.get("args", {})),
                })
        usage = data.get("usageMetadata", {})
        return {
            "text": text,
            "tool_calls": tool_calls,
            "usage": {
                "input_tokens": usage.get("promptTokenCount", 0),
                "output_tokens": usage.get("candidatesTokenCount", 0),
                "total_tokens": usage.get("totalTokenCount", 0),
            },
        }

    return {"error": f"Unsupported provider: {provider}"}


def _build_tool_result_messages(provider: str, tool_call_id: str, tool_name: str,
                                 result_str: str, assistant_msg: dict = None) -> list:
    """Build the messages to append after a tool call for the given provider."""
    if provider == "anthropic":
        return [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_call_id, "content": result_str}
            ]}
        ]
    elif provider == "gemini":
        # Gemini uses functionResponse format
        return [
            {"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result_str}
        ]
    else:
        # OpenAI / Groq format
        return [
            {"role": "tool", "tool_call_id": tool_call_id, "content": result_str}
        ]


def _build_tools_prompt(enabled: List[str], custom_tools: Dict[str, Any] = None) -> str:
    """Build text-mode tool prompt from registry (fallback for JSON mode)."""
    enabled_tools = [t for t in _registry.get_all() if t.name in enabled]
    text = _registry.to_prompt_text(tools=enabled_tools)
    if custom_tools:
        lines = ["\n  [CUSTOM (user-created)]"]
        for tid, tdef in custom_tools.items():
            params_str = ", ".join(f"{k}: {v}" for k, v in tdef.get("params", {}).items())
            lines.append(f"  - {tid}({params_str}): {tdef['desc']}")
        text += "\n".join(lines)
    return text


class AgenticChatRequest(BaseModel):
    message: str
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None  # For persistent conversations
    conversation_history: Optional[List[Dict[str, Any]]] = None
    enabled_tools: Optional[List[str]] = None
    model: Optional[str] = None
    preferred_provider: Optional[str] = None  # "openai", "anthropic", "groq", "auto"
    max_loops: int = 50
    user_api_keys: Optional[Dict[str, str]] = None
    system_prompt: Optional[str] = None


SYSTEM_TEMPLATE = """You are a powerful AI assistant on the ResonantGenesis platform with access to real tools.

{memory_context}

RULES:
1. When the user asks something that needs real data, USE YOUR TOOLS. Don't guess or describe what you would do.
2. You can call multiple tools in sequence — call one, see the result, then decide what's next.
3. For calculations or code: use execute_code. For current info: use web_search. For user data: use memory_read/write.
4. When you have enough information from tools, synthesize the results into a clear response.
5. If a tool fails, explain the error to the user.
6. Be concise. Show tool results clearly. Use Markdown formatting.

You are NOT a basic chatbot. You are an agentic AI that ACTIVELY uses tools to solve problems."""


async def _auto_retrieve_memories(user_id: str, message: str, conversation_history: list = None) -> str:
    """Smart memory retrieval — relevance scoring, dedup, filtering."""
    if not user_id or user_id == "anonymous":
        return ""

    all_memories = []
    seen_content = set()  # For dedup

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Retrieve memories relevant to the current message
            resp = await client.post(
                f"{MEMORY_SERVICE_URL}/memory/retrieve",
                json={
                    "query": message[:500],
                    "limit": 12,
                    "user_id": user_id,
                    "use_vector_search": True,
                    "retrieval_mode": "hybrid",
                },
                headers={"x-user-id": user_id},
            )
            if resp.status_code == 200:
                data = resp.json()
                memories = data if isinstance(data, list) else data.get("memories", data.get("results", []))
                for mem in memories:
                    if isinstance(mem, dict):
                        content = mem.get("content", mem.get("text", ""))[:500]
                        score = mem.get("score", mem.get("similarity", mem.get("relevance", 0.5)))
                    else:
                        content = str(mem)[:500]
                        score = 0.5
                    all_memories.append({"content": content, "score": float(score), "source": "query"})

            # 2. If conversation has context, also search with recent assistant content
            if conversation_history and len(conversation_history) >= 2:
                recent_ctx = " ".join(
                    m.get("content", "")[:200]
                    for m in conversation_history[-4:]
                    if m.get("content")
                )[:500]
                if recent_ctx.strip():
                    resp2 = await client.post(
                        f"{MEMORY_SERVICE_URL}/memory/retrieve",
                        json={
                            "query": recent_ctx,
                            "limit": 5,
                            "user_id": user_id,
                            "use_vector_search": True,
                            "retrieval_mode": "hybrid",
                        },
                        headers={"x-user-id": user_id},
                    )
                    if resp2.status_code == 200:
                        data2 = resp2.json()
                        mems2 = data2 if isinstance(data2, list) else data2.get("memories", data2.get("results", []))
                        for mem in mems2:
                            if isinstance(mem, dict):
                                content = mem.get("content", mem.get("text", ""))[:500]
                                score = mem.get("score", mem.get("similarity", 0.3))
                            else:
                                content = str(mem)[:500]
                                score = 0.3
                            all_memories.append({"content": content, "score": float(score) * 0.8, "source": "context"})

    except Exception as e:
        logger.warning(f"[SmartMemory] Retrieval error: {e}")

    if not all_memories:
        return ""

    # ── Runtime Intelligence: Smart memory scoring, dedup, filtering ──
    ranked = filter_and_rank_memories(
        memories=all_memories,
        query=message,
        history=conversation_history,
        min_score=0.15,
        max_results=5,
    )
    return format_memories_for_prompt(ranked)


# ── Persistent Conversation Endpoints ──

@router.get("/conversations")
async def list_conversations(request: Request):
    """List user's conversations."""
    await _ensure_tables()
    user_id = request.headers.get("x-user-id", "")
    if not user_id or user_id == "anonymous":
        return {"conversations": [], "error": "Not authenticated"}
    try:
        async with _db_engine.begin() as conn:
            result = await conn.execute(sa_text("""
                SELECT id, title, model, message_count, created_at, updated_at
                FROM agentic_chat_conversations
                WHERE user_id = :uid
                ORDER BY updated_at DESC
                LIMIT 50
            """), {"uid": user_id})
            rows = result.fetchall()
            return {"conversations": [
                {
                    "id": str(r[0]),
                    "title": r[1],
                    "model": r[2],
                    "message_count": r[3],
                    "created_at": r[4].isoformat() if r[4] else None,
                    "updated_at": r[5].isoformat() if r[5] else None,
                }
                for r in rows
            ]}
    except Exception as e:
        return {"conversations": [], "error": str(e)[:200]}


@router.post("/conversations")
async def create_conversation(request: Request):
    """Create a new conversation."""
    await _ensure_tables()
    user_id = request.headers.get("x-user-id", "")
    if not user_id or user_id == "anonymous":
        return {"error": "Not authenticated"}
    body = await request.json()
    title = body.get("title", "New conversation")
    try:
        async with _db_engine.begin() as conn:
            result = await conn.execute(sa_text("""
                INSERT INTO agentic_chat_conversations (user_id, title)
                VALUES (:uid, :title)
                RETURNING id, title, created_at
            """), {"uid": user_id, "title": title})
            row = result.fetchone()
            return {"id": str(row[0]), "title": row[1], "created_at": row[2].isoformat()}
    except Exception as e:
        return {"error": str(e)[:200]}


@router.get("/conversations/{conv_id}")
async def load_conversation(conv_id: str, request: Request):
    """Load a conversation with all messages."""
    await _ensure_tables()
    user_id = request.headers.get("x-user-id", "")
    try:
        async with _db_engine.begin() as conn:
            # Verify ownership
            conv = await conn.execute(sa_text("""
                SELECT id, title, model, message_count, created_at
                FROM agentic_chat_conversations
                WHERE id = :cid AND user_id = :uid
            """), {"cid": conv_id, "uid": user_id})
            conv_row = conv.fetchone()
            if not conv_row:
                return {"error": "Conversation not found"}

            # Load messages
            msgs = await conn.execute(sa_text("""
                SELECT id, role, content, tool_calls, tool_results, tokens_used, created_at
                FROM agentic_chat_messages
                WHERE conversation_id = :cid
                ORDER BY created_at ASC
            """), {"cid": conv_id})
            rows = msgs.fetchall()
            return {
                "conversation": {
                    "id": str(conv_row[0]),
                    "title": conv_row[1],
                    "model": conv_row[2],
                    "message_count": conv_row[3],
                    "created_at": conv_row[4].isoformat() if conv_row[4] else None,
                },
                "messages": [
                    {
                        "id": str(r[0]),
                        "role": r[1],
                        "content": r[2],
                        "tool_calls": r[3] or [],
                        "tool_results": r[4] or [],
                        "tokens_used": r[5] or 0,
                        "created_at": r[6].isoformat() if r[6] else None,
                    }
                    for r in rows
                ],
            }
    except Exception as e:
        return {"error": str(e)[:200]}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, request: Request):
    """Delete a conversation."""
    await _ensure_tables()
    user_id = request.headers.get("x-user-id", "")
    try:
        async with _db_engine.begin() as conn:
            await conn.execute(sa_text("""
                DELETE FROM agentic_chat_conversations
                WHERE id = :cid AND user_id = :uid
            """), {"cid": conv_id, "uid": user_id})
            return {"deleted": True}
    except Exception as e:
        return {"error": str(e)[:200]}


async def _save_message(conv_id: str, user_id: str, role: str, content: str,
                        tool_calls: list = None, tool_results: list = None, tokens: int = 0):
    """Save a message to the DB (fire-and-forget)."""
    try:
        await _ensure_tables()
        async with _db_engine.begin() as conn:
            await conn.execute(sa_text("""
                INSERT INTO agentic_chat_messages (conversation_id, role, content, tool_calls, tool_results, tokens_used)
                VALUES (:cid, :role, :content, CAST(:tc AS jsonb), CAST(:tr AS jsonb), :tokens)
            """), {
                "cid": conv_id, "role": role, "content": content,
                "tc": json.dumps(tool_calls or []),
                "tr": json.dumps(tool_results or []),
                "tokens": tokens,
            })
            await conn.execute(sa_text("""
                UPDATE agentic_chat_conversations
                SET message_count = message_count + 1, updated_at = NOW(),
                    title = CASE WHEN message_count = 0 AND :role = 'user'
                                 THEN LEFT(:content, 80) ELSE title END
                WHERE id = :cid
            """), {"cid": conv_id, "role": role, "content": content})
    except Exception as e:
        print(f"[SAVE_MSG] Error: {e}", flush=True)


async def _auto_create_conversation(user_id: str, first_message: str) -> str:
    """Auto-create a conversation and return its ID."""
    try:
        await _ensure_tables()
        async with _db_engine.begin() as conn:
            result = await conn.execute(sa_text("""
                INSERT INTO agentic_chat_conversations (user_id, title)
                VALUES (:uid, :title)
                RETURNING id
            """), {"uid": user_id, "title": first_message[:80]})
            row = result.fetchone()
            return str(row[0])
    except Exception as e:
        print(f"[AUTO_CREATE_CONV] Error: {e}", flush=True)
        return ""


@router.post("/stream")
async def agentic_chat_stream(body: AgenticChatRequest, request: Request):
    """Cascade-style agentic chat with SSE streaming, native tool calling, multi-provider."""

    async def _stream():
        start_time = time.time()
        executor = _get_executor()
        loop_count = 0
        total_tokens = 0
        _failed_auth_providers = set()  # Skip providers with auth errors

        # Session tracker for transparency (session_log tool reads this)
        session_tracker = {
            "tools_called": [],
            "total_tool_calls": 0,
            "total_loops": 0,
            "total_tokens": 0,
            "elapsed_seconds": 0,
            "skills_used": [],
        }

        enabled = _expand_skill_ids(body.enabled_tools) if body.enabled_tools else list(TOOL_DEFS.keys())

        user_id = body.user_id or request.headers.get("x-user-id", "anonymous")
        user_custom_tools = await _load_user_custom_tools(user_id) if user_id and user_id != "anonymous" else {}

        # Build native tool calling definitions (OpenAI/Groq format)
        native_tools = _build_native_tools(enabled, user_custom_tools)
        # Keep text-based tools prompt for JSON-mode fallback (Groq)
        tools_prompt = _build_tools_prompt(enabled, user_custom_tools)
        # Track if we're using JSON-mode fallback
        using_json_mode = False

        # ── Runtime Intelligence: Smart context assembly ──
        memory_context = await _auto_retrieve_memories(user_id, body.message, body.conversation_history)

        system = SYSTEM_TEMPLATE.format(memory_context=memory_context)
        if body.system_prompt:
            system = body.system_prompt + "\n\n" + system

        # Resolve provider early so we know context window size
        _pre_provider = (body.preferred_provider or "openai").lower()

        # Intelligent context window: token counting, history summarization, budget allocation
        messages = _build_context_window(
            system=system,
            history=body.conversation_history or [],
            user_message=body.message,
            provider=_pre_provider,
        )
        logger.info(f"[Runtime] Context: {len(messages)} messages, ~{sum(_estimate_tokens(m.get('content','') if isinstance(m.get('content',''), str) else '') for m in messages)} tokens")

        # ── DEBUG: trace what LLM sees ──
        logger.info(f"[Runtime:DEBUG] body.message (current): {body.message[:200]!r}")
        logger.info(f"[Runtime:DEBUG] history received: {len(body.conversation_history or [])} messages")
        if body.conversation_history:
            for hi, hm in enumerate(body.conversation_history):
                logger.info(f"[Runtime:DEBUG]   hist[{hi}] role={hm.get('role')} content={str(hm.get('content',''))[:120]!r}")
        for mi, mm in enumerate(messages):
            c = mm.get("content", "")
            if isinstance(c, list):
                c = str(c)[:120]
            else:
                c = c[:120] if c else ""
            logger.info(f"[Runtime:DEBUG]   final_msg[{mi}] role={mm.get('role')} content={c!r}")

        tool_context = {
            "user_id": body.user_id or request.headers.get("x-user-id", "anonymous"),
            "org_id": request.headers.get("x-org-id", ""),
            "user_role": request.headers.get("x-user-role", "user"),
            "is_superuser": request.headers.get("x-is-superuser", "false") == "true",
            "unlimited_credits": request.headers.get("x-unlimited-credits", "false") == "true",
            "user_api_keys": body.user_api_keys or {},
            "_session_tracker": session_tracker,
        }

        conv_id = body.conversation_id or ""
        if not conv_id and user_id and user_id != "anonymous":
            conv_id = await _auto_create_conversation(user_id, body.message)

        if conv_id:
            await _save_message(conv_id, user_id, "user", body.message)

        # Fetch user's BYOK keys from auth service (stored in profile)
        byok_keys = await _fetch_user_byok_keys(user_id)
        # Merge: request body keys override stored keys
        merged_api_keys = {**byok_keys, **(body.user_api_keys or {})}

        # Resolve provider: respect user preference, BYOK keys, fallback chain
        provider, model, api_key = _resolve_provider(
            preferred=body.preferred_provider,
            user_api_keys=merged_api_keys,
        )
        if body.model:
            model = body.model

        if not api_key:
            yield f"event: error\ndata: {json.dumps({'error': 'No API key for any provider. Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or GROQ_API_KEY.'})}\n\n"
            return

        logger.info(f"[AgenticChat] provider={provider} model={model} tools={len(native_tools)} user={user_id}")
        yield f"event: status\ndata: {json.dumps({'status': 'started', 'tools_available': len(enabled), 'conversation_id': conv_id, 'provider': provider, 'model': model})}\n\n"

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                while loop_count < body.max_loops:
                    loop_count += 1
                    yield f"event: thinking\ndata: {json.dumps({'loop': loop_count, 'message': 'Reasoning...', 'provider': provider})}\n\n"

                    # ── Registry-based tool selection (priority sort + cap) ──
                    max_tools = MAX_TOOLS_GROQ if provider == "groq" else MAX_TOOLS_DEFAULT
                    if len(native_tools) <= max_tools:
                        call_tools = native_tools
                    else:
                        # Sort by registry priority (lower = higher priority), cap at max
                        def _tool_priority(t):
                            name = t.get("function", {}).get("name", "")
                            tdef = _registry.get(name)
                            return tdef.priority if tdef else 99
                        call_tools = sorted(native_tools, key=_tool_priority)[:max_tools]

                    # Flatten messages for Groq (remove tool_calls/tool role if using JSON mode)
                    call_messages = messages
                    if using_json_mode:
                        # JSON mode uses plain user/assistant messages only
                        flat = []
                        for m in messages:
                            if m.get("role") in ("system", "user", "assistant"):
                                content = m.get("content", "")
                                if isinstance(content, list):
                                    content = " ".join(b.get("text", str(b)) for b in content if isinstance(b, dict))
                                elif content is None:
                                    content = ""
                                flat.append({"role": m["role"], "content": content})
                        call_messages = flat

                    # Call LLM — either native tool calling or JSON-mode fallback
                    if using_json_mode:
                        llm_result = await _call_llm_json_mode(
                            client=client, api_key=api_key,
                            messages=call_messages, tools_prompt=tools_prompt,
                            temperature=0.3,
                        )
                    else:
                        llm_result = await _call_llm_with_tools(
                            client=client, provider=provider, model=model,
                            api_key=api_key, messages=call_messages,
                            tools=call_tools, temperature=0.3,
                        )

                    # Handle LLM errors with fallback chain
                    if llm_result.get("error"):
                        err_str = llm_result["error"]
                        logger.warning(f"[AgenticChat] {provider} failed: {err_str[:200]}")

                        # If Groq tool_use_failed → retry with JSON mode (old approach)
                        if provider == "groq" and "tool_use_failed" in err_str and not using_json_mode:
                            logger.info("[AgenticChat] Groq tool_use_failed → retrying with JSON mode")
                            using_json_mode = True
                            llm_result = await _call_llm_json_mode(
                                client=client, api_key=api_key,
                                messages=call_messages, tools_prompt=tools_prompt,
                                temperature=0.3,
                            )

                        # If still error → try other providers
                        if llm_result.get("error"):
                            err_lower = llm_result["error"].lower()
                            # Track providers that failed with auth errors — don't retry
                            if "401" in err_lower or "api key" in err_lower or "unauthorized" in err_lower:
                                _failed_auth_providers.add(provider)
                            for fb in PROVIDER_FALLBACK_ORDER:
                                if fb == provider or fb in _failed_auth_providers:
                                    continue
                                fb_key = merged_api_keys.get(fb) or PROVIDER_KEYS.get(fb, "")
                                if fb_key:
                                    logger.info(f"[AgenticChat] Fallback → {fb}")
                                    provider, model, api_key = fb, PROVIDER_MODELS[fb], fb_key
                                    using_json_mode = False
                                    # Use filtered call_tools (not ALL native_tools) to save tokens
                                    fb_tools = _limit_tools_for_groq(call_tools) if fb == "groq" else call_tools
                                    llm_result = await _call_llm_with_tools(
                                        client=client, provider=provider, model=model,
                                        api_key=api_key, messages=call_messages,
                                        tools=fb_tools, temperature=0.3,
                                    )
                                    if not llm_result.get("error"):
                                        break
                                    fb_err = llm_result.get("error", "").lower()
                                    if "401" in fb_err or "api key" in fb_err or "unauthorized" in fb_err:
                                        _failed_auth_providers.add(fb)
                                    logger.warning(f"[AgenticChat] {fb} fallback also failed: {llm_result['error'][:150]}")
                                    # Groq fallback: also try JSON mode
                                    if fb == "groq" and "tool_use_failed" in llm_result.get("error", ""):
                                        using_json_mode = True
                                        llm_result = await _call_llm_json_mode(
                                            client=client, api_key=api_key,
                                            messages=call_messages, tools_prompt=tools_prompt,
                                        )
                                        if not llm_result.get("error"):
                                            break

                        if llm_result.get("error"):
                            yield f"event: error\ndata: {json.dumps({'error': llm_result['error']})}\n\n"
                            break

                    usage = llm_result.get("usage", {})
                    total_tokens += usage.get("total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
                    text_content = llm_result.get("text", "")
                    tool_calls = llm_result.get("tool_calls", [])

                    # ── Safety net: detect JSON action text from model ──
                    if not tool_calls and text_content:
                        try:
                            parsed_action = json.loads(text_content)
                            if isinstance(parsed_action, dict) and parsed_action.get("action") == "tool_call":
                                t_name = parsed_action.get("tool", "")
                                t_args = parsed_action.get("args", {})
                                if t_name:
                                    tool_calls = [{"id": f"json_{t_name}", "name": t_name, "arguments": json.dumps(t_args)}]
                                    text_content = parsed_action.get("reasoning", "")
                                    using_json_mode = True
                                    print(f"[Runtime] Detected JSON action in text → parsed tool_call: {t_name}", flush=True)
                        except (json.JSONDecodeError, TypeError, ValueError):
                            pass  # Not JSON, treat as normal text

                    # ── Model returned tool calls → execute them ──
                    if tool_calls:
                        # Append assistant message (format depends on mode)
                        if using_json_mode or llm_result.get("json_mode"):
                            # JSON mode: plain text messages only
                            messages.append({"role": "assistant", "content": text_content or f"Calling {tool_calls[0]['name']}..."})
                        elif provider in ("openai", "groq"):
                            messages.append({
                                "role": "assistant", "content": text_content or None,
                                "tool_calls": [
                                    {"id": tc["id"], "type": "function",
                                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                                    for tc in tool_calls
                                ],
                            })
                        elif provider == "anthropic":
                            blocks = []
                            if text_content:
                                blocks.append({"type": "text", "text": text_content})
                            for tc in tool_calls:
                                try:
                                    inp = json.loads(tc["arguments"])
                                except (json.JSONDecodeError, TypeError):
                                    inp = {}
                                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": inp})
                            messages.append({"role": "assistant", "content": blocks})

                        # Execute each tool call
                        for tc in tool_calls:
                            tool_name = tc["name"]
                            try:
                                tool_args = json.loads(tc["arguments"])
                            except (json.JSONDecodeError, TypeError):
                                tool_args = {}

                            yield f"event: tool_call\ndata: {json.dumps({'tool': tool_name, 'args': tool_args, 'loop': loop_count})}\n\n"

                            # Update session tracker
                            tdef_cat = TOOL_DEFS.get(tool_name, {}).get("category", "custom")
                            session_tracker["tools_called"].append({"tool": tool_name, "category": tdef_cat, "loop": loop_count})
                            session_tracker["total_tool_calls"] += 1
                            session_tracker["total_loops"] = loop_count
                            session_tracker["total_tokens"] = total_tokens
                            session_tracker["elapsed_seconds"] = round(time.time() - start_time, 2)
                            if tdef_cat not in session_tracker["skills_used"]:
                                session_tracker["skills_used"].append(tdef_cat)

                            # Execute tool handler (with observability)
                            _obs_ctx = _agentic_observer.observe(
                                tool_name, user_id=user_id, session_id=conv_id,
                                loop_number=loop_count, provider=provider, args=tool_args,
                            )
                            _obs_handle = await _obs_ctx.__aenter__()
                            _obs_tool_start = time.time()
                            tdef = TOOL_DEFS.get(tool_name)
                            is_custom_dynamic = tool_name in user_custom_tools
                            if not tdef and not is_custom_dynamic:
                                tool_result = {"error": f"Tool '{tool_name}' not found."}
                            elif is_custom_dynamic:
                                try:
                                    tool_result = await _execute_dynamic_custom_tool(tool_name, tool_args, tool_context)
                                except Exception as e:
                                    tool_result = {"error": str(e)[:500]}
                            elif tool_name not in enabled:
                                tool_result = {"error": f"Tool '{tool_name}' not enabled."}
                            else:
                                handler_key = tdef["handler"]
                                if handler_key in CUSTOM_HANDLERS:
                                    try:
                                        tool_result = await CUSTOM_HANDLERS[handler_key](tool_args, tool_context)
                                    except Exception as e:
                                        tool_result = {"error": str(e)[:500]}
                                else:
                                    handler = executor.tool_handlers.get(handler_key)
                                    if not handler:
                                        tool_result = {"error": f"No handler for '{tool_name}'"}
                                    else:
                                        try:
                                            class _Ctx:
                                                context = tool_context
                                                id = "agentic-chat"
                                                user_id = tool_context.get("user_id", "anonymous")
                                                current_goal = None
                                                status = "running"
                                                loop_count = 0
                                                total_tool_calls = 0
                                                total_tokens_used = 0
                                                error_message = None
                                            tool_result = await handler(tool_args, session=_Ctx())
                                        except Exception as e:
                                            tool_result = {"error": str(e)[:500]}

                            # Finalize observability context
                            result_str = json.dumps(tool_result, default=str)
                            _truncated = len(result_str) > 8000
                            if _truncated:
                                result_str = result_str[:8000] + "...(truncated)"
                            if isinstance(tool_result, dict) and tool_result.get("error"):
                                _obs_handle.set_error(tool_result["error"])
                            else:
                                _obs_handle.set_result(result_str)
                            _obs_handle.set_truncated(_truncated)
                            await _obs_ctx.__aexit__(None, None, None)

                            yield f"event: tool_result\ndata: {json.dumps({'tool': tool_name, 'result': result_str[:4000], 'loop': loop_count})}\n\n"

                            # Append tool result (JSON mode uses plain messages, native uses structured)
                            if using_json_mode or llm_result.get("json_mode"):
                                messages.append({"role": "user", "content": f"Tool result for {tool_name}:\n{result_str}"})
                            else:
                                for msg in _build_tool_result_messages(provider, tc["id"], tool_name, result_str):
                                    messages.append(msg)

                        # ── Runtime Intelligence: Trim context window after tool results ──
                        try:
                            _ctx_mgr = ContextWindowManager(model)
                            _tools_tok = _ctx_mgr.estimate_tools_tokens(call_tools)
                            messages = _ctx_mgr.trim_to_fit(messages, _tools_tok)
                        except Exception as _trim_err:
                            logger.debug(f"[Runtime] Context trim skipped: {_trim_err}")

                        continue  # Loop back for next LLM call

                    # ── Model returned text only → final response ──
                    else:
                        content = text_content or "No response from model."
                        if conv_id and content:
                            await _save_message(conv_id, user_id, "assistant", content, tokens=total_tokens)
                        yield f"event: response\ndata: {json.dumps({'content': content, 'loop': loop_count, 'tokens': total_tokens, 'provider': provider, 'model': model})}\n\n"
                        break

            elapsed = round(time.time() - start_time, 2)
            yield f"event: done\ndata: {json.dumps({'loops': loop_count, 'tokens': total_tokens, 'elapsed_seconds': elapsed, 'provider': provider, 'model': model, 'tools_called_count': session_tracker['total_tool_calls'], 'skills_used': session_tracker['skills_used']})}\n\n"

        except Exception as e:
            logger.exception("Agentic chat error")
            yield f"event: error\ndata: {json.dumps({'error': str(e)[:500]})}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/health")
async def agentic_chat_health():
    executor = _get_executor()
    available = [p for p, k in PROVIDER_KEYS.items() if k]
    return {
        "status": "healthy",
        "tools_available": len(TOOL_DEFS),
        "tool_categories": list(set(t.get("category", "") for t in TOOL_DEFS.values())),
        "tool_handlers_registered": len(executor.tool_handlers),
        "custom_handlers": list(CUSTOM_HANDLERS.keys()),
        "providers_available": available,
        "default_provider": PROVIDER_FALLBACK_ORDER[0] if available else "none",
        "mode": "native-tool-calling",
    }


# ── Observability API Endpoints ──

@router.get("/observability/summary")
async def tool_observability_summary(request: Request):
    """Get observability summary across all systems (agentic_chat, public_chat, executor)."""
    from .rg_tool_registry.observability import ToolObserver; get_observer = lambda name: ToolObserver(system=name)
    systems = ["agentic_chat", "public_chat", "executor"]
    result = {}
    for sys_name in systems:
        obs = get_observer(sys_name)
        result[sys_name] = obs.get_summary()
    return result


@router.get("/observability/tools")
async def tool_observability_all_tools(request: Request):
    """Get per-tool stats across all systems."""
    from .rg_tool_registry.observability import ToolObserver; get_observer = lambda name: ToolObserver(system=name)
    systems = ["agentic_chat", "public_chat", "executor"]
    result = {}
    for sys_name in systems:
        obs = get_observer(sys_name)
        result[sys_name] = obs.get_all_stats()
    return result


@router.get("/observability/tool/{tool_name}")
async def tool_observability_single_tool(tool_name: str, request: Request):
    """Get stats for a specific tool across all systems."""
    from .rg_tool_registry.observability import ToolObserver; get_observer = lambda name: ToolObserver(system=name)
    systems = ["agentic_chat", "public_chat", "executor"]
    result = {}
    for sys_name in systems:
        obs = get_observer(sys_name)
        stats = obs.get_tool_stats(tool_name)
        if stats:
            result[sys_name] = stats
    return result or {"error": f"No data for tool '{tool_name}'"}


@router.get("/observability/recent")
async def tool_observability_recent(limit: int = 50, request: Request = None):
    """Get recent tool call records across all systems."""
    from .rg_tool_registry.observability import ToolObserver; get_observer = lambda name: ToolObserver(system=name)
    systems = ["agentic_chat", "public_chat", "executor"]
    records = []
    for sys_name in systems:
        obs = get_observer(sys_name)
        records.extend(obs.get_recent_records(limit=limit))
    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return records[:limit]


@router.get("/observability/session/{session_id}")
async def tool_observability_session(session_id: str, request: Request):
    """Get all tool calls for a specific session/conversation."""
    from .rg_tool_registry.observability import ToolObserver; get_observer = lambda name: ToolObserver(system=name)
    systems = ["agentic_chat", "public_chat", "executor"]
    records = []
    for sys_name in systems:
        obs = get_observer(sys_name)
        records.extend(obs.get_records_for_session(session_id))
    return records


@router.get("/observability/user/{user_id}")
async def tool_observability_user(user_id: str, limit: int = 100, request: Request = None):
    """Get recent tool calls for a specific user."""
    from .rg_tool_registry.observability import ToolObserver; get_observer = lambda name: ToolObserver(system=name)
    systems = ["agentic_chat", "public_chat", "executor"]
    records = []
    for sys_name in systems:
        obs = get_observer(sys_name)
        records.extend(obs.get_records_for_user(user_id, limit=limit))
    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return records[:limit]
