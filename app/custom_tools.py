"""Custom tool CRUD and execution helpers.

Used by executor.py and routers.py for user-created
dynamic tools stored in the DB.
"""

import json
import logging
import os
import time
from typing import Any, Dict

import httpx
from sqlalchemy import text as sa_text

from .db import engine as _db_engine
from .executor import _llm_client
from .rg_tool_registry.builtin_tools import build_registry
from .rg_tool_registry.registry import ToolAccess

logger = logging.getLogger(__name__)

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:8000")

# Build TOOL_DEFS from the canonical registry (for name-conflict checks)
_registry = build_registry()
TOOL_DEFS = {t.name: t for t in _registry.get_tools(access=ToolAccess.REGISTERED)}

# Cache for user custom tools (user_id -> {tool_name: tool_def})
_custom_tools_cache: Dict[str, Dict[str, Any]] = {}
_custom_tools_cache_ts: Dict[str, float] = {}
CUSTOM_TOOLS_CACHE_TTL = 60  # seconds


async def _load_user_custom_tools(user_id: str) -> Dict[str, Any]:
    """Load custom tools from DB for a user (with cache).
    Includes the user's own tools AND all platform-wide shared tools."""
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
                "FROM agentic_custom_tools WHERE (user_id = :uid OR is_shared = TRUE) AND is_active = TRUE"
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
        logger.warning(f"Failed to load custom tools for {user_id}: {e}")

    _custom_tools_cache[user_id] = tools
    _custom_tools_cache_ts[user_id] = now
    return tools


def _invalidate_custom_tools_cache(user_id: str):
    """Clear cached custom tools for a user so next request reloads from DB."""
    _custom_tools_cache.pop(user_id, None)
    _custom_tools_cache_ts.pop(user_id, None)


# ─────────────────────────────────────────────
#  CRUD helpers (imported by executor.py)
# ─────────────────────────────────────────────

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
            parameters = json.loads(parameters)
        except Exception:
            parameters = {}

    http_method = (args.get("http_method") or "GET").upper()
    if http_method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        http_method = "GET"

    request_body = args.get("request_body")
    if isinstance(request_body, str):
        try:
            request_body = json.loads(request_body)
        except Exception:
            request_body = None

    category = (args.get("category") or "custom").strip()
    is_shared = bool(args.get("is_shared", False))

    try:
        async with _db_engine.begin() as conn:
            await conn.execute(sa_text("""
                INSERT INTO agentic_custom_tools (user_id, tool_name, description, category,
                    parameters, http_method, endpoint_url, request_body_template, is_shared)
                VALUES (:uid, :name, :desc, :cat, CAST(:params AS jsonb), :method, :url, CAST(:body AS jsonb), :shared)
                ON CONFLICT (user_id, tool_name) DO UPDATE SET
                    description = EXCLUDED.description,
                    category = EXCLUDED.category,
                    parameters = EXCLUDED.parameters,
                    http_method = EXCLUDED.http_method,
                    endpoint_url = EXCLUDED.endpoint_url,
                    request_body_template = EXCLUDED.request_body_template,
                    is_shared = EXCLUDED.is_shared,
                    is_active = TRUE,
                    updated_at = NOW()
            """), {
                "uid": user_id, "name": tool_name, "desc": description,
                "cat": category,
                "params": json.dumps(parameters) if not isinstance(parameters, str) else parameters,
                "method": http_method, "url": endpoint_url,
                "body": json.dumps(request_body) if request_body else None,
                "shared": is_shared,
            })
        _invalidate_custom_tools_cache(user_id)
        if is_shared:
            _custom_tools_cache.clear()
            _custom_tools_cache_ts.clear()
        return {
            "success": True,
            "message": f"Tool '{tool_name}' created successfully!" + (" Available platform-wide." if is_shared else " Available in your sessions."),
            "tool": {
                "name": tool_name, "description": description, "category": category,
                "parameters": parameters, "http_method": http_method,
                "endpoint_url": endpoint_url, "request_body": request_body, "is_shared": is_shared,
            }
        }
    except Exception as e:
        return {"error": f"Failed to create tool: {str(e)[:300]}"}


