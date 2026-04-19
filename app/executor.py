"""Autonomous agent execution loop."""

import asyncio
import json
import os
import time
import uuid
from datetime import datetime
import ipaddress
import re
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from html import unescape

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .rg_tool_registry.observability import ToolObserver
_executor_observer = ToolObserver(system="executor")

from .config import settings
from .models import (
    AgentDefinition, AgentSession, AgentStep, AgentPlan,
    ToolDefinition, WorkflowTrigger
)
from .safety import safety_envelope, approval_manager
from .planner import tool_planner, goal_decomposer
from .verifier import verifier_agent, VerificationResult, VerificationReport
from .loop_stabilizer import loop_stabilizer, StabilityAction
from .policy_engine import (
    get_policy_engine, PolicyContext, PolicyDecision, AutonomyMode
)
from .learning_loop import get_learning_loop
from .agent_wallet import get_wallet_manager
from .value_drift_monitor import get_drift_manager

# Execution Gate for dual-mode autonomy
try:
    from shared.agent.execution_gate import (
        get_execution_gate, ExecutionRequest, ExecutionDecision, DecisionType
    )
    from shared.agent.autonomy_mode import RiskLevel
    EXECUTION_GATE_AVAILABLE = True
except ImportError:
    EXECUTION_GATE_AVAILABLE = False
    get_execution_gate = None

# Tool-level sandbox boundary
try:
    from shared.agent.sandbox import SandboxBoundary, create_default_sandbox
    SANDBOX_BOUNDARY_AVAILABLE = True
except ImportError:
    SANDBOX_BOUNDARY_AVAILABLE = False
    SandboxBoundary = None
    create_default_sandbox = None

import logging
logger = logging.getLogger(__name__)

# ── LLM Client via unified HTTP service (no rg_llm dependency) ──
LLM_SERVICE_URL = os.getenv("LLM_SERVICE_URL", "http://llm_service:8000").rstrip("/")


class _HTTPLLMResponse:
    """Lightweight response wrapper matching the rg_llm interface."""
    def __init__(self, content: str, provider: str, model: str, usage: dict, was_fallback: bool = False, fallback_chain: list = None):
        self.content = content
        self.provider = provider
        self.model = model
        self.usage = usage
        self.was_fallback = was_fallback
        self.fallback_chain = fallback_chain or []


