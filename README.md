# RG Agent Engine

> **Part of the [ResonantGenesis](https://resonant.dev-swat.com) platform** — Autonomous agent execution framework with neural tool classifier, goal pursuit, multi-agent orchestration, 200+ tools, BYOK LLM routing, and safety-governed execution loops.

[![Status: Production](https://img.shields.io/badge/Status-Production-brightgreen.svg)]()
[![License: RG Source Available](https://img.shields.io/badge/License-RG%20Source%20Available-blue.svg)](LICENSE.txt)

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Directory Structure](#directory-structure)
4. [Core Modules](#core-modules)
5. [LLM Integration](#llm-integration)
6. [Tool Registry](#tool-registry)
7. [Database Models](#database-models)
8. [API Endpoints](#api-endpoints)
9. [Background Daemons](#background-daemons)
10. [Dependencies](#dependencies)
11. [Environment Variables](#environment-variables)
12. [Docker & Deployment](#docker--deployment)
13. [Local Development](#local-development)
14. [Tests](#tests)
15. [Known Issues & Gotchas](#known-issues--gotchas)

---

## Overview

The Agent Engine is the core service that lets users **create, configure, and run AI agents** on the Resonant Genesis platform. An agent is defined by a system prompt, an LLM provider/model, and a safety envelope. When a user starts an agent session with a goal, the executor runs an autonomous **ReAct loop** (Reason → Act → Observe) until the goal is achieved or limits are hit.

**Key capabilities:**
- CRUD for agent definitions, teams, schedules, triggers, and webhooks
- Autonomous execution loop with up to 200 iterations per session
- 200+ tools with **neural ML classifier** (sentence-transformer + MLP) for intelligent tool selection
- Per-agent tool configuration: `tool_mode="smart"` (classifier picks) or `"manual"` (only selected tools)
- BYOK (Bring Your Own Key) — users can provide their own LLM API keys
- Multi-provider LLM fallback via `rg_llm.UnifiedLLMClient`
- Safety envelope with threat-level classification, approval gates, and rate limiting
- Scheduler daemon for cron-based and interval-based agent runs
- Agent teams with collaborative/hierarchical workflows
- Publish-as-API — expose any agent as a REST endpoint
- Discord bot integration
- Webhook triggers
- Project builder sub-agent for full-stack code generation
- Federation support for user-hosted agents (OpenClaw connector)

**Codebase size:** ~65,000 lines of Python across 110+ files.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    RG Gateway                            │
│          (proxies /agents/* to this service)             │
└──────────────┬───────────────────────────────────────────┘
               │ HTTP
┌──────────────▼───────────────────────────────────────────┐
│              Agent Engine Service (:8000)                 │
│                                                          │
│  ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌─────────┐ │
│  │ Routers │→ │ Executor │→ │ LLM Client│→ │ rg_llm  │ │
│  │ (FastAPI│  │ (ReAct   │  │ (Adapter) │  │ (volume) │ │
│  │  14     │  │  loop)   │  └───────────┘  └────┬────┘ │
│  │ routers)│  └────┬─────┘                       │      │
│  └─────────┘       │                     ┌───────▼────┐ │
│                    │                     │ Groq/OpenAI │ │
│              ┌─────▼─────┐               │ Anthropic/  │ │
│              │ Tool       │               │ Google/etc. │ │
│              │ Registry   │               └────────────┘ │
│              │ (161 tools)│                               │
│              └─────┬──────┘                               │
│                    │ HTTP                                 │
│    ┌───────────────┼──────────────────┐                  │
│    ▼               ▼                  ▼                  │
│ Memory Svc    Code Exec Svc    Auth Service              │
│ Chat Svc      Sandbox Runner   Billing Svc               │
│ Blockchain    Notification     Marketplace               │
└──────────────────────────────────────────────────────────┘
```

### Request Flow (agent session)

1. **Gateway** receives `POST /agents/sessions` with `agent_id`, `goal`, `user_id`
2. **Routers** (`routers.py`) validate, create `AgentSession` in DB
3. **Executor** (`executor.py`) starts the ReAct loop in a background task:
   - Fetches user's BYOK keys from auth service (`_fetch_user_byok_keys`)
   - For each step, calls `_get_next_action` → sends messages to LLM
   - LLM returns JSON: `{action, tool_name, tool_input, reasoning, ...}`
   - If action is `use_tool`: executes tool handler, appends result to history
   - If action is `respond`: returns response to user, checks if goal is achieved
   - Safety envelope checks at each step
   - Credit deduction per LLM call (skipped for BYOK users / privileged roles)
4. **Steps** are persisted to `agent_steps` table for audit
5. **Session** marked as `completed` or `failed`

---

## Directory Structure

```
RG_Agent_Engine/
├── app/                           # Main application package
│   ├── main.py                    # FastAPI app, router wiring, startup hooks, DDL migrations
│   ├── config.py                  # Pydantic settings (env vars)
│   ├── db.py                      # SQLAlchemy async engine + session factory
│   │
│   ├── # ── CORE EXECUTION ──
│   ├── executor.py                # Main ReAct loop, tool handlers, neural classifier integration
│   ├── planner.py                 # Goal decomposition + plan generation via LLM
│   ├── verifier.py                # Post-execution verification agent
│   ├── safety.py                  # Safety envelope (threat classification, approval gates)
│   ├── loop_stabilizer.py         # Detects/breaks infinite loops in agent execution
│   ├── learning_loop.py           # Tracks tool sequences, success rates, recommendations
│   ├── policy_engine.py           # Autonomy mode policy (governed vs unbounded)
│   │
│   ├── # ── MODELS ──
│   ├── models.py                  # Core DB models: AgentDefinition, Session, Step, Team, etc.
│   ├── models_autonomy.py         # Autonomy models: Wallet, Goal, Negotiation, Contract, etc.
│   ├── models_schedule.py         # Schedule, Trigger, Execution audit models
│   ├── models_billing.py          # Billing-related models
│   │
│   ├── # ── API ROUTERS ──  (14 routers)
│   ├── routers.py                 # /agents/* — CRUD, sessions, steps, tools, safety rules (5955 lines)
│   ├── routers_teams.py           # /agent-teams/* — team CRUD, workflows, rentals
│   ├── routers_billing.py         # /billing/* — usage tracking, stripe webhooks
│   ├── routers_execution.py       # /execution/* — direct execute, session management
│   ├── routers_autonomy.py        # /autonomy/*, /wallets/*, /goals/*, /negotiations/*, /approvals/*, /agents/dsidp/*
│   ├── routers_chat_bridge.py     # /agents/chat/* — bridge to IDE terminal
│   ├── routers_ssh.py             # /agents/*/terminal — SSH/terminal for agents
│   ├── routers_discord.py         # /discord/* — Discord bot integration
│   ├── routers_advanced.py        # /advanced/* — agent memory, learning, self-improvement
│   ├── routers_autonomous.py      # /autonomous/* — autonomous daemon control
│   ├── routers_full_autonomy.py   # /autonomy/* — system watchdog, auto-startup
│   ├── routers_max_autonomy.py    # /max-autonomy/* — proactive behavior, personality
│   ├── routers_orchestration.py   # /orchestration/* — swarm controller, blockchain
│   ├── routers_ultimate.py        # /ultimate/* — emergent intelligence, world model
│   ├── settings_routes.py         # /agents/settings/* — user default settings, templates
│   ├── webhooks.py                # /webhooks/* — webhook triggers for agents
│   │
│   ├── # ── SUB-PACKAGES ──
│   ├── tool_classifier/            # Neural tool classifier (ML-based tool routing)
│   │   ├── __init__.py            # Exports: ToolClassifier, tool_classifier, preload_tool_classifier
│   │   ├── classifier.py          # Sentence-transformer + MLP classifier, active learning, DB persistence
│   │   └── training_data.py       # 1500+ labeled training samples for 200+ tools
│   ├── rg_tool_registry/          # Tool definitions + observability (lite)
│   │   ├── registry.py            # ToolDef dataclass, ToolRegistry, format converters
│   │   ├── builtin_tools.py       # Built-in tool definitions (19 categories)
│   │   ├── observability.py       # Tool call timing, success/fail tracking
│   │   └── api_catalog.py         # Platform API service catalog
│   ├── project_builder/           # Full-stack project builder sub-agent
│   │   ├── builder_agent.py       # Multi-step code generation agent
│   │   ├── code_validator.py      # Validates generated code
│   │   ├── delivery_manager.py    # Manages project delivery
│   │   ├── rara_governance.py     # RARA governance checks
│   │   ├── state_tracker.py       # Build state tracking
│   │   ├── template_engine.py     # Project templates
│   │   ├── workspace_manager.py   # User workspace management
│   │   └── router.py              # /project-builder/* endpoints
│   ├── routers_subdir/            # Additional router sub-package
│   │
│   ├── # ── INTELLIGENCE MODULES ──
│   ├── agent_brain.py             # Agent cognitive architecture
│   ├── agent_reasoning.py         # Multi-step reasoning
│   ├── agent_memory.py            # Agent-specific memory (not user memory)
│   ├── hybrid_agent_memory.py     # Hybrid RAG + hash-sphere memory
│   ├── isolated_agent_memory.py   # Per-agent isolated memory
│   ├── agent_consciousness.py     # Consciousness simulation
│   ├── agent_personality.py       # Personality traits
│   ├── agent_collaboration.py     # Multi-agent collaboration
│   ├── agent_resilience.py        # Error recovery and resilience
│   ├── agent_network.py           # Agent-to-agent networking
│   ├── emergent_intelligence.py   # Emergent behavior detection
│   ├── world_model.py             # Agent world model
│   ├── value_drift_monitor.py     # Value alignment monitoring
│   ├── self_improvement.py        # Self-improvement loops
│   ├── survival_system.py         # Agent self-preservation
│   │
│   ├── # ── AUTONOMY & EXECUTION ──
│   ├── autonomous_daemon.py       # Background daemon for autonomous agents
│   ├── autonomous_queue.py        # Queue for autonomous execution
│   ├── scheduler_daemon.py        # Cron/interval scheduler daemon
│   ├── full_autonomy.py           # Full autonomy mode
│   ├── goal_engine.py             # Goal management
│   ├── goal_generation.py         # LLM-based goal generation
│   ├── goal_pursuit.py            # Goal pursuit strategies
│   ├── proactive_behavior.py      # Proactive agent behavior
│   ├── multi_agent_orchestrator.py # Multi-agent task orchestration
│   ├── parallel_agent_runtime.py  # Parallel agent execution
│   ├── temporal_planner.py        # Time-aware planning
│   ├── workflow_executor.py       # Workflow execution
│   ├── auto_startup.py            # Auto-startup manager
│   │
│   ├── # ── INTEGRATIONS ──
│   ├── agent_wallet.py            # RGT token wallet for agents
│   ├── blockchain_integration.py  # DSID blockchain integration
│   ├── publish_agent.py           # Publish agent to marketplace/blockchain
│   ├── repo_to_agent.py           # Convert GitHub repo to agent
│   ├── platform_api_tools.py      # Platform API tool implementations
│   ├── custom_tools.py            # User-defined custom tool execution
│   ├── wasm_runtime.py            # WASM sandbox for tool execution
│   │
│   ├── # ── INFRASTRUCTURE ──
│   ├── celery_app.py              # Celery config for background tasks
│   ├── tasks.py                   # Celery task definitions
│   ├── rate_limiter.py            # Request rate limiting
│   ├── usage_middleware.py        # Usage tracking middleware
│   ├── websocket_streaming.py     # WebSocket streaming for live execution
│   ├── manifest.py                # Agent manifest utilities
│   ├── tool_spec.py               # Tool spec helpers
│   │
│   └── services/                  # (if present) Service layer abstractions
│
├── shared/                        # Shared libraries (also used by other services)
│   ├── agent/                     # Agent-specific shared modules
│   │   ├── execution_gate.py      # Dual-mode execution gate (governed/unbounded)
│   │   ├── sandbox.py             # Tool-level sandbox boundary
│   │   ├── autonomy_mode.py       # Autonomy mode + risk level definitions
│   │   ├── concurrency.py         # Concurrent agent execution
│   │   ├── delegation.py          # Cross-agent task delegation
│   │   ├── goal_generation.py     # Shared goal generation
│   │   ├── negotiation.py         # Agent-to-agent negotiation
│   │   ├── scheduler.py           # Shared scheduler utilities
│   │   ├── wallet.py              # Shared wallet operations
│   │   └── approval_notifications.py
│   ├── messaging/                 # WebSocket, backpressure, ordering
│   ├── observability/             # Metrics, tracing, structured logging, SLO
│   ├── rag/                       # Shared RAG config (embedding versioning, vector schema)
│   ├── resilience/                # Circuit breaker, retry, request tracing
│   ├── security/                  # API keys, CSRF, idempotency, rate limiter, merkle audit
│   └── crypto_identity.py         # Crypto identity helpers
│
├── agents/                        # Example agent definitions
│   ├── code-analyzer/             # manifest.json + main.py
│   ├── data-summarizer/
│   ├── hello-world/
│   ├── json-validator/
│   └── task-planner/
│
├── migrations/                    # Alembic database migrations
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│
├── tests/                         # Test suite
│   ├── test_agent_crud.py
│   ├── test_agent_teams.py
│   ├── test_auth_flows.py
│   ├── test_autonomous_flow.py
│   ├── test_billing.py
│   ├── test_platform_integration.py
│   ├── test_project_builder.py
│   └── test_rate_limiter.py
│
├── Dockerfile                     # Python 3.11-slim, uvicorn 4 workers
├── requirements.txt               # Python dependencies
├── alembic.ini                    # Alembic migration config
└── LICENSE.txt
```

---

## Core Modules

### `executor.py` — The Main Execution Engine (3806 lines)

The heart of the service. Contains:

- **`_LLMClientAdapter`** — Wraps `rg_llm.UnifiedLLMClient` into a `.complete(request, user_keys)` interface
- **`AgentExecutor`** — The main class with:
  - `run_agent_loop()` — Autonomous ReAct loop (reason/act/observe cycle)
  - `_get_next_action()` — Sends conversation to LLM, parses JSON response
  - `_fetch_user_byok_keys()` — Fetches user's BYOK keys from auth service
  - `_execute_step()` — Executes one step (LLM call + optional tool execution)
  - `_tool_*` methods — 30+ built-in tool handlers (web_search, code execution, image generation, etc.)
  - `_tool_generate_image()`, `_tool_generate_audio()`, `_tool_generate_music()`, `_tool_generate_video()` — Media generation (BYOK)
  - Safety checks, credit deduction, learning loop integration
- **`TriggerManager`** — Manages webhook/schedule/event triggers
- **`_llm_client`** — Singleton `_LLMClientAdapter` instance (used by executor, planner, agentic_chat)

### `tool_classifier/classifier.py` — Neural Tool Classifier

- **`ToolClassifier`** — ML-based tool routing using sentence-transformer + sklearn MLP
  - `predict(goal, enabled_tool_ids)` — Predict best tool for a goal
  - `predict_top_n(goal, n=10)` — Get ranked top-N tools for EXECUTION_FRAME injection
  - `add_custom_tools(names)` — Expand label space with user-defined tools
  - `retrain(custom_samples)` — Retrain with seed data + active learning + custom samples
- Model and active learning data persisted to PostgreSQL (survives container restarts)
- Per-agent tool filtering: `tool_mode="smart"` uses all tools, `"manual"` restricts to `agent.tools` array
- ~200 built-in tool labels, expandable via custom tools
- Active learning: every prediction saved to DB for continuous improvement

### `planner.py` — Goal Decomposition (247 lines)

- `ToolPlanner` — Plans which tools to use for a goal
- `GoalDecomposer` — Breaks complex goals into sub-tasks
- Uses `_llm_client.complete()` with JSON mode

### `safety.py` — Safety Envelope (661 lines)

- `SafetyEnvelope` — Loads/evaluates safety rules per agent
- `ApprovalManager` — Manages human-in-the-loop approvals
- Threat levels: CRITICAL → HIGH → MEDIUM → LOW → INFO
- URL validation, domain blocking, content filtering

### `scheduler_daemon.py` — Cron Scheduler (285 lines)

- Polls DB every 60s for due schedules/triggers
- Fires agent sessions via `agent_executor.run_agent_loop()`
- Controlled by `AGENT_ENGINE_ENABLE_SCHEDULER` env var

---

## LLM Integration

### How agents call LLMs

```
executor.py::_get_next_action()
  ↓
_llm_client.complete({messages, provider, model, ...}, user_keys=byok_keys)
  ↓
rg_llm.UnifiedLLMClient.complete(LLMRequest, user_keys)
  ↓
rg_llm.keys.build_provider_chain(providers, preferred, user_keys)
  ↓
Tries each provider in order: preferred(BYOK→env) → fallback(BYOK→env each)
  ↓
Direct HTTPS calls to: Groq, OpenAI, Anthropic, Google, DeepSeek, Mistral, OpenRouter, etc.
```

### `rg_llm` library

**NOT installed via pip** — mounted as a Docker volume:
```yaml
volumes:
  - /home/deploy/RG_UnifiedLLMClient/src/rg_llm:/app/rg_llm:ro
```

Source code: `RG_UnifiedLLMClient/src/rg_llm/` (separate repo)

Key files:
- `client.py` — `UnifiedLLMClient` class (non-streaming + streaming)
- `keys.py` — BYOK dual-key resolution + provider chain builder
- `providers.py` — `BUILTIN_PROVIDERS` dict (11 providers), `DEFAULT_FALLBACK_ORDER`
- `models.py` — `LLMRequest`, `LLMResponse`, `ProviderConfig`, etc.

### BYOK (Bring Your Own Key) flow

1. At session start, executor calls `_fetch_user_byok_keys(user_id)`
2. This hits auth service: `GET /auth/internal/user-api-keys/{user_id}`
3. Returns decrypted keys: `{provider: api_key, ...}`
4. Keys are passed as `user_keys` to every `_llm_client.complete()` call
5. `rg_llm` tries BYOK key first per provider, then system env key as fallback

### Provider fallback order

Default (from `rg_llm/providers.py`): `openai → anthropic → groq → google → deepseek → mistral`

Each provider tries: **BYOK key → system env key** before moving to next provider.

---

## Tool System

### Neural Tool Classifier (`tool_classifier/`)

The Agent Engine uses an **embedded ML classifier** (same architecture as RG_Chat) to pre-filter the tools shown to the LLM in the execution prompt. This reduces token usage and improves tool selection accuracy.

**Architecture:**
1. Goal text encoded via `sentence-transformers/all-MiniLM-L6-v2` → 384-dim embedding
2. Trained `MLPClassifier(256, 128)` predicts tool probabilities over 200+ labels
3. Top-N tools injected into EXECUTION_FRAME (replacing the old hardcoded list)
4. Per-agent filtering via `tool_mode` + `tools` columns on `AgentDefinition`
5. Active learning: every prediction saved to PostgreSQL for periodic retraining

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/agents/tools/classifier/predict` | Predict top tools for a goal |
| POST | `/agents/tools/classifier/retrain` | Retrain with seed + active + custom data |
| GET | `/agents/tools/classifier/stats` | Model stats (version, accuracy, samples) |
| POST | `/agents/tools/classifier/add-custom-tools` | Add custom tool labels |

### `rg_tool_registry/builtin_tools.py` — Tool Definitions (19 categories)

| Category | Examples |
|----------|----------|
| **SEARCH** | web_search, fetch_url, read_webpage, reddit_search, image_search, news_search, youtube_search, deep_research, wikipedia |
| **MEMORY** | memory_store, memory_search, memory_ask |
| **HASH_SPHERE** | hash_sphere_store, hash_sphere_search |
| **UTILITY** | calculate, convert_units, generate_qr, json_parse |
| **CODE_VISUALIZER** | analyze_code, visualize_dependencies |
| **AGENT** | create_agent, run_agent, list_agents |
| **MEDIA** | generate_image, generate_audio, generate_music, generate_video |
| **INTEGRATION** | send_email, google_calendar, google_drive |
| **GITHUB** | github_search, github_create_repo, github_push |
| **GIT** | git_clone, git_commit, git_push |
| **EMAIL** | send_email, read_emails |
| **DEVELOPER** | run_code, run_terminal, read_file, write_file |
| **PLATFORM_API** | platform API wrappers |
| **IDE_FILESYSTEM** | IDE file operations |
| **CHAT_SKILL** | Skill proxies for Resonant Chat |
| ... | + STATE_PHYSICS, COMMUNITY, AGENT_ARCHITECT, TOOL_MANAGEMENT |

Tools are defined using the `ToolDef` dataclass with params, access levels (`REGISTERED`, `GUEST`, `AGENT`, `IDE`), and handler function names.

---

## Database Models

All models use **SQLAlchemy async** with PostgreSQL (asyncpg driver).

### Core Tables

| Table | Model | Purpose |
|-------|-------|---------|
| `agent_definitions` | `AgentDefinition` | Agent config: name, system_prompt, provider, model, tools, safety |
| `agent_definition_versions` | `AgentVersion` | Version history for agents |
| `agent_sessions` | `AgentSession` | Active/completed execution sessions |
| `agent_steps` | `AgentStep` | Individual steps in execution loop |
| `agent_plans` | `AgentPlan` | Planned action sequences |
| `tool_definitions` | `ToolDefinition` | User-created custom tools (HTTP/webhook) |
| `safety_rules` | `SafetyRule` | Safety rules and constraints |
| `agent_user_settings` | `AgentUserSettings` | Per-user default agent settings |

### Team Tables

| Table | Model | Purpose |
|-------|-------|---------|
| `agent_teams` | `AgentTeam` | Team definitions + NFT/rental config |
| `agent_team_members` | `AgentTeamMember` | Team membership |
| `agent_team_workflows` | `AgentTeamWorkflow` | Team workflow executions |
| `agent_team_rentals` | `AgentTeamRental` | Team rental records |

### Autonomy Tables

| Table | Model | Purpose |
|-------|-------|---------|
| `agent_autonomy_modes` | `AgentAutonomyMode` | Governed vs unbounded mode |
| `agent_mode_transitions` | `AgentModeTransition` | Mode change audit trail |
| `agent_wallets` | `AgentWallet` | RGT token wallets for agents |
| `wallet_transactions` | `WalletTransaction` | Wallet transaction history |
| `agent_goals` | `AgentGoal` | Agent goal records |
| `agent_negotiations` | `AgentNegotiation` | Agent-to-agent negotiations |
| `agent_bids` | `AgentBid` | Bids in negotiations |
| `agent_contracts` | `AgentContract` | Negotiated contracts |
| `contract_obligations` | `ContractObligation` | Contract obligation tracking |
| `approval_requests` | `ApprovalRequest` | Human approval requests |
| `execution_audit_entries` | `ExecutionAuditEntry` | Execution audit log |

### Schedule Tables

| Table | Model | Purpose |
|-------|-------|---------|
| `agent_schedules` | `AgentSchedule` | Cron/interval schedules |
| `agent_triggers` | `AgentTrigger` | Webhook/file/message triggers |
| `agent_executions` | `AgentExecution` | Execution audit records |
| `workflow_triggers` | `WorkflowTrigger` | Legacy workflow triggers |

### Other Tables (created by DDL in `main.py`)

- `discord_connections` — Discord guild→agent mappings
- `anomaly_triggers` — Auto-fire agents on system anomalies
- `published_agent_apis` — Published agent API endpoints
- `federated_tasks` — Tasks for federated (user-hosted) agents

---

## API Endpoints

### Primary Router: `/agents/*` (routers.py)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/agents/` | Create agent |
| GET | `/agents/` | List agents (by user) |
| GET | `/agents/{id}` | Get agent details |
| PUT | `/agents/{id}` | Update agent |
| DELETE | `/agents/{id}` | Delete agent |
| POST | `/agents/{id}/archive` | Archive agent |
| POST | `/agents/{id}/unarchive` | Unarchive agent |
| POST | `/agents/sessions` | Start agent session (run agent) |
| GET | `/agents/sessions/{id}` | Get session status |
| GET | `/agents/sessions/{id}/steps` | Get session steps |
| POST | `/agents/sessions/{id}/cancel` | Cancel running session |
| GET | `/agents/providers` | List available LLM providers |
| POST | `/agents/tools/execute` | Execute a single tool |
| POST | `/agents/{id}/publish-api` | Publish agent as API |
| POST | `/agents/api/{slug}/call` | Call published agent API |

### Other Routers

| Prefix | Router | Purpose |
|--------|--------|---------|
| `/agent-teams/*` | `routers_teams.py` | Team CRUD, workflows, rentals |
| `/billing/*` | `routers_billing.py` | Usage tracking, Stripe |
| `/execution/*` | `routers_execution.py` | Direct execution, federation |
| `/autonomy/*` | `routers_autonomy.py` | Autonomy mode, wallets |
| `/agents/goals/*` | `routers_autonomy.py` | Goal management |
| `/wallets/*` | `routers_autonomy.py` | Agent wallets |
| `/negotiations/*` | `routers_autonomy.py` | Agent negotiations |
| `/approvals/*` | `routers_autonomy.py` | Human approvals |
| `/agents/dsidp/*` | `routers_autonomy.py` | DSID-P workforce/federation |
| `/agents/chat/*` | `routers_chat_bridge.py` | Agent ↔ IDE terminal bridge |
| `/agents/*/terminal` | `routers_ssh.py` | Agent terminal access |
| `/agents/settings/*` | `settings_routes.py` | User settings, templates |
| `/discord/*` | `routers_discord.py` | Discord bot integration |
| `/webhooks/*` | `webhooks.py` | Webhook trigger endpoints |
| `/project-builder/*` | `project_builder/` | Project builder sub-agent |
| `/advanced/*` | `routers_advanced.py` | Agent memory, learning, evolution |
| `/autonomous/*` | `routers_autonomous.py` | Autonomous daemon control |
| `/max-autonomy/*` | `routers_max_autonomy.py` | Proactive behavior, personality |
| `/orchestration/*` | `routers_orchestration.py` | Swarm control, blockchain |
| `/ultimate/*` | `routers_ultimate.py` | Emergent intelligence, world model |
| `/health` | `main.py` | Health check |

---

## Background Daemons

### Scheduler Daemon (`scheduler_daemon.py`)
- **Enabled by:** `AGENT_ENGINE_ENABLE_SCHEDULER=true`
- **Polls:** Every 60s (configurable via `SCHEDULER_POLL_INTERVAL`)
- **Max concurrent:** 2 (configurable via `SCHEDULER_MAX_CONCURRENT`)
- **What it does:** Finds due `agent_schedules` and `workflow_triggers`, fires sessions

### Autonomous Daemon (`autonomous_daemon.py`)
- **Enabled by:** `AGENT_ENGINE_ENABLE_AUTONOMOUS_DAEMON=true`
- **What it does:** Monitors agents in autonomous mode, triggers goal pursuit

### Celery Workers (`celery_app.py`, `tasks.py`)
- **Broker:** Redis (`CELERY_BROKER_URL`)
- **Queue:** `agent_tasks`
- **What it does:** Async background execution of agent sessions

---

## Dependencies

### Python Packages (`requirements.txt`)

| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi` | ≥0.104.0 | Web framework |
| `uvicorn` | ≥0.24.0 | ASGI server |
| `sqlalchemy` | ≥2.0.0 | ORM (async) |
| `alembic` | ≥1.13.0 | DB migrations |
| `asyncpg` | ≥0.29.0 | PostgreSQL async driver |
| `pydantic` | ≥2.0.0 | Data validation |
| `pydantic-settings` | ≥2.0.0 | Settings management |
| `httpx` | ≥0.25.0 | HTTP client (LLM calls, service-to-service) |
| `redis` | ≥5.0.0 | Caching, pub/sub |
| `celery[redis]` | ≥5.3.0 | Background task queue |
| `stripe` | ≥7.0.0 | Payment processing |
| `pynacl` | ≥1.5.0 | Crypto (DSID-P) |
| `wasmtime` | ≥15.0.0 | WASM runtime for sandboxed tools |
| `croniter` | ≥1.4.0 | Cron expression parsing |
| `beautifulsoup4` | ≥4.12.0 | HTML parsing (web scraping tools) |
| `lxml` | ≥5.0.0 | XML/HTML parser |
| `tiktoken` | ≥0.5.0 | Token counting |
| `websockets` | ≥12.0 | WebSocket support |
| `sentence-transformers` | ≥2.2.0 | Neural tool classifier encoder |
| `scikit-learn` | ≥1.3.0 | MLP classifier for tool routing |
| `numpy` | ≥1.24.0 | Numerical operations |

### External Libraries (NOT in requirements.txt)

| Library | How Installed | Purpose |
|---------|--------------|---------|
| `rg_llm` | Docker volume mount from `RG_UnifiedLLMClient/src/rg_llm` | Multi-provider LLM client |

### Service Dependencies (inter-service HTTP calls)

| Service | Env Var | Default URL | Used For |
|---------|---------|-------------|----------|
| Auth | `AUTH_SERVICE_URL` | `http://auth_service:8000` | BYOK key retrieval, user validation |
| Billing | `BILLING_SERVICE_URL` | `http://billing_service:8000` | Credit checks, deductions |
| Memory | `MEMORY_SERVICE_URL` | `http://memory_service:8000` | Agent memory (RAG, hash-sphere) |
| LLM Service | `LLM_SERVICE_URL` | `http://llm_service:8000` | Provider list proxy only |
| Code Execution | `CODE_EXECUTION_SERVICE_URL` | `http://code_execution_service:8002` | Code running |
| Sandbox Runner | `SANDBOX_RUNNER_URL` | `http://sandbox_runner_service:9001` | Docker sandbox |
| Chat Service | `CHAT_SERVICE_URL` | `http://chat_service:8000` | Chat bridge |
| Ed Service | `ED_SERVICE_URL` | `http://ed_service:8000` | IDE integration |
| Blockchain | `BLOCKCHAIN_SERVICE_URL` | `http://blockchain_service:8000` | DSID blockchain |
| Marketplace | `MARKETPLACE_SERVICE_URL` | `http://marketplace_service:8000` | Agent marketplace |
| Notification | `NOTIFICATION_SERVICE_URL` | `http://notification_service:8000` | Alerts |
| RARA | `RARA_SERVICE_URL` | `http://rg_internal_invarients_sim:8093` | Governance checks |
| Redis | `REDIS_URL` | `redis://shared_redis:6379/0` | Cache, Celery broker |

---

## Environment Variables

### Required

| Variable | Example | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL connection string |
| `REDIS_URL` | `redis://shared_redis:6379/0` | Redis URL |

### LLM API Keys (system fallback keys — used when user has no BYOK)

| Variable | Provider |
|----------|----------|
| `GROQ_API_KEY` | Groq |
| `OPENAI_API_KEY` | OpenAI |
| `ANTHROPIC_API_KEY` | Anthropic |
| `GEMINI_API_KEY` | Google Gemini |
| `GOOGLE_API_KEY` | Google (alias) |
| `OPENROUTER_API_KEY` | OpenRouter |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `MISTRAL_API_KEY` | Mistral |

### Service URLs

See [Service Dependencies](#service-dependencies-inter-service-http-calls) table above.

### Feature Flags

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_ENGINE_ENABLE_SCHEDULER` | `true` | Enable scheduler daemon |
| `AGENT_ENGINE_ENABLE_AUTONOMOUS_DAEMON` | `false` | Enable autonomous daemon |
| `AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_ENABLED` | `false` | Enable Docker sandbox per tool run |
| `AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_IMAGE` | `python:3.11-alpine` | Sandbox Docker image |
| `AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_TIMEOUT_SECONDS` | `20` | Sandbox timeout |
| `AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_MEMORY` | `256m` | Sandbox memory limit |
| `AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_CPUS` | `0.5` | Sandbox CPU limit |

### Database Pool

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_ENGINE_DB_POOL_SIZE` | `3` | Connection pool size |
| `AGENT_ENGINE_DB_MAX_OVERFLOW` | `2` | Max overflow connections |
| `AGENT_ENGINE_DB_POOL_TIMEOUT` | `30` | Pool wait timeout (seconds) |
| `AGENT_ENGINE_DB_POOL_RECYCLE` | `1800` | Connection recycle time (seconds) |
| `AGENT_ENGINE_DB_POOL_CLASS` | `queue` | Pool class (`queue` or `null`) |

### Scheduler

| Variable | Default | Description |
|----------|---------|-------------|
| `SCHEDULER_POLL_INTERVAL` | `60` | Seconds between scheduler polls |
| `SCHEDULER_MAX_CONCURRENT` | `2` | Max concurrent scheduled runs |

### Auth

| Variable | Description |
|----------|-------------|
| `AUTH_INTERNAL_SERVICE_KEY` | Internal service key for auth service calls |
| `INTERNAL_SERVICE_KEY` | Fallback internal key |

---

## Docker & Deployment

### Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
EXPOSE 8000
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### docker-compose.unified.yml (production)

```yaml
agent_engine_service:
  build:
    context: /home/deploy/RG_Agent_Engine
    dockerfile: Dockerfile
  container_name: agent_engine_service
  env_file: ./.env.production
  environment:
    DATABASE_URL: ${AGENT_DATABASE_URL}
    REDIS_URL: redis://shared_redis:6379/0
    AGENT_ENGINE_DB_POOL_CLASS: 'null'
    PYTHONPATH: /app
    # ... (see full list in Environment Variables section)
  volumes:
    - build_projects:/opt/resonant/user_workspaces
    - /home/deploy/RG_UnifiedLLMClient/src/rg_llm:/app/rg_llm:ro
  depends_on:
    - shared_redis
    - llm_service
    - auth_service
```

### Deploy Path

- **Server:** `deploy@resonant.dev-swat.com`
- **Code path:** `/home/deploy/RG_Agent_Engine`
- **Container name:** `agent_engine_service`
- **Port:** `8000` (internal Docker network)
- **Gateway proxy:** All `/agents/*` requests are proxied from RG_Gateway

### Rebuild & Deploy

```bash
# SSH into server
ssh deploy@resonant.dev-swat.com

# Rebuild just agent engine
cd /home/deploy/genesis2026_production_backend
sudo docker-compose -f docker-compose.unified.yml build agent_engine_service
sudo docker-compose -f docker-compose.unified.yml up -d agent_engine_service

# Check logs
sudo docker logs -f agent_engine_service --tail 100
```

---

## Local Development

### Prerequisites

- Python 3.11+
- PostgreSQL (with async support)
- Redis
- The `rg_llm` library (clone `RG_UnifiedLLMClient` and symlink or add to PYTHONPATH)

### Setup

```bash
# Clone
git clone git@github.com:DevSwat-ResonantGenesis/RG_Agent_Engine.git
cd RG_Agent_Engine

# Install deps
pip install -r requirements.txt

# Make rg_llm available (option 1: symlink)
ln -s ../RG_UnifiedLLMClient/src/rg_llm ./rg_llm

# Set env vars
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/resonant_agents"
export REDIS_URL="redis://localhost:6379/0"
export GROQ_API_KEY="your-key"          # or any LLM provider key
export AUTH_SERVICE_URL="http://localhost:8004"
export BILLING_SERVICE_URL="http://localhost:8005"

# Run
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Database Setup

The service auto-creates tables on startup via DDL statements in `main.py::ensure_schema()`. For formal migrations, use Alembic:

```bash
alembic upgrade head
```

---

## Tests

```bash
# Run all tests
pytest tests/ -v

# Individual test files
pytest tests/test_agent_crud.py -v
pytest tests/test_agent_teams.py -v
pytest tests/test_autonomous_flow.py -v
```

Test files:
- `test_agent_crud.py` — Agent CRUD operations
- `test_agent_teams.py` — Team creation, workflows
- `test_auth_flows.py` — Authentication flows
- `test_autonomous_flow.py` — Autonomous execution
- `test_billing.py` — Credit deduction, billing
- `test_platform_integration.py` — Cross-service integration
- `test_project_builder.py` — Project builder agent
- `test_rate_limiter.py` — Rate limiting

---

## Known Issues & Gotchas

1. **`rg_llm` is NOT in `requirements.txt`** — It's mounted as a read-only Docker volume from `RG_UnifiedLLMClient/src/rg_llm`. For local dev, you must symlink or copy it.

2. **DB pool size matters** — Production uses `AGENT_ENGINE_DB_POOL_CLASS=null` (NullPool, no persistent connections) because 21+ services share the same PostgreSQL instance with a 100-connection limit. If you see `TooManyConnectionsError`, switch to NullPool.

3. **Startup DDL in `main.py`** — Schema changes are done via raw DDL `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in the startup hook, not Alembic migrations. This is intentional for rapid iteration but means Alembic `versions/` may be out of date.

4. **BYOK key flow** — If `_fetch_user_byok_keys()` fails silently (e.g., auth service down), the agent falls back to system env keys only. If system keys are expired, all providers fail. Check `[BYOK-EXEC]` log prefix.

5. **LLM provider cooldown** — After a 401/403 error, `rg_llm` puts that provider+key on a 30-second cooldown. This is per-process and resets on restart.

6. **`shared/` directory** — Contains shared libraries that are also used by other services. Changes here may affect other containers.

7. **The `executor.py` is 3806 lines** — This is the most critical and complex file. Handle with care. It contains the ReAct loop, all tool handlers, BYOK resolution, credit deduction, safety checks, and learning loop integration.

8. **Advanced routers are wrapped in try/except** — `routers_advanced.py`, `routers_autonomous.py`, `routers_full_autonomy.py`, `routers_max_autonomy.py`, `routers_orchestration.py`, `routers_ultimate.py` — these fail silently on import error. If endpoints return 404, check these imports.

---

**Organization**: [DevSwat-ResonantGenesis](https://github.com/DevSwat-ResonantGenesis) | **Platform**: [resonant.dev-swat.com](https://resonant.dev-swat.com)