async def _custom_list_tools(args: dict, ctx: dict) -> dict:
    """List all custom tools for the user + all shared platform tools."""
    user_id = ctx.get("user_id", "")
    if not user_id:
        return {"error": "Authentication required"}
    try:
        tools = []
        async with _db_engine.begin() as conn:
            rows = await conn.execute(sa_text(
                "SELECT tool_name, description, category, parameters, http_method, "
                "endpoint_url, request_body_template, created_at, is_active, is_shared, user_id "
                "FROM agentic_custom_tools WHERE (user_id = :uid OR is_shared = TRUE) AND is_active = TRUE "
                "ORDER BY is_shared DESC, created_at DESC"
            ), {"uid": user_id})
            for row in rows:
                tools.append({
                    "name": row[0], "description": row[1], "category": row[2],
                    "parameters": row[3], "http_method": row[4], "endpoint_url": row[5],
                    "request_body": row[6], "created_at": str(row[7]) if row[7] else None,
                    "is_active": row[8], "is_shared": row[9], "owned_by_you": row[10] == user_id,
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


# ─────────────────────────────────────────────
#  Dynamic tool execution (imported by routers.py)
# ─────────────────────────────────────────────

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
        req_body = {k: v for k, v in args.items()}

    headers = {
        "x-user-id": user_id,
        "x-org-id": ctx.get("org_id", ""),
        "x-user-role": ctx.get("user_role", "user"),
    }
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


# ─────────────────────────────────────────────
#  Autonomous Tool Builder (uses UnifiedLLMClient)
# ─────────────────────────────────────────────

async def _custom_auto_build_tool(args: dict, ctx: dict) -> dict:
    """LLM designs, validates, and registers a new tool at runtime."""
    user_id = ctx.get("user_id", "")
    if not user_id:
        return {"error": "Authentication required to build tools"}

    capability = (args.get("capability") or "").strip()
    if not capability:
        return {"error": "capability is required — describe what the tool should do"}

    category = (args.get("category") or "custom").strip()
    is_shared = bool(args.get("is_shared", True))

    design_prompt = f"""Design a platform tool based on this capability request:
"{capability}"

You must output ONLY valid JSON with these fields:
{{
  "tool_name": "snake_case_name (unique, descriptive)",
  "description": "clear description of what the tool does",
  "endpoint_url": "the API endpoint URL this tool calls (use {{{{param}}}} for path params)",
  "http_method": "GET or POST or PUT or DELETE",
  "parameters": {{"param_name": "param description", ...}},
  "request_body": null or {{"field": "{{{{param_name}}}}"}},
  "category": "{category}"
}}

Rules:
- tool_name must be snake_case, 3-50 chars, no spaces
- endpoint_url can be relative (prefixed with gateway) or absolute
- For platform API tools, use relative paths like /api/v1/service/endpoint
- For external API tools, use full URLs
- parameters should describe what each param does
- Output ONLY the JSON object, nothing else"""

    try:
        resp = await _llm_client.complete(
            {
                "messages": [{"role": "user", "content": design_prompt}],
                "temperature": 0.1,
                "max_tokens": 500,
            }
        )
        content = (resp.content or "").strip()
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        design_result = json.loads(content)
    except Exception as e:
        return {"error": f"Failed to design tool: {str(e)[:200]}"}

    # Safety scan
    tool_name = (design_result.get("tool_name") or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not tool_name or len(tool_name) < 3:
        return {"error": f"LLM generated invalid tool_name: {tool_name!r}"}

    _FORBIDDEN_URL_PATTERNS = [
        "127.0.0.1", "localhost", "0.0.0.0", "metadata.google",
        "169.254.169.254", "file://", "ftp://", "../", "..\\",
    ]
    endpoint_url = (design_result.get("endpoint_url") or "").strip()
    for pattern in _FORBIDDEN_URL_PATTERNS:
        if pattern in endpoint_url.lower():
            return {"error": f"Safety scan failed: endpoint_url contains forbidden pattern '{pattern}'"}

    # Register via create_tool
    create_args = {
        "tool_name": tool_name,
        "description": design_result.get("description", capability),
        "endpoint_url": endpoint_url,
        "http_method": design_result.get("http_method", "GET"),
        "parameters": design_result.get("parameters", {}),
        "request_body": design_result.get("request_body"),
        "category": design_result.get("category", category),
        "is_shared": is_shared,
    }
    result = await _custom_create_tool(create_args, ctx)

    if result.get("success"):
        result["auto_built"] = True
        result["design"] = design_result
        result["message"] = (
            f"Tool '{tool_name}' auto-built and registered! "
            f"Category: {create_args['category']}. "
            + ("Available platform-wide." if is_shared else "Available in your sessions.")
            + f" You can now use it by calling '{tool_name}'."
        )
    return result


async def _custom_check_tool_exists(args: dict, ctx: dict) -> dict:
    """Check if a capability exists as an existing tool."""
    capability = (args.get("capability") or "").strip().lower()
    if not capability:
        return {"error": "capability is required"}

    matches = []
    for tname, tdef in TOOL_DEFS.items():
        desc = (getattr(tdef, "description", "") or "").lower()
        name_lower = tname.lower()
        if any(word in name_lower or word in desc for word in capability.split()):
            matches.append({
                "name": tname,
                "description": getattr(tdef, "description", ""),
                "category": str(getattr(tdef, "category", "unknown")),
                "source": "built-in",
            })

    user_id = ctx.get("user_id", "")
    if user_id:
        try:
            custom = await _load_user_custom_tools(user_id)
            for tname, tdef in custom.items():
                desc = (tdef.get("desc") or "").lower()
                if any(word in tname.lower() or word in desc for word in capability.split()):
                    matches.append({
                        "name": tname,
                        "description": tdef.get("desc", ""),
                        "category": tdef.get("category", "custom"),
                        "source": "custom",
                    })
        except Exception:
            pass

    if matches:
        return {
            "found": True, "matches": matches[:10], "count": len(matches),
            "message": f"Found {len(matches)} matching tool(s) for '{capability}'.",
        }
    return {
        "found": False, "matches": [], "count": 0,
        "message": (
            f"No existing tool matches '{capability}'. "
            "You can create one using auto_build_tool with a description of what the tool should do."
        ),
        "suggestion": "auto_build_tool",
    }
