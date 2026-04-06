"""
Webhook Trigger System
======================

Enables agents to react to external events via webhooks.
Provides CRUD for webhook triggers and public endpoints for receiving webhooks.
"""

import hmac
import hashlib
import json
import logging
import secrets
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, HTTPException, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

PLATFORM_DOMAIN = os.getenv("PLATFORM_DOMAIN", "resonantgenesis.xyz")


# ============================================
# Pydantic Models
# ============================================

class WebhookPayload(BaseModel):
    """Generic webhook payload."""
    event: str = "incoming"
    data: Dict[str, Any] = {}
    timestamp: Optional[str] = None


class WebhookResponse(BaseModel):
    """Webhook response."""
    status: str
    session_id: Optional[str] = None
    message: Optional[str] = None


class CreateWebhookTriggerRequest(BaseModel):
    """Request to create a webhook trigger for an agent."""
    name: Optional[str] = None
    goal_template: str = "Process incoming webhook event: {event}"
    webhook_secret: Optional[str] = None
    debounce_seconds: int = 5


class WebhookTriggerInfo(BaseModel):
    """Webhook trigger info returned to user."""
    id: str
    agent_id: str
    agent_name: Optional[str] = None
    name: str
    enabled: bool
    webhook_url: str
    webhook_path: str
    webhook_secret: Optional[str] = None
    trigger_count: int = 0
    last_triggered_at: Optional[str] = None
    created_at: Optional[str] = None


# ============================================
# Helpers
# ============================================

def verify_webhook_signature(
    payload: bytes,
    signature: str,
    secret: str,
) -> bool:
    """Verify webhook signature using HMAC-SHA256."""
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


def _build_public_url(webhook_path: str) -> str:
    """Build the full public webhook URL."""
    return f"https://{PLATFORM_DOMAIN}/api/v1{webhook_path}"


def _try_queue_execution(agent_id: str, goal: str, context: dict, user_id: str) -> Optional[str]:
    """Queue agent execution. Uses asyncio background task (no Celery needed).
    Returns session_id or None."""
    import asyncio
    session_id = str(uuid4())

    async def _run():
        try:
            from .scheduler_daemon import _fire_session
            await _fire_session(
                agent_id=agent_id,
                goal=goal,
                context=context,
                user_id=user_id,
                source=f"webhook:{agent_id}",
            )
        except Exception as e:
            logger.error(f"Webhook agent execution failed for {agent_id}: {e}")

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
        return session_id
    except RuntimeError:
        logger.warning("No running event loop for webhook execution")
        return None


# ============================================
# CRUD Endpoints (authenticated, via gateway)
# ============================================

