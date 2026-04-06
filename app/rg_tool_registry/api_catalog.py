"""
Unified API Catalog — ALL platform services and their internal APIs.

This is the single source of truth for service discovery. Agents and tools
can call ANY platform API by specifying: service_name + endpoint + method.

The catalog provides:
  - Service URLs (internal Docker network)
  - Categories for LLM reasoning
  - Key capabilities per service
  - Auth requirements

Architecture: Agents call platform_api(service, endpoint, method, body)
and the executor resolves the URL from this catalog and proxies the call.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum


class ServiceCategory(str, Enum):
    CORE = "core"                    # Auth, users, billing, gateway
    AI = "ai"                        # LLM, memory, cognitive, ML
    AGENTS = "agents"                # Agent engine, architect, cascade control
    COMMUNITY = "community"          # Rabbit (posts, communities, votes, moderation)
    DEVELOPER = "developer"          # ED service, code exec, sandbox, build, AST, IDE
    INTEGRATIONS = "integrations"    # Discord, notifications, workflow
    BLOCKCHAIN = "blockchain"        # External blockchain, crypto, mining, lighthouse
    STORAGE = "storage"              # Storage, marketplace
    SIMULATION = "simulation"        # Internal/user invariant sims


@dataclass
class ServiceDef:
    """A platform service definition."""
    name: str
    url: str
    category: ServiceCategory
    description: str
    capabilities: List[str] = field(default_factory=list)
    auth: str = "internal"           # internal | jwt | none
    health_endpoint: str = "/health"


# ── ALL PLATFORM SERVICES ──────────────────────────────────────

SERVICES: Dict[str, ServiceDef] = {}


def _reg(s: ServiceDef):
    SERVICES[s.name] = s


# ── CORE ──
_reg(ServiceDef(
    name="auth", url="http://auth_service:8000", category=ServiceCategory.CORE,
    description="Authentication, user registration, JWT tokens, API key management, OAuth flows",
    capabilities=["login", "register", "verify_token", "user_api_keys", "oauth_connect", "oauth_callback",
                   "identity_register", "password_reset", "session_management"],
    auth="none",
))
_reg(ServiceDef(
    name="users", url="http://user_service:8000", category=ServiceCategory.CORE,
    description="User profiles, preferences, settings, org membership",
    capabilities=["get_profile", "update_profile", "list_users", "user_preferences",
                   "org_members", "user_search", "avatar_upload"],
    auth="jwt",
))
_reg(ServiceDef(
    name="billing", url="http://billing_service:8000", category=ServiceCategory.CORE,
    description="Credits, subscriptions, payments, usage tracking, RGT token management",
    capabilities=["check_balance", "deduct_credits", "add_credits", "transaction_history",
                   "subscription_status", "usage_report", "stripe_checkout"],
    auth="jwt",
))
_reg(ServiceDef(
    name="gateway", url="http://gateway:8000", category=ServiceCategory.CORE,
    description="API gateway, request routing, rate limiting",
    capabilities=["route_request", "rate_limit_status"],
    auth="none",
))
_reg(ServiceDef(
    name="notification", url="http://notification_service:8000", category=ServiceCategory.CORE,
    description="Push notifications, email alerts, in-app notifications, webhooks",
    capabilities=["send_notification", "list_notifications", "mark_read",
                   "email_alert", "webhook_dispatch", "notification_preferences"],
    auth="internal",
))

# ── AI ──
_reg(ServiceDef(
    name="llm", url="http://llm_service:8000", category=ServiceCategory.AI,
    description="Unified LLM client — multi-provider (Groq, OpenAI, Anthropic, Gemini, local Resonant)",
    capabilities=["complete", "stream", "embeddings", "provider_status", "model_list",
                   "token_count", "provider_health"],
    auth="internal",
))
_reg(ServiceDef(
    name="memory", url="http://memory_service:8000", category=ServiceCategory.AI,
    description="Hash Sphere semantic memory — store, recall, search memories with ML embeddings",
    capabilities=["store_memory", "recall", "search", "delete_memory", "memory_stats",
                   "hash_sphere_query", "semantic_search", "memory_graph", "universe_state"],
    auth="internal",
))
_reg(ServiceDef(
    name="user_memory", url="http://user_memory_service:8000", category=ServiceCategory.AI,
    description="Per-user memory store — facts, preferences, conversation context",
    capabilities=["store_fact", "get_facts", "search_memories", "delete_fact",
                   "conversation_context", "user_knowledge_graph"],
    auth="internal",
))
_reg(ServiceDef(
    name="cognitive", url="http://cognitive_service:8000", category=ServiceCategory.AI,
    description="Cognitive processing — intent detection, entity extraction, sentiment, summarization",
    capabilities=["detect_intent", "extract_entities", "sentiment_analysis",
                   "summarize", "classify_text", "generate_embeddings"],
    auth="internal",
))
_reg(ServiceDef(
    name="ml", url="http://ml_service:8000", category=ServiceCategory.AI,
    description="ML model registry, training jobs, model serving, fine-tuning",
    capabilities=["list_models", "register_model", "start_training", "training_status",
                   "model_inference", "fine_tune", "model_metrics"],
    auth="internal",
))

# ── AGENTS ──
_reg(ServiceDef(
    name="agent_engine", url="http://agent_engine_service:8000", category=ServiceCategory.AGENTS,
    description="Agent execution engine — create, run, manage autonomous agents",
    capabilities=["create_agent", "start_session", "get_session", "list_agents",
                   "agent_status", "approve_step", "cancel_session", "agent_config",
                   "publish_api", "agent_schedules", "agent_triggers", "agent_teams"],
    auth="jwt",
))
_reg(ServiceDef(
    name="agent_architect", url="http://agent_architect:8000", category=ServiceCategory.AGENTS,
    description="Agent Architect — design, plan, create agents with goal crafting pipeline",
    capabilities=["brainstorm", "craft_goal", "create_agent", "review_agent",
                   "list_tools", "list_providers", "assign_goal", "set_autonomy"],
    auth="internal",
))
_reg(ServiceDef(
    name="cascade_control", url="http://cascade_control_plane:8000", category=ServiceCategory.AGENTS,
    description="Cascade control plane — multi-agent orchestration, agent lifecycle",
    capabilities=["orchestrate", "agent_lifecycle", "control_status"],
    auth="internal",
))

# ── COMMUNITY (Rabbit) ──
_reg(ServiceDef(
    name="rabbit_api", url="http://rabbit_api_service:8000", category=ServiceCategory.COMMUNITY,
    description="Rabbit social platform — main API for posts, feeds, user interactions",
    capabilities=["create_post", "get_feed", "get_post", "user_profile",
                   "follow_user", "like_post", "comment", "share_post", "trending"],
    auth="jwt",
))
_reg(ServiceDef(
    name="rabbit_content", url="http://rabbit_content_service:8000", category=ServiceCategory.COMMUNITY,
    description="Rabbit content service — media uploads, content processing, CDN",
    capabilities=["upload_media", "process_image", "get_content", "content_moderation"],
    auth="internal",
))
_reg(ServiceDef(
    name="rabbit_community", url="http://rabbit_community_service:8000", category=ServiceCategory.COMMUNITY,
    description="Rabbit communities — create, join, manage community groups",
    capabilities=["create_community", "join_community", "leave_community",
                   "list_communities", "community_members", "community_settings"],
    auth="jwt",
))
_reg(ServiceDef(
    name="rabbit_vote", url="http://rabbit_vote_service:8000", category=ServiceCategory.COMMUNITY,
    description="Rabbit voting — upvotes, downvotes, polls, reputation",
    capabilities=["upvote", "downvote", "create_poll", "vote_poll", "reputation_score"],
    auth="jwt",
))
_reg(ServiceDef(
    name="rabbit_moderation", url="http://rabbit_moderation_service:8000", category=ServiceCategory.COMMUNITY,
    description="Rabbit moderation — content review, reports, bans, auto-moderation",
    capabilities=["report_content", "review_report", "ban_user", "auto_moderate",
                   "moderation_queue", "content_filter"],
    auth="internal",
))

# ── DEVELOPER ──
_reg(ServiceDef(
    name="ed", url="http://ed_service:8000", category=ServiceCategory.DEVELOPER,
    description="Engineering Director — file ops, git, docker, testing, code analysis, workflows",
    capabilities=["read_file", "write_file", "list_files", "search_files", "search_content",
                   "git_clone", "git_status", "git_commit", "git_push", "git_pull",
                   "docker_ps", "docker_logs", "docker_restart",
                   "run_pytest", "run_jest", "run_lint", "run_coverage",
                   "trigger_workflow", "ask_llm", "validate_code"],
    auth="internal",
))
_reg(ServiceDef(
    name="code_execution", url="http://code_execution_service:8000", category=ServiceCategory.DEVELOPER,
    description="Sandboxed code execution — Python, JS, shell in isolated containers",
    capabilities=["execute_python", "execute_javascript", "execute_shell",
                   "execution_status", "list_sessions"],
    auth="internal",
))
_reg(ServiceDef(
    name="sandbox_runner", url="http://sandbox_runner_service:8000", category=ServiceCategory.DEVELOPER,
    description="Sandbox runner — isolated execution environments for agent tool calls",
    capabilities=["create_sandbox", "execute_in_sandbox", "destroy_sandbox"],
    auth="internal",
))
_reg(ServiceDef(
    name="build", url="http://build_service:8000", category=ServiceCategory.DEVELOPER,
    description="Build service — compile, deploy, CI/CD pipeline management",
    capabilities=["start_build", "build_status", "deploy", "rollback", "build_logs"],
    auth="internal",
))
_reg(ServiceDef(
    name="ast_analysis", url="http://rg_ast_analysis:8000", category=ServiceCategory.DEVELOPER,
    description="AST analysis — code structure analysis, dependency graphs, symbol resolution",
    capabilities=["analyze_file", "dependency_graph", "symbol_lookup", "code_metrics"],
    auth="internal",
))
_reg(ServiceDef(
    name="ide", url="http://ide_service:8000", category=ServiceCategory.DEVELOPER,
    description="IDE service — editor state, file sync, live collaboration",
    capabilities=["open_file", "save_file", "file_tree", "editor_state", "collab_session"],
    auth="jwt",
))
_reg(ServiceDef(
    name="ide_agent", url="http://ide_agent_service:8000", category=ServiceCategory.DEVELOPER,
    description="IDE agent — AI-powered code assistant within the IDE",
    capabilities=["code_complete", "code_explain", "code_refactor", "code_review",
                   "generate_tests", "fix_bug"],
    auth="internal",
))
_reg(ServiceDef(
    name="v8_api", url="http://v8_api_service:8000", category=ServiceCategory.DEVELOPER,
    description="V8 API service — JavaScript/TypeScript execution engine",
    capabilities=["execute_js", "compile_ts", "evaluate_expression"],
    auth="internal",
))

# ── INTEGRATIONS ──
_reg(ServiceDef(
    name="chat", url="http://chat_service:8000", category=ServiceCategory.INTEGRATIONS,
    description="Chat service — real-time messaging, chat skills, AI chat orchestration",
    capabilities=["send_message", "get_history", "create_conversation", "chat_skill_execute",
                   "agentic_chat", "stream_response", "detect_intent"],
    auth="jwt",
))
_reg(ServiceDef(
    name="discord", url="http://discord_bridge:8000", category=ServiceCategory.INTEGRATIONS,
    description="Discord bridge — bot integration, message relay, server management",
    capabilities=["send_message", "read_messages", "list_channels", "bot_status",
                   "webhook_relay"],
    auth="internal",
))
_reg(ServiceDef(
    name="workflow", url="http://workflow_service:8000", category=ServiceCategory.INTEGRATIONS,
    description="Workflow engine — automation pipelines, triggers, scheduled tasks, cron jobs",
    capabilities=["create_workflow", "run_workflow", "workflow_status", "list_workflows",
                   "create_trigger", "create_schedule", "workflow_logs"],
    auth="internal",
))
_reg(ServiceDef(
    name="agentic_chat", url="http://rg_agentic_chat:8000", category=ServiceCategory.INTEGRATIONS,
    description="Agentic chat — agent-powered conversations with streaming",
    capabilities=["start_session", "stream_response", "get_history"],
    auth="jwt",
))
_reg(ServiceDef(
    name="guest_chat", url="http://rg_public_guest_chat:8000", category=ServiceCategory.INTEGRATIONS,
    description="Public guest chat — unauthenticated chat for visitors",
    capabilities=["send_message", "get_response"],
    auth="none",
))

# ── BLOCKCHAIN ──
_reg(ServiceDef(
    name="blockchain", url="http://blockchain_service:8000", category=ServiceCategory.BLOCKCHAIN,
    description="Internal blockchain service — on-chain identity, transactions, smart contracts",
    capabilities=["register_identity", "get_identity", "submit_transaction",
                   "get_balance", "contract_call", "chain_status"],
    auth="internal",
))
_reg(ServiceDef(
    name="external_blockchain", url="http://external_blockchain_service:8000", category=ServiceCategory.BLOCKCHAIN,
    description="ResonantGenesis external blockchain — Raft consensus, block production, P2P network",
    capabilities=["get_blocks", "submit_block", "peer_status", "consensus_status",
                   "identity_register", "identity_lookup", "chain_info"],
    auth="internal",
))
_reg(ServiceDef(
    name="crypto", url="http://crypto_service:8000", category=ServiceCategory.BLOCKCHAIN,
    description="Crypto service — RGT token operations, wallets, transfers, staking",
    capabilities=["get_wallet", "transfer", "stake", "unstake", "balance",
                   "transaction_history", "mint", "burn"],
    auth="jwt",
))
_reg(ServiceDef(
    name="mining", url="http://mining_service:8701", category=ServiceCategory.BLOCKCHAIN,
    description="Mining service — distributed model training, gradient aggregation, miner management",
    capabilities=["start_training", "training_status", "miner_connect", "gradient_submit",
                   "aggregation_status", "dashboard_data"],
    auth="internal",
))
_reg(ServiceDef(
    name="lighthouse", url="http://lighthouse_service:8700", category=ServiceCategory.BLOCKCHAIN,
    description="Lighthouse — P2P discovery, peer registry, service mesh coordination",
    capabilities=["register_peer", "discover_peers", "peer_status", "heartbeat",
                   "service_registry"],
    auth="internal",
))

# ── STORAGE ──
_reg(ServiceDef(
    name="storage", url="http://storage_service:8000", category=ServiceCategory.STORAGE,
    description="File storage — upload, download, S3/local storage, CDN, media processing",
    capabilities=["upload_file", "download_file", "list_files", "delete_file",
                   "presigned_url", "media_transform"],
    auth="jwt",
))
_reg(ServiceDef(
    name="marketplace", url="http://marketplace_service:8000", category=ServiceCategory.STORAGE,
    description="Marketplace — agent marketplace, tool marketplace, templates, publishing",
    capabilities=["list_items", "publish_item", "purchase_item", "rate_item",
                   "search_marketplace", "featured_items"],
    auth="jwt",
))

# ── SIMULATION ──
_reg(ServiceDef(
    name="internal_sim", url="http://rg_internal_invarients_sim:8000", category=ServiceCategory.SIMULATION,
    description="Internal invariant simulation — system state testing, invariant verification",
    capabilities=["run_simulation", "check_invariants", "simulation_status"],
    auth="internal",
))
_reg(ServiceDef(
    name="user_sim", url="http://rg_users_invarients_sim:8000", category=ServiceCategory.SIMULATION,
    description="User invariant simulation — user behavior modeling, load testing",
    capabilities=["simulate_users", "load_test", "behavior_model"],
    auth="internal",
))


# ── HELPER FUNCTIONS ──────────────────────────────────────────

def get_service(name: str) -> Optional[ServiceDef]:
    """Look up a service by name."""
    return SERVICES.get(name)


def get_services_by_category(category: ServiceCategory) -> List[ServiceDef]:
    """Get all services in a category."""
    return [s for s in SERVICES.values() if s.category == category]


def get_all_services() -> List[ServiceDef]:
    """Get all registered services."""
    return list(SERVICES.values())


def get_service_summary() -> Dict[str, List[str]]:
    """Get a category → service names summary for LLM discovery."""
    summary = {}
    for cat in ServiceCategory:
        services = get_services_by_category(cat)
        if services:
            summary[cat.value] = [f"{s.name}: {s.description[:60]}" for s in services]
    return summary


def get_compact_catalog() -> str:
    """Get a compact one-line-per-service catalog string for prompts."""
    lines = []
    for cat in ServiceCategory:
        services = get_services_by_category(cat)
        if services:
            names = ", ".join(s.name for s in services)
            lines.append(f"  {cat.value}: {names}")
    return "\n".join(lines)
