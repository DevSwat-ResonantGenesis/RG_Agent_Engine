"""
Platform API Tools for Agentic Chat
====================================
Provides two tools that give the Resonant Assistant access to ALL ~383 user-facing
platform endpoints:

  1. platform_api_search  — search the endpoint catalog by keyword
  2. platform_api_call    — make authenticated HTTP calls to any platform endpoint

The catalog is a static list built from the actual production backend routers.
The call handler resolves the correct internal Docker service URL, forwards
user context headers, and returns the response.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ── Internal Docker service URLs ──
AGENT_ENGINE_URL = "http://localhost:8000"  # self
CHAT_SERVICE_URL = os.getenv("CHAT_SERVICE_URL", "http://chat_service:8000")
WORKFLOW_SERVICE_URL = os.getenv("WORKFLOW_SERVICE_URL", "http://workflow_service:8000")
MEMORY_SERVICE_URL = os.getenv("MEMORY_SERVICE_URL", "http://memory_service:8000")
BILLING_SERVICE_URL = os.getenv("BILLING_SERVICE_URL", "http://billing_service:8000")
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth_service:8000")
BLOCKCHAIN_SERVICE_URL = os.getenv("BLOCKCHAIN_SERVICE_URL", "http://blockchain_service:8000")
NOTIFICATION_SERVICE_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://notification_service:8000")
IDE_SERVICE_URL = os.getenv("IDE_SERVICE_URL", "http://ide_platform_service:8080")
CODE_EXEC_SERVICE_URL = os.getenv("CODE_EXECUTION_SERVICE_URL", "http://code_execution_service:8000")
STORAGE_SERVICE_URL = os.getenv("STORAGE_SERVICE_URL", "http://storage_service:8000")
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://ml_service:8000")
MARKETPLACE_SERVICE_URL = os.getenv("MARKETPLACE_SERVICE_URL", "http://marketplace_service:8000")
CRYPTO_SERVICE_URL = os.getenv("CRYPTO_SERVICE_URL", "http://crypto_service:8000")
RABBIT_SERVICE_URL = os.getenv("RABBIT_SERVICE_URL", "http://rabbit_api_service:8000")
USER_MEMORY_SERVICE_URL = os.getenv("USER_MEMORY_SERVICE_URL", "http://user_memory_service:8000")
USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://user_service:8000")

SERVICE_URLS = {
    "agent_engine": AGENT_ENGINE_URL,
    "chat": CHAT_SERVICE_URL,
    "workflow": WORKFLOW_SERVICE_URL,
    "memory": MEMORY_SERVICE_URL,
    "billing": BILLING_SERVICE_URL,
    "auth": AUTH_SERVICE_URL,
    "blockchain": BLOCKCHAIN_SERVICE_URL,
    "notification": NOTIFICATION_SERVICE_URL,
    "ide": IDE_SERVICE_URL,
    "code_execution": CODE_EXEC_SERVICE_URL,
    "storage": STORAGE_SERVICE_URL,
    "ml": ML_SERVICE_URL,
    "marketplace": MARKETPLACE_SERVICE_URL,
    "crypto": CRYPTO_SERVICE_URL,
    "rabbit": RABBIT_SERVICE_URL,
    "user_memory": USER_MEMORY_SERVICE_URL,
    "user": USER_SERVICE_URL,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  PLATFORM API CATALOG — every user-facing endpoint on the platform
# ═══════════════════════════════════════════════════════════════════════════════
# Each entry: { "method", "path", "service", "desc", "cat" }
#   method   — HTTP method
#   path     — internal service path (what the service sees)
#   service  — key in SERVICE_URLS
#   desc     — short description for search
#   cat      — category tag for grouping

PLATFORM_API_CATALOG: List[Dict[str, str]] = [
    # ── AGENT CRUD ──
    {"method": "POST", "path": "/agents/", "service": "agent_engine", "desc": "Create a new agent", "cat": "agent_crud"},
    {"method": "GET", "path": "/agents/", "service": "agent_engine", "desc": "List all agents (scoped to user org)", "cat": "agent_crud"},
    {"method": "GET", "path": "/agents/{agent_id}", "service": "agent_engine", "desc": "Get agent details", "cat": "agent_crud"},
    {"method": "PATCH", "path": "/agents/{agent_id}", "service": "agent_engine", "desc": "Update agent configuration", "cat": "agent_crud"},
    {"method": "DELETE", "path": "/agents/{agent_id}", "service": "agent_engine", "desc": "Delete agent", "cat": "agent_crud"},
    {"method": "PATCH", "path": "/agents/{agent_id}/unarchive", "service": "agent_engine", "desc": "Unarchive agent", "cat": "agent_crud"},

    # ── AGENT TEMPLATES ──
    {"method": "GET", "path": "/agents/templates", "service": "agent_engine", "desc": "List agent templates", "cat": "agent_templates"},
    {"method": "POST", "path": "/agents/templates/{template_id}/instantiate", "service": "agent_engine", "desc": "Create agent from template", "cat": "agent_templates"},

    # ── AGENT DISCOVERY & METADATA ──
    {"method": "GET", "path": "/agents/available-tools", "service": "agent_engine", "desc": "Get all tools an agent can use", "cat": "agent_discovery"},
    {"method": "GET", "path": "/agents/capabilities", "service": "agent_engine", "desc": "Get platform capabilities", "cat": "agent_discovery"},
    {"method": "GET", "path": "/agents/providers", "service": "agent_engine", "desc": "Get LLM provider catalog", "cat": "agent_discovery"},
    {"method": "GET", "path": "/agents/metrics", "service": "agent_engine", "desc": "Get global agent metrics", "cat": "agent_metrics"},
    {"method": "GET", "path": "/agents/metrics/summary", "service": "agent_engine", "desc": "Get agent metrics summary", "cat": "agent_metrics"},
    {"method": "GET", "path": "/agents/{agent_id}/metrics", "service": "agent_engine", "desc": "Get individual agent metrics", "cat": "agent_metrics"},
    {"method": "GET", "path": "/agents/{agent_id}/versions", "service": "agent_engine", "desc": "Get agent version history", "cat": "agent_crud"},

    # ── AGENT SESSIONS ──
    {"method": "POST", "path": "/agents/{agent_id}/sessions", "service": "agent_engine", "desc": "Start a new agent session (give it a goal)", "cat": "agent_sessions"},
    {"method": "GET", "path": "/agents/{agent_id}/sessions", "service": "agent_engine", "desc": "List sessions for an agent", "cat": "agent_sessions"},
    {"method": "GET", "path": "/agents/{agent_id}/sessions/{session_id}", "service": "agent_engine", "desc": "Get specific session", "cat": "agent_sessions"},
    {"method": "GET", "path": "/agents/sessions/{session_id}", "service": "agent_engine", "desc": "Get session by ID", "cat": "agent_sessions"},
    {"method": "GET", "path": "/agents/sessions/{session_id}/steps", "service": "agent_engine", "desc": "Get all steps in a session", "cat": "agent_sessions"},
    {"method": "GET", "path": "/agents/sessions/{session_id}/trace", "service": "agent_engine", "desc": "Get full execution trace", "cat": "agent_sessions"},
    {"method": "POST", "path": "/agents/sessions/{session_id}/cancel", "service": "agent_engine", "desc": "Cancel a running session", "cat": "agent_sessions"},
    {"method": "POST", "path": "/agents/sessions/{session_id}/feedback", "service": "agent_engine", "desc": "Submit feedback for a session", "cat": "agent_sessions"},
    {"method": "POST", "path": "/agents/sessions/{session_id}/approve/{step_id}", "service": "agent_engine", "desc": "Approve a pending step", "cat": "agent_sessions"},
    {"method": "GET", "path": "/agents/sessions/{session_id}/sse", "service": "agent_engine", "desc": "SSE stream for live session updates", "cat": "agent_sessions"},

    # ── APPROVALS & LIMITS ──
    {"method": "GET", "path": "/agents/approvals/pending", "service": "agent_engine", "desc": "List pending approval requests", "cat": "agent_approvals"},
    {"method": "POST", "path": "/agents/approvals/{approval_id}/approve", "service": "agent_engine", "desc": "Approve an action", "cat": "agent_approvals"},
    {"method": "POST", "path": "/agents/approvals/{approval_id}/reject", "service": "agent_engine", "desc": "Reject an action", "cat": "agent_approvals"},
    {"method": "GET", "path": "/agents/limits", "service": "agent_engine", "desc": "Get resource limits", "cat": "agent_approvals"},
    {"method": "PUT", "path": "/agents/limits/{limit_id}", "service": "agent_engine", "desc": "Update a resource limit", "cat": "agent_approvals"},

    # ── TRIGGERS & SCHEDULES ──
    {"method": "POST", "path": "/agents/{agent_id}/triggers", "service": "agent_engine", "desc": "Create a trigger for an agent", "cat": "agent_triggers"},
    {"method": "GET", "path": "/agents/{agent_id}/triggers", "service": "agent_engine", "desc": "List agent triggers", "cat": "agent_triggers"},
    {"method": "POST", "path": "/agents/triggers/webhook/{trigger_id}", "service": "agent_engine", "desc": "Fire a webhook trigger", "cat": "agent_triggers"},
    {"method": "POST", "path": "/agents/{agent_id}/schedules", "service": "agent_engine", "desc": "Create a scheduled task", "cat": "agent_schedules"},
    {"method": "GET", "path": "/agents/{agent_id}/schedules", "service": "agent_engine", "desc": "List scheduled tasks", "cat": "agent_schedules"},
    {"method": "PATCH", "path": "/agents/schedules/{schedule_id}", "service": "agent_engine", "desc": "Update a schedule", "cat": "agent_schedules"},
    {"method": "DELETE", "path": "/agents/schedules/{schedule_id}", "service": "agent_engine", "desc": "Delete a schedule", "cat": "agent_schedules"},
    {"method": "POST", "path": "/agents/anomaly-triggers", "service": "agent_engine", "desc": "Create anomaly trigger", "cat": "agent_triggers"},
    {"method": "GET", "path": "/agents/anomaly-triggers", "service": "agent_engine", "desc": "List anomaly triggers", "cat": "agent_triggers"},
    {"method": "DELETE", "path": "/agents/anomaly-triggers/{trigger_id}", "service": "agent_engine", "desc": "Delete anomaly trigger", "cat": "agent_triggers"},
    {"method": "POST", "path": "/agents/anomaly-triggers/fire", "service": "agent_engine", "desc": "Manually fire anomaly trigger", "cat": "agent_triggers"},

    # ── PUBLISHING & MARKETPLACE ──
    {"method": "POST", "path": "/agents/{agent_id}/publish", "service": "agent_engine", "desc": "Publish agent", "cat": "agent_publishing"},
    {"method": "POST", "path": "/agents/{agent_id}/marketplace-publish", "service": "agent_engine", "desc": "Publish agent to marketplace", "cat": "agent_publishing"},
    {"method": "POST", "path": "/agents/{agent_id}/marketplace-unpublish", "service": "agent_engine", "desc": "Remove agent from marketplace", "cat": "agent_publishing"},
    {"method": "POST", "path": "/agents/{agent_id}/publish-api", "service": "agent_engine", "desc": "Publish agent as a public API", "cat": "agent_publishing"},
    {"method": "GET", "path": "/agents/{agent_id}/published-apis", "service": "agent_engine", "desc": "List published APIs for agent", "cat": "agent_publishing"},
    {"method": "DELETE", "path": "/agents/published-apis/{pub_id}", "service": "agent_engine", "desc": "Delete published API", "cat": "agent_publishing"},
    {"method": "POST", "path": "/agents/{agent_id}/unpublish-api", "service": "agent_engine", "desc": "Unpublish agent API", "cat": "agent_publishing"},

    # ── REPO TO AGENT ──
    {"method": "POST", "path": "/agents/repo-to-agent", "service": "agent_engine", "desc": "Create agent from a GitHub repo", "cat": "agent_crud"},
    {"method": "POST", "path": "/agents/repo-to-agent/analyze", "service": "agent_engine", "desc": "Analyze a repo for agent creation", "cat": "agent_crud"},

    # ── GOVERNANCE & COMPLIANCE ──
    {"method": "POST", "path": "/agents/governance/evaluate", "service": "agent_engine", "desc": "Evaluate governance policy", "cat": "governance"},
    {"method": "GET", "path": "/agents/governance/audit-trail", "service": "agent_engine", "desc": "Get governance audit trail", "cat": "governance"},
    {"method": "GET", "path": "/agents/governance/compliance-report", "service": "agent_engine", "desc": "Get compliance report", "cat": "governance"},
    {"method": "GET", "path": "/agents/compliance/audit-export", "service": "agent_engine", "desc": "Export audit trail (JSON/CSV)", "cat": "governance"},
    {"method": "GET", "path": "/agents/compliance/score", "service": "agent_engine", "desc": "Get SOC2 compliance score", "cat": "governance"},
    {"method": "GET", "path": "/agents/compliance/evidence-checklist", "service": "agent_engine", "desc": "Get evidence checklist", "cat": "governance"},
    {"method": "GET", "path": "/agents/watchdog/status", "service": "agent_engine", "desc": "Get watchdog status", "cat": "governance"},

    # ── LEARNING & EVOLUTION ──
    {"method": "GET", "path": "/agents/learning/stats", "service": "agent_engine", "desc": "Get learning stats", "cat": "agent_learning"},
    {"method": "GET", "path": "/agents/learning/recommendations/{agent_id}", "service": "agent_engine", "desc": "Get learning recommendations", "cat": "agent_learning"},
    {"method": "GET", "path": "/agents/learning/patterns", "service": "agent_engine", "desc": "Get learned patterns", "cat": "agent_learning"},

    # ── AGENT TEAMS ──
    {"method": "POST", "path": "/agents/teams", "service": "agent_engine", "desc": "Create a new agent team", "cat": "teams"},
    {"method": "GET", "path": "/agents/teams", "service": "agent_engine", "desc": "List all teams", "cat": "teams"},
    {"method": "GET", "path": "/agents/teams/{team_id}", "service": "agent_engine", "desc": "Get team details", "cat": "teams"},
    {"method": "PUT", "path": "/agents/teams/{team_id}", "service": "agent_engine", "desc": "Update team configuration", "cat": "teams"},
    {"method": "PATCH", "path": "/agents/teams/{team_id}", "service": "agent_engine", "desc": "Partial update team", "cat": "teams"},
    {"method": "DELETE", "path": "/agents/teams/{team_id}", "service": "agent_engine", "desc": "Delete team", "cat": "teams"},
    {"method": "PATCH", "path": "/agents/teams/{team_id}/archive", "service": "agent_engine", "desc": "Archive team", "cat": "teams"},
    {"method": "PATCH", "path": "/agents/teams/{team_id}/unarchive", "service": "agent_engine", "desc": "Unarchive team", "cat": "teams"},
    {"method": "GET", "path": "/agents/teams/{team_id}/members", "service": "agent_engine", "desc": "Get team members", "cat": "teams"},
    {"method": "POST", "path": "/agents/teams/{team_id}/execute", "service": "agent_engine", "desc": "Execute a team workflow", "cat": "teams"},
    {"method": "GET", "path": "/agents/teams/{team_id}/workflows", "service": "agent_engine", "desc": "List team workflows", "cat": "teams"},
    {"method": "GET", "path": "/agents/teams/workflows/{workflow_id}", "service": "agent_engine", "desc": "Get workflow status", "cat": "teams"},
    {"method": "GET", "path": "/agents/teams/workflows/{workflow_id}/conversation", "service": "agent_engine", "desc": "Get workflow conversation", "cat": "teams"},
    {"method": "POST", "path": "/agents/teams/workflows/{workflow_id}/cancel", "service": "agent_engine", "desc": "Cancel team workflow", "cat": "teams"},
    {"method": "GET", "path": "/agents/teams/{team_id}/ownership", "service": "agent_engine", "desc": "Get team ownership info", "cat": "teams"},
    {"method": "POST", "path": "/agents/teams/{team_id}/transfer", "service": "agent_engine", "desc": "Transfer team ownership", "cat": "teams"},
    {"method": "POST", "path": "/agents/teams/{team_id}/rent", "service": "agent_engine", "desc": "Rent team", "cat": "teams"},
    {"method": "GET", "path": "/agents/teams/{team_id}/rentals", "service": "agent_engine", "desc": "List team rentals", "cat": "teams"},
    {"method": "GET", "path": "/agents/teams/my-rentals", "service": "agent_engine", "desc": "List my rented teams", "cat": "teams"},
    {"method": "POST", "path": "/agents/teams/{team_id}/mint-nft", "service": "agent_engine", "desc": "Mint team as NFT", "cat": "teams"},
    {"method": "GET", "path": "/agents/teams/marketplace", "service": "agent_engine", "desc": "Team marketplace listings", "cat": "teams"},

    # ── EXECUTION ENGINE ──
    {"method": "POST", "path": "/execution/agents/{agent_id}/execute", "service": "agent_engine", "desc": "Execute agent", "cat": "execution"},
    {"method": "GET", "path": "/execution/tools", "service": "agent_engine", "desc": "List execution tools", "cat": "execution"},
    {"method": "GET", "path": "/execution/agents/{agent_id}/executions", "service": "agent_engine", "desc": "Execution history", "cat": "execution"},
    {"method": "GET", "path": "/execution/executions/{execution_id}", "service": "agent_engine", "desc": "Get execution details", "cat": "execution"},
    {"method": "POST", "path": "/execution/agents/{agent_id}/reason", "service": "agent_engine", "desc": "Reasoning step", "cat": "execution"},
    {"method": "POST", "path": "/execution/agents/{agent_id}/reflect", "service": "agent_engine", "desc": "Reflection step", "cat": "execution"},
    {"method": "POST", "path": "/execution/agents/{agent_id}/justify", "service": "agent_engine", "desc": "Justification", "cat": "execution"},
    {"method": "POST", "path": "/execution/agents/{agent_id}/metacognition", "service": "agent_engine", "desc": "Metacognition", "cat": "execution"},
    {"method": "POST", "path": "/execution/agents/{agent_id}/delegate", "service": "agent_engine", "desc": "Delegate task to another agent", "cat": "execution"},
    {"method": "POST", "path": "/execution/delegations/{delegation_id}/accept", "service": "agent_engine", "desc": "Accept delegation", "cat": "execution"},
    {"method": "POST", "path": "/execution/delegations/{delegation_id}/reject", "service": "agent_engine", "desc": "Reject delegation", "cat": "execution"},
    {"method": "POST", "path": "/execution/delegations/{delegation_id}/complete", "service": "agent_engine", "desc": "Complete delegation", "cat": "execution"},
    {"method": "POST", "path": "/execution/consensus/propose", "service": "agent_engine", "desc": "Propose consensus vote", "cat": "execution"},
    {"method": "POST", "path": "/execution/consensus/{proposal_id}/vote", "service": "agent_engine", "desc": "Cast vote", "cat": "execution"},
    {"method": "GET", "path": "/execution/consensus/{proposal_id}", "service": "agent_engine", "desc": "Get proposal status", "cat": "execution"},
    {"method": "POST", "path": "/execution/agents/{agent_id}/share", "service": "agent_engine", "desc": "Share knowledge", "cat": "execution"},
    {"method": "GET", "path": "/execution/agents/{agent_id}/collective-knowledge", "service": "agent_engine", "desc": "Get shared knowledge", "cat": "execution"},
    {"method": "GET", "path": "/execution/collaboration/stats", "service": "agent_engine", "desc": "Collaboration stats", "cat": "execution"},

    # ── ADVANCED AGENT CAPABILITIES ──
    {"method": "POST", "path": "/advanced/goals/decompose", "service": "agent_engine", "desc": "Decompose goal into subtasks", "cat": "advanced"},
    {"method": "POST", "path": "/advanced/goals/plan-adaptive", "service": "agent_engine", "desc": "Adaptive planning", "cat": "advanced"},
    {"method": "POST", "path": "/advanced/agents/{agent_id}/memory/remember", "service": "agent_engine", "desc": "Store agent memory", "cat": "advanced"},
    {"method": "POST", "path": "/advanced/agents/{agent_id}/memory/recall", "service": "agent_engine", "desc": "Recall agent memories", "cat": "advanced"},
    {"method": "GET", "path": "/advanced/agents/{agent_id}/memory/summary", "service": "agent_engine", "desc": "Agent memory summary", "cat": "advanced"},
    {"method": "POST", "path": "/advanced/agents/{agent_id}/memory/pattern", "service": "agent_engine", "desc": "Detect patterns in memory", "cat": "advanced"},
    {"method": "POST", "path": "/advanced/agents/{agent_id}/memory/apply-patterns", "service": "agent_engine", "desc": "Apply detected patterns", "cat": "advanced"},
    {"method": "POST", "path": "/advanced/agents/{agent_id}/learning/experience", "service": "agent_engine", "desc": "Record learning experience", "cat": "advanced"},
    {"method": "GET", "path": "/advanced/agents/{agent_id}/learning/recommendations", "service": "agent_engine", "desc": "Learning recommendations", "cat": "advanced"},
    {"method": "GET", "path": "/advanced/agents/{agent_id}/learning/skill/{skill}", "service": "agent_engine", "desc": "Skill proficiency", "cat": "advanced"},
    {"method": "GET", "path": "/advanced/agents/{agent_id}/learning/summary", "service": "agent_engine", "desc": "Learning summary", "cat": "advanced"},
    {"method": "POST", "path": "/advanced/agents/{agent_id}/improvement/register", "service": "agent_engine", "desc": "Register improvement", "cat": "advanced"},
    {"method": "GET", "path": "/advanced/agents/{agent_id}/improvement/actions", "service": "agent_engine", "desc": "Improvement actions", "cat": "advanced"},
    {"method": "GET", "path": "/advanced/agents/{agent_id}/improvement/metrics", "service": "agent_engine", "desc": "Improvement metrics", "cat": "advanced"},
    {"method": "POST", "path": "/advanced/agents/{agent_id}/evolve", "service": "agent_engine", "desc": "Trigger agent evolution", "cat": "advanced"},
    {"method": "GET", "path": "/advanced/agents/{agent_id}/evolution", "service": "agent_engine", "desc": "Evolution history", "cat": "advanced"},

    # ── MAX AUTONOMY ──
    {"method": "POST", "path": "/max-autonomy/agents/{agent_id}/goals", "service": "agent_engine", "desc": "Set autonomous goal", "cat": "autonomy"},
    {"method": "GET", "path": "/max-autonomy/agents/{agent_id}/goals", "service": "agent_engine", "desc": "Get agent goals", "cat": "autonomy"},
    {"method": "GET", "path": "/max-autonomy/goals/{goal_id}", "service": "agent_engine", "desc": "Get specific goal", "cat": "autonomy"},
    {"method": "GET", "path": "/max-autonomy/goals/stats", "service": "agent_engine", "desc": "Goal statistics", "cat": "autonomy"},
    {"method": "POST", "path": "/max-autonomy/agents/{agent_id}/resilience/register", "service": "agent_engine", "desc": "Register resilience", "cat": "autonomy"},
    {"method": "GET", "path": "/max-autonomy/agents/{agent_id}/health", "service": "agent_engine", "desc": "Agent health check", "cat": "autonomy"},
    {"method": "POST", "path": "/max-autonomy/agents/{agent_id}/checkpoint", "service": "agent_engine", "desc": "Create checkpoint", "cat": "autonomy"},
    {"method": "GET", "path": "/max-autonomy/resilience/stats", "service": "agent_engine", "desc": "Resilience stats", "cat": "autonomy"},
    {"method": "POST", "path": "/max-autonomy/agents/{agent_id}/initiative", "service": "agent_engine", "desc": "Proactive initiative", "cat": "autonomy"},
    {"method": "GET", "path": "/max-autonomy/agents/{agent_id}/proactive-tasks", "service": "agent_engine", "desc": "Proactive tasks", "cat": "autonomy"},
    {"method": "GET", "path": "/max-autonomy/proactive/stats", "service": "agent_engine", "desc": "Proactive stats", "cat": "autonomy"},
    {"method": "POST", "path": "/max-autonomy/agents/{agent_id}/personality", "service": "agent_engine", "desc": "Set personality", "cat": "autonomy"},
    {"method": "GET", "path": "/max-autonomy/agents/{agent_id}/personality", "service": "agent_engine", "desc": "Get personality", "cat": "autonomy"},
    {"method": "GET", "path": "/max-autonomy/personality/archetypes", "service": "agent_engine", "desc": "List personality archetypes", "cat": "autonomy"},

    # ── FULL AUTONOMY ──
    {"method": "POST", "path": "/autonomy/start", "service": "agent_engine", "desc": "Start full autonomy system", "cat": "full_autonomy"},
    {"method": "POST", "path": "/autonomy/stop", "service": "agent_engine", "desc": "Stop full autonomy system", "cat": "full_autonomy"},
    {"method": "GET", "path": "/autonomy/status", "service": "agent_engine", "desc": "Full autonomy status", "cat": "full_autonomy"},
    {"method": "GET", "path": "/autonomy/stats", "service": "agent_engine", "desc": "Autonomy statistics", "cat": "full_autonomy"},
    {"method": "POST", "path": "/autonomy/quick-start", "service": "agent_engine", "desc": "Quick start with name+goal", "cat": "full_autonomy"},
    {"method": "POST", "path": "/autonomy/agents/create", "service": "agent_engine", "desc": "Create autonomous agent", "cat": "full_autonomy"},
    {"method": "GET", "path": "/autonomy/network/hierarchy", "service": "agent_engine", "desc": "Network hierarchy", "cat": "full_autonomy"},
    {"method": "GET", "path": "/autonomy/network/stats", "service": "agent_engine", "desc": "Network stats", "cat": "full_autonomy"},
    {"method": "POST", "path": "/autonomy/network/spawn", "service": "agent_engine", "desc": "Spawn network agent", "cat": "full_autonomy"},
    {"method": "GET", "path": "/autonomy/network/agents/{agent_id}", "service": "agent_engine", "desc": "Network agent details", "cat": "full_autonomy"},
    {"method": "GET", "path": "/autonomy/brains", "service": "agent_engine", "desc": "List agent brains", "cat": "full_autonomy"},
    {"method": "GET", "path": "/autonomy/brains/{brain_id}", "service": "agent_engine", "desc": "Brain status", "cat": "full_autonomy"},
    {"method": "POST", "path": "/autonomy/brains/{brain_id}/goal", "service": "agent_engine", "desc": "Set brain goal", "cat": "full_autonomy"},
    {"method": "GET", "path": "/autonomy/queue/stats", "service": "agent_engine", "desc": "Queue stats", "cat": "full_autonomy"},
    {"method": "GET", "path": "/autonomy/queue/tasks", "service": "agent_engine", "desc": "Queue tasks", "cat": "full_autonomy"},
    {"method": "GET", "path": "/autonomy/watchdog/status", "service": "agent_engine", "desc": "Watchdog status", "cat": "full_autonomy"},
    {"method": "GET", "path": "/autonomy/watchdog/alerts", "service": "agent_engine", "desc": "Watchdog alerts", "cat": "full_autonomy"},
    {"method": "POST", "path": "/autonomy/watchdog/alerts/{alert_id}/acknowledge", "service": "agent_engine", "desc": "Acknowledge watchdog alert", "cat": "full_autonomy"},
    {"method": "GET", "path": "/autonomy/startup/status", "service": "agent_engine", "desc": "Startup status", "cat": "full_autonomy"},

    # ── ULTIMATE AUTONOMY ──
    {"method": "POST", "path": "/ultimate/agents/{agent_id}/awaken", "service": "agent_engine", "desc": "Awaken agent consciousness", "cat": "ultimate_autonomy"},
    {"method": "POST", "path": "/ultimate/agents/{agent_id}/sleep", "service": "agent_engine", "desc": "Sleep agent", "cat": "ultimate_autonomy"},
    {"method": "GET", "path": "/ultimate/agents/{agent_id}/consciousness", "service": "agent_engine", "desc": "Consciousness state", "cat": "ultimate_autonomy"},
    {"method": "GET", "path": "/ultimate/consciousness/agents", "service": "agent_engine", "desc": "All conscious agents", "cat": "ultimate_autonomy"},
    {"method": "POST", "path": "/ultimate/collective/contribute", "service": "agent_engine", "desc": "Contribute to collective intelligence", "cat": "ultimate_autonomy"},
    {"method": "GET", "path": "/ultimate/collective/query", "service": "agent_engine", "desc": "Query collective intelligence", "cat": "ultimate_autonomy"},
    {"method": "POST", "path": "/ultimate/collective/solve", "service": "agent_engine", "desc": "Collective problem solving", "cat": "ultimate_autonomy"},
    {"method": "POST", "path": "/ultimate/swarm/create", "service": "agent_engine", "desc": "Create swarm", "cat": "ultimate_autonomy"},
    {"method": "POST", "path": "/ultimate/voting/propose", "service": "agent_engine", "desc": "Propose vote", "cat": "ultimate_autonomy"},
    {"method": "POST", "path": "/ultimate/voting/{proposal_id}/vote", "service": "agent_engine", "desc": "Cast vote", "cat": "ultimate_autonomy"},
    {"method": "GET", "path": "/ultimate/emergent/stats", "service": "agent_engine", "desc": "Emergent behavior stats", "cat": "ultimate_autonomy"},
    {"method": "POST", "path": "/ultimate/world/observe", "service": "agent_engine", "desc": "World observation", "cat": "ultimate_autonomy"},
    {"method": "POST", "path": "/ultimate/world/event", "service": "agent_engine", "desc": "Register world event", "cat": "ultimate_autonomy"},
    {"method": "POST", "path": "/ultimate/world/relate", "service": "agent_engine", "desc": "Create entity relationship", "cat": "ultimate_autonomy"},
    {"method": "GET", "path": "/ultimate/world/understand", "service": "agent_engine", "desc": "World understanding", "cat": "ultimate_autonomy"},
    {"method": "GET", "path": "/ultimate/world/entities", "service": "agent_engine", "desc": "List world entities", "cat": "ultimate_autonomy"},
    {"method": "GET", "path": "/ultimate/world/stats", "service": "agent_engine", "desc": "World model stats", "cat": "ultimate_autonomy"},
    {"method": "GET", "path": "/ultimate/status", "service": "agent_engine", "desc": "Ultimate autonomy status", "cat": "ultimate_autonomy"},

    # ── AUTONOMOUS RUNTIME ──
    {"method": "POST", "path": "/autonomous/daemon/start", "service": "agent_engine", "desc": "Start autonomous daemon", "cat": "autonomous"},
    {"method": "GET", "path": "/autonomous/daemon/status", "service": "agent_engine", "desc": "Daemon status", "cat": "autonomous"},
    {"method": "POST", "path": "/autonomous/agents/register", "service": "agent_engine", "desc": "Register agent", "cat": "autonomous"},
    {"method": "GET", "path": "/autonomous/agents/{agent_id}/status", "service": "agent_engine", "desc": "Agent status", "cat": "autonomous"},
    {"method": "POST", "path": "/autonomous/agents/{agent_id}/goal", "service": "agent_engine", "desc": "Set agent goal", "cat": "autonomous"},
    {"method": "POST", "path": "/autonomous/events/inject", "service": "agent_engine", "desc": "Inject event", "cat": "autonomous"},
    {"method": "POST", "path": "/autonomous/agents/{agent_id}/message", "service": "agent_engine", "desc": "Send message to agent", "cat": "autonomous"},
    {"method": "POST", "path": "/autonomous/agents/{agent_id}/broadcast", "service": "agent_engine", "desc": "Broadcast to agents", "cat": "autonomous"},
    {"method": "POST", "path": "/autonomous/agents/{agent_id}/service", "service": "agent_engine", "desc": "Register service", "cat": "autonomous"},
    {"method": "GET", "path": "/autonomous/capabilities", "service": "agent_engine", "desc": "List capabilities", "cat": "autonomous"},
    {"method": "GET", "path": "/autonomous/capabilities/{capability}/agents", "service": "agent_engine", "desc": "Find agents by capability", "cat": "autonomous"},
    {"method": "POST", "path": "/autonomous/teams/{team_name}", "service": "agent_engine", "desc": "Create autonomous team", "cat": "autonomous"},
    {"method": "GET", "path": "/autonomous/runtime/stats", "service": "agent_engine", "desc": "Runtime stats", "cat": "autonomous"},

    # ── ORCHESTRATION ──
    {"method": "POST", "path": "/orchestration/agents/register", "service": "agent_engine", "desc": "Register for orchestration", "cat": "orchestration"},
    {"method": "POST", "path": "/orchestration/goals/submit", "service": "agent_engine", "desc": "Submit orchestration goal", "cat": "orchestration"},
    {"method": "GET", "path": "/orchestration/goals/{goal_id}", "service": "agent_engine", "desc": "Goal status", "cat": "orchestration"},
    {"method": "POST", "path": "/orchestration/tasks/report", "service": "agent_engine", "desc": "Report task completion", "cat": "orchestration"},
    {"method": "GET", "path": "/orchestration/stats", "service": "agent_engine", "desc": "Orchestration stats", "cat": "orchestration"},
    {"method": "POST", "path": "/orchestration/swarms/create", "service": "agent_engine", "desc": "Create orchestration swarm", "cat": "orchestration"},
    {"method": "GET", "path": "/orchestration/swarms/{swarm_id}", "service": "agent_engine", "desc": "Get swarm", "cat": "orchestration"},
    {"method": "POST", "path": "/orchestration/swarms/{swarm_id}/scale", "service": "agent_engine", "desc": "Scale swarm", "cat": "orchestration"},
    {"method": "GET", "path": "/orchestration/swarms", "service": "agent_engine", "desc": "List swarms", "cat": "orchestration"},
    {"method": "GET", "path": "/orchestration/agents/{agent_id}/reputation", "service": "agent_engine", "desc": "Agent reputation", "cat": "orchestration"},
    {"method": "GET", "path": "/orchestration/agents/{agent_id}/history", "service": "agent_engine", "desc": "Agent history", "cat": "orchestration"},
    {"method": "GET", "path": "/orchestration/verify/{tx_hash}", "service": "agent_engine", "desc": "Verify transaction", "cat": "orchestration"},

    # ── WEBHOOKS ──
    {"method": "POST", "path": "/webhooks/agent/{agent_id}/create", "service": "agent_engine", "desc": "Create webhook", "cat": "webhooks"},
    {"method": "GET", "path": "/webhooks/agent/{agent_id}/list", "service": "agent_engine", "desc": "List agent webhooks", "cat": "webhooks"},
    {"method": "GET", "path": "/webhooks/user/list", "service": "agent_engine", "desc": "List all my webhooks", "cat": "webhooks"},
    {"method": "DELETE", "path": "/webhooks/trigger/{trigger_id}", "service": "agent_engine", "desc": "Delete trigger", "cat": "webhooks"},
    {"method": "PATCH", "path": "/webhooks/trigger/{trigger_id}/toggle", "service": "agent_engine", "desc": "Toggle webhook on/off", "cat": "webhooks"},

    # ── DISCORD ──
    {"method": "POST", "path": "/discord/connections", "service": "agent_engine", "desc": "Create Discord connection", "cat": "discord"},
    {"method": "GET", "path": "/discord/connections", "service": "agent_engine", "desc": "List Discord connections", "cat": "discord"},
    {"method": "GET", "path": "/discord/connections/{connection_id}", "service": "agent_engine", "desc": "Get Discord connection", "cat": "discord"},
    {"method": "PATCH", "path": "/discord/connections/{connection_id}", "service": "agent_engine", "desc": "Update Discord connection", "cat": "discord"},
    {"method": "DELETE", "path": "/discord/connections/{connection_id}", "service": "agent_engine", "desc": "Delete Discord connection", "cat": "discord"},
    {"method": "GET", "path": "/discord/invite-url", "service": "agent_engine", "desc": "Get Discord bot invite URL", "cat": "discord"},

    # ── SSH TERMINAL ──
    {"method": "POST", "path": "/agents/{agent_id}/terminal/execute", "service": "agent_engine", "desc": "Execute SSH command on agent", "cat": "terminal"},
    {"method": "GET", "path": "/agents/{agent_id}/terminal/audit", "service": "agent_engine", "desc": "Terminal audit log", "cat": "terminal"},
    {"method": "GET", "path": "/agents/terminal/audit", "service": "agent_engine", "desc": "All terminal audits", "cat": "terminal"},
    {"method": "GET", "path": "/agents/{agent_id}/terminal/status", "service": "agent_engine", "desc": "Terminal status", "cat": "terminal"},

    # ── CHAT BRIDGE ──
    {"method": "POST", "path": "/agents/chat/send", "service": "agent_engine", "desc": "Send message via chat bridge", "cat": "chat_bridge"},
    {"method": "GET", "path": "/agents/chat/read", "service": "agent_engine", "desc": "Read chat bridge messages", "cat": "chat_bridge"},
    {"method": "GET", "path": "/agents/chat/history", "service": "agent_engine", "desc": "Chat bridge history", "cat": "chat_bridge"},
    {"method": "POST", "path": "/agents/chat/{agent_id}/send", "service": "agent_engine", "desc": "Send to specific agent chat", "cat": "chat_bridge"},
    {"method": "GET", "path": "/agents/chat/status", "service": "agent_engine", "desc": "Chat bridge status", "cat": "chat_bridge"},

    # ── WORKFLOWS ──
    {"method": "POST", "path": "/workflow/workflows", "service": "workflow", "desc": "Create workflow", "cat": "workflows"},
    {"method": "PUT", "path": "/workflow/workflows/{workflow_id}", "service": "workflow", "desc": "Update workflow", "cat": "workflows"},
    {"method": "GET", "path": "/workflow/workflows", "service": "workflow", "desc": "List workflows", "cat": "workflows"},
    {"method": "GET", "path": "/workflow/workflows/{workflow_id}", "service": "workflow", "desc": "Get workflow details", "cat": "workflows"},
    {"method": "DELETE", "path": "/workflow/workflows/{workflow_id}", "service": "workflow", "desc": "Delete workflow", "cat": "workflows"},
    {"method": "POST", "path": "/workflow/workflows/{workflow_id}/run", "service": "workflow", "desc": "Run workflow", "cat": "workflows"},
    {"method": "GET", "path": "/workflow/runs", "service": "workflow", "desc": "List workflow runs", "cat": "workflows"},
    {"method": "GET", "path": "/workflow/runs/{run_id}", "service": "workflow", "desc": "Get run details", "cat": "workflows"},
    {"method": "GET", "path": "/workflow/runs/{run_id}/steps", "service": "workflow", "desc": "Get run steps", "cat": "workflows"},
    {"method": "POST", "path": "/workflow/runs/{run_id}/cancel", "service": "workflow", "desc": "Cancel workflow run", "cat": "workflows"},
    {"method": "POST", "path": "/workflow/events", "service": "workflow", "desc": "Publish workflow event", "cat": "workflows"},
    {"method": "GET", "path": "/workflow/events", "service": "workflow", "desc": "List workflow events", "cat": "workflows"},

    # ── RESONANT CHAT ──
    {"method": "POST", "path": "/resonant-chat/message", "service": "chat", "desc": "Send resonant chat message", "cat": "chat"},
    {"method": "POST", "path": "/resonant-chat/message/stream", "service": "chat", "desc": "Stream resonant chat message (SSE)", "cat": "chat"},
    {"method": "POST", "path": "/resonant-chat/create", "service": "chat", "desc": "Create resonant chat", "cat": "chat"},
    {"method": "POST", "path": "/resonant-chat/conversations", "service": "chat", "desc": "Create conversation", "cat": "chat"},
    {"method": "GET", "path": "/resonant-chat/conversations", "service": "chat", "desc": "List conversations", "cat": "chat"},
    {"method": "GET", "path": "/resonant-chat/conversations/{conversation_id}", "service": "chat", "desc": "Get conversation", "cat": "chat"},
    {"method": "PUT", "path": "/resonant-chat/conversations/{conversation_id}/archive", "service": "chat", "desc": "Archive conversation", "cat": "chat"},
    {"method": "DELETE", "path": "/resonant-chat/conversations/{conversation_id}", "service": "chat", "desc": "Delete conversation", "cat": "chat"},
    {"method": "DELETE", "path": "/resonant-chat/conversations/{conversation_id}/messages/{message_id}", "service": "chat", "desc": "Delete message", "cat": "chat"},
    {"method": "POST", "path": "/resonant-chat/conversations/{conversation_id}/messages", "service": "chat", "desc": "Add message", "cat": "chat"},
    {"method": "POST", "path": "/resonant-chat/save-agentic", "service": "chat", "desc": "Cross-save agentic messages", "cat": "chat"},
    {"method": "GET", "path": "/resonant-chat/history", "service": "chat", "desc": "All chat history", "cat": "chat"},
    {"method": "GET", "path": "/resonant-chat/history/{chat_id}", "service": "chat", "desc": "Chat history by ID", "cat": "chat"},
    {"method": "GET", "path": "/resonant-chat/providers", "service": "chat", "desc": "Available LLM providers", "cat": "chat"},
    {"method": "GET", "path": "/resonant-chat/evidence-graph/{message_id}", "service": "chat", "desc": "Evidence graph for message", "cat": "chat_analytics"},
    {"method": "GET", "path": "/resonant-chat/metrics/{chat_id}", "service": "chat", "desc": "Chat metrics", "cat": "chat_analytics"},
    {"method": "GET", "path": "/resonant-chat/message-metrics/{message_id}", "service": "chat", "desc": "Message metrics", "cat": "chat_analytics"},
    {"method": "GET", "path": "/resonant-chat/anchors", "service": "chat", "desc": "List chat anchors", "cat": "chat_analytics"},
    {"method": "GET", "path": "/resonant-chat/clusters", "service": "chat", "desc": "Conversation clusters", "cat": "chat_analytics"},
    {"method": "GET", "path": "/resonant-chat/hallucination-settings", "service": "chat", "desc": "Get hallucination settings", "cat": "chat_settings"},
    {"method": "PATCH", "path": "/resonant-chat/hallucination-settings", "service": "chat", "desc": "Update hallucination settings", "cat": "chat_settings"},
    {"method": "POST", "path": "/resonant-chat/knowledge-base", "service": "chat", "desc": "Add knowledge base entry", "cat": "knowledge_base"},
    {"method": "POST", "path": "/resonant-chat/knowledge-base/upload", "service": "chat", "desc": "Upload to knowledge base", "cat": "knowledge_base"},
    {"method": "GET", "path": "/resonant-chat/knowledge-base", "service": "chat", "desc": "List knowledge base entries", "cat": "knowledge_base"},
    {"method": "DELETE", "path": "/resonant-chat/knowledge-base/{entry_id}", "service": "chat", "desc": "Delete knowledge base entry", "cat": "knowledge_base"},
    {"method": "GET", "path": "/resonant-chat/chains", "service": "chat", "desc": "List prompt chains", "cat": "chat_advanced"},
    {"method": "POST", "path": "/resonant-chat/chains", "service": "chat", "desc": "Create prompt chain", "cat": "chat_advanced"},
    {"method": "POST", "path": "/resonant-chat/chains/execute", "service": "chat", "desc": "Execute prompt chain", "cat": "chat_advanced"},
    {"method": "POST", "path": "/resonant-chat/sandbox/execute", "service": "chat", "desc": "Sandbox code execution", "cat": "chat_advanced"},
    {"method": "POST", "path": "/resonant-chat/analyze/confidence", "service": "chat", "desc": "Confidence analysis", "cat": "chat_advanced"},
    {"method": "POST", "path": "/resonant-chat/analyze/hallucinations", "service": "chat", "desc": "Hallucination detection", "cat": "chat_advanced"},
    {"method": "POST", "path": "/resonant-chat/analyze/citations", "service": "chat", "desc": "Citation analysis", "cat": "chat_advanced"},
    {"method": "POST", "path": "/resonant-chat/validate", "service": "chat", "desc": "Validate AI response", "cat": "chat_advanced"},
    {"method": "POST", "path": "/resonant-chat/voting", "service": "chat", "desc": "Multi-model voting", "cat": "chat_advanced"},
    {"method": "POST", "path": "/resonant-chat/sentiment/analyze", "service": "chat", "desc": "Sentiment analysis", "cat": "chat_advanced"},
    {"method": "POST", "path": "/resonant-chat/code/execute", "service": "chat", "desc": "Code execution in chat", "cat": "chat_advanced"},
    {"method": "POST", "path": "/resonant-chat/chunking/info", "service": "chat", "desc": "Chunking info", "cat": "chat_advanced"},
    {"method": "POST", "path": "/resonant-chat/chunking/process", "service": "chat", "desc": "Process with chunking", "cat": "chat_advanced"},
    {"method": "POST", "path": "/resonant-chat/context/project", "service": "chat", "desc": "Set project context", "cat": "chat_advanced"},
    {"method": "POST", "path": "/resonant-chat/feedback", "service": "chat", "desc": "Submit chat feedback", "cat": "chat_feedback"},
    {"method": "GET", "path": "/resonant-chat/autonomous/stats", "service": "chat", "desc": "Autonomous chat stats", "cat": "chat_autonomous"},
    {"method": "GET", "path": "/resonant-chat/autonomous/routing/stats", "service": "chat", "desc": "Routing stats", "cat": "chat_autonomous"},
    {"method": "POST", "path": "/resonant-chat/autonomous/routing/test", "service": "chat", "desc": "Test routing", "cat": "chat_autonomous"},
    {"method": "GET", "path": "/resonant-chat/autonomous/cache/stats", "service": "chat", "desc": "Cache stats", "cat": "chat_autonomous"},
    {"method": "POST", "path": "/resonant-chat/autonomous/cache/clear", "service": "chat", "desc": "Clear cache", "cat": "chat_autonomous"},
    {"method": "GET", "path": "/resonant-chat/autonomous/learning/stats", "service": "chat", "desc": "Learning stats", "cat": "chat_autonomous"},
    {"method": "GET", "path": "/resonant-chat/autonomous/learning/agent/{agent_id}", "service": "chat", "desc": "Agent learning details", "cat": "chat_autonomous"},
    {"method": "GET", "path": "/resonant-chat/autonomous/learning/agent/{agent_id}/suggestions", "service": "chat", "desc": "Agent suggestions", "cat": "chat_autonomous"},
    {"method": "POST", "path": "/resonant-chat/autonomous/feedback", "service": "chat", "desc": "Autonomous feedback", "cat": "chat_autonomous"},
    {"method": "GET", "path": "/resonant-chat/autonomous/planning/stats", "service": "chat", "desc": "Planning stats", "cat": "chat_autonomous"},
    {"method": "POST", "path": "/resonant-chat/autonomous/planning/create", "service": "chat", "desc": "Create plan", "cat": "chat_autonomous"},
    {"method": "GET", "path": "/resonant-chat/autonomous/planning/{plan_id}", "service": "chat", "desc": "Get plan", "cat": "chat_autonomous"},
    {"method": "GET", "path": "/resonant-chat/dsid/stats", "service": "chat", "desc": "DSID stats", "cat": "dsid"},
    {"method": "GET", "path": "/resonant-chat/dsid/message/{message_id}", "service": "chat", "desc": "Message DSID", "cat": "dsid"},
    {"method": "GET", "path": "/resonant-chat/dsid/lineage/{dsid}", "service": "chat", "desc": "DSID lineage", "cat": "dsid"},
    {"method": "GET", "path": "/resonant-chat/dsid/conversation/{conversation_id}", "service": "chat", "desc": "Conversation DSIDs", "cat": "dsid"},
    {"method": "POST", "path": "/resonant-chat/dsid/verify/{dsid}", "service": "chat", "desc": "Verify DSID", "cat": "dsid"},
    {"method": "GET", "path": "/resonant-chat/dsid/proof/{dsid}", "service": "chat", "desc": "Get DSID proof", "cat": "dsid"},
    {"method": "POST", "path": "/resonant-chat/dsid/verify-proof", "service": "chat", "desc": "Verify DSID proof", "cat": "dsid"},
    {"method": "POST", "path": "/resonant-chat/extract-memories", "service": "chat", "desc": "Extract memories from chat", "cat": "chat_memory"},
    {"method": "POST", "path": "/resonant-chat/memories/save", "service": "chat", "desc": "Save memory", "cat": "chat_memory"},
    {"method": "GET", "path": "/resonant-chat/memories/list", "service": "chat", "desc": "List memories", "cat": "chat_memory"},
    {"method": "DELETE", "path": "/resonant-chat/memories/{memory_id}", "service": "chat", "desc": "Delete memory", "cat": "chat_memory"},
    {"method": "POST", "path": "/resonant-chat/conversations/categorize", "service": "chat", "desc": "Auto-categorize conversations", "cat": "chat_memory"},

    # ── SKILLS ──
    {"method": "GET", "path": "/skills/list", "service": "chat", "desc": "List available chat skills", "cat": "skills"},
    {"method": "POST", "path": "/skills/toggle", "service": "chat", "desc": "Toggle skill on/off", "cat": "skills"},
    {"method": "POST", "path": "/skills/execute", "service": "chat", "desc": "Execute a chat skill", "cat": "skills"},
    {"method": "POST", "path": "/skills/create", "service": "chat", "desc": "Create custom skill", "cat": "skills"},
    {"method": "DELETE", "path": "/skills/delete/{skill_id}", "service": "chat", "desc": "Delete custom skill", "cat": "skills"},
    {"method": "GET", "path": "/skills/enabled", "service": "chat", "desc": "Get enabled skills", "cat": "skills"},

    # ── SEARCH & FEEDBACK ──
    {"method": "GET", "path": "/search", "service": "chat", "desc": "Search conversations", "cat": "search"},
    {"method": "GET", "path": "/search/semantic", "service": "chat", "desc": "Semantic search", "cat": "search"},
    {"method": "GET", "path": "/search/suggestions", "service": "chat", "desc": "Search suggestions", "cat": "search"},
    {"method": "POST", "path": "/feedback/message/{message_id}/feedback", "service": "chat", "desc": "Submit message feedback", "cat": "feedback"},
    {"method": "GET", "path": "/feedback/message/{message_id}/feedback", "service": "chat", "desc": "Get message feedback", "cat": "feedback"},
    {"method": "POST", "path": "/feedback/message/{message_id}/thumbs-up", "service": "chat", "desc": "Thumbs up", "cat": "feedback"},
    {"method": "POST", "path": "/feedback/message/{message_id}/thumbs-down", "service": "chat", "desc": "Thumbs down", "cat": "feedback"},

    # ── ANALYTICS ──
    {"method": "GET", "path": "/analytics", "service": "chat", "desc": "Analytics dashboard", "cat": "analytics"},
    {"method": "GET", "path": "/analytics/usage", "service": "chat", "desc": "Usage analytics", "cat": "analytics"},
    {"method": "GET", "path": "/analytics/quality", "service": "chat", "desc": "Quality analytics", "cat": "analytics"},
    {"method": "GET", "path": "/analytics/topics", "service": "chat", "desc": "Topic analytics", "cat": "analytics"},
    {"method": "GET", "path": "/analytics/memory", "service": "chat", "desc": "Memory analytics", "cat": "analytics"},

    # ── BILLING ──
    {"method": "POST", "path": "/billing/checkout/subscription", "service": "billing", "desc": "Subscribe to a plan", "cat": "billing"},
    {"method": "POST", "path": "/billing/checkout/credits", "service": "billing", "desc": "Buy credits", "cat": "billing"},
    {"method": "GET", "path": "/billing/subscription", "service": "billing", "desc": "Get subscription details", "cat": "billing"},
    {"method": "POST", "path": "/billing/subscription/cancel", "service": "billing", "desc": "Cancel subscription", "cat": "billing"},
    {"method": "POST", "path": "/billing/subscription/reactivate", "service": "billing", "desc": "Reactivate subscription", "cat": "billing"},
    {"method": "POST", "path": "/billing/subscription/change-plan", "service": "billing", "desc": "Change plan", "cat": "billing"},
    {"method": "GET", "path": "/billing/credits", "service": "billing", "desc": "Get credit balance", "cat": "billing"},
    {"method": "POST", "path": "/billing/credits/purchase", "service": "billing", "desc": "Purchase credits", "cat": "billing"},
    {"method": "GET", "path": "/billing/credits/transactions", "service": "billing", "desc": "Credit transactions", "cat": "billing"},
    {"method": "GET", "path": "/billing/invoices", "service": "billing", "desc": "List invoices", "cat": "billing"},
    {"method": "GET", "path": "/billing/invoices/{invoice_id}", "service": "billing", "desc": "Get invoice", "cat": "billing"},
    {"method": "GET", "path": "/billing/invoices/{invoice_id}/pdf", "service": "billing", "desc": "Download invoice PDF", "cat": "billing"},
    {"method": "GET", "path": "/billing/payment-methods", "service": "billing", "desc": "List payment methods", "cat": "billing"},
    {"method": "DELETE", "path": "/billing/payment-methods/{pm_id}", "service": "billing", "desc": "Delete payment method", "cat": "billing"},
    {"method": "POST", "path": "/billing/payment-methods/{pm_id}/default", "service": "billing", "desc": "Set default payment method", "cat": "billing"},
    {"method": "POST", "path": "/billing/portal", "service": "billing", "desc": "Create Stripe portal session", "cat": "billing"},
    {"method": "GET", "path": "/billing/pricing", "service": "billing", "desc": "Get pricing page data", "cat": "billing"},
    {"method": "GET", "path": "/billing/usage", "service": "billing", "desc": "Get usage data", "cat": "billing"},

    # ── NOTIFICATIONS ──
    {"method": "GET", "path": "/notifications", "service": "notification", "desc": "List notifications", "cat": "notifications"},
    {"method": "GET", "path": "/notifications/{notification_id}", "service": "notification", "desc": "Get notification", "cat": "notifications"},
    {"method": "POST", "path": "/notifications", "service": "notification", "desc": "Create notification", "cat": "notifications"},
    {"method": "PUT", "path": "/notifications/{notification_id}/read", "service": "notification", "desc": "Mark as read", "cat": "notifications"},
    {"method": "DELETE", "path": "/notifications/{notification_id}", "service": "notification", "desc": "Delete notification", "cat": "notifications"},
    {"method": "GET", "path": "/notifications/unread/count", "service": "notification", "desc": "Unread count", "cat": "notifications"},

    # ── MEMORY (Hash Sphere) ──
    {"method": "POST", "path": "/memory/hash-sphere/anchors", "service": "memory", "desc": "Create memory anchor", "cat": "memory"},
    {"method": "GET", "path": "/memory/hash-sphere/anchors", "service": "memory", "desc": "List memory anchors", "cat": "memory"},
    {"method": "POST", "path": "/memory/hash-sphere/search", "service": "memory", "desc": "Search Hash Sphere", "cat": "memory"},
    {"method": "POST", "path": "/memory/hash-sphere/hash", "service": "memory", "desc": "Generate Hash Sphere hash", "cat": "memory"},
    {"method": "POST", "path": "/memory/hash-sphere/resonance", "service": "memory", "desc": "Check resonance", "cat": "memory"},
    {"method": "POST", "path": "/memory/retrieve", "service": "memory", "desc": "Retrieve memories", "cat": "memory"},
    {"method": "POST", "path": "/memory/embed", "service": "memory", "desc": "Embed memory", "cat": "memory"},
    {"method": "GET", "path": "/memory/stats", "service": "memory", "desc": "Memory stats", "cat": "memory"},
    {"method": "GET", "path": "/memory/visualizer/universe", "service": "memory", "desc": "Memory universe visualization", "cat": "memory"},

    # ── BLOCKCHAIN ──
    {"method": "GET", "path": "/blockchain/status", "service": "blockchain", "desc": "Blockchain status", "cat": "blockchain"},
    {"method": "GET", "path": "/blockchain/dsid/{dsid_id}", "service": "blockchain", "desc": "Get DSID", "cat": "blockchain"},
    {"method": "POST", "path": "/blockchain/dsid/create", "service": "blockchain", "desc": "Create DSID", "cat": "blockchain"},
    {"method": "POST", "path": "/blockchain/dsid/verify", "service": "blockchain", "desc": "Verify DSID", "cat": "blockchain"},
    {"method": "GET", "path": "/blockchain/blocks", "service": "blockchain", "desc": "List blocks", "cat": "blockchain"},
    {"method": "GET", "path": "/blockchain/blocks/{block_id}", "service": "blockchain", "desc": "Get block", "cat": "blockchain"},
    {"method": "GET", "path": "/blockchain/transactions/{tx_id}", "service": "blockchain", "desc": "Get transaction", "cat": "blockchain"},
    {"method": "GET", "path": "/blockchain/audit", "service": "blockchain", "desc": "Audit chain", "cat": "blockchain"},

    # ── STORAGE ──
    {"method": "POST", "path": "/storage/upload", "service": "storage", "desc": "Upload file to storage", "cat": "storage"},
    {"method": "GET", "path": "/storage/download/{file_id}", "service": "storage", "desc": "Download file from storage", "cat": "storage"},
    {"method": "GET", "path": "/storage/files", "service": "storage", "desc": "List files in storage", "cat": "storage"},
    {"method": "DELETE", "path": "/storage/files/{file_id}", "service": "storage", "desc": "Delete file from storage", "cat": "storage"},

    # ── ML ──
    {"method": "GET", "path": "/ml/models", "service": "ml", "desc": "List ML models", "cat": "ml"},
    {"method": "POST", "path": "/ml/train", "service": "ml", "desc": "Start training job", "cat": "ml"},
    {"method": "GET", "path": "/ml/jobs", "service": "ml", "desc": "List training jobs", "cat": "ml"},
    {"method": "GET", "path": "/ml/jobs/{job_id}", "service": "ml", "desc": "Get training job", "cat": "ml"},
    {"method": "POST", "path": "/ml/jobs/{job_id}/stop", "service": "ml", "desc": "Stop training job", "cat": "ml"},

    # ── MARKETPLACE ──
    {"method": "GET", "path": "/marketplace/listings", "service": "marketplace", "desc": "List marketplace items", "cat": "marketplace"},
    {"method": "GET", "path": "/marketplace/listings/{listing_id}", "service": "marketplace", "desc": "Get listing details", "cat": "marketplace"},
    {"method": "GET", "path": "/marketplace/categories", "service": "marketplace", "desc": "List categories", "cat": "marketplace"},
    {"method": "GET", "path": "/marketplace/featured", "service": "marketplace", "desc": "Featured listings", "cat": "marketplace"},
    {"method": "GET", "path": "/marketplace/stats", "service": "marketplace", "desc": "Marketplace stats", "cat": "marketplace"},

    # ── RABBIT (Reddit-like Social) — Communities ──
    {"method": "POST", "path": "/rabbit/communities", "service": "rabbit", "desc": "Create a community (name, slug, description)", "cat": "rabbit_communities"},
    {"method": "GET", "path": "/rabbit/communities", "service": "rabbit", "desc": "List all communities", "cat": "rabbit_communities"},
    {"method": "GET", "path": "/rabbit/communities/{slug}", "service": "rabbit", "desc": "Get community by slug", "cat": "rabbit_communities"},

    # ── RABBIT — Posts ──
    {"method": "POST", "path": "/rabbit/posts", "service": "rabbit", "desc": "Create a post (title, body, image_url, community_slug)", "cat": "rabbit_posts"},
    {"method": "GET", "path": "/rabbit/posts", "service": "rabbit", "desc": "List all posts (global feed)", "cat": "rabbit_posts"},
    {"method": "GET", "path": "/rabbit/posts/search", "service": "rabbit", "desc": "Search posts by title or body", "cat": "rabbit_posts"},
    {"method": "GET", "path": "/rabbit/posts/{post_id}", "service": "rabbit", "desc": "Get post by ID", "cat": "rabbit_posts"},
    {"method": "GET", "path": "/rabbit/communities/{slug}/posts", "service": "rabbit", "desc": "List posts in a community", "cat": "rabbit_posts"},
    {"method": "DELETE", "path": "/rabbit/posts/{post_id}", "service": "rabbit", "desc": "Delete a post (soft delete, owner only)", "cat": "rabbit_posts"},
    {"method": "GET", "path": "/rabbit/posts/{post_id}/og", "service": "rabbit", "desc": "Get Open Graph HTML for post sharing", "cat": "rabbit_posts"},

    # ── RABBIT — Comments ──
    {"method": "POST", "path": "/rabbit/posts/{post_id}/comments", "service": "rabbit", "desc": "Create a comment on a post (supports nested replies via parent_comment_id)", "cat": "rabbit_comments"},
    {"method": "GET", "path": "/rabbit/posts/{post_id}/comments", "service": "rabbit", "desc": "List comments on a post", "cat": "rabbit_comments"},
    {"method": "GET", "path": "/rabbit/comments", "service": "rabbit", "desc": "List all comments (optionally by author)", "cat": "rabbit_comments"},
    {"method": "DELETE", "path": "/rabbit/comments/{comment_id}", "service": "rabbit", "desc": "Delete a comment (soft delete, owner only)", "cat": "rabbit_comments"},

    # ── RABBIT — Votes (upvote/downvote) ──
    {"method": "PUT", "path": "/rabbit/votes", "service": "rabbit", "desc": "Upvote/downvote a post or comment (value: -1, 0, or 1)", "cat": "rabbit_votes"},

    # ── RABBIT — Images ──
    {"method": "POST", "path": "/rabbit/images/upload", "service": "rabbit", "desc": "Upload an image (max 10MB, returns URL for use in posts)", "cat": "rabbit_images"},
    {"method": "GET", "path": "/rabbit/images/{key}", "service": "rabbit", "desc": "Download/view an uploaded image", "cat": "rabbit_images"},

    # ── CRYPTO (RGT Credits & Wallet) ──
    {"method": "POST", "path": "/crypto/wallet", "service": "crypto", "desc": "Create crypto wallet", "cat": "crypto"},
    {"method": "GET", "path": "/crypto/wallet", "service": "crypto", "desc": "Get my wallet", "cat": "crypto"},
    {"method": "GET", "path": "/crypto/wallets", "service": "crypto", "desc": "List wallets", "cat": "crypto"},
    {"method": "GET", "path": "/crypto/wallet/balance", "service": "crypto", "desc": "Get wallet balance", "cat": "crypto"},
    {"method": "POST", "path": "/crypto/deposit", "service": "crypto", "desc": "Deposit credits", "cat": "crypto"},
    {"method": "POST", "path": "/crypto/withdraw", "service": "crypto", "desc": "Withdraw credits", "cat": "crypto"},
    {"method": "GET", "path": "/crypto/withdrawals", "service": "crypto", "desc": "List withdrawals", "cat": "crypto"},
    {"method": "POST", "path": "/crypto/withdrawals/{withdrawal_id}/cancel", "service": "crypto", "desc": "Cancel withdrawal", "cat": "crypto"},
    {"method": "GET", "path": "/crypto/transactions", "service": "crypto", "desc": "List transactions", "cat": "crypto"},
    {"method": "GET", "path": "/crypto/transactions/{tx_id}", "service": "crypto", "desc": "Get transaction details", "cat": "crypto"},
    {"method": "POST", "path": "/crypto/transfer", "service": "crypto", "desc": "Transfer credits to another user", "cat": "crypto"},
    {"method": "POST", "path": "/crypto/funding-sources", "service": "crypto", "desc": "Add funding source", "cat": "crypto"},
    {"method": "GET", "path": "/crypto/funding-sources", "service": "crypto", "desc": "List funding sources", "cat": "crypto"},
    {"method": "DELETE", "path": "/crypto/funding-sources/{source_id}", "service": "crypto", "desc": "Remove funding source", "cat": "crypto"},
    {"method": "GET", "path": "/crypto/receipts/{receipt_id}", "service": "crypto", "desc": "Get receipt", "cat": "crypto"},
    {"method": "GET", "path": "/crypto/receipts/transaction/{tx_id}", "service": "crypto", "desc": "Get receipt by transaction", "cat": "crypto"},
    {"method": "GET", "path": "/crypto/token/stats", "service": "crypto", "desc": "RGT token statistics", "cat": "crypto"},
    {"method": "GET", "path": "/crypto/token/price", "service": "crypto", "desc": "RGT token price", "cat": "crypto"},
    {"method": "POST", "path": "/crypto/wallet/reward", "service": "crypto", "desc": "Send reward to wallet", "cat": "crypto"},

    # ── AUTH & IDENTITY ──
    {"method": "GET", "path": "/auth/me", "service": "auth", "desc": "Get current user profile", "cat": "auth"},
    {"method": "GET", "path": "/auth/identity", "service": "auth", "desc": "Get user identity", "cat": "auth"},
    {"method": "GET", "path": "/auth/api-keys", "service": "auth", "desc": "List API keys", "cat": "auth"},
    {"method": "POST", "path": "/auth/api-keys", "service": "auth", "desc": "Create API key", "cat": "auth"},
    {"method": "POST", "path": "/auth/api-keys/revoke", "service": "auth", "desc": "Revoke API key", "cat": "auth"},
    {"method": "GET", "path": "/auth/user/api-keys", "service": "auth", "desc": "List user API keys (provider keys)", "cat": "auth"},
    {"method": "POST", "path": "/auth/user/api-keys", "service": "auth", "desc": "Save user API key (OpenAI, Anthropic, etc.)", "cat": "auth"},
    {"method": "POST", "path": "/auth/user/api-keys/validate", "service": "auth", "desc": "Validate an API key", "cat": "auth"},
    {"method": "DELETE", "path": "/auth/user/api-keys/{key_id}", "service": "auth", "desc": "Delete user API key", "cat": "auth"},
    {"method": "GET", "path": "/auth/user/trial-status", "service": "auth", "desc": "Get trial status", "cat": "auth"},
    {"method": "GET", "path": "/auth/user/service-access", "service": "auth", "desc": "Get service access levels", "cat": "auth"},
    {"method": "GET", "path": "/auth/user/available-providers", "service": "auth", "desc": "Get available LLM providers for user", "cat": "auth"},
    {"method": "GET", "path": "/auth/orgs", "service": "auth", "desc": "List user organizations", "cat": "auth_orgs"},
    {"method": "POST", "path": "/auth/orgs/invite", "service": "auth", "desc": "Invite user to organization", "cat": "auth_orgs"},
    {"method": "GET", "path": "/auth/settings/agents", "service": "auth", "desc": "Get agent settings", "cat": "auth_settings"},
    {"method": "POST", "path": "/auth/settings/agents", "service": "auth", "desc": "Update agent settings", "cat": "auth_settings"},
    {"method": "GET", "path": "/auth/settings/agents/{agent_id}", "service": "auth", "desc": "Get settings for specific agent", "cat": "auth_settings"},
    {"method": "POST", "path": "/auth/mnemonic", "service": "auth", "desc": "Generate mnemonic seed phrase", "cat": "auth"},

    # ── STORAGE (expanded) ──
    {"method": "POST", "path": "/storage/upload/batch", "service": "storage", "desc": "Upload multiple files", "cat": "storage"},
    {"method": "GET", "path": "/storage/files/{key}", "service": "storage", "desc": "Get file info by key", "cat": "storage"},
    {"method": "GET", "path": "/storage/buckets", "service": "storage", "desc": "List storage buckets", "cat": "storage"},
    {"method": "POST", "path": "/storage/buckets/{bucket_name}", "service": "storage", "desc": "Create storage bucket", "cat": "storage"},
    {"method": "GET", "path": "/storage/presigned/{key}", "service": "storage", "desc": "Get presigned download URL", "cat": "storage"},

    # ── NOTIFICATIONS (expanded) ──
    {"method": "POST", "path": "/notifications/read-all", "service": "notification", "desc": "Mark all notifications as read", "cat": "notifications"},
    {"method": "GET", "path": "/notifications/preferences", "service": "notification", "desc": "Get notification preferences", "cat": "notifications"},
    {"method": "PUT", "path": "/notifications/preferences", "service": "notification", "desc": "Update notification preferences", "cat": "notifications"},

    # ── USER MEMORY (Hash Sphere advanced) ──
    {"method": "POST", "path": "/memories/embed", "service": "user_memory", "desc": "Embed/store a new memory", "cat": "user_memory"},
    {"method": "POST", "path": "/memories/retrieve", "service": "user_memory", "desc": "Retrieve memories by semantic search", "cat": "user_memory"},
    {"method": "GET", "path": "/memories", "service": "user_memory", "desc": "List all memories", "cat": "user_memory"},
    {"method": "GET", "path": "/memories/{memory_id}", "service": "user_memory", "desc": "Get specific memory", "cat": "user_memory"},
    {"method": "PATCH", "path": "/memories/{memory_id}/archive", "service": "user_memory", "desc": "Archive a memory", "cat": "user_memory"},
    {"method": "PATCH", "path": "/memories/{memory_id}/restore", "service": "user_memory", "desc": "Restore archived memory", "cat": "user_memory"},
    {"method": "POST", "path": "/clusters", "service": "user_memory", "desc": "Create memory cluster", "cat": "user_memory"},
    {"method": "GET", "path": "/clusters", "service": "user_memory", "desc": "List memory clusters", "cat": "user_memory"},
    {"method": "GET", "path": "/universe", "service": "user_memory", "desc": "Get memory universe visualization data", "cat": "user_memory"},
    {"method": "GET", "path": "/stats", "service": "user_memory", "desc": "Get memory stats", "cat": "user_memory"},
    {"method": "GET", "path": "/visualizer/semantic-space", "service": "user_memory", "desc": "Get semantic space visualization", "cat": "user_memory"},
]

# Build quick-lookup index for categories
_CATEGORIES = sorted(set(e["cat"] for e in PLATFORM_API_CATALOG))


# ═══════════════════════════════════════════════════════════════════════════════
#  TOOL HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

async def platform_api_search(args: dict, ctx: dict) -> dict:
    """Search the platform API catalog by keyword.

    Returns matching endpoints the user can call via platform_api_call.
    """
    query = (args.get("query") or args.get("keyword") or args.get("search") or "").lower().strip()
    category = (args.get("category") or "").lower().strip()
    method_filter = (args.get("method") or "").upper().strip()
    limit = int(args.get("limit", 20))

    if not query and not category:
        # Return category summary
        cat_counts = {}
        for e in PLATFORM_API_CATALOG:
            c = e["cat"]
            cat_counts[c] = cat_counts.get(c, 0) + 1
        return {
            "total_endpoints": len(PLATFORM_API_CATALOG),
            "categories": cat_counts,
            "hint": "Provide a 'query' to search or 'category' to filter. Example: platform_api_search(query='create agent') or platform_api_search(category='teams')",
        }

    results = []
    for entry in PLATFORM_API_CATALOG:
        # Category filter
        if category and category not in entry["cat"]:
            continue
        # Method filter
        if method_filter and entry["method"] != method_filter:
            continue
        # Keyword match (path, desc, cat)
        if query:
            searchable = f"{entry['path']} {entry['desc']} {entry['cat']}".lower()
            if not all(word in searchable for word in query.split()):
                continue
        results.append({
            "method": entry["method"],
            "path": entry["path"],
            "service": entry["service"],
            "description": entry["desc"],
            "category": entry["cat"],
        })
        if len(results) >= limit:
            break

    return {
        "matches": len(results),
        "total_catalog": len(PLATFORM_API_CATALOG),
        "results": results,
        "hint": "Use platform_api_call with method, path, and optionally body/query_params to call any of these endpoints. Path parameters like {agent_id} must be replaced with actual IDs.",
    }


async def platform_api_call(args: dict, ctx: dict) -> dict:
    """Make an authenticated HTTP call to any platform API endpoint.

    The user's context (user_id, role, org_id) is automatically forwarded.
    """
    method = (args.get("method") or "GET").upper()
    path = (args.get("path") or "").strip()
    body = args.get("body") or args.get("data") or args.get("json")
    query_params = args.get("query_params") or args.get("params")
    service_override = (args.get("service") or "").strip()

    if not path:
        return {"error": "Missing 'path' parameter. Use platform_api_search to find available endpoints."}

    # Resolve which service to call
    service_key = service_override
    if not service_key:
        # Auto-detect service from path
        if path.startswith("/agents") or path.startswith("/execution") or path.startswith("/autonomy") or \
           path.startswith("/autonomous") or path.startswith("/orchestration") or path.startswith("/ultimate") or \
           path.startswith("/max-autonomy") or path.startswith("/advanced") or path.startswith("/webhooks") or \
           path.startswith("/discord") or path.startswith("/agentic-chat") or path.startswith("/billing"):
            if path.startswith("/billing"):
                service_key = "billing"
            else:
                service_key = "agent_engine"
        elif path.startswith("/resonant-chat") or path.startswith("/skills") or path.startswith("/search") or \
             path.startswith("/feedback") or path.startswith("/analytics"):
            service_key = "chat"
        elif path.startswith("/workflow"):
            service_key = "workflow"
        elif path.startswith("/memory") or path.startswith("/hash-sphere"):
            service_key = "memory"
        elif path.startswith("/blockchain"):
            service_key = "blockchain"
        elif path.startswith("/notifications"):
            service_key = "notification"
        elif path.startswith("/storage"):
            service_key = "storage"
        elif path.startswith("/ml"):
            service_key = "ml"
        elif path.startswith("/marketplace"):
            service_key = "marketplace"
        elif path.startswith("/crypto"):
            service_key = "crypto"
        elif path.startswith("/rabbit"):
            service_key = "rabbit"
        elif path.startswith("/auth"):
            service_key = "auth"
        elif path.startswith("/memories") or path.startswith("/clusters") or path.startswith("/universe") or \
             path.startswith("/visualizer") or path.startswith("/stats"):
            service_key = "user_memory"
        else:
            return {"error": f"Cannot auto-detect service for path '{path}'. Specify 'service' parameter (agent_engine, chat, workflow, memory, billing, blockchain, notification, storage, ml, marketplace, crypto, rabbit, auth, user_memory)."}

    base_url = SERVICE_URLS.get(service_key)
    if not base_url:
        return {"error": f"Unknown service '{service_key}'. Valid: {', '.join(SERVICE_URLS.keys())}"}

    # Build full URL
    url = f"{base_url}{path}" if path.startswith("/") else f"{base_url}/{path}"

    # Build auth headers from context
    user_id = ctx.get("user_id", "anonymous")
    headers = {
        "x-user-id": user_id,
        "x-user-role": ctx.get("user_role", "user"),
        "x-is-superuser": "true" if ctx.get("is_superuser") else "false",
        "x-org-id": ctx.get("org_id", user_id),
        "content-type": "application/json",
    }

    # Safety: block dangerous paths
    dangerous_prefixes = ("/admin", "/rara", "/daemon", "/control")
    if any(path.startswith(p) for p in dangerous_prefixes):
        user_role = ctx.get("user_role", "user")
        if user_role not in ("platform_owner", "owner", "admin"):
            return {"error": f"Access denied: {path} requires admin/owner role. Your role: {user_role}"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=body if method in ("POST", "PUT", "PATCH") and body else None,
                params=query_params,
            )

            # Parse response
            try:
                result = resp.json()
            except Exception:
                result = resp.text[:4000]

            return {
                "status_code": resp.status_code,
                "success": 200 <= resp.status_code < 300,
                "data": result if isinstance(result, (dict, list)) else {"raw": str(result)[:4000]},
                "method": method,
                "path": path,
                "service": service_key,
            }

    except httpx.TimeoutException:
        return {"error": f"Request timed out calling {service_key} at {path}"}
    except Exception as e:
        logger.exception(f"platform_api_call error: {e}")
        return {"error": f"Request failed: {str(e)[:500]}"}