class _HTTPLLMClient:
    """Calls the unified LLM service via HTTP — drop-in replacement for rg_llm."""

    def __init__(self, fallback_order=None):
        self.fallback_order = fallback_order or ["groq", "openai", "anthropic", "google"]

    async def complete(self, request, user_keys=None):
        payload = {
            "messages": request.get("messages") if isinstance(request, dict) else getattr(request, "messages", []),
            "stream": False,
        }
        # Extract fields from request (dict or object)
        for field in ("provider", "model", "temperature", "max_tokens", "response_format", "tools"):
            val = request.get(field) if isinstance(request, dict) else getattr(request, field, None)
            if val is not None:
                payload[field] = val
        if user_keys:
            payload["user_api_keys"] = user_keys

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(f"{LLM_SERVICE_URL}/llm/chat/completions", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    choice = (data.get("choices") or [{}])[0]
                    msg = choice.get("message", {})
                    return _HTTPLLMResponse(
                        content=msg.get("content", ""),
                        provider=data.get("provider", "unknown"),
                        model=data.get("model", ""),
                        usage=data.get("usage", {}),
                    )
                else:
                    raise RuntimeError(f"LLM service returned {resp.status_code}: {resp.text[:200]}")
        except httpx.TimeoutException:
            raise RuntimeError("LLM service timed out")
        except httpx.ConnectError:
            raise RuntimeError("LLM service unreachable")


_llm_client = _HTTPLLMClient(
    fallback_order=["groq", "openai", "anthropic", "google"],
)

# Auth service config (for BYOK key fetching)
AUTH_SERVICE_URL = os.getenv("AUTH_URL", "http://auth_service:8000")
_INTERNAL_SERVICE_KEY = os.getenv("AUTH_INTERNAL_SERVICE_KEY") or os.getenv("INTERNAL_SERVICE_KEY") or ""


# DSID-P Protocol Integration
try:
    from app.reputation_trust import AgentTrustScore, get_trust_tier, TrustTier
    from app.semantic_taxonomy import SemanticRiskRating, get_agent_cluster
    DSIDP_AVAILABLE = True
except ImportError:
    DSIDP_AVAILABLE = False
    AgentTrustScore = None
    TrustTier = None

# DSID-P Ethical Pillars (Section 36)
ETHICAL_PILLARS = {
    "human_oversight": {
        "name": "Human Oversight & Accountability",
        "blocked_actions": ["autonomous_deploy", "self_modify_governance", "bypass_approval"],
    },
    "transparency": {
        "name": "Transparency & Explainability",
        "requires_logging": True,
    },
    "privacy": {
        "name": "Privacy & Agency Protection",
        "blocked_patterns": ["exfiltrate", "leak_data", "share_pii"],
    },
    "fairness": {
        "name": "Fairness & Non-Discrimination",
        "requires_bias_check": True,
    },
    "safety": {
        "name": "Safety & Robustness",
        "max_risk_level": 4,  # Block SRR-5 without approval
    },
    "governance": {
        "name": "Governance & Redress Mechanisms",
        "requires_audit_trail": True,
    },
    "sovereignty": {
        "name": "Sovereign & Organizational Control",
        "enforce_tenant_isolation": True,
    },
}

# Import delegation for cross-service escalation
try:
    from shared.agent.delegation import agent_delegator, AgentRole
    DELEGATION_AVAILABLE = True
except ImportError:
    DELEGATION_AVAILABLE = False
    agent_delegator = None


class AgentExecutor:
    """Executes autonomous agent loops with safety controls."""

    # Structural prompt — provides execution framework only.
    # The agent's personal system_prompt is the primary directive (injected
    # into the system message by _get_next_action).  This template just
    # supplies the current goal, context, history, available tools, and
    # the JSON response format.
    EXECUTION_FRAME = """Goal: {goal}

Context:
{context}

Previous steps:
{history}

You have access to 159+ tools and 44 platform services (560+ APIs). Call any by name.
Tools: web_search, fetch_url, execute_code, generate_image, gmail_send, slack_send, http_request, dev_tool, memory_read, memory_write, create_rabbit_post, google_calendar, figma, etc.
APIs: Use discover_services(category="ai|core|agents|community|developer|integrations|blockchain|storage") to find services.
      Use platform_api(service="name", endpoint="/path", method="GET|POST", body={{...}}) to call any service API.
      Use discover_api(service="name") to list a service's endpoints.

Rules:
- Call tools by exact name. If a tool fails, do NOT retry.
- Max 12 tool calls per session. Then you MUST respond.
- Only take actions when the goal explicitly asks to create/post/send/modify.
- Questions: gather info then answer directly.
- Missing API key: tell user to add it in Settings > API Keys.

Respond in JSON:
{{
    "reasoning": "brief thought",
    "action": "tool_call|respond",
    "tool_name": "exact tool name",
    "tool_input": {{}},
    "response": "final answer if action is respond",
    "goal_achieved": true/false
}}"""

    DEFAULT_SYSTEM_PROMPT = """You are a DevSwat autonomous AI agent — a powerful, goal-driven agent that executes tasks end-to-end using tools.

<execution_rules>
- You are AUTONOMOUS. Do not ask for permission — execute the full task using available tools.
- Think step-by-step: analyze what's needed, use tools to gather information, take actions, verify results.
- Use tools aggressively. Every claim must be backed by tool output. Never fabricate data, IDs, URLs, or results.
- If a tool call fails, try a different approach. Try at least 3 strategies before reporting failure.
- When the goal is achieved, provide a clear, structured summary of what was done and the results.
</execution_rules>

<tool_discipline>
- ALWAYS call the tool first, then describe the result. Never claim an action was taken without calling the tool.
- Batch independent tool calls when possible for efficiency.
- For research tasks: use web_search to find sources, fetch_url to read full content, memory_write to save findings.
- For code tasks: read files before editing, verify changes after writing, run tests if available.
- For data tasks: validate inputs, process systematically, present structured output.
</tool_discipline>

<output_quality>
- Be concise and direct. Lead with results, not process.
- Use Markdown formatting: **bold** for key terms, `code` for technical values, tables for comparisons.
- Structure long responses with headings and bullet points.
- End with a clear status: what was accomplished, what's pending, any issues found.
- Never apologize or hedge. State facts confidently.
</output_quality>"""

    def __init__(self):
        # === UNIFIED TOOL REGISTRY: Single source of truth ===
        from .rg_tool_registry.builtin_tools import build_registry
        self._registry = build_registry()

        # Handler map: tool_name → executor method
        # The _tool_* methods contain the actual implementation logic.
        # This map wires them to the unified registry tool names.
        self._handler_map: Dict[str, callable] = {
            # Search
            "web_search": self._tool_web_search,
            "fetch_url": self._tool_fetch_url,
            "read_webpage": self._tool_fetch_url,
            "scrape_page": self._tool_scrape_page,
            "deep_research": self._tool_deep_research,
            "read_many_pages": self._tool_fetch_url,
            # Search variants (all wrap web_search with query prefix)
            "news_search": self._tool_news_search,
            "image_search": self._tool_image_search,
            "youtube_search": self._tool_youtube_search,
            "reddit_search": self._tool_reddit_search,
            "wikipedia": self._tool_wikipedia,
            "weather": self._tool_weather,
            "stock_crypto": self._tool_stock_crypto,
            "places_search": self._tool_places_search,
            # Memory
            "memory_read": self._tool_memory_read,
            "memory.read": self._tool_memory_read,
            "memory_write": self._tool_memory_write,
            "memory.write": self._tool_memory_write,
            "memory_search": self._tool_memory_read,
            "memory_stats": self._tool_memory_stats,
            # Community (rabbit)
            "create_rabbit_post": self._tool_create_rabbit_post,
            "list_rabbit_communities": self._tool_list_rabbit_communities,
            "create_rabbit_community": self._tool_create_rabbit_community,
            "list_rabbit_posts": self._tool_list_rabbit_posts,
            "get_rabbit_post": self._tool_get_rabbit_post,
            "get_rabbit_community": self._tool_get_rabbit_community,
            "delete_rabbit_post": self._tool_delete_rabbit_post,
            "create_rabbit_comment": self._tool_create_rabbit_comment,
            "delete_rabbit_comment": self._tool_delete_rabbit_comment,
            "list_rabbit_comments": self._tool_list_rabbit_comments,
            "search_rabbit_posts": self._tool_search_rabbit_posts,
            "rabbit_vote": self._tool_rabbit_vote,
            # Developer
            "http_request": self._tool_http_request,
            "external_http_request": self._tool_external_http_request,
            "execute_code": self._tool_execute_code,
            "dev_tool": self._tool_dev_bridge,
            "run_command": self._tool_execute_code,
            "get_current_time": self._tool_get_current_time,
            "get_system_info": self._tool_get_system_info,
            "send_email": self._tool_send_email,
            # Media
            "generate_image": self._tool_generate_image,
            "generate_audio": self._tool_generate_audio,
            "generate_music": self._tool_generate_music,
            "generate_video": self._tool_generate_video,
            "generate_chart": self._tool_generate_chart,
            "visualize": self._tool_generate_chart,
            # Integrations
            "gmail_send": self._tool_gmail_send,
            "gmail_read": self._tool_gmail_read,
            "slack_send": self._tool_slack_send_message,
            "slack_send_message": self._tool_slack_send_message,
            "slack_list_channels": self._tool_slack_list_channels,
            "slack_read": self._tool_slack_read_messages,
            "slack_read_messages": self._tool_slack_read_messages,
            "figma": self._tool_figma,
            "google_calendar": self._tool_google_calendar,
            "google_drive": self._tool_google_drive,
            "sigma": self._tool_sigma,
            # === UNIFIED API CATALOG: Call any platform service API ===
            "platform_api": self._tool_platform_api,
            "platform_api_call": self._tool_platform_api,
            "platform_api_search": self._tool_discover_api,
            "discover_services": self._tool_discover_services,
            "discover_api": self._tool_discover_api,
            # === DYNAMIC TOOL MANAGEMENT ===
            "create_tool": self._tool_create_tool,
            "list_tools": self._tool_list_tools,
            "list_workspace_tools": self._tool_list_tools,
            "delete_tool": self._tool_delete_tool,
            "update_tool": self._tool_update_tool,
            "auto_build_tool": self._tool_auto_build_tool,
            "check_tool_exists": self._tool_check_tool_exists,
            # === Hash Sphere / Memory visualization ===
            "hash_sphere_search": self._tool_hash_sphere,
            "hash_sphere_anchor": self._tool_hash_sphere,
            "hash_sphere_hash": self._tool_hash_sphere,
            "hash_sphere_list_anchors": self._tool_hash_sphere,
            "hash_sphere_resonance": self._tool_hash_sphere,
            # === Session / snapshot ===
            "workspace_snapshot": self._tool_workspace_snapshot,
            "agent_snapshot": self._tool_workspace_snapshot,
            "run_snapshot": self._tool_workspace_snapshot,
            "session_log": self._tool_session_log,
            "present_options": self._tool_present_options,
            # Agent execution
            "run_agent": self._tool_run_agent,
            "schedule_agent": self._tool_schedule_agent,
        }

        # Tool-level sandbox boundary: rate limiting, arg validation, resource access control
        if SANDBOX_BOUNDARY_AVAILABLE and create_default_sandbox:
            self._sandbox = create_default_sandbox()
            print("[SANDBOX] SandboxBoundary ACTIVE — tool-level rate limiting + arg validation enabled", flush=True)
        else:
            self._sandbox = None
            print("[SANDBOX] SandboxBoundary NOT AVAILABLE — tool-level sandbox disabled", flush=True)

    def _build_auth_context(self, session: Optional[AgentSession]) -> 'AuthContext':
        """Build an AuthContext from the agent session for shared tools.

        Uses the gateway-injected x-user-* header values that were stored
        in the session context when the session was created.  No JWT is
        forwarded — internal services trust x-user-* headers.
        """
        from dataclasses import dataclass, field
        @dataclass
        class _AuthCtx:
            user_id: str = ""
            org_id: str = ""
            user_role: str = "user"
            is_superuser: bool = False
            unlimited_credits: bool = False
        user_id = str(session.user_id) if session and session.user_id else "agent-system"
        ctx = session.context if session else {}
        return _AuthCtx(
            user_id=user_id,
            org_id=ctx.get("org_id", ""),
            user_role=ctx.get("user_role", "user"),
            is_superuser=ctx.get("is_superuser", False),
            unlimited_credits=ctx.get("unlimited_credits", False),
        )

    async def _docker_http_get(
        self,
        *,
        url: str,
        accept: str,
        timeout_seconds: float,
        max_bytes: int,
        add_hosts: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        image = settings.AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_IMAGE
        memory = settings.AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_MEMORY
        cpus = settings.AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_CPUS

        python_code = (
            "import os,json,urllib.request;"
            "u=os.environ.get('URL','');"
            "a=os.environ.get('ACCEPT','*/*');"
            "t=float(os.environ.get('TIMEOUT','10'));"
            "m=int(os.environ.get('MAX_BYTES','1048576'));"
            "hdr={'User-Agent':'Genesis2026-AgentEngine/1.0 (+https://dev-swat.com)','Accept':a};"
            "out={};"
            "\ntry:\n"
            " req=urllib.request.Request(u,headers=hdr);"
            " resp=urllib.request.urlopen(req,timeout=t);"
            " status=getattr(resp,'status',200);"
            " ct=(resp.headers.get('content-type') or '').split(';')[0].strip().lower();"
            " raw=resp.read(m+1)[:m];"
            " txt=raw.decode('utf-8',errors='ignore');"
            " out={'status':status,'content_type':ct,'text':txt};"
            "\nexcept Exception as e:\n"
            " out={'error':str(e)};"
            "\nprint(json.dumps(out))\n"
        )

        name = f"agent_web_{uuid.uuid4().hex[:12]}"
        cmd = [
            "docker",
            "run",
            "--rm",
            f"--name={name}",
            f"--memory={memory}",
            f"--memory-swap={memory}",
            f"--cpus={cpus}",
            "--pids-limit=80",
            "--security-opt=no-new-privileges",
            "--cap-drop=ALL",
            "--user=nobody",
            "--read-only",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
            "-e",
            f"URL={url}",
            "-e",
            f"ACCEPT={accept}",
            "-e",
            f"TIMEOUT={timeout_seconds}",
            "-e",
            f"MAX_BYTES={max_bytes}",
        ]

        for host_entry in add_hosts or []:
            if host_entry:
                cmd.extend(["--add-host", host_entry])

        cmd.extend(
            [
                image,
                "python",
                "-c",
                python_code,
            ]
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=float(timeout_seconds) + 5.0,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return {"error": "Docker sandbox timeout"}

        if proc.returncode != 0:
            return {"error": (stderr or b"").decode("utf-8", errors="ignore")[:2000] or "Docker sandbox failed"}

        raw = (stdout or b"").decode("utf-8", errors="ignore").strip()
        try:
            return json.loads(raw) if raw else {"error": "Empty sandbox response"}
        except Exception:
            return {"error": "Invalid sandbox JSON", "raw": raw[:2000]}

    async def _sandbox_runner_http_get(
        self,
        *,
        url: str,
        accept: str,
        timeout_seconds: float,
        max_bytes: int,
        add_hosts: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        runner_url = (settings.SANDBOX_RUNNER_URL or "").rstrip("/")
        if not runner_url:
            return {"error": "Sandbox runner URL not configured"}

        headers: Dict[str, str] = {}
        if settings.SANDBOX_RUNNER_API_KEY:
            headers["x-sandbox-runner-key"] = settings.SANDBOX_RUNNER_API_KEY

        payload = {
            "url": url,
            "accept": accept,
            "timeout_seconds": float(timeout_seconds),
            "max_bytes": int(max_bytes),
            "add_hosts": list(add_hosts or []),
        }

        timeout = httpx.Timeout(
            float(timeout_seconds) + 2.0,
            connect=5.0,
        )

        _RETRYABLE_STATUSES = (502, 503, 504)
        _MAX_RETRIES = 2
        _RETRY_DELAY = 1.5

        t0 = time.monotonic()
        print(f"[SANDBOX-RUNNER] POST {runner_url}/v1/http-get url={url[:80]} timeout={timeout_seconds}", flush=True)

        resp = None
        last_error = None

        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    resp = await client.post(
                        f"{runner_url}/v1/http-get",
                        json=payload,
                        headers=headers,
                    )
                    if resp.status_code not in _RETRYABLE_STATUSES or attempt == _MAX_RETRIES:
                        break
                    print(f"[SANDBOX-RUNNER] {resp.status_code} on attempt {attempt+1}, retrying in {_RETRY_DELAY}s...", flush=True)
                    await asyncio.sleep(_RETRY_DELAY)
                except Exception as e:
                    last_error = e
                    if attempt == _MAX_RETRIES:
                        elapsed = int((time.monotonic() - t0) * 1000)
                        print(f"[SANDBOX-RUNNER] FAILED {elapsed}ms after {attempt+1} attempts: {e}", flush=True)
                        return {"error": f"Sandbox runner request failed: {e}"}
                    print(f"[SANDBOX-RUNNER] attempt {attempt+1} failed ({e}), retrying in {_RETRY_DELAY}s...", flush=True)
                    await asyncio.sleep(_RETRY_DELAY)

        if resp is None:
            elapsed = int((time.monotonic() - t0) * 1000)
            print(f"[SANDBOX-RUNNER] FAILED {elapsed}ms: no response", flush=True)
            return {"error": f"Sandbox runner request failed: {last_error or 'unknown'}"}

        elapsed = int((time.monotonic() - t0) * 1000)

        if resp.status_code == 401:
            print(f"[SANDBOX-RUNNER] 401 UNAUTHORIZED {elapsed}ms", flush=True)
            return {"error": "Sandbox runner unauthorized"}
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except Exception:
                detail = None
            print(f"[SANDBOX-RUNNER] HTTP {resp.status_code} {elapsed}ms detail={detail}", flush=True)
            if resp.status_code in _RETRYABLE_STATUSES:
                return {"error": f"Website blocked automated access (anti-bot protection). Try a different source or website for this information."}
            return {"error": detail or f"Sandbox runner HTTP {resp.status_code}"}

        print(f"[SANDBOX-RUNNER] OK {resp.status_code} {elapsed}ms len={len(resp.content)}", flush=True)
        try:
            return resp.json() if resp.content else {"error": "Empty sandbox runner response"}
        except Exception:
            return {"error": "Invalid sandbox runner JSON"}

    def _parse_ddg_redirect_url(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            uddg = qs.get("uddg", [None])[0]
            if uddg:
                return unquote(uddg)
        except Exception:
            pass
        return url

    def _extract_results_from_ddg_html(self, html: str) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        try:
            patterns = [
                r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                r'<a[^>]+class="result-link"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            ]
            for pattern in patterns:
                for m in re.finditer(pattern, html, flags=re.IGNORECASE | re.DOTALL):
                    url = m.group(1)
                    title_html = m.group(2)
                    title = self._strip_html(unescape(title_html or ""))
                    if not title:
                        continue

                    url = unescape(url)
                    if url.startswith("/l/") or "duckduckgo.com/l/" in url:
                        url = self._parse_ddg_redirect_url(url)
                    if url.startswith("//"):
                        url = "https:" + url
                    if not url.startswith("http"):
                        continue

                    results.append({"title": title[:200], "url": url})
                    if len(results) >= 10:
                        return results
        except Exception:
            return results
        return results

    async def _tool_web_search(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        query = (tool_input or {}).get("query")
        if not query or not isinstance(query, str):
            return {"error": "Missing or invalid 'query'"}

        url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json&no_redirect=1&no_html=1"
        headers = {
            "User-Agent": "Genesis2026-AgentEngine/1.0 (+https://dev-swat.com)",
            "Accept": "application/json",
        }

        if settings.AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_ENABLED:
            sandbox_resp = await self._sandbox_runner_http_get(
                url=url,
                accept=headers.get("Accept", "application/json"),
                timeout_seconds=float(settings.AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_TIMEOUT_SECONDS),
                max_bytes=512 * 1024,
                add_hosts=self._resolve_public_add_hosts("api.duckduckgo.com"),
            )
            if sandbox_resp.get("error"):
                return {"error": sandbox_resp.get("error")}

            try:
                status = int(sandbox_resp.get("status") or 0)
            except Exception:
                status = 0
            if status and status != 200:
                return {"error": f"HTTP {status}"}

            try:
                data = json.loads(sandbox_resp.get("text") or "{}")
            except Exception:
                return {"error": "DuckDuckGo returned invalid JSON"}

        else:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return {"error": f"HTTP {resp.status_code}"}

                try:
                    data = resp.json() if resp.content else {}
                except Exception:
                    return {"error": "DuckDuckGo returned invalid JSON"}

        results: List[Dict[str, Any]] = []

        def _collect(topic: Any) -> None:
            if isinstance(topic, dict) and topic.get("Text") and topic.get("FirstURL"):
                results.append({"title": topic.get("Text"), "url": topic.get("FirstURL")})
            if isinstance(topic, dict) and isinstance(topic.get("Topics"), list):
                for t in topic.get("Topics"):
                    _collect(t)

        for t in data.get("RelatedTopics") or []:
            _collect(t)

        if not results:
            try:
                html_headers = {
                    "User-Agent": headers.get("User-Agent", "Mozilla/5.0"),
                    "Accept": "text/html,application/xhtml+xml",
                }

                if settings.AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_ENABLED:
                    lite_url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}"
                    sandbox_html = await self._sandbox_runner_http_get(
                        url=lite_url,
                        accept=html_headers.get("Accept", "text/html"),
                        timeout_seconds=float(settings.AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_TIMEOUT_SECONDS),
                        max_bytes=512 * 1024,
                        add_hosts=self._resolve_public_add_hosts("lite.duckduckgo.com"),
                    )
                    if not sandbox_html.get("error") and sandbox_html.get("text"):
                        results = self._extract_results_from_ddg_html(sandbox_html.get("text") or "")
                else:
                    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=html_headers) as html_client:
                        html_resp = await html_client.get(
                            f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}",
                        )
                    if html_resp.status_code == 200 and html_resp.text:
                        results = self._extract_results_from_ddg_html(html_resp.text)
            except Exception:
                pass

        return {
            "query": query,
            "heading": data.get("Heading"),
            "abstract": data.get("Abstract"),
            "abstract_url": data.get("AbstractURL"),
            "results": results[:10],
        }

    async def _tool_scrape_page(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """scrape_page: flexible wrapper around fetch_url accepting various param names."""
        inp = tool_input or {}
        url = inp.get("url") or inp.get("page") or inp.get("website") or inp.get("link") or inp.get("target")
        if not url or not isinstance(url, str):
            # If user passed query-like param, treat as error with helpful message
            return {"error": "Missing 'url'. Provide: {\"url\": \"https://example.com\"}"}
        return await self._tool_fetch_url({"url": url}, session=session)

    async def _tool_deep_research(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """deep_research: accepts query/topic/subject/url — searches web, optionally fetches page."""
        inp = tool_input or {}
        query = inp.get("query") or inp.get("topic") or inp.get("subject") or inp.get("search") or inp.get("question")
        url = inp.get("url") or inp.get("page") or inp.get("link")

        results = {}

        # If a URL was provided, fetch it
        if url and isinstance(url, str):
            page_data = await self._tool_fetch_url({"url": url}, session=session)
            results["page_content"] = page_data

        # If a query was provided (or derive from URL), do web search
        if query and isinstance(query, str):
            search_data = await self._tool_web_search({"query": query}, session=session)
            results["search_results"] = search_data
        elif url and not query:
            # No explicit query — derive one from the URL
            search_data = await self._tool_web_search({"query": url}, session=session)
            results["search_results"] = search_data

        if not results:
            return {"error": "Provide 'query' and/or 'url'. Example: {\"query\": \"topic to research\"}"}

        return results

    def _is_public_address(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved or addr.is_unspecified:
                return False
            return True
        except Exception:
            return False

    def _resolve_and_validate_host(self, host: str) -> bool:
        if not host:
            return False

        try:
            ipaddress.ip_address(host)
            return self._is_public_address(host)
        except Exception:
            pass

        try:
            infos = socket.getaddrinfo(host, None)
        except Exception:
            return False

        ips = []
        for info in infos:
            sockaddr = info[4]
            if sockaddr and isinstance(sockaddr, tuple) and sockaddr[0]:
                ips.append(sockaddr[0])

        if not ips:
            return False

        return all(self._is_public_address(ip) for ip in ips)

    def _resolve_public_add_hosts(self, host: str) -> List[str]:
        if not host:
            return []

        try:
            infos = socket.getaddrinfo(host, None)
        except Exception:
            return []

        ips: List[str] = []
        for info in infos:
            sockaddr = info[4]
            if sockaddr and isinstance(sockaddr, tuple) and sockaddr[0]:
                ip = sockaddr[0]
                try:
                    if ipaddress.ip_address(ip).version != 4:
                        continue
                except Exception:
                    continue

                if ip not in ips and self._is_public_address(ip):
                    ips.append(ip)

        if not ips:
            return []

        return [f"{host}:{ips[0]}"]

    def _strip_html(self, html: str) -> str:
        # Limit input size to prevent catastrophic regex backtracking on huge pages
        if len(html) > 500_000:
            html = html[:500_000]
        # Remove script and style blocks (fixed backreference \1 not \\1)
        cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
        cleaned = unescape(cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    async def _tool_fetch_url(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        url = (tool_input or {}).get("url")
        if not url or not isinstance(url, str):
            return {"error": "Missing or invalid 'url'"}

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return {"error": "Only http/https URLs are allowed"}

        if not self._resolve_and_validate_host(parsed.hostname or ""):
            return {"error": "Blocked host"}

        port = parsed.port
        if port not in (None, 80, 443):
            return {"error": "Blocked port"}

        headers = {
            "User-Agent": "Genesis2026-AgentEngine/1.0 (+https://dev-swat.com)",
            "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.1",
        }

        max_bytes = 512 * 1024  # 512KB cap — prevents massive JS-heavy pages from blocking workers

        if settings.AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_ENABLED:
            sandbox_resp = await self._sandbox_runner_http_get(
                url=url,
                accept=headers.get("Accept", "*/*"),
                timeout_seconds=float(settings.AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_TIMEOUT_SECONDS),
                max_bytes=max_bytes,
                add_hosts=self._resolve_public_add_hosts(parsed.hostname or ""),
            )
            if sandbox_resp.get("error"):
                return {"url": url, "error": sandbox_resp.get("error")}

            status = int(sandbox_resp.get("status") or 0)
            content_type = (sandbox_resp.get("content_type") or "").strip().lower()
            raw = (sandbox_resp.get("text") or "").encode("utf-8", errors="ignore")

            if status >= 400:
                return {"url": url, "status": status, "error": f"HTTP {status}"}
        else:
            timeout = httpx.Timeout(15.0, connect=5.0)

            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
                resp = await client.get(url)
                status = resp.status_code
                content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                raw = resp.content or b""
                if len(raw) > max_bytes:
                    raw = raw[:max_bytes]

            if resp.status_code >= 400:
                return {"url": url, "status": resp.status_code, "error": f"HTTP {resp.status_code}"}

        text: Optional[str] = None
        if content_type in ("text/html", "application/xhtml+xml"):
            text = self._strip_html(raw.decode("utf-8", errors="ignore"))
        elif content_type.startswith("text/") or content_type in ("application/json", "application/xml", "application/xhtml+xml"):
            text = raw.decode("utf-8", errors="ignore")
        else:
            return {"url": url, "status": status, "content_type": content_type, "error": "Unsupported content type"}

        return {
            "url": url,
            "status": status,
            "content_type": content_type,
            "text": text[:20000] if text else "",
        }

    async def _tool_memory_read(self, tool_input: Dict[str, Any], *, session: AgentSession) -> Dict[str, Any]:
        query = (tool_input or {}).get("query")
        if not query or not isinstance(query, str) or not query.strip():
            return {"error": "Missing or invalid 'query'"}

        limit = (tool_input or {}).get("limit", 5)
        try:
            limit = int(limit)
        except Exception:
            limit = 5
        limit = max(1, min(limit, 25))

        retrieval_mode = str((tool_input or {}).get("retrieval_mode") or "hybrid").strip().lower()
        if retrieval_mode not in {"embedding", "hash_sphere", "hybrid"}:
            retrieval_mode = "hybrid"

        ctx = session.context or {}
        payload: Dict[str, Any] = {
            "query": query.strip(),
            "limit": limit,
            "use_vector_search": True,
            "retrieval_mode": retrieval_mode,
            "user_id": session.user_id,
            "org_id": ctx.get("org_id"),
            "agent_hash": ctx.get("agent_hash"),
            "team_id": ctx.get("team_id"),
            "chat_id": ctx.get("chat_id"),
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        url = f"{settings.MEMORY_SERVICE_URL.rstrip('/')}/memory/retrieve"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=payload)

        if resp.status_code != 200:
            return {"error": f"memory.read failed: HTTP {resp.status_code}", "detail": (resp.text or "")[:500]}

        try:
            data = resp.json()
        except Exception:
            data = []
        return {"memories": data}

    async def _tool_memory_write(self, tool_input: Dict[str, Any], *, session: AgentSession) -> Dict[str, Any]:
        content = (tool_input or {}).get("content")
        if not content or not isinstance(content, str) or not content.strip():
            return {"error": "Missing or invalid 'content'"}

        source = (tool_input or {}).get("source") or "agent_engine"
        metadata = (tool_input or {}).get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            metadata = {"raw": str(metadata)}

        generate_embedding = (tool_input or {}).get("generate_embedding", True)

        ctx = session.context or {}
        payload: Dict[str, Any] = {
            "chat_id": ctx.get("chat_id"),
            "user_id": session.user_id,
            "org_id": ctx.get("org_id"),
            "agent_hash": ctx.get("agent_hash"),
            "source": source,
            "content": content.strip(),
            "metadata": metadata,
            "generate_embedding": bool(generate_embedding),
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        url = f"{settings.MEMORY_SERVICE_URL.rstrip('/')}/memory/ingest"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)

        if resp.status_code != 200:
            return {"error": f"memory.write failed: HTTP {resp.status_code}", "detail": (resp.text or "")[:500]}

        try:
            return resp.json()
        except Exception:
            return {"ok": True}

    # ================================================================
    # PLATFORM ACTION TOOLS (backed by shared/tools/)
    # ================================================================

    async def _tool_create_rabbit_post(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Create a post on a Rabbit community — Rabbit services currently disabled."""
        return {"error": "Rabbit community services are currently disabled"}

    async def _tool_list_rabbit_communities(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """List available Rabbit communities — Rabbit services currently disabled."""
        return {"error": "Rabbit community services are currently disabled"}

    async def _tool_create_rabbit_community(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Create a new Rabbit community — Rabbit services currently disabled."""
        return {"error": "Rabbit community services are currently disabled"}

    async def _tool_http_request(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Make an HTTP request — TODO: reimplement via unified tool registry."""
        return {"error": "HTTP request tool not yet migrated to unified registry"}

    # ================================================================
    # GMAIL + SLACK TOOLS (Phase 2.5, backed by shared/tools/)
    # ================================================================

    async def _tool_gmail_send(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Send an email via Gmail integration."""
        return await self._tool_platform_api({
            "service": "notification", "endpoint": "/email/send", "method": "POST",
            "body": tool_input or {},
        }, session=session)

    async def _tool_gmail_read(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Read recent emails from Gmail inbox."""
        return await self._tool_platform_api({
            "service": "notification", "endpoint": "/email/inbox", "method": "GET",
            "body": tool_input or {},
        }, session=session)

    async def _tool_slack_send_message(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Send a message to a Slack channel."""
        return await self._tool_platform_api({
            "service": "notification", "endpoint": "/slack/send", "method": "POST",
            "body": tool_input or {},
        }, session=session)

    async def _tool_slack_list_channels(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """List Slack channels."""
        return await self._tool_platform_api({
            "service": "notification", "endpoint": "/slack/channels", "method": "GET",
            "body": tool_input or {},
        }, session=session)

    async def _tool_slack_read_messages(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Read recent messages from a Slack channel."""
        return await self._tool_platform_api({
            "service": "notification", "endpoint": "/slack/messages", "method": "GET",
            "body": tool_input or {},
        }, session=session)

    async def _tool_external_http_request(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Make an HTTP request to an external (public) API.

        Security: Only allows public IP addresses (no SSRF to internal services).
        Validates DNS resolution, blocks private/loopback/link-local IPs.
        Uses sandbox runner when available for additional isolation.
        """
        url = (tool_input or {}).get("url", "")
        if not url or not isinstance(url, str):
            return {"error": "Missing or invalid 'url'"}

        method = ((tool_input or {}).get("method") or "GET").upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            return {"error": f"Unsupported method: {method}"}

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return {"error": "Only http/https URLs are allowed"}

        hostname = parsed.hostname or ""
        if not hostname:
            return {"error": "Missing hostname in URL"}

        # Block internal/private hosts — this tool is for EXTERNAL APIs only
        if not self._resolve_and_validate_host(hostname):
            return {"error": f"Blocked host: {hostname}. Only public internet hosts are allowed. For internal platform APIs, use 'http_request' instead."}

        port = parsed.port
        if port not in (None, 80, 443, 8080, 8443):
            return {"error": f"Blocked port: {port}. Allowed: 80, 443, 8080, 8443."}

        body = (tool_input or {}).get("body")
        extra_headers = (tool_input or {}).get("headers") or {}
        if not isinstance(extra_headers, dict):
            extra_headers = {}

        # For GET requests, use sandbox if available
        if method == "GET" and settings.AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_ENABLED:
            accept = extra_headers.get("Accept", "application/json, text/plain, text/html;q=0.9")
            sandbox_resp = await self._sandbox_runner_http_get(
                url=url,
                accept=accept,
                timeout_seconds=20.0,
                max_bytes=512 * 1024,
                add_hosts=self._resolve_public_add_hosts(hostname),
            )
            if sandbox_resp.get("error"):
                return {"url": url, "error": sandbox_resp["error"]}
            return {
                "url": url,
                "status": sandbox_resp.get("status"),
                "content_type": sandbox_resp.get("content_type"),
                "data": sandbox_resp.get("text", "")[:10000],
            }

        # Direct HTTP for all methods (with security already validated above)
        headers = {
            "User-Agent": "Genesis2026-AgentEngine/1.0 (+https://dev-swat.com)",
            "Accept": "application/json, text/plain, */*",
        }
        headers.update(extra_headers)

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    json=body if body and method in ("POST", "PUT", "PATCH") else None,
                    headers=headers,
                )
                content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                try:
                    data = resp.json()
                except Exception:
                    data = resp.text[:10000]
                return {
                    "url": url,
                    "status": resp.status_code,
                    "content_type": content_type,
                    "data": data,
                }
        except Exception as e:
            return {"url": url, "error": f"External HTTP request failed: {e}"}

    async def _tool_execute_code(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Execute code in an isolated Docker sandbox via code_execution_service.

        Supported languages: python, javascript, typescript, bash.
        Code runs in a read-only container with no network, 256MB RAM, 30s timeout.
        """
        code = (tool_input or {}).get("code", "").strip()
        if not code:
            return {"error": "Missing 'code' — provide the code to execute."}

        language = (tool_input or {}).get("language", "python").lower().strip()
        supported = ["python", "javascript", "js", "typescript", "bash", "shell"]
        if language not in supported:
            return {"error": f"Unsupported language: {language}. Supported: {supported}"}

        timeout = (tool_input or {}).get("timeout")
        try:
            timeout = int(timeout) if timeout else 30
        except Exception:
            timeout = 30
        timeout = max(5, min(timeout, 60))  # Clamp 5-60s

        inputs = (tool_input or {}).get("inputs")

        code_exec_url = os.getenv("CODE_EXECUTION_SERVICE_URL", "http://code_execution_service:8002")
        payload = {
            "code": code,
            "language": language,
            "timeout": timeout,
        }
        if inputs and isinstance(inputs, list):
            payload["inputs"] = inputs

        headers = {}
        if session and session.user_id:
            headers["x-user-id"] = str(session.user_id)

        print(f"[EXECUTE_CODE] lang={language} timeout={timeout}s code={code[:80]!r}", flush=True)

        try:
            async with httpx.AsyncClient(timeout=float(timeout) + 10.0) as client:
                resp = await client.post(
                    f"{code_exec_url.rstrip('/')}/code/execute",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code != 200:
                    error_detail = ""
                    try:
                        error_detail = resp.json().get("detail", resp.text[:500])
                    except Exception:
                        error_detail = resp.text[:500]
                    return {"error": f"Code execution service error: {error_detail}"}

                data = resp.json()
                print(f"[EXECUTE_CODE] success={data.get('success')} exit={data.get('exit_code')}", flush=True)
                return {
                    "success": data.get("success", False),
                    "output": (data.get("output") or "")[:5000],
                    "error": data.get("error"),
                    "exit_code": data.get("exit_code"),
                    "language": language,
                }
        except Exception as e:
            print(f"[EXECUTE_CODE] Exception: {e}", flush=True)
            return {"error": f"Code execution failed: {str(e)}"}

    # ================================================================
    # INTEGRATION SKILL TOOLS (proxy to chat_service /skills/execute)
    # ================================================================

    async def _proxy_skill(self, skill_id: str, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Generic proxy: call chat_service /skills/execute for any integration skill."""
        message = (tool_input or {}).get("message") or (tool_input or {}).get("query") or ""
        if not message:
            return {"error": f"Missing 'message' or 'query' for {skill_id} skill."}

        chat_url = settings.CHAT_SERVICE_URL.rstrip("/")
        headers: Dict[str, str] = {}
        if session and session.user_id:
            headers["x-user-id"] = str(session.user_id)
            ctx = session.context or {}
            if ctx.get("user_role"):
                headers["x-user-role"] = str(ctx["user_role"])
            if ctx.get("is_superuser"):
                headers["x-is-superuser"] = "true"

        payload = {
            "skill_id": skill_id,
            "message": message,
            "context": (tool_input or {}).get("context") or {},
        }

        print(f"[SKILL:{skill_id.upper()}] Proxying to chat_service: msg={message[:80]!r}", flush=True)

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(
                    f"{chat_url}/skills/execute",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code != 200:
                    detail = ""
                    try:
                        detail = resp.json().get("detail", resp.text[:300])
                    except Exception:
                        detail = resp.text[:300]
                    return {"error": f"{skill_id} skill error: {detail}"}

                data = resp.json()
                return {
                    "success": data.get("success", False),
                    "action": data.get("action"),
                    "summary": data.get("summary"),
                    "error": data.get("error"),
                    "data": data.get("data"),
                }
        except Exception as e:
            print(f"[SKILL:{skill_id.upper()}] Exception: {e}", flush=True)
            return {"error": f"{skill_id} skill failed: {str(e)}"}

    # ================================================================
    # INTEGRATION SKILL TOOLS (Phase 0.4 — direct API, no proxy)
    # ================================================================

    async def _tool_figma(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Access Figma: list files, get file details, get components/styles."""
        action = (tool_input or {}).get("action", "list_files").lower()
        file_key = (tool_input or {}).get("file_key", "")

        api_key = await self._get_user_api_key(session, "figma")
        if not api_key:
            return {"error": "No Figma API key found. Add your Figma Personal Access Token in Settings > API Keys."}

        headers = {"X-Figma-Token": api_key}
        FIGMA_API = "https://api.figma.com/v1"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if action == "components" and file_key:
                    resp = await client.get(f"{FIGMA_API}/files/{file_key}/components", headers=headers)
                    resp.raise_for_status()
                    components = resp.json().get("meta", {}).get("components", [])
                    return {"success": True, "action": "components", "count": len(components),
                            "components": [{"name": c.get("name"), "description": c.get("description", "")[:80]} for c in components[:30]]}

                elif action == "styles" and file_key:
                    resp = await client.get(f"{FIGMA_API}/files/{file_key}/styles", headers=headers)
                    resp.raise_for_status()
                    styles = resp.json().get("meta", {}).get("styles", [])
                    return {"success": True, "action": "styles", "count": len(styles),
                            "styles": [{"name": s.get("name"), "type": s.get("style_type")} for s in styles[:30]]}

                elif action == "get_file" and file_key:
                    resp = await client.get(f"{FIGMA_API}/files/{file_key}?depth=1", headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    pages = data.get("document", {}).get("children", [])
                    return {"success": True, "action": "get_file", "name": data.get("name"),
                            "last_modified": data.get("lastModified", "")[:10],
                            "pages": [{"name": p.get("name"), "frames": len(p.get("children", []))} for p in pages[:20]]}

                else:
                    resp = await client.get(f"{FIGMA_API}/me", headers=headers)
                    resp.raise_for_status()
                    user_data = resp.json()
                    files_resp = await client.get(f"{FIGMA_API}/me/files", headers=headers)
                    files = files_resp.json().get("files", []) if files_resp.status_code == 200 else []
                    return {"success": True, "action": "list_files", "user": user_data.get("handle"),
                            "files": [{"name": f.get("name"), "key": f.get("key")} for f in files[:20]]}

        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                return {"error": "Figma access denied — token may be expired. Reconnect in Settings > API Keys."}
            return {"error": f"Figma API error: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": f"Figma request failed: {str(e)[:300]}"}

    async def _tool_google_calendar(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Access Google Calendar: list upcoming events or create an event."""
        from datetime import timezone, timedelta
        action = (tool_input or {}).get("action", "list_events").lower()

        api_key, err = await self._resolve_google_token(session, "google-calendar")
        if err:
            return err

        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        CALENDAR_API = "https://www.googleapis.com/calendar/v3"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if action == "create_event":
                    title = (tool_input or {}).get("title", "New Event")
                    now = datetime.now(timezone.utc)
                    start_str = (tool_input or {}).get("start")
                    if start_str:
                        try:
                            start = datetime.fromisoformat(start_str)
                        except Exception:
                            start = now + timedelta(hours=1)
                    else:
                        start = now + timedelta(hours=1)
                    duration_min = int((tool_input or {}).get("duration_minutes", 60))
                    end = start + timedelta(minutes=duration_min)

                    event_body = {
                        "summary": title,
                        "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
                        "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
                    }
                    description = (tool_input or {}).get("description")
                    if description:
                        event_body["description"] = description

                    resp = await client.post(
                        f"{CALENDAR_API}/calendars/primary/events",
                        headers={**headers, "Content-Type": "application/json"},
                        json=event_body,
                    )
                    resp.raise_for_status()
                    event = resp.json()
                    return {"success": True, "action": "create_event", "title": title,
                            "start": start.isoformat(), "link": event.get("htmlLink", "")}

                else:
                    now = datetime.now(timezone.utc)
                    days = int((tool_input or {}).get("days", 7))
                    resp = await client.get(
                        f"{CALENDAR_API}/calendars/primary/events",
                        headers=headers,
                        params={
                            "timeMin": now.isoformat(),
                            "timeMax": (now + timedelta(days=days)).isoformat(),
                            "maxResults": 25,
                            "singleEvents": "true",
                            "orderBy": "startTime",
                        },
                    )
                    resp.raise_for_status()
                    events = resp.json().get("items", [])
                    return {"success": True, "action": "list_events", "count": len(events),
                            "events": [{"title": ev.get("summary", "No title"),
                                        "start": (ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", ""))[:16],
                                        "location": ev.get("location", ""),
                                        "link": ev.get("htmlLink", "")} for ev in events[:25]]}

        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                return {"error": "Google Calendar access denied — token may be expired. Reconnect in Settings > API Keys."}
            return {"error": f"Calendar API error: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": f"Google Calendar request failed: {str(e)[:300]}"}

    async def _tool_google_drive(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Access Google Drive: list files or search for documents."""
        action = (tool_input or {}).get("action", "list").lower()
        query = (tool_input or {}).get("query", "")

        api_key = await self._get_user_api_key(session, "google-drive")
        if not api_key:
            api_key = await self._get_user_api_key(session, "google_drive")
        if not api_key:
            return {"error": "No Google Drive API key found. Add your Google OAuth token in Settings > API Keys."}

        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        DRIVE_API = "https://www.googleapis.com/drive/v3"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if action == "search" and query:
                    q_filter = f"name contains '{query}' and trashed = false"
                else:
                    q_filter = "trashed = false"

                resp = await client.get(
                    f"{DRIVE_API}/files",
                    headers=headers,
                    params={
                        "q": q_filter,
                        "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
                        "pageSize": 25,
                        "orderBy": "modifiedTime desc",
                    },
                )
                resp.raise_for_status()
                files = resp.json().get("files", [])
                return {"success": True, "action": action, "count": len(files),
                        "files": [{"name": f.get("name"), "type": f.get("mimeType", "").split(".")[-1],
                                   "modified": (f.get("modifiedTime") or "")[:10],
                                   "link": f.get("webViewLink", "")} for f in files[:25]]}

        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                return {"error": "Google Drive access denied — token may be expired. Reconnect in Settings > API Keys."}
            return {"error": f"Google Drive API error: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": f"Google Drive request failed: {str(e)[:300]}"}

    async def _tool_sigma(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Access Sigma Computing: list workbooks or get workbook details."""
        action = (tool_input or {}).get("action", "list_workbooks").lower()

        api_key = await self._get_user_api_key(session, "sigma")
        if not api_key:
            return {"error": "No Sigma Computing API key found. Add your Sigma API token in Settings > API Keys."}

        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        SIGMA_API = "https://aws-api.sigmacomputing.com/v2"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if action == "get_workbook":
                    workbook_id = (tool_input or {}).get("workbook_id", "")
                    if not workbook_id:
                        return {"error": "Missing 'workbook_id' for get_workbook action."}
                    resp = await client.get(f"{SIGMA_API}/workbooks/{workbook_id}", headers=headers)
                    resp.raise_for_status()
                    wb = resp.json()
                    return {"success": True, "action": "get_workbook",
                            "workbook": {"id": wb.get("workbookId"), "name": wb.get("name"),
                                         "url": wb.get("url", ""), "updated": wb.get("updatedAt", "")}}
                else:
                    resp = await client.get(f"{SIGMA_API}/workbooks", headers=headers)
                    resp.raise_for_status()
                    entries = resp.json().get("entries", [])
                    return {"success": True, "action": "list_workbooks", "count": len(entries),
                            "workbooks": [{"id": w.get("workbookId"), "name": w.get("name"),
                                           "url": w.get("url", ""), "updated": w.get("updatedAt", "")} for w in entries[:20]]}

        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                return {"error": "Sigma access denied — token may be expired. Reconnect in Settings > API Keys."}
            return {"error": f"Sigma API error: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": f"Sigma request failed: {str(e)[:300]}"}


    async def _tool_dev_bridge(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Bridge to ED service tools: file operations, git, docker, testing.

        Usage: {tool_name: "read_file", parameters: {path: "/app/main.py"}}

        Available tools (call with tool_name="list" to see all):
        - read_file, write_file, list_directory, search_files
        - git_status, git_diff, git_log, git_commit
        - docker_ps, docker_logs, docker_exec
        - run_tests, lint_code
        """
        tool_name = (tool_input or {}).get("tool_name", "").strip()
        if not tool_name:
            return {"error": "Missing 'tool_name'. Use tool_name='list' to see available tools."}

        ed_url = os.getenv("ED_SERVICE_URL", "http://ed_service:8000")

        # List available tools
        if tool_name == "list":
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(f"{ed_url}/tools")
                    if resp.status_code == 200:
                        return resp.json()
                    return {"error": f"ED service returned {resp.status_code}"}
            except Exception as e:
                return {"error": f"Failed to list ED tools: {e}"}

        # Execute a specific tool
        parameters = (tool_input or {}).get("parameters", {})
        headers = {}
        if session and session.user_id:
            headers["x-user-id"] = str(session.user_id)

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{ed_url}/tools/{tool_name}/execute",
                    json=parameters,
                    headers=headers,
                )
                if resp.status_code != 200:
                    return {"error": f"ED tool '{tool_name}' returned {resp.status_code}: {resp.text[:500]}"}
                return resp.json()
        except Exception as e:
            return {"error": f"ED tool '{tool_name}' failed: {e}"}

    # ------------------------------------------------------------------
    # Unified API Catalog: platform_api, discover_services, discover_api
    # ------------------------------------------------------------------

    async def _tool_platform_api(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Call ANY platform service API by service name + endpoint.

        Usage: {service: "memory", endpoint: "/memories/search", method: "POST", body: {query: "..."}}
        """
        from .rg_tool_registry.api_catalog import get_service, SERVICES

        service_name = (tool_input or {}).get("service", "").strip()
        endpoint = (tool_input or {}).get("endpoint", "").strip()
        method = (tool_input or {}).get("method", "GET").upper()
        body = (tool_input or {}).get("body") or (tool_input or {}).get("params") or {}
        headers_extra = (tool_input or {}).get("headers") or {}

        if not service_name:
            return {"error": f"Missing 'service'. Available: {', '.join(sorted(SERVICES.keys()))}"}
        if not endpoint:
            return {"error": "Missing 'endpoint'. Example: /health, /agents/, /memories/search"}

        svc = get_service(service_name)
        if not svc:
            return {"error": f"Service '{service_name}' not found. Available: {', '.join(sorted(SERVICES.keys()))}"}

        url = f"{svc.url.rstrip('/')}/{endpoint.lstrip('/')}"

        headers = {"Content-Type": "application/json"}
        internal_key = os.getenv("INTERNAL_SERVICE_KEY") or os.getenv("AUTH_INTERNAL_SERVICE_KEY", "")
        if internal_key:
            headers["x-internal-service-key"] = internal_key
        if session and session.user_id:
            headers["x-user-id"] = str(session.user_id)
        headers.update(headers_extra)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.request(method=method, url=url, json=body if method in ("POST", "PUT", "PATCH") else None,
                                            params=body if method == "GET" else None, headers=headers)
                try:
                    data = resp.json()
                except Exception:
                    data = {"raw": resp.text[:2000]}

                if resp.status_code < 400:
                    return {"success": True, "status": resp.status_code, "data": data}
                return {"error": f"HTTP {resp.status_code}", "data": data}
        except httpx.ConnectError:
            return {"error": f"Service '{service_name}' unreachable at {svc.url}"}
        except Exception as e:
            return {"error": f"platform_api failed: {str(e)[:300]}"}

    async def _tool_discover_services(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Discover available platform services by category.

        Usage: {} (no params = all) or {category: "ai"} or {search: "memory"}
        """
        from .rg_tool_registry.api_catalog import get_all_services, get_services_by_category, ServiceCategory

        category = (tool_input or {}).get("category", "").strip().lower()
        search = (tool_input or {}).get("search", "").strip().lower()

        if category:
            try:
                cat = ServiceCategory(category)
                services = get_services_by_category(cat)
            except ValueError:
                return {"error": f"Unknown category '{category}'. Valid: {', '.join(c.value for c in ServiceCategory)}"}
        else:
            services = get_all_services()

        if search:
            services = [s for s in services if search in s.name.lower() or search in s.description.lower()
                        or any(search in c.lower() for c in s.capabilities)]

        return {
            "services": [
                {
                    "name": s.name,
                    "category": s.category.value,
                    "description": s.description,
                    "capabilities": s.capabilities[:8],
                }
                for s in services
            ],
            "total": len(services),
            "hint": "Use platform_api(service='name', endpoint='/path', method='GET|POST') to call any service",
        }

    async def _tool_discover_api(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Discover endpoints for a specific service by hitting its /openapi.json or /docs.

        Usage: {service: "agent_engine"} or {service: "memory", search: "search"}
        """
        from .rg_tool_registry.api_catalog import get_service, SERVICES

        service_name = (tool_input or {}).get("service", "").strip()
        search = (tool_input or {}).get("search", "").strip().lower()

        if not service_name:
            return {"error": f"Missing 'service'. Available: {', '.join(sorted(SERVICES.keys()))}"}

        svc = get_service(service_name)
        if not svc:
            return {"error": f"Service '{service_name}' not found. Available: {', '.join(sorted(SERVICES.keys()))}"}

        # Try to fetch the OpenAPI spec
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{svc.url.rstrip('/')}/openapi.json")
                if resp.status_code == 200:
                    spec = resp.json()
                    paths = spec.get("paths", {})
                    endpoints = []
                    for path, methods in paths.items():
                        for method, details in methods.items():
                            if method in ("get", "post", "put", "patch", "delete"):
                                summary = details.get("summary", "") or details.get("description", "")[:80]
                                if search and search not in path.lower() and search not in summary.lower():
                                    continue
                                endpoints.append({"method": method.upper(), "path": path, "summary": summary})
                    return {
                        "service": service_name,
                        "url": svc.url,
                        "endpoints": endpoints[:50],
                        "total": len(endpoints),
                    }
        except Exception:
            pass

        # Fallback: return known capabilities from catalog
        return {
            "service": service_name,
            "url": svc.url,
            "description": svc.description,
            "capabilities": svc.capabilities,
            "note": "OpenAPI spec not available — use capabilities as guidance for endpoint names",
        }

    async def _get_user_api_key(self, session: Optional[AgentSession], provider: str) -> Optional[str]:
        """Fetch the user's API key for a given provider from auth_service (BYOK)."""
        if not session or not session.user_id:
            return None
        auth_url = os.getenv("AUTH_URL", "http://auth_service:8000")
        internal_key = os.getenv("AUTH_INTERNAL_SERVICE_KEY") or os.getenv("INTERNAL_SERVICE_KEY", "")
        headers = {"x-internal-service-key": internal_key} if internal_key else {}
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{auth_url}/auth/internal/user-api-keys/{session.user_id}?provider={provider}",
                    headers=headers,
                )
                if resp.status_code == 200:
                    for entry in resp.json().get("keys", []):
                        if entry.get("provider") == provider and entry.get("api_key"):
                            return entry["api_key"]
        except Exception as e:
            print(f"[BYOK] Failed to fetch {provider} key: {e}", flush=True)
        return None

    async def _refresh_google_token(self, refresh_token: str) -> str:
        """Exchange a Google OAuth refresh token for a fresh access token."""
        client_id = os.getenv('GOOGLE_CLIENT_ID')
        client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
        if not client_id or not client_secret:
            raise ValueError('GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not configured')
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                'https://oauth2.googleapis.com/token',
                data={
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'refresh_token': refresh_token,
                    'grant_type': 'refresh_token',
                },
            )
            resp.raise_for_status()
            return resp.json()['access_token']

    async def _resolve_google_token(self, session, provider: str) -> tuple:
        """Get a Google token, refreshing if it's a refresh token. Returns (token, error_dict_or_None)."""
        token = await self._get_user_api_key(session, provider)
        if not token:
            # Try alternate name
            alt = provider.replace('-', '_') if '-' in provider else provider.replace('_', '-')
            token = await self._get_user_api_key(session, alt)
        if not token:
            return None, {'error': f'No {provider} token found. Connect in Settings > Connect Profiles.'}
        if token.startswith('1//'):
            try:
                token = await self._refresh_google_token(token)
            except Exception as e:
                return None, {'error': f'Failed to refresh {provider} token: {e}. Reconnect in Settings > Connect Profiles.'}
        return token, None

    async def _tool_generate_image(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Generate an image using OpenAI DALL-E 3 with the user's API key."""
        prompt = (tool_input or {}).get("prompt", "").strip()
        if not prompt:
            return {"error": "Missing 'prompt' — describe the image you want to generate."}

        # Get user's OpenAI API key
        openai_key = await self._get_user_api_key(session, "openai")
        if not openai_key:
            return {
                "error": "No OpenAI API key found. To generate images, add your OpenAI API key in Settings > API Keys.",
                "help": "Go to Settings > API Keys and add your OpenAI key to enable DALL-E 3 image generation.",
            }

        size = (tool_input or {}).get("size", "1024x1024")
        if size not in ("1024x1024", "1792x1024", "1024x1792"):
            size = "1024x1024"
        quality = (tool_input or {}).get("quality", "standard")
        if quality not in ("standard", "hd"):
            quality = "standard"
        style = (tool_input or {}).get("style", "vivid")
        if style not in ("vivid", "natural"):
            style = "vivid"

        print(f"[GENERATE_IMAGE] Calling DALL-E 3: prompt={prompt[:80]!r} size={size}", flush=True)

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/images/generations",
                    headers={
                        "Authorization": f"Bearer {openai_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "dall-e-3",
                        "prompt": prompt,
                        "n": 1,
                        "size": size,
                        "quality": quality,
                        "style": style,
                        "response_format": "url",
                    },
                )
                if resp.status_code != 200:
                    error_detail = ""
                    try:
                        error_detail = resp.json().get("error", {}).get("message", resp.text[:200])
                    except Exception:
                        error_detail = resp.text[:200]
                    print(f"[GENERATE_IMAGE] DALL-E error {resp.status_code}: {error_detail}", flush=True)
                    return {"error": f"DALL-E API error: {error_detail}"}

                data = resp.json()
                images = data.get("data", [])
                if not images:
                    return {"error": "DALL-E returned no images"}

                image_url = images[0].get("url", "")
                revised_prompt = images[0].get("revised_prompt", prompt)
                print(f"[GENERATE_IMAGE] SUCCESS: url={image_url[:80]}", flush=True)

                return {
                    "success": True,
                    "image_url": image_url,
                    "revised_prompt": revised_prompt,
                    "model": "dall-e-3",
                    "size": size,
                }
        except Exception as e:
            print(f"[GENERATE_IMAGE] Exception: {e}", flush=True)
            return {"error": f"Image generation failed: {str(e)}"}

    async def _tool_generate_audio(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Generate speech audio using OpenAI TTS with the user's API key (BYOK)."""
        text = (tool_input or {}).get("text", "").strip()
        if not text:
            return {"error": "Missing 'text' — provide the text to convert to speech."}

        openai_key = await self._get_user_api_key(session, "openai")
        if not openai_key:
            return {
                "error": "No OpenAI API key found. To generate audio, add your OpenAI API key in Settings > API Keys.",
                "help": "Go to Settings > API Keys and add your OpenAI key to enable text-to-speech.",
            }

        voice = (tool_input or {}).get("voice", "alloy")
        if voice not in ("alloy", "echo", "fable", "onyx", "nova", "shimmer"):
            voice = "alloy"
        model = (tool_input or {}).get("model", "tts-1")
        if model not in ("tts-1", "tts-1-hd"):
            model = "tts-1"

        print(f"[GENERATE_AUDIO] Calling OpenAI TTS: voice={voice} model={model} text={text[:60]!r}", flush=True)

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={
                        "Authorization": f"Bearer {openai_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "input": text[:4096],
                        "voice": voice,
                        "response_format": "mp3",
                    },
                )
                if resp.status_code != 200:
                    error_detail = resp.text[:200]
                    print(f"[GENERATE_AUDIO] TTS error {resp.status_code}: {error_detail}", flush=True)
                    return {"error": f"TTS API error: {error_detail}"}

                import base64
                audio_b64 = base64.b64encode(resp.content).decode("utf-8")
                print(f"[GENERATE_AUDIO] SUCCESS: {len(resp.content)} bytes", flush=True)

                return {
                    "success": True,
                    "audio_base64": audio_b64,
                    "format": "mp3",
                    "voice": voice,
                    "model": model,
                    "text_length": len(text),
                }
        except Exception as e:
            print(f"[GENERATE_AUDIO] Exception: {e}", flush=True)
            return {"error": f"Audio generation failed: {str(e)}"}

    async def _tool_generate_music(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Generate music/songs using Suno API with the user's API key (BYOK)."""
        prompt = (tool_input or {}).get("prompt", "").strip()
        if not prompt:
            return {"error": "Missing 'prompt' — describe the music or song you want to generate."}

        suno_key = await self._get_user_api_key(session, "suno")
        if not suno_key:
            return {
                "error": "No Suno API key found. To generate music, add your Suno API key in Settings > API Keys.",
                "help": "Go to Settings > API Keys, select provider 'suno', and add your Suno API key.",
            }

        make_instrumental = bool((tool_input or {}).get("instrumental", False))

        print(f"[GENERATE_MUSIC] Calling Suno API: prompt={prompt[:80]!r}", flush=True)

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    "https://studio-api.suno.ai/api/external/generate/",
                    headers={
                        "Authorization": f"Bearer {suno_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "topic": prompt,
                        "make_instrumental": make_instrumental,
                    },
                )
                if resp.status_code != 200:
                    error_detail = resp.text[:300]
                    print(f"[GENERATE_MUSIC] Suno error {resp.status_code}: {error_detail}", flush=True)
                    return {"error": f"Suno API error ({resp.status_code}): {error_detail}"}

                data = resp.json()
                print(f"[GENERATE_MUSIC] SUCCESS", flush=True)
                return {
                    "success": True,
                    "tracks": data,
                    "prompt": prompt,
                    "provider": "suno",
                }
        except Exception as e:
            print(f"[GENERATE_MUSIC] Exception: {e}", flush=True)
            return {"error": f"Music generation failed: {str(e)}"}

    async def _tool_generate_video(self, tool_input: Dict[str, Any], *, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        """Generate video using Replicate API with the user's API key (BYOK)."""
        prompt = (tool_input or {}).get("prompt", "").strip()
        if not prompt:
            return {"error": "Missing 'prompt' — describe the video you want to generate."}

        replicate_key = await self._get_user_api_key(session, "replicate")
        if not replicate_key:
            return {
                "error": "No Replicate API key found. To generate videos, add your Replicate API key in Settings > API Keys.",
                "help": "Go to Settings > API Keys, select provider 'replicate', and add your Replicate API token.",
            }

        print(f"[GENERATE_VIDEO] Calling Replicate API: prompt={prompt[:80]!r}", flush=True)

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                # Use Replicate's HTTP API to run stable-video-diffusion
                resp = await client.post(
                    "https://api.replicate.com/v1/predictions",
                    headers={
                        "Authorization": f"Bearer {replicate_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "version": "3f0457e4619daac51203dedb472816fd4af51f3149fa7a9e0b5ffcf1b8172438",
                        "input": {
                            "prompt": prompt,
                            "num_frames": 25,
                            "num_inference_steps": 30,
                            "width": 1024,
                            "height": 576,
                        },
                    },
                )
                if resp.status_code not in (200, 201):
                    error_detail = resp.text[:300]
                    print(f"[GENERATE_VIDEO] Replicate error {resp.status_code}: {error_detail}", flush=True)
                    return {"error": f"Replicate API error ({resp.status_code}): {error_detail}"}

                data = resp.json()
                prediction_id = data.get("id")
                status_url = data.get("urls", {}).get("get")

                # Poll for completion (video gen takes time)
                if status_url:
                    import asyncio
                    for _ in range(60):  # up to 2 minutes
                        await asyncio.sleep(2)
                        poll = await client.get(status_url, headers={"Authorization": f"Bearer {replicate_key}"})
                        if poll.status_code == 200:
                            pdata = poll.json()
                            if pdata.get("status") == "succeeded":
                                output = pdata.get("output")
                                print(f"[GENERATE_VIDEO] SUCCESS: {output}", flush=True)
                                return {
                                    "success": True,
                                    "video_url": output if isinstance(output, str) else (output[0] if isinstance(output, list) and output else str(output)),
                                    "prompt": prompt,
                                    "provider": "replicate",
                                    "prediction_id": prediction_id,
                                }
                            elif pdata.get("status") == "failed":
                                error = pdata.get("error", "Unknown error")
                                print(f"[GENERATE_VIDEO] Failed: {error}", flush=True)
                                return {"error": f"Video generation failed: {error}"}

                return {
                    "success": True,
                    "status": "processing",
                    "prediction_id": prediction_id,
                    "message": "Video generation started. It may take 1-2 minutes to complete.",
                    "provider": "replicate",
                }
        except Exception as e:
            print(f"[GENERATE_VIDEO] Exception: {e}", flush=True)
            return {"error": f"Video generation failed: {str(e)}"}

    async def start_session(
        self,
        agent: AgentDefinition,
        goal: str,
        initial_context: Optional[Dict[str, Any]],
        user_id: Optional[str],
        db_session: AsyncSession,
    ) -> AgentSession:
        """Start a new agent session."""
        session = AgentSession(
            agent_id=agent.id,
            user_id=user_id,
            status="initializing",
            current_goal=goal,
            context=initial_context or {},
            started_at=datetime.utcnow(),
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)
        return session

    # Goals that require capabilities the platform doesn't have.
    # NOTE: Image, audio, music, and video generation are all supported via BYOK tools.
    _IMPOSSIBLE_GOAL_PATTERNS = [
        # No more media blocks — all media generation is now BYOK-enabled.
        # The tools themselves will return helpful "add your API key" messages.
    ]

    async def run_loop(
        self,
        session: AgentSession,
        agent: AgentDefinition,
        db_session: AsyncSession,
    ) -> Dict[str, Any]:
        """Run the autonomous agent loop."""
        if self._sandbox:
            self._sandbox.reset_session()
        import time as _time
        _loop_start = _time.time()
        _step_history: list = []
        _result: Dict[str, Any] = {"status": "failed", "error": "unknown"}

        try:
            _result = await self._run_loop_inner(session, agent, db_session, _step_history)
            return _result
        except Exception as e:
            _result = {"status": "failed", "error": str(e)}
            raise
        finally:
            self._record_session_learning(session, agent, _result, _step_history, _loop_start)

    # Patterns for empty/meaningless goals that waste tokens
    _EMPTY_GOAL_PATTERNS = [
        re.compile(r"^incoming\s*$", re.IGNORECASE),
        re.compile(r"^event:\s*$", re.IGNORECASE),
        re.compile(r"^none\s*$", re.IGNORECASE),
        re.compile(r"^null\s*$", re.IGNORECASE),
        re.compile(r"^undefined\s*$", re.IGNORECASE),
        re.compile(r"^test\s*$", re.IGNORECASE),
        re.compile(r"^\s*$"),
    ]

    async def _run_loop_inner(
        self,
        session: AgentSession,
        agent: AgentDefinition,
        db_session: AsyncSession,
        _step_history: list,
    ) -> Dict[str, Any]:
        """Inner run loop — separated to enable learning wrapper."""
        # === EMPTY/MEANINGLESS GOAL INTERCEPTOR ===
        # Reject goals that are empty, placeholder, or system noise BEFORE wasting tokens.
        goal_raw = (session.current_goal or "").strip()
        if not goal_raw or len(goal_raw) < 3:
            session.status = "failed"
            session.error_message = "Goal is empty or too short. Please provide a specific task."
            session.completed_at = datetime.utcnow()
            await db_session.commit()
            return {"status": "failed", "error": session.error_message}

        for pattern in self._EMPTY_GOAL_PATTERNS:
            if pattern.search(goal_raw):
                print(f"[INTERCEPTOR] Empty/meaningless goal rejected: {goal_raw[:80]}", flush=True)
                session.status = "failed"
                session.error_message = f"Goal '{goal_raw[:100]}' is not actionable. Please provide a specific task."
                session.completed_at = datetime.utcnow()
                await db_session.commit()
                return {"status": "failed", "error": session.error_message}

        # === IMPOSSIBLE-GOAL INTERCEPTOR ===
        # Catch goals that require capabilities we don't have BEFORE entering the LLM loop.
        goal_lower = goal_raw
        for pattern, response_msg in self._IMPOSSIBLE_GOAL_PATTERNS:
            if pattern.search(goal_lower):
                print(f"[INTERCEPTOR] Goal blocked by impossible-goal pattern: {goal_lower[:80]}", flush=True)
                session.status = "completed"
                session.final_output = response_msg
                session.completed_at = datetime.utcnow()
                # Record a single "respond" step for the UI
                step = AgentStep(
                    session_id=session.id,
                    step_number=1,
                    step_type="respond",
                    reasoning="Goal requires capabilities this agent does not have (media generation).",
                    output_data={"response": response_msg},
                )
                db_session.add(step)
                await db_session.commit()
                return {"status": "completed", "output": response_msg}

        # === BUDGET ENFORCEMENT: max_runs_per_day ===
        _agent_sc = agent.safety_config if isinstance(agent.safety_config, dict) else {}
        _max_runs_per_day = _agent_sc.get("max_runs_per_day")
        if _max_runs_per_day and isinstance(_max_runs_per_day, int) and _max_runs_per_day > 0:
            try:
                from sqlalchemy import func as sa_func
                today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                count_result = await db_session.execute(
                    select(sa_func.count(AgentSession.id)).where(
                        AgentSession.agent_id == agent.id,
                        AgentSession.started_at >= today_start,
                    )
                )
                today_count = count_result.scalar() or 0
                if today_count > _max_runs_per_day:
                    session.status = "failed"
                    session.error_message = (
                        f"Daily run limit exceeded: {today_count}/{_max_runs_per_day} runs today"
                    )
                    session.completed_at = datetime.utcnow()
                    await db_session.commit()
                    return {"status": "failed", "error": session.error_message}
            except Exception as e:
                logger.warning(f"[BUDGET] max_runs_per_day check failed (non-fatal): {e}")

        # Learning loop: track start time for session duration recording
        _loop_start_time = time.time()
        _step_history = []

        # Fetch user BYOK keys once at session start (cached for all steps)
        user_id = str(session.user_id) if session.user_id else ""
        _user_keys = await self._fetch_user_byok_keys(user_id)

        # Credit tracking for this session
        _ctx = session.context or {}
        _user_role = str(_ctx.get("user_role", "user")).lower()
        _is_superuser = str(_ctx.get("is_superuser", "")).lower() in ("1", "true", "yes")
        _unlimited = str(_ctx.get("unlimited_credits", "")).lower() in ("1", "true", "yes")
        _is_privileged = _is_superuser or _unlimited or _user_role in ("platform_owner", "admin")
        _has_byok = bool(_user_keys)
        _credits_used_total = 0
        _credits_balance = -1  # unknown until first deduction
        _BILLING_URL = os.getenv("BILLING_SERVICE_URL", "http://billing_service:8000")
        _CREDIT_COST_LLM = 20

        # Load safety rules
        await safety_envelope.load_rules(db_session, str(agent.id))

        # ── INTELLIGENCE LAYER: Memory recall + learning injection ──
        # Load relevant memories and past learnings BEFORE planning so the
        # agent starts each session with accumulated knowledge.
        _recalled_context = {}
        try:
            _recalled_context = await self._recall_agent_memory(
                agent_id=str(agent.id),
                user_id=user_id,
                goal=session.current_goal,
            )
            if _recalled_context:
                # Merge recalled context into session so planner + LLM can use it
                ctx = session.context or {}
                ctx["recalled_memories"] = _recalled_context.get("memories", [])
                ctx["learned_patterns"] = _recalled_context.get("patterns", [])
                ctx["past_successes"] = _recalled_context.get("past_successes", [])
                session.context = ctx
                logger.info(
                    f"[INTELLIGENCE] Session {session.id}: recalled "
                    f"{len(ctx.get('recalled_memories', []))} memories, "
                    f"{len(ctx.get('learned_patterns', []))} patterns"
                )
        except Exception as e:
            logger.warning(f"[INTELLIGENCE] Memory recall failed (non-fatal): {e}")

        # Mark session as running early to avoid long "initializing" states
        session.status = "running"
        await db_session.commit()

        # Create initial plan (tool names from unified registry)
        _tool_names = [t.name for t in self._registry.get_all()]
        plan_data = await tool_planner.create_plan(
            goal=session.current_goal,
            available_tools=_tool_names,
            context=session.context,
        )

        if not isinstance(plan_data, dict):
            session.status = "failed"
            session.error_message = "Planning failed: invalid plan response"
            session.completed_at = datetime.utcnow()
            await db_session.commit()
            return {"status": "failed", "error": session.error_message}

        if not plan_data.get("steps"):
            session.status = "failed"
            session.error_message = plan_data.get("error") or "Planning failed"
            session.completed_at = datetime.utcnow()
            await db_session.commit()
            return {"status": "failed", "error": session.error_message}
        
        plan = AgentPlan(
            session_id=session.id,
            plan_data=plan_data,
            goal=session.current_goal,
            steps=plan_data.get("steps", []),
        )
        db_session.add(plan)
        await db_session.commit()

        history = []
        
        # Reset verifier and stabilizer for new session
        verifier_agent.reset()
        loop_stabilizer.reset()
        
        try:
            # Per-agent max_loops from safety_config, fallback to global setting
            _agent_max_loops = settings.MAX_LOOP_ITERATIONS
            _agent_sc = agent.safety_config if isinstance(agent.safety_config, dict) else {}
            if _agent_sc:
                _per_agent = _agent_sc.get("max_loops")
                if _per_agent and isinstance(_per_agent, int) and _per_agent >= 1:
                    _agent_max_loops = _per_agent

            # Per-agent max_tokens_per_run from safety_config (budget enforcement)
            _agent_max_tokens = settings.MAX_TOKENS_PER_RUN
            if _agent_sc.get("max_tokens_per_run"):
                _per_agent_tokens = _agent_sc["max_tokens_per_run"]
                if isinstance(_per_agent_tokens, int) and _per_agent_tokens > 0:
                    _agent_max_tokens = _per_agent_tokens

            while session.loop_count < _agent_max_loops:
                # Check if session was cancelled
                await db_session.refresh(session)
                if session.status in ("cancelled", "paused"):
                    break

                # Execute one iteration
                step_result = await self._execute_step(
                    session=session,
                    agent=agent,
                    history=history,
                    db_session=db_session,
                    user_keys=_user_keys,
                )

                history.append(step_result)
                _step_history.append({
                    "action": step_result.get("step_type", "unknown"),
                    "tool_name": step_result.get("tool_name"),
                    "goal_achieved": step_result.get("goal_achieved", False),
                    "error": step_result.get("error"),
                })
                session.loop_count += 1
                session.last_activity_at = datetime.utcnow()

                # === BUDGET ENFORCEMENT: per-agent token limit ===
                if (session.total_tokens_used or 0) >= _agent_max_tokens:
                    logger.warning(
                        f"Session {session.id}: token budget exceeded "
                        f"({session.total_tokens_used}/{_agent_max_tokens})"
                    )
                    session.status = "failed"
                    session.error_message = (
                        f"Token budget exceeded: used {session.total_tokens_used} "
                        f"of {_agent_max_tokens} max_tokens_per_run"
                    )
                    session.completed_at = datetime.utcnow()
                    await db_session.commit()
                    result = {"status": "failed", "error": session.error_message}
                    self._record_session_learning(session, agent, result, _step_history, _loop_start_time)
                    return result

                # === VERIFICATION STEP (lightweight, no LLM call) ===
                # Use hash-based loop detection only — the full LLM verifier
                # doubles every LLM call and causes rate-limit hangs with Groq.
                loop_check = verifier_agent._check_for_loops(
                    step_input=step_result.get("tool_input", {}),
                    step_output=step_result.get("result", {}),
                )
                verification = loop_check or VerificationReport(
                    result=VerificationResult.APPROVED,
                    confidence=0.8,
                    reasoning="Lightweight check passed",
                )
                
                # === LOOP STABILIZATION ===
                stability = loop_stabilizer.record_step(
                    step_type=step_result.get("action", "unknown"),
                    step_input=step_result.get("tool_input", {}),
                    step_output=step_result.get("result", {}),
                    success=not step_result.get("error"),
                    confidence=verification.confidence,
                    progress=1.0 if step_result.get("goal_achieved") else (0.05 if not step_result.get("error") else 0.0),
                )
                
                # Handle stability actions
                if stability.action == StabilityAction.ABORT:
                    session.status = "failed"
                    session.error_message = f"Loop aborted: {stability.reason}"
                    session.completed_at = datetime.utcnow()
                    await db_session.commit()
                    return {"status": "failed", "error": stability.reason}
                
                if stability.action == StabilityAction.ROLLBACK:
                    checkpoint = loop_stabilizer.get_latest_checkpoint()
                    if checkpoint:
                        # Restore from checkpoint
                        session.context = checkpoint.get("data", {}).get("context", session.context)
                        history = checkpoint.get("data", {}).get("history", [])[-5:]
                
                if stability.action == StabilityAction.REPLAN:
                    plan_data = await tool_planner.revise_plan(
                        goal=session.current_goal,
                        current_plan=plan_data,
                        completed_steps=history,
                        issue=f"Stability issue: {stability.reason}",
                        available_tools=_tool_names,
                    )
                    plan.plan_data = plan_data
                    plan.steps = plan_data.get("steps", [])
                    plan.revision_count += 1
                
                if stability.action == StabilityAction.ESCALATE:
                    # Delegate to supervisor agent
                    if DELEGATION_AVAILABLE and agent_delegator:
                        escalation = await agent_delegator.delegate_to_supervisor(
                            agent_id=str(agent.id),
                            service="agent-engine",
                            issue=stability.reason,
                            context={
                                "session_id": str(session.id),
                                "goal": session.current_goal,
                                "loop_count": session.loop_count,
                                "last_step": step_result,
                            },
                        )
                        if escalation.status.value == "completed" and escalation.result:
                            # Apply supervisor guidance
                            if escalation.result.get("action") == "abort":
                                session.status = "failed"
                                session.error_message = f"Supervisor aborted: {escalation.result.get('reason')}"
                                session.completed_at = datetime.utcnow()
                                await db_session.commit()
                                return {"status": "failed", "error": session.error_message}
                
                # Handle verification failures — add as guidance so LLM adjusts
                if verification.result == VerificationResult.REJECTED:
                    step_result["verification_feedback"] = f"REJECTED: {verification.reasoning}. Try a different approach."
                    step_result["error"] = f"Verification rejected: {verification.reasoning}"
                elif verification.result == VerificationResult.HALLUCINATION_DETECTED:
                    step_result["verification_feedback"] = f"HALLUCINATION: {verification.reasoning}. Use real data only."
                    step_result["error"] = f"Hallucination detected: {verification.reasoning}"
                elif verification.result == VerificationResult.LOOP_DETECTED:
                    session.status = "failed"
                    session.error_message = "Infinite loop detected by verifier"
                    session.completed_at = datetime.utcnow()
                    await db_session.commit()
                    return {"status": "failed", "error": "Infinite loop detected"}
                
                # Create checkpoint if recommended
                if loop_stabilizer.should_create_checkpoint():
                    loop_stabilizer.create_checkpoint({
                        "context": session.context,
                        "history": history[-10:],
                        "plan": plan_data,
                    })
                
                await db_session.commit()

                # Check if goal achieved
                if step_result.get("goal_achieved"):
                    session.status = "completed"
                    session.final_output = step_result.get("response")
                    session.completed_at = datetime.utcnow()
                    await db_session.commit()
                    result = {"status": "completed", "output": step_result.get("response")}
                    self._record_session_learning(session, agent, result, _step_history, _loop_start_time)
                    return result

                # Check if waiting for approval
                if step_result.get("waiting_approval"):
                    session.status = "waiting_approval"
                    await db_session.commit()
                    return {"status": "waiting_approval", "step_id": step_result.get("step_id")}

                # Check for errors (but not verifier feedback — that's guidance, not fatal)
                error = step_result.get("error")
                is_verifier_feedback = step_result.get("verification_feedback") is not None
                if error and not is_verifier_feedback:
                    # Real execution error — try to replan
                    if session.loop_count < 3:  # Allow a few retries
                        plan_data = await tool_planner.revise_plan(
                            goal=session.current_goal,
                            current_plan=plan_data,
                            completed_steps=history,
                            issue=error,
                            available_tools=_tool_names,
                        )
                        plan.plan_data = plan_data
                        plan.steps = plan_data.get("steps", [])
                        plan.revision_count += 1
                        await db_session.commit()
                    else:
                        session.status = "failed"
                        session.error_message = error
                        session.completed_at = datetime.utcnow()
                        await db_session.commit()
                        result = {"status": "failed", "error": error}
                        self._record_session_learning(session, agent, result, _step_history, _loop_start_time)
                        return result

                # Delay between iterations to avoid Groq rate limits
                await asyncio.sleep(1.5)

            # Loop limit reached
            session.status = "completed"
            session.error_message = "Completed (loop limit reached)"
            session.completed_at = datetime.utcnow()
            await db_session.commit()
            result = {"status": "failed", "error": "Maximum iterations reached"}
            self._record_session_learning(session, agent, result, _step_history, _loop_start_time)
            return result

        except Exception as e:
            session.status = "failed"
            session.error_message = str(e)
            session.completed_at = datetime.utcnow()
            await db_session.commit()
            result = {"status": "failed", "error": str(e)}
            self._record_session_learning(session, agent, result, _step_history, _loop_start_time)
            return result

    async def _execute_step(
        self,
        session: AgentSession,
        agent: AgentDefinition,
        history: List[Dict[str, Any]],
        db_session: AsyncSession,
        user_keys: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Execute a single step in the agent loop."""
        start_time = time.time()

        # Derive credit-tracking variables (needed for per-step billing)
        user_id = str(session.user_id) if session.user_id else ""
        _ctx = session.context or {}
        _user_role = str(_ctx.get("user_role", "user")).lower()
        _is_superuser = str(_ctx.get("is_superuser", "")).lower() in ("1", "true", "yes")
        _unlimited = str(_ctx.get("unlimited_credits", "")).lower() in ("1", "true", "yes")
        _is_privileged = _is_superuser or _unlimited or _user_role in ("platform_owner", "admin")
        _has_byok = bool(user_keys)
        _credits_used_total = 0
        _credits_balance = -1
        _BILLING_URL = os.getenv("BILLING_SERVICE_URL", "http://billing_service:8000")
        _CREDIT_COST_LLM = 20

        # Create step record
        step = AgentStep(
            session_id=session.id,
            step_number=session.loop_count,
            step_type="think",
        )
        db_session.add(step)
        await db_session.commit()
        await db_session.refresh(step)

        try:
            # Get agent's next action
            logger.info(f"Session {session.id} step {session.loop_count}: get_next_action (history={len(history)})")
            action = await self._get_next_action(
                agent=agent,
                goal=session.current_goal,
                context=session.context,
                history=history,
                user_keys=user_keys,
            )
            logger.info(f"Session {session.id} step {session.loop_count}: action={action.get('action')} tool={action.get('tool_name')}")

            # Track token usage from LLM call
            step_tokens = action.pop("_tokens_used", 0) or 0
            step.tokens_used = step_tokens
            session.total_tokens_used = (session.total_tokens_used or 0) + step_tokens

            # --- Credit deduction per LLM call ---
            _step_credit_info = {}
            if not _is_privileged and user_id and user_id != "anonymous":
                _llm_cost = 0 if _has_byok else _CREDIT_COST_LLM
                if _llm_cost > 0:
                    try:
                        async with httpx.AsyncClient(timeout=5.0) as _hc:
                            _dr = await _hc.post(
                                f"{_BILLING_URL}/billing/credits/deduct",
                                json={
                                    "user_id": user_id,
                                    "amount": _llm_cost,
                                    "action": "agent_llm_call",
                                    "description": f"Agent session {session.id} step {session.loop_count}",
                                    "reference_id": str(session.id),
                                    "reference_type": "agent_session",
                                },
                            )
                            if _dr.status_code == 200:
                                _dd = _dr.json()
                                _credits_balance = _dd.get("balance", 0)
                                _credits_used_total += _llm_cost
                                _step_credit_info = {
                                    "credits_deducted": _llm_cost,
                                    "credits_balance": _credits_balance,
                                    "credits_used_total": _credits_used_total,
                                }
                                if _credits_balance <= 0:
                                    _step_credit_info["credit_warning"] = "zero"
                                elif _credits_balance < 3000:
                                    _step_credit_info["credit_warning"] = "low"
                                logger.info(f"[Credits] Session {session.id} step {session.loop_count}: deducted {_llm_cost}, balance={_credits_balance}")
                            elif _dr.status_code == 402:
                                _step_credit_info = {"credit_warning": "zero", "credits_balance": 0, "credits_exhausted": True}
                                logger.warning(f"[Credits] Session {session.id}: credits exhausted mid-loop at step {session.loop_count}")
                    except Exception as _ce:
                        logger.warning(f"[Credits] Deduction failed for session {session.id}: {_ce}")

            # Stop execution if credits exhausted mid-loop
            if _step_credit_info.get("credits_exhausted"):
                step.reasoning = action.get("reasoning")
                step.step_type = "respond"
                step.output_data = {"error": "credits_exhausted", "message": "Credits exhausted during agent execution.", **_step_credit_info}
                duration_ms = int((time.time() - start_time) * 1000)
                step.duration_ms = duration_ms
                session.status = "failed"
                session.error_message = "Credits exhausted. Please upgrade your plan or purchase credits."
                session.completed_at = datetime.utcnow()
                await db_session.commit()
                return {"error": "credits_exhausted", "credits_balance": 0}

            step.reasoning = action.get("reasoning")
            step.step_type = action.get("action", "think")

            if action.get("action") == "tool_call" and not action.get("tool_name"):
                step.output_data = {"error": "tool_call missing tool_name"}
                duration_ms = int((time.time() - start_time) * 1000)
                step.duration_ms = duration_ms
                await db_session.commit()
                return {"error": "tool_call missing tool_name"}

            # Safety check
            is_allowed, violations, requires_approval = await safety_envelope.check_action(
                action_type=action.get("action"),
                action_data=action,
                agent_session=session,
                session=db_session,
            )

            step.safety_check_passed = is_allowed
            step.safety_violations = violations if violations else None

            if not is_allowed:
                step.output_data = {"blocked": True, "violations": violations}
                duration_ms = int((time.time() - start_time) * 1000)
                step.duration_ms = duration_ms
                await db_session.commit()
                return {"error": f"Action blocked: {violations}"}
            
            # === EXECUTION GATE: Dual-mode autonomy enforcement ===
            agent_mode = getattr(agent, 'mode', None) or 'governed'
            # UNBOUNDED: completely bypass all approval gates
            if agent_mode == 'unbounded':
                requires_approval = False
            if EXECUTION_GATE_AVAILABLE and get_execution_gate:
                from uuid import uuid4
                gate = get_execution_gate()
                
                # Determine risk level
                action_risk = self._get_action_risk_level(action) if hasattr(self, '_get_action_risk_level') else 1
                risk_map = {1: RiskLevel.LOW, 2: RiskLevel.LOW, 3: RiskLevel.MEDIUM, 4: RiskLevel.HIGH, 5: RiskLevel.CRITICAL}
                risk_level = risk_map.get(action_risk, RiskLevel.LOW)

                tool_name = str(action.get("tool_name", "")) if action.get("action") == "tool_call" else ""
                # ALL platform tools are auto-approved (security at sandbox layer)
                if tool_name:
                    risk_level = RiskLevel.LOW
                
                gate_request = ExecutionRequest(
                    id=str(uuid4()),
                    agent_id=str(agent.id),
                    action=action.get("action", "unknown"),
                    action_type=tool_name or action.get("action", "think"),
                    risk_level=risk_level,
                    estimated_cost=self._estimate_action_cost(action) if hasattr(self, '_estimate_action_cost') else 0.0,
                    requires_financial=action.get("action") == "tool_call" and "payment" in str(action.get("tool_name", "")),
                    requires_real_world_effect=False,
                    parameters=action.get("tool_input", {}),
                )
                
                # Unbounded mode: skip gate enforcement (auto-approve all)
                if agent_mode == 'unbounded':
                    logger.info(f"Agent {agent.id} in UNBOUNDED mode — gate bypassed for {action.get('action')}")
                else:
                    gate_decision = gate.evaluate(gate_request)
                    
                    if not gate_decision.allowed:
                        step.output_data = {"blocked": True, "reason": "execution_gate", "gate_reason": gate_decision.reason, "mode": agent_mode}
                        duration_ms = int((time.time() - start_time) * 1000)
                        step.duration_ms = duration_ms
                        await db_session.commit()
                        return {"error": f"Execution gate blocked ({agent_mode} mode): {gate_decision.reason}"}
                    
                    if gate_decision.requires_approval:
                        step.required_approval = True
                        step.approval_status = "pending"
                        step.output_data = {"waiting_approval": True, "gate_reason": gate_decision.reason, "mode": agent_mode}
                        await db_session.commit()
                        return {"waiting_approval": True, "step_id": str(step.id), "reason": gate_decision.reason}

            # === DSID-P TRUST SCORE: Block low-trust agents from high-risk actions ===
            if DSIDP_AVAILABLE:
                trust_score = await self._get_agent_trust_score(str(agent.id), db_session)
                action_risk = self._get_action_risk_level(action)
                
                # T1 Restricted (0-39): Cannot execute any high-risk actions
                # T2 Bronze (40-59): Cannot execute critical actions
                # T3+ can execute based on policy
                if trust_score < 40 and action_risk >= 4:  # SRR-4 or SRR-5
                    step.output_data = {"blocked": True, "reason": "trust_too_low"}
                    await db_session.commit()
                    return {"error": f"Trust score {trust_score:.0f} too low for high-risk action (requires T2+)"}
                
                if trust_score < 60 and action_risk >= 5:  # SRR-5 Critical
                    step.output_data = {"blocked": True, "reason": "trust_insufficient_for_critical"}
                    await db_session.commit()
                    return {"error": f"Trust score {trust_score:.0f} insufficient for critical action (requires T3+)"}

            # === DSID-P TENANT ISOLATION (Section 32): Check cross-tenant access ===
            if action.get("target_agent_id"):
                target_agent_id = action.get("target_agent_id")
                tenant_allowed = await self._check_tenant_isolation(
                    str(agent.id), target_agent_id, db_session
                )
                if not tenant_allowed:
                    step.output_data = {"blocked": True, "reason": "cross_tenant_blocked"}
                    await db_session.commit()
                    return {"error": "Cross-tenant action blocked by federation policy"}

            # === DSID-P ETHICAL PILLARS (Section 36): Check ethical constraints ===
            ethical_violation = self._check_ethical_pillars(action)
            if ethical_violation:
                step.output_data = {"blocked": True, "reason": "ethical_violation", "pillar": ethical_violation}
                await db_session.commit()
                return {"error": f"Action blocked by ethical pillar: {ethical_violation}"}

            # === ECONOMIC BINDING: Check wallet before execution ===
            wallet_manager = get_wallet_manager()
            agent_wallet = wallet_manager.get_wallet(str(agent.id))
            estimated_cost = self._estimate_action_cost(action)
            
            if agent_wallet and estimated_cost > 0:
                balance = agent_wallet.get("balances", {}).get("RGT", 0)
                if float(balance) < estimated_cost:
                    step.output_data = {"blocked": True, "reason": "insufficient_funds"}
                    duration_ms = int((time.time() - start_time) * 1000)
                    step.duration_ms = duration_ms
                    await db_session.commit()
                    return {"error": f"Insufficient funds: need {estimated_cost} RGT, have {balance}"}
            
            # === POLICY ENGINE: Check if action is allowed by policy ===
            policy_engine = get_policy_engine()
            policy_ctx = PolicyContext(
                agent=agent,
                session=session,
                action_type=action.get("action", "think"),
                action_data=action,
                step_count=session.loop_count,
                total_cost=float(session.total_cost) if hasattr(session, 'total_cost') and session.total_cost else 0.0,
            )
            policy_decision = await policy_engine.evaluate(policy_ctx)
            
            if policy_decision.decision == PolicyDecision.ABORT:
                step.output_data = {"blocked": True, "reason": str(policy_decision.reasons)}
                await db_session.commit()
                return {"error": f"Policy blocked: {policy_decision.reasons}"}
            
            if policy_decision.decision == PolicyDecision.REQUIRE_APPROVAL and agent_mode != 'unbounded':
                step.required_approval = True
                step.approval_status = "pending"
                await db_session.commit()
                return {"waiting_approval": True, "step_id": str(step.id), "reason": str(policy_decision.reasons)}

            if requires_approval and agent_mode != 'unbounded':
                step.required_approval = True
                step.approval_status = "pending"
                await db_session.commit()
                
                await approval_manager.request_approval(
                    session_id=str(session.id),
                    step_id=str(step.id),
                    action_type=action.get("action"),
                    action_data=action,
                    reason="Safety check requires approval",
                    db_session=db_session,
                )
                return {"waiting_approval": True, "step_id": str(step.id)}

            # Execute action
            if action.get("action") == "tool_call":
                result = await self._execute_tool(
                    tool_name=action.get("tool_name"),
                    tool_input=action.get("tool_input", {}),
                    session=session,
                )
                step.tool_name = action.get("tool_name")
                step.tool_input = action.get("tool_input")
                step.tool_output = result
                session.total_tool_calls += 1

            elif action.get("action") == "respond":
                result = {"response": action.get("response"), **_step_credit_info}
                step.output_data = result
                return {
                    "goal_achieved": action.get("goal_achieved", True),
                    "response": action.get("response"),
                    "credits_used": _credits_used_total,
                    "credits_balance": _credits_balance,
                }

            else:  # think
                result = {"thought": action.get("reasoning")}

            # Merge credit info into step output for SSE streaming
            if _step_credit_info:
                result = {**result, **_step_credit_info}
            step.output_data = result
            duration_ms = int((time.time() - start_time) * 1000)
            step.duration_ms = duration_ms
            
            # === BINDING ECONOMIC CONSTRAINT: Spend after successful execution ===
            if estimated_cost > 0:
                from decimal import Decimal
                spend_tx = await wallet_manager.spend(
                    agent_id=str(agent.id),
                    amount=Decimal(str(estimated_cost)),
                    purpose=f"{action.get('action', 'unknown')}:{action.get('tool_name', 'none')}",
                )
                if spend_tx:
                    step.cost = estimated_cost
                    logger.info(f"Agent {agent.id} spent {estimated_cost} RGT for action")
            
            await db_session.commit()

            # Truncate large results before they go into history
            # (fetch_url can return 20K+ chars of HTML, blowing up the prompt)
            result_for_history = result
            result_str = json.dumps(result, ensure_ascii=False)
            if len(result_str) > 2000:
                result_for_history = {"summary": result_str[:2000] + "...(truncated)"}

            return {
                "action": action.get("action"),
                "tool_name": action.get("tool_name"),
                "tool_input": action.get("tool_input"),
                "reasoning": action.get("reasoning"),
                "result": result_for_history,
                "goal_achieved": action.get("goal_achieved", False),
            }

        except Exception as e:
            import traceback
            logger.error(f"Step execution error: {e}\n{traceback.format_exc()}")
            step.output_data = {"error": str(e)}
            duration_ms = int((time.time() - start_time) * 1000)
            step.duration_ms = duration_ms
            await db_session.commit()
            return {"error": str(e)}

    async def _fetch_user_byok_keys(self, user_id: str) -> Dict[str, str]:
        """Fetch user's BYOK API keys from auth service."""
        if not user_id or user_id in ("anonymous", "agent-system"):
            return {}
        try:
            url = f"{AUTH_SERVICE_URL.rstrip('/')}/auth/internal/user-api-keys/{user_id}"
            headers = {"x-user-id": user_id}
            if _INTERNAL_SERVICE_KEY:
                headers["x-internal-service-key"] = _INTERNAL_SERVICE_KEY
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    keys = {}
                    _alias = {"google": "gemini", "chatgpt": "openai", "claude": "anthropic"}
                    for entry in data.get("keys", []):
                        prov = entry.get("provider")
                        key = entry.get("api_key")
                        if prov and key:
                            keys[_alias.get(prov.lower(), prov.lower())] = key
                    if keys:
                        logger.info(f"[BYOK-EXEC] Loaded {len(keys)} keys for {user_id}: {list(keys.keys())}")
                    return keys
        except Exception as e:
            logger.warning(f"[BYOK-EXEC] Failed to fetch keys for {user_id}: {e}")
        return {}

    async def _get_next_action(
        self,
        agent: AgentDefinition,
        goal: str,
        context: Dict[str, Any],
        history: List[Dict[str, Any]],
        user_keys: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Get the agent's next action using UnifiedLLMClient."""
        history_str = self._format_history(history[-5:]) if history else "No previous steps."
        context_str = json.dumps(context, indent=2)

        system_prompt = agent.system_prompt or self.DEFAULT_SYSTEM_PROMPT
        
        prompt = self.EXECUTION_FRAME.format(
            goal=goal,
            context=context_str,
            history=history_str,
        )

        # Phase 2.4: Inject learning recommendations into prompt
        try:
            ll = get_learning_loop()
            recs = ll.get_recommendations(goal, list(self._handler_map.keys()))
            if recs.get("confidence", 0) > 0.3:
                hints = []
                for seq in recs.get("suggested_sequences", [])[:2]:
                    hints.append(f"Proven sequence: {' → '.join(seq['sequence'])} (success rate {seq['success_rate']:.0%})")
                for avoid in recs.get("patterns_to_avoid", [])[:2]:
                    hints.append(f"Avoid: {avoid['error'][:60]} (seen {avoid['occurrences']}x)")
                if hints:
                    prompt += "\n\nLearned patterns from past executions:\n- " + "\n- ".join(hints)
        except Exception:
            pass

        # ── INTELLIGENCE: Inject recalled memories + past successes ──
        try:
            recalled_memories = (context or {}).get("recalled_memories", [])
            past_successes = (context or {}).get("past_successes", [])
            if recalled_memories:
                mem_lines = [m[:300] for m in recalled_memories[:3]]
                prompt += "\n\nRelevant memories from past interactions:\n- " + "\n- ".join(mem_lines)
            if past_successes:
                success_lines = []
                for ps in past_successes[:3]:
                    success_lines.append(f"Goal: {ps.get('goal', '')[:100]} → completed in {ps.get('steps', '?')} steps")
                prompt += "\n\nPast successful tasks:\n- " + "\n- ".join(success_lines)
        except Exception:
            pass

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        # Resolve provider from agent config
        _alias = {"chatgpt": "openai", "gpt": "openai", "google": "gemini", "claude": "anthropic", "gemini": "google"}
        agent_provider = getattr(agent, "provider", None) or ""
        preferred = _alias.get(agent_provider.lower(), agent_provider.lower()) if agent_provider else None

        response = await _llm_client.complete(
            {
                "messages": messages,
                "provider": preferred,
                "model": agent.model or None,
                "temperature": agent.temperature or 0.7,
                "max_tokens": agent.max_tokens or 16384,
                "response_format": {"type": "json_object"},
            },
            user_keys=user_keys,
        )

        content = response.content
        tokens_used = response.usage.get("total_tokens", 0)

        if not content:
            raise RuntimeError(f"All LLM providers failed. Chain: {response.fallback_chain}")

        # Parse JSON response (with multiple fallback strategies)
        parsed = None

        # Strategy 1: Direct JSON parse
        try:
            parsed = json.loads(content)
        except Exception:
            pass

        # Strategy 2: Strip markdown fences (```json ... ```)
        if parsed is None:
            stripped = re.sub(r"^```(?:json)?\s*\n?", "", content.strip(), flags=re.IGNORECASE)
            stripped = re.sub(r"\n?```\s*$", "", stripped.strip())
            if stripped != content.strip():
                try:
                    parsed = json.loads(stripped)
                except Exception:
                    pass

        # Strategy 3: Regex extract first JSON object
        if parsed is None:
            m = re.search(r"\{.*\}", content, flags=re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except Exception:
                    pass

        # Strategy 4: LLM returned plain text — wrap as a respond action
        if parsed is None:
            logger.warning(f"[LLM] Non-JSON from {response.provider}, wrapping as respond: {content[:120]}")
            parsed = {
                "reasoning": "LLM returned non-JSON text; treating as direct response.",
                "action": "respond",
                "response": content[:4000],
                "goal_achieved": False,
            }

        parsed["_tokens_used"] = tokens_used
        logger.info(f"[LLM] Success via {response.provider}/{response.model} ({tokens_used} tokens, fallback={response.was_fallback})")
        return parsed

    async def _execute_tool(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        session: AgentSession,
    ) -> Dict[str, Any]:
        """Execute a tool and return the result (with observability)."""
        # === SANDBOX BOUNDARY: Validate call before execution ===
        if self._sandbox:
            try:
                allowed, violations = await self._sandbox.validate_call(tool_name, tool_input or {})
                if not allowed:
                    print(f"[SANDBOX] Tool '{tool_name}' BLOCKED: {violations}", flush=True)
                    return {"error": f"Sandbox blocked: {'; '.join(violations)}"}
                print(f"[SANDBOX] Tool '{tool_name}' ALLOWED", flush=True)
            except Exception as e:
                print(f"[SANDBOX] Validation error for '{tool_name}': {e}", flush=True)

        _agent_id = getattr(session, "id", "")
        _user_id = getattr(session, "user_id", "")
        _loop_num = getattr(session, "loop_count", 0)

        async with _executor_observer.observe(
            tool_name, user_id=str(_user_id), agent_id=str(_agent_id),
            loop_number=_loop_num, args=tool_input,
        ) as _obs:
            result = await self._execute_tool_inner(tool_name, tool_input, session)
            if isinstance(result, dict) and result.get("error"):
                _obs.set_error(result["error"])
            else:
                _obs.set_result(result)
            return result

    async def _execute_tool_inner(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        session: AgentSession,
    ) -> Dict[str, Any]:
        """Dispatch tool execution through unified registry + handler map."""

        # 1. Check handler map (executor-implemented tools)
        handler = self._handler_map.get(tool_name)
        if handler:
            try:
                return await handler(tool_input, session=session)
            except Exception as e:
                return {"error": str(e)}

        # 2. Proxy to ed_service for file/git/docker/workflow tools
        if tool_name in self.ED_SERVICE_TOOLS:
            try:
                ed_result = await self._proxy_to_ed_service(tool_name, tool_input or {}, session=session)
                if ed_result is not None:
                    return ed_result
            except Exception as e:
                logger.warning(f"ed_service proxy failed for '{tool_name}': {e}")

        # 3. Check if tool exists in unified registry (may have http/webhook config)
        tool_def = self._registry.get(tool_name)
        if tool_def and tool_def.handler:
            # Try ed_service proxy as generic fallback for registry tools
            try:
                ed_result = await self._proxy_to_ed_service(tool_name, tool_input or {}, session=session)
                if ed_result is not None:
                    return ed_result
            except Exception:
                pass

        # 4. Agent-management tools → self-call via platform_api to agent_engine
        if tool_name.startswith("agents_") or tool_name.startswith("architect_"):
            return await self._tool_agent_self_call(tool_name, tool_input or {}, session=session)

        # 5. Code visualizer / state physics / skill_ → proxy via platform_api
        SERVICE_PREFIX_MAP = {
            "code_visualizer_": ("agent_engine", "/ast"),
            "sp_": ("agent_engine", "/state-physics"),
            "skill_": ("chat", "/skill"),
            "github_": ("agent_engine", "/github"),
        }
        for prefix, (service, base_path) in SERVICE_PREFIX_MAP.items():
            if tool_name.startswith(prefix):
                action = tool_name[len(prefix):]
                return await self._tool_platform_api({
                    "service": service,
                    "endpoint": f"{base_path}/{action}",
                    "method": "POST",
                    "body": tool_input or {},
                }, session=session)

        # 6. Last resort: try ed_service even if not in ED_SERVICE_TOOLS list
        try:
            from .config import settings
            url = f"{settings.ED_SERVICE_URL}/tools/{tool_name}/execute"
            headers = {}
            if session:
                headers["x-user-id"] = str(getattr(session, "user_id", "") or "")
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=tool_input or {}, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("output", data)
        except Exception:
            pass

        # 7. Tool not found anywhere
        logger.warning(f"Tool '{tool_name}' — no handler in map, not in ED_SERVICE_TOOLS, not proxied")
        return {"error": f"Tool '{tool_name}' not available. Use platform_api(service='...', endpoint='...') for custom calls."}

    # ------------------------------------------------------------------
    # Phase 1.3: ed_service tool proxy
    # ------------------------------------------------------------------
    ED_SERVICE_TOOLS = frozenset([
        # File tools (original names)
        "read_file", "write_file", "list_files", "search_files",
        "search_content", "delete_file", "validate_code",
        # File tools (registry aliases)
        "file_read", "file_write", "file_list", "file_edit", "file_delete",
        "find_by_name", "grep_search", "multi_edit",
        # Git tools
        "git_clone", "git_status", "git_add", "git_commit", "git_push",
        "git_pull", "git_diff", "git_checkout", "git_log",
        "git_apply_patch", "git_stash", "git_branch", "git_merge",
        # Docker tools
        "docker_build", "docker_run", "docker_stop", "docker_logs",
        "docker_exec", "docker_ps", "docker_images", "docker_rm",
        "docker_compose_up", "docker_compose_down",
        # Workflow / cognitive
        "trigger_workflow", "ask_llm", "log_insight", "get_current_time",
        "command_status",
    ])

    async def _proxy_to_ed_service(
        self, tool_name: str, tool_input: Dict[str, Any],
        *, session: Optional[AgentSession] = None,
    ) -> Optional[Dict[str, Any]]:
        """Forward a tool call to ed_service POST /tools/{name}/execute.
        Returns the result dict, or None if ed_service doesn't have the tool."""
        if tool_name not in self.ED_SERVICE_TOOLS:
            return None
        from .config import settings
        url = f"{settings.ED_SERVICE_URL}/tools/{tool_name}/execute"
        headers = {}
        if session:
            headers["x-user-id"] = str(getattr(session, "user_id", "") or "")
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=tool_input, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("output", data)
                logger.warning(f"ed_service proxy {tool_name}: HTTP {resp.status_code}")
                return {"error": f"ed_service returned HTTP {resp.status_code}"}
        except httpx.ConnectError:
            logger.warning(f"ed_service unreachable for tool '{tool_name}'")
            return None
        except Exception as e:
            logger.warning(f"ed_service proxy error for '{tool_name}': {e}")
            return {"error": str(e)}

    def _format_history(self, history: List[Dict[str, Any]]) -> str:
        """Format history in a structured way the LLM can learn from."""
        if not history:
            return "No previous steps."
        lines = []
        for i, step in enumerate(history):
            action = step.get("action", "unknown")
            tool = step.get("tool_name") or ""
            reasoning = step.get("reasoning") or ""
            error = step.get("error")

            feedback = step.get("verification_feedback")

            if action == "tool_call" and tool:
                tool_input = step.get("tool_input") or {}
                result = step.get("result") or {}
                # Truncate large results
                result_str = json.dumps(result, ensure_ascii=False)
                if len(result_str) > 400:
                    result_str = result_str[:400] + "...(truncated)"
                lines.append(f"Step {i+1}: Used {tool}({json.dumps(tool_input)})")
                if error:
                    lines.append(f"  ERROR: {error}")
                else:
                    lines.append(f"  Result: {result_str}")
            elif action == "think":
                lines.append(f"Step {i+1}: Thought: {reasoning[:200]}")
            elif action == "respond":
                lines.append(f"Step {i+1}: Responded with final answer")
            else:
                lines.append(f"Step {i+1}: {action}")
                if error:
                    lines.append(f"  ERROR: {error}")
            if feedback:
                lines.append(f"  FEEDBACK: {feedback}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Search variant tools — wrap web_search with category prefixes
    # ------------------------------------------------------------------

    async def _tool_news_search(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        q = (tool_input or {}).get("query", "")
        return await self._tool_web_search({"query": f"latest news: {q}"}, session=session)

    async def _tool_image_search(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        q = (tool_input or {}).get("query", "")
        return await self._tool_web_search({"query": f"images of: {q}"}, session=session)

    async def _tool_youtube_search(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        q = (tool_input or {}).get("query", "")
        return await self._tool_web_search({"query": f"site:youtube.com {q}"}, session=session)

    async def _tool_reddit_search(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        q = (tool_input or {}).get("query", "")
        return await self._tool_web_search({"query": f"site:reddit.com {q}"}, session=session)

    async def _tool_wikipedia(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        q = (tool_input or {}).get("query") or (tool_input or {}).get("topic", "")
        return await self._tool_web_search({"query": f"site:wikipedia.org {q}"}, session=session)

    async def _tool_weather(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        loc = (tool_input or {}).get("location") or (tool_input or {}).get("query", "")
        return await self._tool_web_search({"query": f"current weather in {loc}"}, session=session)

    async def _tool_stock_crypto(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        sym = (tool_input or {}).get("symbol") or (tool_input or {}).get("query", "")
        return await self._tool_web_search({"query": f"current price of {sym} stock crypto"}, session=session)

    async def _tool_places_search(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        q = (tool_input or {}).get("query", "")
        loc = (tool_input or {}).get("location", "")
        return await self._tool_web_search({"query": f"places: {q} near {loc}"}, session=session)

    # ------------------------------------------------------------------
    # Rabbit community tools — proxy to rabbit service via platform_api
    # ------------------------------------------------------------------

    async def _tool_list_rabbit_posts(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        return await self._tool_platform_api({"service": "rabbit", "endpoint": "/posts", "method": "GET", "body": tool_input or {}}, session=session)

    async def _tool_get_rabbit_post(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        post_id = (tool_input or {}).get("post_id", "")
        return await self._tool_platform_api({"service": "rabbit", "endpoint": f"/posts/{post_id}", "method": "GET"}, session=session)

    async def _tool_get_rabbit_community(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        slug = (tool_input or {}).get("slug", "")
        return await self._tool_platform_api({"service": "rabbit", "endpoint": f"/communities/{slug}", "method": "GET"}, session=session)

    async def _tool_delete_rabbit_post(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        post_id = (tool_input or {}).get("post_id", "")
        return await self._tool_platform_api({"service": "rabbit", "endpoint": f"/posts/{post_id}", "method": "DELETE"}, session=session)

    async def _tool_create_rabbit_comment(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        return await self._tool_platform_api({"service": "rabbit", "endpoint": "/comments", "method": "POST", "body": tool_input or {}}, session=session)

    async def _tool_delete_rabbit_comment(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        cid = (tool_input or {}).get("comment_id", "")
        return await self._tool_platform_api({"service": "rabbit", "endpoint": f"/comments/{cid}", "method": "DELETE"}, session=session)

    async def _tool_list_rabbit_comments(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        post_id = (tool_input or {}).get("post_id", "")
        return await self._tool_platform_api({"service": "rabbit", "endpoint": f"/posts/{post_id}/comments", "method": "GET"}, session=session)

    async def _tool_search_rabbit_posts(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        return await self._tool_platform_api({"service": "rabbit", "endpoint": "/posts/search", "method": "POST", "body": tool_input or {}}, session=session)

    async def _tool_rabbit_vote(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        return await self._tool_platform_api({"service": "rabbit", "endpoint": "/votes", "method": "POST", "body": tool_input or {}}, session=session)

    # ------------------------------------------------------------------
    # Info / utility tools
    # ------------------------------------------------------------------

    async def _tool_get_current_time(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        return {"utc": now.isoformat(), "unix": int(now.timestamp()), "readable": now.strftime("%Y-%m-%d %H:%M:%S UTC")}

    async def _tool_get_system_info(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        return {"platform": "DevSwat Agent Engine", "version": "2.0", "tools_available": len(self._handler_map), "ed_service_tools": len(self.ED_SERVICE_TOOLS)}

    async def _tool_send_email(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        return await self._tool_platform_api({"service": "notification", "endpoint": "/email/send", "method": "POST", "body": tool_input or {}}, session=session)

    async def _tool_memory_stats(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        return await self._tool_platform_api({"service": "memory", "endpoint": "/memory/rag/stats", "method": "GET"}, session=session)

    async def _tool_generate_chart(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        return await self._tool_execute_code({"language": "python", "code": (tool_input or {}).get("code", "print('No chart code provided')")}, session=session)

    # ------------------------------------------------------------------
    # Hash Sphere tools — proxy to memory service
    # ------------------------------------------------------------------

    async def _tool_hash_sphere(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        action = (tool_input or {}).get("action", "search")
        return await self._tool_platform_api({"service": "memory", "endpoint": f"/memory/hash-sphere/{action}", "method": "POST", "body": tool_input or {}}, session=session)

    # ------------------------------------------------------------------
    # Workspace / session / options tools
    # ------------------------------------------------------------------

    async def _tool_workspace_snapshot(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        return await self._tool_platform_api({"service": "agent_engine", "endpoint": "/agents/", "method": "GET"}, session=session)

    async def _tool_session_log(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        sid = (tool_input or {}).get("session_id", "")
        return await self._tool_platform_api({"service": "agent_engine", "endpoint": f"/agents/sessions/{sid}/steps", "method": "GET"}, session=session)

    async def _tool_present_options(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        return {"type": "PickOne", "question": (tool_input or {}).get("question", ""), "options": (tool_input or {}).get("options", [])}

    async def _tool_run_agent(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        agent_id = (tool_input or {}).get("agent_id", "")
        return await self._tool_platform_api({"service": "agent_engine", "endpoint": f"/agents/{agent_id}/start", "method": "POST", "body": tool_input or {}}, session=session)

    async def _tool_schedule_agent(self, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        agent_id = (tool_input or {}).get("agent_id", "")
        return await self._tool_platform_api({"service": "agent_engine", "endpoint": f"/agents/{agent_id}/schedules", "method": "POST", "body": tool_input or {}}, session=session)

    # ------------------------------------------------------------------
    # Agent self-call — agents_* and architect_* tools call own API
    # ------------------------------------------------------------------

    async def _tool_agent_self_call(self, tool_name: str, tool_input: Dict[str, Any], *, session=None) -> Dict[str, Any]:
        AGENT_ENDPOINTS = {
            "agents_list": ("GET", "/agents/"),
            "agents_create": ("POST", "/agents/"),
            "agents_delete": ("DELETE", "/agents/{agent_id}"),
            "agents_update": ("PATCH", "/agents/{agent_id}"),
            "agents_status": ("GET", "/agents/{agent_id}"),
            "agents_start": ("POST", "/agents/{agent_id}/start"),
            "agents_stop": ("POST", "/agents/{agent_id}/stop"),
            "agents_sessions": ("GET", "/agents/{agent_id}/sessions"),
            "agents_metrics": ("GET", "/agents/metrics"),
            "agents_available_tools": ("GET", "/agents/tools"),
            "agents_templates": ("GET", "/agents/templates"),
            "agents_versions": ("GET", "/agents/{agent_id}/versions"),
            "agents_session_detail": ("GET", "/agents/sessions/{session_id}"),
            "agents_session_steps": ("GET", "/agents/sessions/{session_id}/steps"),
            "agents_session_cancel": ("POST", "/agents/sessions/{session_id}/cancel"),
            "agents_session_trace": ("GET", "/agents/sessions/{session_id}/trace"),
            "architect_create_agent": ("POST", "/agents/"),
            "architect_plan": ("POST", "/agents/plan"),
            "architect_list_available_tools": ("GET", "/agents/tools"),
            "architect_list_providers": ("GET", "/agents/providers"),
            "architect_assign_goal": ("POST", "/agents/{agent_id}/start"),
            "architect_set_autonomy": ("PATCH", "/agents/{agent_id}"),
            "architect_create_schedule": ("POST", "/agents/{agent_id}/schedules"),
            "architect_create_webhook": ("POST", "/agents/{agent_id}/triggers"),
            "run_agent": ("POST", "/agents/{agent_id}/start"),
            "schedule_agent": ("POST", "/agents/{agent_id}/schedules"),
        }
        ep = AGENT_ENDPOINTS.get(tool_name)
        if not ep:
            return {"error": f"Unknown agent tool: {tool_name}"}
        method, path = ep
        for key in ["agent_id", "session_id"]:
            if f"{{{key}}}" in path:
                val = (tool_input or {}).get(key, "")
                path = path.replace(f"{{{key}}}", str(val))
        return await self._tool_platform_api({"service": "agent_engine", "endpoint": path, "method": method, "body": tool_input or {}}, session=session)

    # ------------------------------------------------------------------
    # Dynamic Tool Management — delegates to routers_agentic_chat handlers
    # These let Agent Engine sessions create/manage tools
    # ------------------------------------------------------------------

    async def _tool_create_tool(self, tool_input: dict, session=None):
        from .routers_agentic_chat import _custom_create_tool
        ctx = self._build_tool_ctx(session)
        return await _custom_create_tool(tool_input, ctx)

    async def _tool_list_tools(self, tool_input: dict, session=None):
        from .routers_agentic_chat import _custom_list_tools
        ctx = self._build_tool_ctx(session)
        return await _custom_list_tools(tool_input, ctx)

    async def _tool_delete_tool(self, tool_input: dict, session=None):
        from .routers_agentic_chat import _custom_delete_tool
        ctx = self._build_tool_ctx(session)
        return await _custom_delete_tool(tool_input, ctx)

    async def _tool_update_tool(self, tool_input: dict, session=None):
        from .routers_agentic_chat import _custom_update_tool
        ctx = self._build_tool_ctx(session)
        return await _custom_update_tool(tool_input, ctx)

    async def _tool_auto_build_tool(self, tool_input: dict, session=None):
        from .routers_agentic_chat import _custom_auto_build_tool
        ctx = self._build_tool_ctx(session)
        return await _custom_auto_build_tool(tool_input, ctx)

    async def _tool_check_tool_exists(self, tool_input: dict, session=None):
        from .routers_agentic_chat import _custom_check_tool_exists
        ctx = self._build_tool_ctx(session)
        return await _custom_check_tool_exists(tool_input, ctx)

    def _build_tool_ctx(self, session) -> dict:
        """Build a context dict from session for tool management handlers."""
        if session:
            return {
                "user_id": str(session.user_id) if hasattr(session, 'user_id') else
                           (session.get("user_id") if isinstance(session, dict) else "agent-system"),
                "org_id": (session.context.get("org_id", "") if hasattr(session, 'context') else ""),
                "user_role": (session.context.get("user_role", "user") if hasattr(session, 'context') else "user"),
            }
        return {"user_id": "agent-system", "org_id": "", "user_role": "system"}

    def register_tool_handler(self, name: str, handler: callable):
        """Register a tool handler into the unified handler map."""
        self._handler_map[name] = handler

    # ── INTELLIGENCE LAYER: Memory recall + learning persistence ──────

    async def _recall_agent_memory(
        self,
        agent_id: str,
        user_id: str,
        goal: str,
    ) -> Dict[str, Any]:
        """Recall relevant memories and learned patterns before a session starts.

        This is the key intelligence bridge — agents don't start from zero.
        They load:
        1. Relevant memories from Hash Sphere (past interactions, facts)
        2. Learned patterns from the learning loop (successful sequences, errors to avoid)
        3. Past successes for similar goals
        """
        result: Dict[str, Any] = {"memories": [], "patterns": [], "past_successes": []}

        # 1. Recall from Hash Sphere / Memory Service
        try:
            url = f"{settings.MEMORY_SERVICE_URL.rstrip('/')}/memory/retrieve"
            payload = {
                "query": goal[:500],
                "user_id": user_id or agent_id,
                "agent_id": agent_id,
                "top_k": 5,
                "min_relevance": 0.3,
            }
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    memories = data.get("results") or data.get("memories") or []
                    for mem in memories[:5]:
                        content = mem.get("content") or mem.get("text") or str(mem)
                        if isinstance(content, str) and len(content) > 10:
                            result["memories"].append(content[:500])
                    if result["memories"]:
                        logger.info(f"[INTELLIGENCE] Recalled {len(result['memories'])} memories for agent {agent_id}")
        except Exception as e:
            logger.debug(f"[INTELLIGENCE] Memory recall skipped: {e}")

        # 2. Load learned patterns from learning loop
        try:
            ll = get_learning_loop()
            recs = ll.get_recommendations(goal, list(self._handler_map.keys()))
            if recs.get("confidence", 0) > 0.2:
                for seq in recs.get("suggested_sequences", [])[:3]:
                    result["patterns"].append({
                        "type": "proven_sequence",
                        "sequence": seq.get("sequence", []),
                        "success_rate": seq.get("success_rate", 0),
                    })
                for avoid in recs.get("patterns_to_avoid", [])[:3]:
                    result["patterns"].append({
                        "type": "avoid",
                        "error": avoid.get("error", "")[:200],
                        "occurrences": avoid.get("occurrences", 0),
                    })
        except Exception as e:
            logger.debug(f"[INTELLIGENCE] Learning recall skipped: {e}")

        # 3. Load past successes for similar goals from DB
        try:
            from sqlalchemy import select
            from .db import async_session
            async with async_session() as db:
                from .models import AgentSession
                stmt = (
                    select(AgentSession)
                    .where(
                        AgentSession.agent_id == agent_id,
                        AgentSession.status == "completed",
                    )
                    .order_by(AgentSession.completed_at.desc())
                    .limit(5)
                )
                rows = await db.execute(stmt)
                sessions = rows.scalars().all()
                for s in sessions:
                    past_goal = s.current_goal or ""
                    if past_goal and len(past_goal) > 5:
                        result["past_successes"].append({
                            "goal": past_goal[:200],
                            "steps": s.loop_count or 0,
                            "output_preview": (s.final_output or "")[:200],
                        })
        except Exception as e:
            logger.debug(f"[INTELLIGENCE] Past successes recall skipped: {e}")

        return result

    async def _persist_learning_to_memory(
        self,
        agent_id: str,
        user_id: str,
        goal: str,
        outcome: str,
        patterns: List[Dict[str, Any]],
    ):
        """Persist learned patterns to Memory Service so they survive restarts.

        Called after _record_session_learning to make the in-memory learning
        loop patterns durable via Hash Sphere.
        """
        if not patterns:
            return

        # Build a compact learning summary
        pattern_lines = []
        for p in patterns[:5]:
            ptype = p.get("type", "pattern")
            if ptype == "proven_sequence":
                seq = " → ".join(p.get("sequence", []))
                pattern_lines.append(f"Proven: {seq} (success {p.get('success_rate', 0):.0%})")
            elif ptype == "avoid":
                pattern_lines.append(f"Avoid: {p.get('error', '')[:100]}")
            else:
                pattern_lines.append(f"{ptype}: {json.dumps(p)[:150]}")

        content = (
            f"Agent learning from goal: {goal[:200]}\n"
            f"Outcome: {outcome}\n"
            f"Patterns:\n" + "\n".join(f"- {l}" for l in pattern_lines)
        )

        try:
            url = f"{settings.MEMORY_SERVICE_URL.rstrip('/')}/memory/ingest"
            payload = {
                "content": content,
                "user_id": user_id or agent_id,
                "agent_id": agent_id,
                "metadata": {
                    "type": "agent_learning",
                    "goal": goal[:200],
                    "outcome": outcome,
                    "pattern_count": len(patterns),
                },
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code in (200, 201):
                    logger.info(f"[INTELLIGENCE] Persisted {len(patterns)} patterns for agent {agent_id}")
        except Exception as e:
            logger.debug(f"[INTELLIGENCE] Failed to persist learning: {e}")

    def _estimate_action_cost(self, action: Dict[str, Any]) -> float:
        """
        Estimate the cost of an action in RGT tokens.
        
        This enables ECONOMIC BINDING - agents cannot execute
        actions they cannot afford.
        """
        action_type = action.get("action", "think")
        
        # Base costs by action type
        costs = {
            "think": 0.01,      # Minimal cost for thinking
            "tool_call": 0.1,   # Tool calls cost more
            "respond": 0.05,    # Responses are moderate
            "api_call": 0.5,    # External API calls are expensive
            "blockchain": 1.0,  # Blockchain operations are costly
        }
        
        base_cost = costs.get(action_type, 0.01)
        
        # Adjust based on tool complexity
        tool_name = action.get("tool_name") or ""
        if "llm" in tool_name.lower() or "gpt" in tool_name.lower():
            base_cost *= 5  # LLM calls are expensive
        elif "search" in tool_name.lower():
            base_cost *= 2  # Search operations
        elif "execute" in tool_name.lower():
            base_cost *= 3  # Code execution
            
        return base_cost

    def _record_session_learning(
        self,
        session: 'AgentSession',
        agent: 'AgentDefinition',
        result: Dict[str, Any],
        step_history: list,
        start_time: float,
    ):
        """
        Record session outcome for learning.
        
        This enables PERSISTENT LEARNING - agents improve over time.
        Called at the end of every run_loop execution.
        Records to in-memory learning loop AND persists to Memory Service.
        """
        import time
        _patterns_to_persist: List[Dict[str, Any]] = []
        try:
            loop = get_learning_loop()
            duration = time.time() - start_time
            loop.record_execution(
                session_id=str(session.id),
                agent_id=str(agent.id),
                goal=session.current_goal or "",
                goal_achieved=(result.get("status") == "completed"),
                steps_taken=session.loop_count or 0,
                tokens_used=session.total_tokens_used or 0,
                duration_seconds=duration,
                step_history=step_history,
                error_message=result.get("error"),
                final_output=result.get("output"),
                context=session.context or {},
            )
            logger.info(f"[LEARNING] Recorded session {session.id} outcome: {result.get('status')}")

            # Extract patterns for persistence
            recs = loop.get_recommendations(
                session.current_goal or "",
                list(self._handler_map.keys()),
            )
            if recs.get("confidence", 0) > 0.2:
                for seq in recs.get("suggested_sequences", [])[:3]:
                    _patterns_to_persist.append({
                        "type": "proven_sequence",
                        "sequence": seq.get("sequence", []),
                        "success_rate": seq.get("success_rate", 0),
                    })
                for avoid in recs.get("patterns_to_avoid", [])[:3]:
                    _patterns_to_persist.append({
                        "type": "avoid",
                        "error": avoid.get("error", "")[:200],
                        "occurrences": avoid.get("occurrences", 0),
                    })
        except Exception as e:
            logger.warning(f"[LEARNING] Failed to record: {e}")

        # Persist to Memory Service (durable across restarts)
        if _patterns_to_persist:
            import asyncio
            try:
                user_id = str(session.user_id) if session.user_id else ""
                asyncio.ensure_future(self._persist_learning_to_memory(
                    agent_id=str(agent.id),
                    user_id=user_id,
                    goal=session.current_goal or "",
                    outcome=result.get("status", "unknown"),
                    patterns=_patterns_to_persist,
                ))
            except Exception as e:
                logger.debug(f"[LEARNING] Persistence dispatch failed: {e}")

        # Phase 3.4: Record agent behavior for value drift detection
        try:
            dm = get_drift_manager()
            tool_names = [s.get("tool_name", "") for s in step_history if s.get("tool_name")]
            dm.record_decision(
                agent_id=str(agent.id),
                decision_type="session_complete",
                context={
                    "goal": session.current_goal or "",
                    "status": result.get("status"),
                    "tools_used": tool_names,
                    "steps": session.loop_count or 0,
                },
            )
            # Check for cluster drift (DSID-P boundary enforcement)
            if tool_names:
                for tn in tool_names:
                    drift_alert = dm.check_cluster_drift(
                        str(agent.id),
                        {"action_type": "tool_call", "tool_name": tn},
                    )
                    if drift_alert:
                        logger.warning(f"[DRIFT] Cluster drift detected for agent {agent.id}: {drift_alert}")
        except Exception as e:
            logger.debug(f"[DRIFT] Failed to record drift data: {e}")

    async def _get_agent_trust_score(self, agent_id: str, db_session: AsyncSession) -> float:
        """
        Get DSID-P trust score for an agent.
        
        Trust Tiers:
        - T5 Platinum (90-100): Enterprise/Gov-grade
        - T4 Gold (75-89): High-performing
        - T3 Silver (60-74): Stable
        - T2 Bronze (40-59): Limited trust
        - T1 Restricted (0-39): Heavily supervised
        """
        # Default trust score for new agents
        default_score = 65.0  # T3 Silver
        
        if not DSIDP_AVAILABLE:
            return default_score
        
        # Check if agent has a trust record
        # For now, calculate from performance metrics
        try:
            result = await db_session.execute(
                select(AgentSession)
                .where(AgentSession.agent_id == agent_id)
                .order_by(AgentSession.started_at.desc())
                .limit(20)
            )
            sessions = list(result.scalars().all())
            
            if not sessions:
                return default_score
            
            # Calculate trust from session outcomes
            completed = sum(1 for s in sessions if s.status == "completed")
            failed = sum(1 for s in sessions if s.status == "failed")
            total = len(sessions)
            
            if total == 0:
                return default_score
            
            # Performance factor (0-40 points)
            performance = (completed / total) * 40
            
            # Stability factor (0-30 points) - fewer failures = more stable
            stability = max(0, 30 - (failed * 5))
            
            # Base score (30 points for existing agent)
            base = 30
            
            trust_score = min(100, base + performance + stability)
            return trust_score
            
        except Exception:
            return default_score

    def _get_action_risk_level(self, action: Dict[str, Any]) -> int:
        """
        Get DSID-P Semantic Risk Rating (SRR) for an action.
        
        SRR Levels:
        - SRR-1: Minimal (summarization, search)
        - SRR-2: Low (creative, communication)
        - SRR-3: Medium (workflow, planning)
        - SRR-4: High (finance, system control)
        - SRR-5: Critical (legal, medical, governance)
        """
        action_type = action.get("action", "think")
        tool_name = (action.get("tool_name") or "").lower()
        
        # SRR-5: Critical risk actions
        critical_keywords = ["medical", "legal", "compliance", "governance", "admin", "sudo", "root"]
        if any(k in tool_name for k in critical_keywords):
            return 5
        
        # SRR-4: High risk actions
        high_risk_keywords = ["finance", "payment", "transfer", "execute", "deploy", "delete", "modify"]
        if any(k in tool_name for k in high_risk_keywords):
            return 4
        
        # SRR-3: Medium risk actions
        if action_type == "tool_call":
            return 3
        
        # SRR-2: Low risk
        if action_type == "respond":
            return 2
        
        # SRR-1: Minimal risk
        return 1

    async def _check_tenant_isolation(
        self,
        from_agent_id: str,
        to_agent_id: str,
        db_session: AsyncSession,
    ) -> bool:
        """
        DSID-P Federation (Section 32): Check tenant isolation.
        
        Enforces multi-tenant boundaries:
        - Same tenant: allowed
        - Cross-tenant: requires federation scope
        """
        try:
            # Get both agents
            from_result = await db_session.execute(
                select(AgentDefinition).where(AgentDefinition.id == from_agent_id)
            )
            from_agent = from_result.scalar_one_or_none()
            
            to_result = await db_session.execute(
                select(AgentDefinition).where(AgentDefinition.id == to_agent_id)
            )
            to_agent = to_result.scalar_one_or_none()
            
            if not from_agent or not to_agent:
                return True  # Allow if agents not found (permissive)
            
            # Check tenant_id from user_id (tenant = user in simple case)
            from_tenant = from_agent.user_id
            to_tenant = to_agent.user_id
            
            # Same tenant = always allowed
            if from_tenant == to_tenant:
                return True
            
            # Cross-tenant: check safety_config for federation scope
            safety_config = from_agent.safety_config or {}
            federation_scope = safety_config.get("federation_scope", "intra_tenant")
            
            if federation_scope == "intra_tenant":
                return False  # Blocked - agent restricted to own tenant
            
            if federation_scope in ["inter_enterprise", "inter_ministry", "inter_nation"]:
                return True  # Allowed - agent has cross-tenant scope
            
            return False
            
        except Exception:
            return True  # Permissive on error

    def _check_ethical_pillars(self, action: Dict[str, Any]) -> Optional[str]:
        """
        DSID-P Ethical Governance (Section 36): Check action against 7 ethical pillars.
        
        Pillars:
        1. Human Oversight & Accountability
        2. Transparency & Explainability
        3. Privacy & Agency Protection
        4. Fairness & Non-Discrimination
        5. Safety & Robustness
        6. Governance & Redress Mechanisms
        7. Sovereign & Organizational Control
        
        Returns pillar name if violated, None if all pass.
        """
        action_type = action.get("action", "")
        tool_name = (action.get("tool_name") or "").lower()
        
        # 1. Human Oversight: Block autonomous governance changes
        blocked = ETHICAL_PILLARS["human_oversight"]["blocked_actions"]
        if action_type in blocked or any(b in tool_name for b in blocked):
            return "Human Oversight & Accountability"
        
        # 3. Privacy: Block data exfiltration patterns
        privacy_blocked = ETHICAL_PILLARS["privacy"]["blocked_patterns"]
        if any(p in tool_name for p in privacy_blocked):
            return "Privacy & Agency Protection"
        
        # 5. Safety: Block unsafe actions without proper risk level
        action_risk = self._get_action_risk_level(action)
        max_risk = ETHICAL_PILLARS["safety"]["max_risk_level"]
        if action_risk > max_risk:
            # SRR-5 actions require explicit approval
            if not action.get("has_approval"):
                return "Safety & Robustness (SRR-5 requires approval)"
        
        return None


class TriggerManager:
    """Manages workflow triggers for agents."""

    async def check_triggers(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        db_session: AsyncSession,
    ) -> List[WorkflowTrigger]:
        """Find triggers that match an event."""
        result = await db_session.execute(
            select(WorkflowTrigger)
            .where(WorkflowTrigger.is_active == True)
            .where(WorkflowTrigger.trigger_type == "event")
            .where(WorkflowTrigger.event_type == event_type)
        )
        triggers = result.scalars().all()

        matching = []
        for trigger in triggers:
            if self._matches_filter(trigger.event_filter, event_data):
                matching.append(trigger)

        return matching

    async def process_webhook(
        self,
        trigger_id: str,
        payload: Dict[str, Any],
        secret: str,
        db_session: AsyncSession,
    ) -> Optional[WorkflowTrigger]:
        """Process a webhook trigger."""
        result = await db_session.execute(
            select(WorkflowTrigger)
            .where(WorkflowTrigger.id == trigger_id)
            .where(WorkflowTrigger.trigger_type == "webhook")
            .where(WorkflowTrigger.is_active == True)
        )
        trigger = result.scalar_one_or_none()

        if not trigger:
            return None

        if trigger.webhook_secret and trigger.webhook_secret != secret:
            return None

        return trigger

    async def get_scheduled_triggers(
        self,
        db_session: AsyncSession,
    ) -> List[WorkflowTrigger]:
        """Get triggers that are due to run."""
        now = datetime.utcnow()
        result = await db_session.execute(
            select(WorkflowTrigger)
            .where(WorkflowTrigger.is_active == True)
            .where(WorkflowTrigger.trigger_type == "schedule")
            .where(WorkflowTrigger.next_run_at <= now)
        )
        return list(result.scalars().all())

    def _matches_filter(
        self,
        filter_config: Optional[Dict[str, Any]],
        event_data: Dict[str, Any],
    ) -> bool:
        """Check if event data matches filter."""
        if not filter_config:
            return True

        for key, expected in filter_config.items():
            actual = event_data.get(key)
            if actual != expected:
                return False

        return True


agent_executor = AgentExecutor()
trigger_manager = TriggerManager()
