#!/bin/bash
# ============================================================================
# AUDIT: RG_Agent_Engine — Full Agent Inventory & Pipeline Review
# ============================================================================
# Date: Apr 22, 2026
# Reviewer: Cascade
#
# ============================================================================
# AGENT INVENTORY (Production Database)
# ============================================================================
#
# TOTAL AGENTS IN DB: 237
#
# ┌───────────────────────────────────────────────────────┐
# │  BY SOURCE                                            │
# │  cloud:       228  (platform-hosted)                  │
# │  federated:     9  (external/OpenClaw)                │
# ├───────────────────────────────────────────────────────┤
# │  BY OWNERSHIP                                         │
# │  user-created:  235                                   │
# │  system (null):   2  (test-direct-llm, test-openai)   │
# ├───────────────────────────────────────────────────────┤
# │  BY STATUS                                            │
# │  active:     28                                       │
# │  archived:  209                                       │
# ├───────────────────────────────────────────────────────┤
# │  UNIQUE USERS: 16                                     │
# └───────────────────────────────────────────────────────┘
#
# ---- SYSTEM / BUILT-IN AGENTS: 2 ----
# These are "system" agents with NULL user_id (test artifacts):
#   1. test-direct-llm     (cloud, unbounded, active) — created 2026-03-18
#   2. test-openai-routing  (cloud, unbounded, active) — created 2026-03-18
#
# ** NOTE: There are ZERO true "built-in/system" agents registered. **
# ** The 2 null-user agents are old test artifacts, not production system agents. **
# ** Agent Engine has no concept of "system agents" — every agent is user-created. **
#
# ---- FEDERATED AGENTS (External/OpenClaw): 9 ----
#   Active (2):
#     - OpenClaw Federated Agent   (user d85c1fd7, connector v1.0.0) — 2026-04-13
#     - Assistent Local            (user 0a4fbfd4, no connector)     — 2026-04-13
#   Archived (7):
#     - louie-openclaw-agent, test-openclaw-v2, test-fed,
#       openclaw-prod-agent, openclaw-full-flow-v3, openclaw-final-test,
#       Reserch assistant
#
# ---- ACTIVE CLOUD AGENTS (User-Created): 24 ----
#   SmokeTest Publish Agent (x3), Research Assistant, Databot,
#   Testbot, Weatherbot (x2), AEON, HackerNews AI Monitor,
#   SF Events Finder, Sales Lead Nurturer, AI News Collector,
#   TechNewsBot (x3), CryptoTracker, WeatherBot, New Agent,
#   Y Combinator Scraper (x3), Social Media Automation
#
# ============================================================================
# EXECUTION STATS
# ============================================================================
#
# TOTAL SESSIONS: 1,564
#   completed:        734
#   failed:           820
#   cancelled:          6
#   waiting_approval:   3
#   queued:             1
#
# TOP AGENTS BY RUNS:
#   Inbox Zero Pilot                552 runs (221 tool calls, 717 loops)
#   Social Media Monitor            342 runs (1757 tool calls, 8771 loops)
#   Resonant Genesis Twitter Poster 102 runs (320 tool calls, 609 loops)
#   HackerNews AI Monitor            91 runs (572 tool calls, 666 loops)
#   Tech_SanFrancisco_Events_Agent   37 runs
#
# ACTIVE SCHEDULES: 13 (running autonomously)
#   Inbox Zero Pilot:      every 30min (550 runs)
#   Social Media Monitor:  every 1hr  (342 runs)
#   News Briefing Agent:   daily 8am  (15 runs)
#   Daily Morning Checker: daily 7am  (17 runs)
#   + 9 more...
#
# WEBHOOK TRIGGERS: 4
# DISCORD CONNECTIONS: 0 active
# FEDERATED TASKS: 20 (13 completed, 5 failed, 2 pending)
#
# ============================================================================
# A-to-Z AGENT PIPELINE TRACE
# ============================================================================
#
# 1. CREATION
#    Endpoint: POST /agents/
#    File: routers.py → create_agent() (line ~1468)
#    Flow:
#      a. Validate user_id from x-user-id header (gateway auth)
#      b. Credit check via billing service
#      c. DSID-P auto-classification (keyword → domain cluster A/K/L/C/W/S/B/H/P/G/M)
#      d. Pricing tier calculation based on SRR + tools
#      e. Manifest hash computation (SHA-256 of config)
#      f. Agent public hash (SHA-256 of agent_id + user_id)
#      g. Create AgentDefinition row in DB
#      h. Create AgentVersion history row
#      i. Best-effort DSID issuance + blockchain registration
#      j. Return AgentResponse with id, hashes, config
#    STATUS: ✓ WORKING — 237 agents created successfully
#
# 2. CONFIGURATION (tool_mode, model, safety)
#    Model: AgentDefinition (models.py)
#    Fields:
#      - provider: openai/anthropic/groq/google/local
#      - model: e.g. gpt-4o, llama-3.3-70b-versatile
#      - tool_mode: "smart" (classifier picks) | "manual" (only agent.tools)
#      - tools: ARRAY(String) — selected tool IDs
#      - safety_config: JSON (dsidp_cluster, max_tokens_per_run, etc.)
#      - mode: "governed" | "unbounded"
#      - autonomous: Boolean
#    STATUS: ✓ WORKING — agents have diverse configs
#
# 3. TEMPLATE INSTANTIATION
#    Endpoint: GET /agents/templates → list templates
#    Endpoint: POST /agents/templates/{id}/instantiate → create from template
#    Templates: hardcoded list of ~8 templates (Research, Code, Automation, etc.)
#    STATUS: ✓ WIRED — templates return static list, instantiate creates real agent
#
# 4. EXECUTION (Manual Run)
#    Endpoint: POST /agents/{agent_id}/sessions
#    File: routers.py → start_session() (line ~3398)
#    Flow:
#      a. Credit pre-check
#      b. Load AgentDefinition from DB
#      c. Check agent is active
#      d. Merge context (user_id, org_id, agent_hash, user_role)
#      e. If federated → check heartbeat → queue FederatedTask for polling
#      f. If cloud → create AgentSession row (status=initializing)
#      g. Fire background task: _run_agent_session_background()
#         - Semaphore-limited (configurable)
#         - 5-minute timeout
#         - Calls agent_executor.run_loop(session, agent, db)
#      h. Return session_id for SSE polling
#    STATUS: ✓ WORKING — 1564 sessions executed
#
# 5. EXECUTION LOOP (The Brain)
#    File: executor.py → AgentExecutor.run_loop()
#    Flow:
#      a. Set session status = "running"
#      b. Loop (max_steps from safety_config):
#         i.  Neural classifier predicts top-N tools for current goal
#         ii. Build dynamic EXECUTION_FRAME with predicted tools
#         iii. Call LLM (via LLM_SERVICE_URL) with JSON response format
#         iv. Parse action: {tool, input, reasoning, done}
#         v.  Execute tool via _handler_map (74 tool handlers)
#         vi. Record AgentStep (reasoning, tool_name, tool_input, tool_output)
#         vii. Update session (loop_count, tokens_used, tool_calls)
#         viii. If done=true → break
#      c. Set session status = "completed" (or "failed" on error)
#    STATUS: ✓ WORKING — real tool calls executed, steps recorded
#
# 6. TOOL SYSTEM
#    Neural Classifier: 203 tools, 1030 samples, 88.74% accuracy
#    Endpoints:
#      - POST /tools/classifier/predict ✓
#      - POST /tools/classifier/retrain ✓
#      - GET  /tools/classifier/stats ✓
#      - POST /tools/classifier/add-custom-tools ✓
#    Direct tool execution: POST /tools/execute (no session required)
#    Tool list: GET /tools/list (from builtin_tools.py registry)
#    Available tools picker: GET /available-tools
#    STATUS: ✓ WORKING — classifier trained, predict verified live
#
# 7. SCHEDULING (Autonomous Recurring Runs)
#    File: scheduler_daemon.py → polls DB every 60s
#    Tables: agent_schedules (cron or interval-based)
#    Endpoint: POST /agents/{id}/schedules (create schedule)
#    Status: 13 active schedules running, 550+ runs from top scheduler
#    STATUS: ✓ WORKING — agents run autonomously on schedule
#
# 8. FEDERATION (External/OpenClaw Agents)
#    Endpoints:
#      - POST /federation/register → creates agent with source="federated"
#      - POST /federation/heartbeat → connector pings every 3-5s
#      - GET  /federation/tasks/poll → connector picks up tasks
#      - POST /federation/tasks/{id}/step → live step streaming
#      - POST /federation/tasks/{id}/result → submit completion
#      - GET  /federation/agents → list user's federated agents
#      - POST /federation/disconnect/{id} → deactivate
#    Flow: User clicks Run → FederatedTask queued → connector polls → executes locally → submits result
#    Status: 9 federated agents, 20 tasks (13 completed)
#    STATUS: ✓ WORKING — full poll-based federation pipeline operational
#
# 9. SSE STREAMING (Real-time Progress)
#    Endpoint: GET /agents/sessions/{id}/sse
#    Gateway proxy: agent_engine_routes.py → SSE stream proxy
#    File: websocket_streaming.py → SSE event emitter
#    STATUS: ✓ WIRED — gateway proxies SSE with httpx streaming
#
# 10. GATEWAY ROUTING
#     File: RG_Gateway/app/agent_engine_routes.py
#     Prefix: /agents/* → proxy to agent_engine_service:8000
#     Routes:
#       - /agents/autonomous/* → autonomous routes
#       - /agents/sessions/* → session CRUD + SSE
#       - /agents/tools/* → tool routes
#       - /agents/{path} → catch-all proxy
#     STATUS: ✓ WORKING — all routes proxied correctly
#
# ============================================================================
# BUGS & ISSUES FOUND
# ============================================================================
#
# BUG 1: success_count always 0 in agent_schedules
#   All 13 schedules show success_count=0 despite 550+ runs on top agent.
#   The scheduler fires sessions but never updates success_count/failure_count.
#   Impact: Analytics are broken for scheduled runs.
#   Fix: scheduler_daemon.py needs to track session outcome and update counts.
#
# BUG 2: 53% failure rate (820/1564 sessions)
#   Over half of all sessions end in "failed" status.
#   Could be: timeout (5-min limit), tool errors, LLM errors, or billing blocks.
#   Impact: User experience degraded.
#   Investigate: Need to check error_message distribution in failed sessions.
#
# BUG 3: 2 system test agents still in DB with NULL user_id
#   test-direct-llm and test-openai-routing are test artifacts.
#   They have is_active=True and could show in admin queries.
#   Impact: Low (user-scoped queries filter them out).
#   Fix: Archive or delete them.
#
# BUG 4: Gateway proxy doesn't forward all auth headers
#   agent_engine_routes.py only forwards x-user-id, x-org-id, content-type.
#   Missing: x-user-role, x-is-superuser, x-unlimited-credits.
#   Impact: Agent Engine receives empty role headers → falls back to "user".
#   Privileged users may not bypass credit checks correctly.
#   Fix: Forward all x-* headers in proxy_to_agent_engine().
#
# BUG 5: routers_execution.py duplicate execute endpoint
#   Both routers_execution.py (/execution/agents/{id}/execute) and
#   routers.py (/{agent_id}/sessions) handle agent execution.
#   The /execution/* path may be dead or redundant.
#   Impact: Confusion about which endpoint to use.
#
#
# ============================================================================
# SCHEDULE OWNERSHIP (All 13 active schedules)
# ============================================================================
#
# ** ALL schedules are user-owned. ZERO system-owned schedules. **
#
# Owner: d85c1fd7 (louie) — 3 schedules
#   1. Inbox Zero Pilot          | every 30min  | 552 runs
#   2. Social Media Monitor      | every 1hr    | 343 runs
#   3. Sales Lead Nurturer       | daily        |  14 runs
#
# Owner: 0a4fbfd4 — 10 schedules
#   1. Daily Morning Checker     | cron 7am     |  17 runs
#   2. News Briefing Agent       | cron 8am     |  15 runs
#   3. AI Trends Researcher      | daily        |  14 runs
#   4. Twitter Post Creation     | daily        |  14 runs
#   5. Web Scraping Agent        | daily        |  14 runs
#   6. Daily Business News       | daily        |  14 runs
#   7. Tech SF Events (x2)      | daily        |  14 runs each
#   8. Test Sample "Weather SF"  | cron 9am     |  11 runs
#   9. Research assistant        | cron midnight|   9 runs
#
# ============================================================================
# FAILURE ANALYSIS (820 failed sessions)
# ============================================================================
#
#  182 | Infinite loop detected by verifier
#  131 | Failed to create plan
#  118 | Max iterations (50) + oscillation detected
#   89 | No error message (orphan cleanup?)
#   71 | Max tokens (100000) exceeded
#   44 | Timeout (5-min limit)
#   37 | SQL injection blocked by safety
#   30 | Max iterations + same step repeated
#   29 | Bulk cleanup: stuck in WAITING_APPROVAL (pre-fix)
#   22 | Max iterations + consecutive errors + oscillation
#   11 | Maximum loop iterations reached
#    9 | Max iterations + oscillation (tool_call pattern)
#    6 | Max iterations + too many errors + oscillation
#    4 | 'tool_name' key error (malformed LLM response)
#    3 | LLM 429 rate limit (all providers failed)
#    2 | Semaphore (all agent slots occupied)
#    + misc verification rejections
#
# ROOT CAUSE SUMMARY:
#   ~60% — Agent loop issues (infinite loops, oscillation, max iterations)
#   ~15% — Plan creation failures
#   ~9%  — Safety blocks (token limit, SQL injection)
#   ~5%  — Timeouts
#   ~4%  — Orphan cleanup (legacy)
#   ~3%  — LLM response issues (malformed JSON, 429 rate limits)
#   ~2%  — Concurrency limits
#
# ============================================================================
# FIX PLAN — CHECKLIST
# ============================================================================
#
# [✓] FIX 1: Scheduler success_count/failure_count always 0
#     Root cause: _fire_session_inner() never updates schedule counts after run
#     File: scheduler_daemon.py
#     Fix: After run_loop completes, check session.status and UPDATE
#          success_count or failure_count on the schedule row.
#     Status: FIXED — deployed
#
# [✓] FIX 2: Gateway proxy missing auth headers
#     Root cause: proxy_to_agent_engine() only forwards x-user-id, x-org-id
#     File: RG_Gateway/app/agent_engine_routes.py
#     Fix: Forward all x-* headers (x-user-role, x-is-superuser, x-unlimited-credits)
#     Status: FIXED — deployed
#
# [✓] FIX 3: Clean 2 test artifacts with NULL user_id
#     IDs: 6ec1df5c, a8f97682
#     Fix: Archive them (set archived_at, is_active=false)
#     Status: FIXED — cleaned via SQL
#
# [✓] FIX 4: Audit duplicate execute endpoint
#     routers_execution.py /execution/agents/{id}/execute vs routers.py /{id}/sessions
#     Analysis: routers_execution.py is a SECONDARY path used by inter-service calls
#               and the /execution/* prefix. The primary UI path is /{id}/sessions.
#               Both are live and used — not dead code.
#     Status: REVIEWED — both endpoints serve different consumers, kept
#
# [ ] FIX 5: Reduce 53% failure rate (future work)
#     Key areas:
#     - Infinite loop / oscillation detection needs earlier bailout
#     - Plan creation failures need better fallback
#     - Token limit (100k) may be too low for complex tasks
#     - Consider increasing 5-min timeout for scheduled agents
#
# ============================================================================
echo "This is a report file. Read it, don't run it."
