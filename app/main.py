import sys
from pathlib import Path
import logging
import json
import uuid
import os
import asyncio

# Add shared modules to path
SHARED_PATH = Path(__file__).resolve().parents[2] / "shared"
if str(SHARED_PATH) not in sys.path:
    sys.path.insert(0, str(SHARED_PATH))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

# Deterministic sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Single service entrypoint
app = FastAPI(
    title="Agent Engine Service",
    description="AgentOS - Autonomous Agent Management for Genesis2026",
    version="1.0.0"
    # redirect_slashes=True is the default - gateway now calls with correct trailing slash
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers (routers already have /agents prefix)
from .routers import router as agents_router
from .routers import tools_router, safety_router
from .routers_teams import router as teams_router
from .settings_routes import router as settings_router
from .routers_billing import router as billing_router
from .routers_execution import router as execution_router
from .routers_autonomy import (
    autonomy_router,
    wallet_router,
    goals_router,
    negotiation_router,
    approval_router,
    dsidp_router,
)
from .routers_chat_bridge import router as chat_bridge_router
from .routers_ssh import router as ssh_router
from .routers_subdir import project_builder_router
from .webhooks import router as webhooks_router
from .routers_discord import router as discord_router
# AGENTIC CHAT REMOVED — now standalone service at RG_Registered_Users_Agentic_Chat
# PUBLIC CHAT REMOVED — now standalone service at RG_Public/Guest_Agentic_Chat
from .db import engine

logger = logging.getLogger(__name__)

app.include_router(agents_router)
app.include_router(tools_router)
app.include_router(safety_router)
app.include_router(teams_router)
app.include_router(settings_router)
app.include_router(billing_router)
app.include_router(execution_router)
app.include_router(chat_bridge_router)
app.include_router(ssh_router)
app.include_router(project_builder_router)
app.include_router(webhooks_router)
app.include_router(discord_router)
# agentic_chat_router REMOVED — standalone service at RG_Registered_Users_Agentic_Chat
# public_chat_router REMOVED — standalone service at RG_Public/Guest_Agentic_Chat

# Autonomy & goal management routers
# - Autonomy endpoints are mounted at root (gateway proxies /autonomy/*)
# - Goals endpoints are mounted under /agents so UI calls /agents/goals/*
app.include_router(autonomy_router)
app.include_router(wallet_router)
app.include_router(negotiation_router)
app.include_router(approval_router)
app.include_router(dsidp_router)
app.include_router(goals_router, prefix="/agents")

# Startup hook to ensure safety_rules.parameters column exists
@app.on_event("startup")
async def ensure_schema():
    ddls = [
        "ALTER TABLE safety_rules ADD COLUMN IF NOT EXISTS parameters JSONB",
        "ALTER TABLE safety_rules ADD COLUMN IF NOT EXISTS applies_to_agents UUID[]",
        "ALTER TABLE safety_rules ADD COLUMN IF NOT EXISTS applies_to_tools TEXT[]",
        "ALTER TABLE tool_definitions ADD COLUMN IF NOT EXISTS description TEXT",
        "ALTER TABLE tool_definitions ADD COLUMN IF NOT EXISTS category VARCHAR(64)",
        "ALTER TABLE tool_definitions ADD COLUMN IF NOT EXISTS parameters_schema JSON",
        "ALTER TABLE tool_definitions ADD COLUMN IF NOT EXISTS handler_type VARCHAR(32)",
        "ALTER TABLE tool_definitions ADD COLUMN IF NOT EXISTS handler_config JSON",
        "ALTER TABLE tool_definitions ADD COLUMN IF NOT EXISTS risk_level VARCHAR(16) DEFAULT 'low'",
        "ALTER TABLE tool_definitions ADD COLUMN IF NOT EXISTS requires_approval BOOLEAN DEFAULT FALSE",
        "ALTER TABLE tool_definitions ADD COLUMN IF NOT EXISTS allowed_contexts TEXT[]",
        "ALTER TABLE tool_definitions ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
        "ALTER TABLE tool_definitions ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()",
        "ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS goal TEXT",
        "ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS steps JSON",
        "ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS current_step_index INTEGER DEFAULT 0",
        "ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS status VARCHAR(32) DEFAULT 'active'",
        "ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS revision_count INTEGER DEFAULT 0",
        "ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS parent_plan_id UUID",
        "ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()",
        "ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
        "ALTER TABLE agent_plans ALTER COLUMN plan_data SET DEFAULT '{}'::jsonb",
        "ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS input_data JSON",
        "ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS output_data JSON",
        "ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS reasoning TEXT",
        "ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS tool_name VARCHAR(128)",
        "ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS tool_input JSON",
        "ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS tool_output JSON",
        "ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS safety_check_passed BOOLEAN DEFAULT TRUE",
        "ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS safety_violations TEXT[]",
        "ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS required_approval BOOLEAN DEFAULT FALSE",
        "ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS approval_status VARCHAR(32)",
        "ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS tokens_used INTEGER DEFAULT 0",
        "ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS duration_ms INTEGER",
        "ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()",
        "ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
        "ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS tool_mode VARCHAR(16) DEFAULT 'smart'",
        # Webhook triggers table
        """CREATE TABLE IF NOT EXISTS agent_triggers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id UUID NOT NULL REFERENCES agent_definitions(id) ON DELETE CASCADE,
            user_id VARCHAR(255),
            name VARCHAR(255) NOT NULL,
            trigger_type VARCHAR(50) NOT NULL DEFAULT 'webhook',
            enabled BOOLEAN DEFAULT TRUE,
            webhook_secret VARCHAR(255),
            webhook_path VARCHAR(255),
            watch_path VARCHAR(500),
            file_patterns JSON DEFAULT '[]'::json,
            message_topic VARCHAR(255),
            message_filter JSON DEFAULT '{}'::json,
            goal_template TEXT NOT NULL DEFAULT 'Process incoming webhook event: {event}',
            context_template JSON DEFAULT '{}'::json,
            debounce_seconds INTEGER DEFAULT 5,
            last_triggered_at TIMESTAMPTZ,
            trigger_count INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_agent_triggers_agent_id ON agent_triggers(agent_id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_triggers_webhook_path ON agent_triggers(webhook_path)",
        # Discord connections table (multi-tenant: guild → agent mapping)
        """CREATE TABLE IF NOT EXISTS discord_connections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id VARCHAR(255) NOT NULL,
            agent_id UUID NOT NULL REFERENCES agent_definitions(id) ON DELETE CASCADE,
            guild_id VARCHAR(64) NOT NULL,
            guild_name VARCHAR(255),
            channel_id VARCHAR(64),
            channel_name VARCHAR(255),
            enabled BOOLEAN DEFAULT TRUE,
            respond_to_mentions BOOLEAN DEFAULT TRUE,
            respond_to_dms BOOLEAN DEFAULT TRUE,
            respond_to_all BOOLEAN DEFAULT FALSE,
            custom_system_prompt TEXT,
            message_count INTEGER DEFAULT 0,
            last_message_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_discord_conn_guild_channel ON discord_connections(guild_id, COALESCE(channel_id, '__all__')) WHERE enabled = true",
        "CREATE INDEX IF NOT EXISTS idx_discord_conn_user ON discord_connections(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_discord_conn_guild ON discord_connections(guild_id)",
        # Agent schedules table (for scheduler daemon)
        """CREATE TABLE IF NOT EXISTS agent_schedules (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id UUID NOT NULL REFERENCES agent_definitions(id) ON DELETE CASCADE,
            user_id VARCHAR(255),
            name VARCHAR(255) NOT NULL,
            description TEXT,
            enabled BOOLEAN DEFAULT TRUE,
            cron_expression VARCHAR(100),
            interval_seconds INTEGER,
            goal TEXT NOT NULL,
            context JSON DEFAULT '{}'::json,
            max_retries INTEGER DEFAULT 3,
            timeout_seconds INTEGER DEFAULT 3600,
            last_run_at TIMESTAMPTZ,
            next_run_at TIMESTAMPTZ,
            run_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_agent_schedules_agent ON agent_schedules(agent_id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_schedules_next_run ON agent_schedules(next_run_at) WHERE enabled = true",
        # Workflow triggers table (for scheduler daemon + webhook triggers)
        """CREATE TABLE IF NOT EXISTS workflow_triggers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id UUID NOT NULL,
            name VARCHAR(128) NOT NULL,
            trigger_type VARCHAR(32) NOT NULL,
            config JSON NOT NULL DEFAULT '{}'::json,
            cron_expression VARCHAR(64),
            next_run_at TIMESTAMPTZ,
            webhook_secret VARCHAR(128),
            event_type VARCHAR(64),
            event_filter JSON,
            input_template JSON,
            is_active BOOLEAN DEFAULT TRUE,
            last_triggered_at TIMESTAMPTZ,
            trigger_count INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_workflow_triggers_agent ON workflow_triggers(agent_id)",
        "CREATE INDEX IF NOT EXISTS idx_workflow_triggers_next_run ON workflow_triggers(next_run_at) WHERE is_active = true AND trigger_type = 'schedule'",
        # Anomaly triggers table (Phase 3.4 — persist across restarts)
        """CREATE TABLE IF NOT EXISTS anomaly_triggers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(255) NOT NULL,
            subsystem VARCHAR(128),
            severity VARCHAR(32) DEFAULT 'critical',
            agent_id UUID NOT NULL,
            goal_template TEXT DEFAULT 'Investigate and resolve {subsystem} anomaly: {message}',
            cooldown_seconds INTEGER DEFAULT 300,
            enabled BOOLEAN DEFAULT TRUE,
            created_by VARCHAR(255),
            last_fired_at TIMESTAMPTZ,
            fire_count INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_anomaly_triggers_enabled ON anomaly_triggers(enabled) WHERE enabled = true",
        # Agent wallets table (Phase 3.3 — persist across restarts)
        """CREATE TABLE IF NOT EXISTS agent_wallets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id UUID NOT NULL UNIQUE,
            wallet_id VARCHAR(128) NOT NULL,
            addresses JSON DEFAULT '{}'::json,
            balances JSON DEFAULT '{"RGT": "100.0"}'::json,
            staked JSON DEFAULT '{}'::json,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_wallets_agent ON agent_wallets(agent_id)",
        # Wallet transactions table
        """CREATE TABLE IF NOT EXISTS wallet_transactions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            wallet_id VARCHAR(128) NOT NULL,
            agent_id UUID NOT NULL,
            tx_type VARCHAR(32) NOT NULL,
            amount DECIMAL(20,8) NOT NULL,
            currency VARCHAR(16) DEFAULT 'RGT',
            purpose TEXT,
            counterparty_agent_id UUID,
            balance_after DECIMAL(20,8),
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wallet_tx_agent ON wallet_transactions(agent_id)",
        # Agent executions audit table
        """CREATE TABLE IF NOT EXISTS agent_executions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id UUID REFERENCES agent_sessions(id),
            schedule_id UUID,
            trigger_id UUID,
            execution_type VARCHAR(50) NOT NULL DEFAULT 'manual',
            celery_task_id VARCHAR(255),
            status VARCHAR(50) DEFAULT 'pending',
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            duration_ms INTEGER,
            steps_executed INTEGER DEFAULT 0,
            output JSON,
            error TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
    ]

    async with engine.begin() as conn:
        for ddl in ddls:
            try:
                await conn.execute(text(ddl))
                logger.info(f"Schema guard OK: {ddl}")
            except Exception as e:
                logger.warning(f"Schema guard failed for '{ddl}': {e}")

        # NOTE: Built-in tool seeds removed — all tools now live in
        # rg_tool_registry/builtin_tools.py (unified registry, 137 tools).
        # The tool_definitions DB table is only used for user-created custom
        # tools (HTTP/webhook) via /tools/custom endpoints.


@app.on_event("startup")
async def maybe_start_full_autonomy():
    if os.getenv("AGENT_ENGINE_ENABLE_AUTO_STARTUP", "false").lower() != "true":
        return

    try:
        from .auto_startup import auto_startup

        asyncio.create_task(auto_startup())
        logger.info("Auto-startup enabled: starting full autonomy system")
    except Exception as e:
        logger.error(f"Failed to start auto-startup: {e}")


@app.on_event("shutdown")
async def maybe_stop_full_autonomy():
    if os.getenv("AGENT_ENGINE_ENABLE_AUTO_STARTUP", "false").lower() != "true":
        return

    try:
        from .auto_startup import auto_shutdown

        await auto_shutdown()
        logger.info("Auto-startup enabled: full autonomy system shutdown complete")
    except Exception as e:
        logger.error(f"Failed to shutdown auto-startup: {e}")


@app.on_event("startup")
async def maybe_start_scheduler():
    if os.getenv("AGENT_ENGINE_ENABLE_SCHEDULER", "true").lower() != "true":
        return
    try:
        from .scheduler_daemon import start_scheduler
        asyncio.create_task(start_scheduler())
        logger.info("Scheduler daemon enabled: polling for due schedules/triggers")
    except Exception as e:
        logger.error(f"Failed to start scheduler daemon: {e}")


@app.on_event("shutdown")
async def maybe_stop_scheduler():
    if os.getenv("AGENT_ENGINE_ENABLE_SCHEDULER", "true").lower() != "true":
        return
    try:
        from .scheduler_daemon import stop_scheduler
        await stop_scheduler()
        logger.info("Scheduler daemon stopped")
    except Exception as e:
        logger.error(f"Failed to stop scheduler daemon: {e}")


@app.on_event("startup")
async def maybe_start_autonomous_daemon():
    if os.getenv("AGENT_ENGINE_ENABLE_AUTONOMOUS_DAEMON", "false").lower() != "true":
        return

    try:
        from .autonomous_daemon import start_autonomous_daemon

        asyncio.create_task(start_autonomous_daemon())
        logger.info("Autonomous daemon enabled: starting internal autonomous daemon")
    except Exception as e:
        logger.error(f"Failed to start autonomous daemon: {e}")


@app.on_event("shutdown")
async def maybe_stop_autonomous_daemon():
    if os.getenv("AGENT_ENGINE_ENABLE_AUTONOMOUS_DAEMON", "false").lower() != "true":
        return

    try:
        from .autonomous_daemon import stop_autonomous_daemon

        await stop_autonomous_daemon()
        logger.info("Autonomous daemon enabled: internal autonomous daemon shutdown complete")
    except Exception as e:
        logger.error(f"Failed to shutdown autonomous daemon: {e}")

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "agent_engine_service"}

# Root endpoint
@app.get("/")
async def root():
    return {"message": "Agent Engine Service is running"}

# Service-specific endpoint
@app.get("/api/v1/status")
async def status():
    return {"service": "agent_engine_service", "status": "active", "version": "1.0.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8009)