@router.post("/agent/{agent_id}/create")
async def create_webhook_trigger(
    agent_id: str,
    body: CreateWebhookTriggerRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Create a webhook trigger for an agent. Returns the public webhook URL."""
    user_id = request.headers.get("x-user-id", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing x-user-id header")

    # Verify agent exists and belongs to user
    agent_row = await db.execute(
        text("SELECT id, name, user_id FROM agent_definitions WHERE id = :aid"),
        {"aid": agent_id},
    )
    agent = agent_row.mappings().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Check if trigger already exists
    existing = await db.execute(
        text("SELECT id FROM agent_triggers WHERE agent_id = :aid AND trigger_type = 'webhook' AND enabled = true"),
        {"aid": agent_id},
    )
    if existing.first():
        raise HTTPException(status_code=409, detail="Webhook trigger already exists for this agent. Delete the existing one first.")

    trigger_id = str(uuid4())
    webhook_path = f"/webhooks/agent/{agent_id}/trigger"
    trigger_name = body.name or f"Webhook for {agent.get('name', 'Agent')}"
    wh_secret = body.webhook_secret or secrets.token_hex(32)

    await db.execute(
        text("""
            INSERT INTO agent_triggers (id, agent_id, user_id, name, trigger_type, enabled,
                webhook_secret, webhook_path, goal_template, debounce_seconds)
            VALUES (:id, :agent_id, :user_id, :name, 'webhook', true,
                :secret, :path, :goal, :debounce)
        """),
        {
            "id": trigger_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "name": trigger_name,
            "secret": wh_secret,
            "path": webhook_path,
            "goal": body.goal_template,
            "debounce": body.debounce_seconds,
        },
    )
    await db.commit()

    public_url = _build_public_url(webhook_path)
    logger.info(f"Created webhook trigger {trigger_id} for agent {agent_id}: {public_url}")

    return {
        "id": trigger_id,
        "agent_id": agent_id,
        "agent_name": agent.get("name"),
        "name": trigger_name,
        "enabled": True,
        "webhook_url": public_url,
        "webhook_path": webhook_path,
        "webhook_secret": wh_secret,
        "trigger_count": 0,
        "message": f"Webhook created. Use this URL to receive events: {public_url}",
    }


@router.get("/agent/{agent_id}/list")
async def list_agent_webhooks(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """List all webhook triggers for an agent."""
    result = await db.execute(
        text("""
            SELECT t.id, t.agent_id, t.name, t.enabled, t.webhook_path,
                   t.webhook_secret, t.trigger_count, t.last_triggered_at, t.created_at,
                   a.name as agent_name
            FROM agent_triggers t
            LEFT JOIN agent_definitions a ON a.id = t.agent_id
            WHERE t.agent_id = :aid AND t.trigger_type = 'webhook'
            ORDER BY t.created_at DESC
        """),
        {"aid": agent_id},
    )
    rows = result.mappings().all()

    triggers = []
    for row in rows:
        triggers.append({
            "id": str(row["id"]),
            "agent_id": str(row["agent_id"]),
            "agent_name": row.get("agent_name"),
            "name": row["name"],
            "enabled": row["enabled"],
            "webhook_url": _build_public_url(row["webhook_path"]),
            "webhook_path": row["webhook_path"],
            "webhook_secret": row.get("webhook_secret"),
            "trigger_count": row.get("trigger_count", 0),
            "last_triggered_at": row["last_triggered_at"].isoformat() if row.get("last_triggered_at") else None,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        })

    return {"agent_id": agent_id, "triggers": triggers, "count": len(triggers)}


@router.get("/user/list")
async def list_user_webhooks(
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """List all webhook triggers for a user (across all agents)."""
    user_id = request.headers.get("x-user-id", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing x-user-id header")

    result = await db.execute(
        text("""
            SELECT t.id, t.agent_id, t.name, t.enabled, t.webhook_path,
                   t.webhook_secret, t.trigger_count, t.last_triggered_at, t.created_at,
                   t.goal_template, t.context_template,
                   a.name as agent_name
            FROM agent_triggers t
            LEFT JOIN agent_definitions a ON a.id = t.agent_id
            WHERE t.user_id = :uid AND t.trigger_type = 'webhook'
            ORDER BY t.created_at DESC
        """),
        {"uid": user_id},
    )
    rows = result.mappings().all()

    triggers = []
    for row in rows:
        triggers.append({
            "id": str(row["id"]),
            "agent_id": str(row["agent_id"]),
            "agent_name": row.get("agent_name"),
            "name": row["name"],
            "enabled": row["enabled"],
            "webhook_url": _build_public_url(row["webhook_path"]),
            "webhook_path": row["webhook_path"],
            "webhook_secret": row.get("webhook_secret"),
            "trigger_count": row.get("trigger_count", 0),
            "last_triggered_at": row["last_triggered_at"].isoformat() if row.get("last_triggered_at") else None,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "goal_template": row.get("goal_template"),
            "context_template": row.get("context_template"),
        })

    return {"user_id": user_id, "triggers": triggers, "count": len(triggers)}


@router.delete("/trigger/{trigger_id}")
async def delete_webhook_trigger(
    trigger_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Delete a webhook trigger."""
    user_id = request.headers.get("x-user-id", "")
    result = await db.execute(
        text("DELETE FROM agent_triggers WHERE id = :tid AND (user_id = :uid OR :uid = '') RETURNING id"),
        {"tid": trigger_id, "uid": user_id},
    )
    deleted = result.first()
    if not deleted:
        raise HTTPException(status_code=404, detail="Trigger not found")
    await db.commit()
    return {"status": "deleted", "trigger_id": trigger_id}


@router.patch("/trigger/{trigger_id}/toggle")
async def toggle_webhook_trigger(
    trigger_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Enable or disable a webhook trigger."""
    result = await db.execute(
        text("UPDATE agent_triggers SET enabled = NOT enabled WHERE id = :tid RETURNING id, enabled"),
        {"tid": trigger_id},
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Trigger not found")
    await db.commit()
    return {"trigger_id": trigger_id, "enabled": row[1]}


# ============================================
# Public Webhook Receiver Endpoints (no auth)
# ============================================

@router.post("/agent/{agent_id}/trigger", response_model=WebhookResponse)
async def trigger_agent_webhook(
    agent_id: str,
    request: Request,
    x_webhook_signature: Optional[str] = Header(None),
    x_internal_service_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_session),
):
    """
    Trigger an agent via webhook.
    This is the PUBLIC endpoint that external services (Discord, GitHub, etc.) call.
    
    Internal services can bypass signature verification by providing valid
    X-Internal-Service-Key header (must match INTERNAL_SERVICE_KEY env var).
    """
    webhook_path = f"/webhooks/agent/{agent_id}/trigger"

    # Find trigger by path
    result = await db.execute(
        text("""
            SELECT id, agent_id, user_id, webhook_secret, goal_template,
                   context_template, debounce_seconds, last_triggered_at, trigger_count
            FROM agent_triggers
            WHERE webhook_path = :path AND enabled = true AND trigger_type = 'webhook'
        """),
        {"path": webhook_path},
    )
    trigger = result.mappings().first()

    if not trigger:
        raise HTTPException(status_code=404, detail="Webhook trigger not found for this agent")

    # Internal services can bypass signature verification with valid INTERNAL_SERVICE_KEY
    is_internal = False
    if x_internal_service_key and settings.INTERNAL_SERVICE_KEY:
        is_internal = x_internal_service_key == settings.INTERNAL_SERVICE_KEY

    # Verify signature if secret is configured (skip for authenticated internal callers)
    if trigger.get("webhook_secret") and not is_internal:
        if not x_webhook_signature:
            raise HTTPException(status_code=401, detail="Missing webhook signature")
        body_bytes = await request.body()
        if not verify_webhook_signature(body_bytes, x_webhook_signature, trigger["webhook_secret"]):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Check debounce (skip for internal service calls)
    if not is_internal and trigger.get("last_triggered_at"):
        elapsed = (datetime.now(timezone.utc) - trigger["last_triggered_at"]).total_seconds()
        if elapsed < trigger.get("debounce_seconds", 5):
            return WebhookResponse(
                status="debounced",
                message=f"Trigger debounced, wait {trigger['debounce_seconds'] - elapsed:.0f}s",
            )

    # Parse incoming body
    try:
        body_bytes = await request.body()
        event_data = json.loads(body_bytes) if body_bytes else {}
    except json.JSONDecodeError:
        event_data = {"raw_body": (await request.body()).decode("utf-8", errors="replace")}

    if "event" not in event_data:
        event_data = {"event": "incoming", "data": event_data}

    # Render goal
    goal_template = trigger.get("goal_template", "Process incoming webhook event: {event}")
    try:
        goal = goal_template.format(
            event=event_data.get("event", ""),
            data=event_data.get("data", {}),
        )
    except (KeyError, IndexError):
        goal = goal_template

    context = {
        "webhook_event": event_data.get("event"),
        "webhook_data": event_data.get("data", {}),
        "webhook_timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Try to queue agent execution (graceful if Celery is down)
    session_id = _try_queue_execution(
        agent_id=str(trigger["agent_id"]),
        goal=goal,
        context=context,
        user_id=trigger.get("user_id", ""),
    )

    # Update trigger stats
    await db.execute(
        text("""
            UPDATE agent_triggers
            SET last_triggered_at = :now, trigger_count = trigger_count + 1
            WHERE id = :tid
        """),
        {"now": datetime.now(timezone.utc), "tid": str(trigger["id"])},
    )
    await db.commit()

    logger.info(f"Webhook triggered agent {agent_id}, session: {session_id}")

    if session_id:
        return WebhookResponse(status="triggered", session_id=session_id)
    else:
        return WebhookResponse(
            status="received",
            message="Webhook received and recorded. Agent execution queue is currently unavailable.",
        )


@router.post("/github/{trigger_id}", response_model=WebhookResponse)
async def github_webhook(
    trigger_id: str,
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None),
    x_github_event: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_session),
):
    """Handle GitHub webhooks."""
    result = await db.execute(
        text("""
            SELECT id, agent_id, user_id, webhook_secret, goal_template,
                   debounce_seconds, last_triggered_at
            FROM agent_triggers WHERE id = :tid AND enabled = true
        """),
        {"tid": trigger_id},
    )
    trigger = result.mappings().first()

    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")

    # Verify GitHub signature
    if trigger.get("webhook_secret") and x_hub_signature_256:
        body_bytes = await request.body()
        if not verify_webhook_signature(body_bytes, x_hub_signature_256, trigger["webhook_secret"]):
            raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse payload
    try:
        body_bytes = await request.body()
        payload = json.loads(body_bytes) if body_bytes else {}
    except json.JSONDecodeError:
        payload = {}

    event_data = {"event": x_github_event or "unknown", "data": payload}
    goal_template = trigger.get("goal_template", "Process GitHub {event} event")
    try:
        goal = goal_template.format(event=event_data["event"], data=payload)
    except (KeyError, IndexError):
        goal = goal_template

    context = {
        "webhook_event": event_data["event"],
        "webhook_data": payload,
        "webhook_timestamp": datetime.now(timezone.utc).isoformat(),
        "github_event": x_github_event,
    }

    session_id = _try_queue_execution(
        agent_id=str(trigger["agent_id"]),
        goal=goal,
        context=context,
        user_id=trigger.get("user_id", ""),
    )

    await db.execute(
        text("UPDATE agent_triggers SET last_triggered_at = :now, trigger_count = trigger_count + 1 WHERE id = :tid"),
        {"now": datetime.now(timezone.utc), "tid": str(trigger["id"])},
    )
    await db.commit()

    return WebhookResponse(
        status="triggered" if session_id else "received",
        session_id=session_id,
    )


# ============================================
# Auto-create helper (called from skill_executor)
# ============================================

async def auto_create_webhook_trigger(
    agent_id: str,
    agent_name: str,
    user_id: str,
    db: AsyncSession,
) -> Optional[Dict[str, str]]:
    """
    Auto-create a webhook trigger for a newly created agent.
    Returns {"webhook_url": "...", "webhook_path": "...", "trigger_id": "..."} or None.
    """
    try:
        # Check if one already exists
        existing = await db.execute(
            text("SELECT id FROM agent_triggers WHERE agent_id = :aid AND trigger_type = 'webhook' AND enabled = true"),
            {"aid": agent_id},
        )
        if existing.first():
            return None

        trigger_id = str(uuid4())
        webhook_path = f"/webhooks/agent/{agent_id}/trigger"
        wh_secret = secrets.token_hex(32)

        await db.execute(
            text("""
                INSERT INTO agent_triggers (id, agent_id, user_id, name, trigger_type, enabled,
                    webhook_secret, webhook_path, goal_template, debounce_seconds)
                VALUES (:id, :agent_id, :user_id, :name, 'webhook', true,
                    :secret, :path, :goal, 5)
            """),
            {
                "id": trigger_id,
                "agent_id": agent_id,
                "user_id": user_id,
                "name": f"Webhook for {agent_name}",
                "secret": wh_secret,
                "path": webhook_path,
                "goal": f"Process incoming webhook event for {agent_name}: {{event}}",
            },
        )
        await db.commit()

        public_url = _build_public_url(webhook_path)
        logger.info(f"Auto-created webhook trigger {trigger_id} for agent {agent_id}: {public_url}")
        return {
            "trigger_id": trigger_id,
            "webhook_url": public_url,
            "webhook_path": webhook_path,
            "webhook_secret": wh_secret,
        }
    except Exception as e:
        logger.warning(f"Failed to auto-create webhook trigger for agent {agent_id}: {e}")
        return None
