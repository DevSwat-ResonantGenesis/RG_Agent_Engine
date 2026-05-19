"""
Webhook Trigger System
======================

Enables agents to react to external events via webhooks.
Provides CRUD for webhook triggers and public endpoints for receiving webhooks.

Supported providers:
  - GitHub  (HMAC-SHA256 via X-Hub-Signature-256)
  - Stripe  (HMAC-SHA256 via Stripe-Signature, t=timestamp,v1=sig)
  - Slack   (HMAC-SHA256 via X-Slack-Signature + URL verification challenge)
  - Generic (X-Webhook-Signature header or plain X-Webhook-Secret)

Features:
  - Provider-specific signature verification
  - Rich event parsing (extracts PR titles, commit messages, amounts, etc.)
  - Goal template interpolation with event fields
  - Debounce (configurable per trigger)
  - Full event audit log (webhook_event_log table)
  - Setup instructions per provider
"""

import hmac
import hashlib
import json
import logging
import secrets
import os
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

PLATFORM_DOMAIN = os.getenv("PLATFORM_DOMAIN", "dev-swat.com")


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
    provider: str = "generic"  # github, stripe, slack, generic
    events: List[str] = Field(default_factory=list)  # e.g. ["push", "pull_request"]
    goal_template: str = "Process incoming {provider} webhook: {event_summary}"
    webhook_secret: Optional[str] = None
    debounce_seconds: int = 5


class WebhookTriggerInfo(BaseModel):
    """Webhook trigger info returned to user."""
    id: str
    agent_id: str
    agent_name: Optional[str] = None
    name: str
    enabled: bool
    provider: str = "generic"
    events: List[str] = []
    webhook_url: str
    webhook_path: str
    webhook_secret: Optional[str] = None
    trigger_count: int = 0
    last_triggered_at: Optional[str] = None
    created_at: Optional[str] = None
    setup_instructions: Optional[str] = None


# ============================================
# Signature Verification (Multi-Provider)
# ============================================

def _verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub HMAC-SHA256 (X-Hub-Signature-256: sha256=...)."""
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """Verify Stripe webhook signature (t=timestamp,v1=signature)."""
    if not sig_header:
        return False
    elements = dict(pair.split("=", 1) for pair in sig_header.split(",") if "=" in pair)
    timestamp = elements.get("t", "")
    v1_sig = elements.get("v1", "")
    if not timestamp or not v1_sig:
        return False
    signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
    expected = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1_sig)


def _verify_slack_signature(payload: bytes, sig_header: str, timestamp: str, secret: str) -> bool:
    """Verify Slack request signature (v0=hmac)."""
    if not sig_header or not timestamp:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except (ValueError, TypeError):
        return False
    basestring = f"v0:{timestamp}:{payload.decode('utf-8')}"
    expected = "v0=" + hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


def verify_webhook_signature(
    payload: bytes,
    signature: str,
    secret: str,
    provider: str = "generic",
    headers: Optional[Dict[str, str]] = None,
) -> bool:
    """Dispatch to provider-specific signature verification."""
    if not secret:
        return True
    headers = headers or {}
    if provider == "github":
        return _verify_github_signature(payload, signature or headers.get("x-hub-signature-256", ""), secret)
    elif provider == "stripe":
        return _verify_stripe_signature(payload, headers.get("stripe-signature", ""), secret)
    elif provider == "slack":
        return _verify_slack_signature(
            payload, headers.get("x-slack-signature", ""),
            headers.get("x-slack-request-timestamp", ""), secret,
        )
    # Generic: try HMAC header, then plain secret comparison
    if signature:
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        clean_sig = signature.replace("sha256=", "")
        return hmac.compare_digest(expected, clean_sig)
    plain = headers.get("x-webhook-secret", "")
    return plain == secret


# ============================================
# Event Parsing (Multi-Provider)
# ============================================

def parse_webhook_event(provider: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extract structured event info from provider-specific payload."""
    if provider == "github":
        return _parse_github_event(headers, payload)
    elif provider == "stripe":
        return _parse_stripe_event(payload)
    elif provider == "slack":
        return _parse_slack_event(payload)
    return _parse_generic_event(payload)


def _parse_github_event(headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    event_type = headers.get("x-github-event", "unknown")
    action = payload.get("action", "")
    repo = payload.get("repository", {}).get("full_name", "")
    sender = payload.get("sender", {}).get("login", "")

    parts = [event_type]
    if action:
        parts.append(f"({action})")
    if repo:
        parts.append(f"on {repo}")
    if sender:
        parts.append(f"by {sender}")

    event = {
        "provider": "github",
        "event_type": event_type,
        "action": action,
        "repo": repo,
        "sender": sender,
        "event_summary": " ".join(parts),
    }

    if event_type == "push":
        commits = payload.get("commits", [])
        event["branch"] = payload.get("ref", "").replace("refs/heads/", "")
        event["commit_count"] = len(commits)
        event["commit_messages"] = [c.get("message", "")[:100] for c in commits[:5]]
    elif event_type == "pull_request":
        pr = payload.get("pull_request", {})
        event["pr_title"] = pr.get("title", "")
        event["pr_number"] = pr.get("number", 0)
        event["pr_body"] = (pr.get("body") or "")[:500]
        event["pr_url"] = pr.get("html_url", "")
    elif event_type == "issues":
        issue = payload.get("issue", {})
        event["issue_title"] = issue.get("title", "")
        event["issue_number"] = issue.get("number", 0)
        event["issue_body"] = (issue.get("body") or "")[:500]
        event["issue_url"] = issue.get("html_url", "")
        event["labels"] = [l.get("name", "") for l in issue.get("labels", [])]
    elif event_type in ("issue_comment", "pull_request_review_comment"):
        comment = payload.get("comment", {})
        event["comment_body"] = (comment.get("body") or "")[:500]
        event["comment_author"] = comment.get("user", {}).get("login", "")

    return event


def _parse_stripe_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    event_type = payload.get("type", "unknown")
    obj = payload.get("data", {}).get("object", {})
    return {
        "provider": "stripe",
        "event_type": event_type,
        "action": event_type.split(".")[-1] if "." in event_type else "",
        "object_id": obj.get("id", ""),
        "amount": obj.get("amount"),
        "currency": obj.get("currency"),
        "customer": obj.get("customer", ""),
        "event_summary": f"Stripe {event_type} — {obj.get('id', '')}",
    }


def _parse_slack_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    event = payload.get("event", {})
    event_type = event.get("type", payload.get("type", "unknown"))
    return {
        "provider": "slack",
        "event_type": event_type,
        "action": event_type,
        "user": event.get("user", ""),
        "channel": event.get("channel", ""),
        "text": event.get("text", "")[:500],
        "event_summary": f"Slack {event_type} in #{event.get('channel', 'unknown')}",
    }


def _parse_generic_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    event_type = payload.get("event", payload.get("type", payload.get("action", "incoming")))
    return {
        "provider": "generic",
        "event_type": str(event_type),
        "action": str(event_type),
        "event_summary": f"Webhook event: {event_type}",
    }


# ============================================
# Goal Interpolation
# ============================================

def interpolate_goal(template: str, event: Dict[str, Any]) -> str:
    """Interpolate event data into goal template using {key} syntax."""
    result = template
    for key, value in event.items():
        placeholder = "{" + key + "}"
        if placeholder in result:
            if isinstance(value, (list, dict)):
                result = result.replace(placeholder, json.dumps(value))
            else:
                result = result.replace(placeholder, str(value))
    return result


# ============================================
# Helpers
# ============================================

def _build_public_url(webhook_path: str) -> str:
    """Build the full public webhook URL."""
    return f"https://{PLATFORM_DOMAIN}/api/v1{webhook_path}"


def _try_queue_execution(agent_id: str, goal: str, context: dict, user_id: str, source_id: str) -> Optional[str]:
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
                source=f"webhook:{source_id}",
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


async def _log_event(
    db: AsyncSession,
    trigger_id: str,
    agent_id: str,
    user_id: str,
    provider: str,
    event_type: str,
    event_summary: str,
    event_status: str,
    goal: Optional[str] = None,
    session_id: Optional[str] = None,
    payload: Optional[Dict] = None,
    error: Optional[str] = None,
):
    """Log webhook event to audit table."""
    try:
        await db.execute(
            text("""
                INSERT INTO webhook_event_log
                    (trigger_id, agent_id, user_id, provider, event_type, event_summary,
                     status, session_id, goal, payload, error)
                VALUES (:tid, :aid, :uid, :provider, :etype, :esummary,
                        :status, :sid, :goal, :payload, :error)
            """),
            {
                "tid": trigger_id,
                "aid": agent_id,
                "uid": user_id,
                "provider": provider,
                "etype": event_type[:128],
                "esummary": (event_summary or "")[:500],
                "status": event_status,
                "sid": session_id,
                "goal": goal,
                "payload": json.dumps(payload)[:10000] if payload else None,
                "error": error,
            },
        )
    except Exception as e:
        logger.debug(f"Failed to log webhook event: {e}")


def _get_setup_instructions(provider: str, webhook_url: str, secret: str, events: List[str]) -> str:
    """Generate provider-specific setup instructions."""
    if provider == "github":
        ev = ", ".join(events) if events else "push, pull_request, issues"
        return (
            f"Go to your repo → Settings → Webhooks → Add webhook\n"
            f"Payload URL: {webhook_url}\n"
            f"Content type: application/json\n"
            f"Secret: {secret}\n"
            f"Events: {ev}\n"
            f"Click Add webhook. Your agent will fire on every matching event."
        )
    elif provider == "stripe":
        ev = ", ".join(events) if events else "payment_intent.succeeded, invoice.paid"
        return (
            f"Go to Stripe Dashboard → Developers → Webhooks → Add endpoint\n"
            f"Endpoint URL: {webhook_url}\n"
            f"Events: {ev}\n"
            f"Use the Stripe-generated signing secret OR this auto-generated one: {secret}"
        )
    elif provider == "slack":
        return (
            f"Go to api.slack.com → Your App → Event Subscriptions\n"
            f"Request URL: {webhook_url}\n"
            f"Copy Signing Secret from Basic Information and update trigger secret.\n"
            f"Subscribe to: {', '.join(events) if events else 'message.channels, app_mention'}"
        )
    return (
        f"POST to: {webhook_url}\n"
        f"Auth: Header X-Webhook-Signature: sha256=HMAC(secret, body) OR X-Webhook-Secret: {secret}\n"
        f"Body: JSON with your event data."
    )


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

    # Verify agent exists AND belongs to this user
    agent_row = await db.execute(
        text("SELECT id, name, user_id FROM agent_definitions WHERE id = :aid AND user_id = :uid"),
        {"aid": agent_id, "uid": user_id},
    )
    agent = agent_row.mappings().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found or not owned by you")

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

    # Store provider + events in context_template JSON column
    context_template = json.dumps({
        "provider": body.provider,
        "events": body.events,
    })

    await db.execute(
        text("""
            INSERT INTO agent_triggers (id, agent_id, user_id, name, trigger_type, enabled,
                webhook_secret, webhook_path, goal_template, context_template, debounce_seconds)
            VALUES (:id, :agent_id, :user_id, :name, 'webhook', true,
                :secret, :path, :goal, :ctx, :debounce)
        """),
        {
            "id": trigger_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "name": trigger_name,
            "secret": wh_secret,
            "path": webhook_path,
            "goal": body.goal_template,
            "ctx": context_template,
            "debounce": body.debounce_seconds,
        },
    )
    await db.commit()

    public_url = _build_public_url(webhook_path)
    instructions = _get_setup_instructions(body.provider, public_url, wh_secret, body.events)
    logger.info(f"Created webhook trigger {trigger_id} for agent {agent_id} (provider={body.provider}): {public_url}")

    return {
        "id": trigger_id,
        "agent_id": agent_id,
        "agent_name": agent.get("name"),
        "name": trigger_name,
        "enabled": True,
        "provider": body.provider,
        "events": body.events,
        "webhook_url": public_url,
        "webhook_path": webhook_path,
        "webhook_secret": wh_secret,
        "trigger_count": 0,
        "setup_instructions": instructions,
        "message": f"Webhook created. Use this URL to receive events: {public_url}",
    }


@router.get("/agent/{agent_id}/list")
async def list_agent_webhooks(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """List all webhook triggers for an agent."""
    user_id = request.headers.get("x-user-id", "")
    result = await db.execute(
        text("""
            SELECT t.id, t.agent_id, t.name, t.enabled, t.webhook_path,
                   t.webhook_secret, t.trigger_count, t.last_triggered_at, t.created_at,
                   t.context_template, a.name as agent_name
            FROM agent_triggers t
            LEFT JOIN agent_definitions a ON a.id = t.agent_id
            WHERE t.agent_id = :aid AND t.trigger_type = 'webhook'
              AND (t.user_id = :uid OR :uid = '')
            ORDER BY t.created_at DESC
        """),
        {"aid": agent_id, "uid": user_id},
    )
    rows = result.mappings().all()

    triggers = []
    for row in rows:
        ctx = row.get("context_template")
        if isinstance(ctx, str):
            try:
                ctx = json.loads(ctx)
            except Exception:
                ctx = {}
        ctx = ctx or {}
        triggers.append({
            "id": str(row["id"]),
            "agent_id": str(row["agent_id"]),
            "agent_name": row.get("agent_name"),
            "name": row["name"],
            "enabled": row["enabled"],
            "provider": ctx.get("provider", "generic"),
            "events": ctx.get("events", []),
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
        ctx = row.get("context_template")
        if isinstance(ctx, str):
            try:
                ctx = json.loads(ctx)
            except Exception:
                ctx = {}
        ctx = ctx or {}
        triggers.append({
            "id": str(row["id"]),
            "agent_id": str(row["agent_id"]),
            "agent_name": row.get("agent_name"),
            "name": row["name"],
            "enabled": row["enabled"],
            "provider": ctx.get("provider", "generic"),
            "events": ctx.get("events", []),
            "webhook_url": _build_public_url(row["webhook_path"]),
            "webhook_path": row["webhook_path"],
            "webhook_secret": row.get("webhook_secret"),
            "trigger_count": row.get("trigger_count", 0),
            "last_triggered_at": row["last_triggered_at"].isoformat() if row.get("last_triggered_at") else None,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "goal_template": row.get("goal_template"),
        })

    return {"user_id": user_id, "triggers": triggers, "count": len(triggers)}


@router.get("/agent/{agent_id}/events")
async def list_webhook_events(
    agent_id: str,
    request: Request,
    limit: int = 50,
    db: AsyncSession = Depends(get_session),
):
    """List recent webhook events for an agent (audit log)."""
    result = await db.execute(
        text("""
            SELECT id, trigger_id, provider, event_type, event_summary,
                   status, session_id, goal, error, received_at
            FROM webhook_event_log
            WHERE agent_id = :aid
            ORDER BY received_at DESC
            LIMIT :lim
        """),
        {"aid": agent_id, "lim": limit},
    )
    rows = result.mappings().all()
    return {
        "agent_id": agent_id,
        "events": [
            {
                "id": str(r["id"]),
                "trigger_id": str(r["trigger_id"]),
                "provider": r["provider"],
                "event_type": r["event_type"],
                "event_summary": r.get("event_summary"),
                "status": r["status"],
                "session_id": str(r["session_id"]) if r.get("session_id") else None,
                "goal": r.get("goal"),
                "error": r.get("error"),
                "received_at": r["received_at"].isoformat() if r.get("received_at") else None,
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.delete("/trigger/{trigger_id}")
async def delete_webhook_trigger(
    trigger_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Delete a webhook trigger."""
    user_id = request.headers.get("x-user-id", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing x-user-id header")
    result = await db.execute(
        text("DELETE FROM agent_triggers WHERE id = :tid AND user_id = :uid RETURNING id"),
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
    user_id = request.headers.get("x-user-id", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing x-user-id header")
    result = await db.execute(
        text("UPDATE agent_triggers SET enabled = NOT enabled WHERE id = :tid AND user_id = :uid RETURNING id, enabled"),
        {"tid": trigger_id, "uid": user_id},
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
    Universal webhook receiver — handles GitHub, Stripe, Slack, and generic webhooks.
    This is the PUBLIC endpoint that external services call.
    
    Provider detection:
      - GitHub: X-Hub-Signature-256 + X-GitHub-Event headers
      - Stripe: Stripe-Signature header
      - Slack: X-Slack-Signature + X-Slack-Request-Timestamp headers
      - Generic: X-Webhook-Signature or X-Webhook-Secret header
    
    Internal services can bypass signature verification with X-Internal-Service-Key.
    """
    webhook_path = f"/webhooks/agent/{agent_id}/trigger"
    body_bytes = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

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

    trigger_id = str(trigger["id"])

    # Determine provider from stored config or auto-detect from headers
    ctx_raw = trigger.get("context_template")
    if isinstance(ctx_raw, str):
        try:
            ctx_cfg = json.loads(ctx_raw)
        except Exception:
            ctx_cfg = {}
    else:
        ctx_cfg = ctx_raw or {}
    provider = ctx_cfg.get("provider", "generic")
    allowed_events = ctx_cfg.get("events", [])

    # Auto-detect provider from headers if not configured
    if provider == "generic":
        if headers.get("x-github-event"):
            provider = "github"
        elif headers.get("stripe-signature"):
            provider = "stripe"
        elif headers.get("x-slack-signature"):
            provider = "slack"

    # Slack URL verification challenge (must respond immediately)
    if provider == "slack" and body_bytes:
        try:
            slack_body = json.loads(body_bytes)
            if slack_body.get("type") == "url_verification":
                return JSONResponse({"challenge": slack_body.get("challenge", "")})
        except (json.JSONDecodeError, KeyError):
            pass

    # Internal services can bypass signature verification
    is_internal = False
    if x_internal_service_key and settings.INTERNAL_SERVICE_KEY:
        is_internal = x_internal_service_key == settings.INTERNAL_SERVICE_KEY

    # Verify signature (provider-aware)
    if trigger.get("webhook_secret") and not is_internal:
        if not verify_webhook_signature(
            body_bytes, x_webhook_signature or "",
            trigger["webhook_secret"], provider, headers,
        ):
            await _log_event(
                db, trigger_id, agent_id, trigger.get("user_id", ""),
                provider, "unknown", "", "rejected", error="Invalid signature",
            )
            await db.commit()
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Check debounce (skip for internal)
    if not is_internal and trigger.get("last_triggered_at"):
        elapsed = (datetime.now(timezone.utc) - trigger["last_triggered_at"]).total_seconds()
        if elapsed < trigger.get("debounce_seconds", 5):
            await _log_event(
                db, trigger_id, agent_id, trigger.get("user_id", ""),
                provider, "debounced", "", "debounced",
            )
            await db.commit()
            return WebhookResponse(
                status="debounced",
                message=f"Trigger debounced, wait {trigger['debounce_seconds'] - elapsed:.0f}s",
            )

    # Parse payload
    try:
        payload = json.loads(body_bytes) if body_bytes else {}
    except json.JSONDecodeError:
        payload = {"raw_body": body_bytes.decode("utf-8", errors="replace")[:5000]}

    # Parse event with provider-specific extraction
    event = parse_webhook_event(provider, headers, payload)
    event_type = event.get("event_type", "unknown")
    event_summary = event.get("event_summary", "")

    # Filter by allowed events (if configured)
    if allowed_events and event_type not in allowed_events:
        combo = f"{event_type}.{event.get('action', '')}"
        if combo not in allowed_events and f"{event_type}.*" not in allowed_events:
            await _log_event(
                db, trigger_id, agent_id, trigger.get("user_id", ""),
                provider, event_type, event_summary, "filtered",
            )
            await db.commit()
            return WebhookResponse(status="filtered", message=f"Event '{event_type}' not in allowed list")

    # Interpolate goal from template
    goal_template = trigger.get("goal_template", "Process incoming {provider} webhook: {event_summary}")
    goal = interpolate_goal(goal_template, event)

    # Build rich context for agent
    context = {
        "webhook_event": event,
        "webhook_payload": payload,
        "webhook_provider": provider,
        "webhook_trigger_id": trigger_id,
        "webhook_timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Queue agent execution
    session_id = _try_queue_execution(
        agent_id=str(trigger["agent_id"]),
        goal=goal,
        context=context,
        user_id=trigger.get("user_id", ""),
        source_id=trigger_id,
    )

    # Update trigger stats
    await db.execute(
        text("""
            UPDATE agent_triggers
            SET last_triggered_at = :now, trigger_count = trigger_count + 1
            WHERE id = :tid
        """),
        {"now": datetime.now(timezone.utc), "tid": trigger_id},
    )

    # Log event
    await _log_event(
        db, trigger_id, agent_id, trigger.get("user_id", ""),
        provider, event_type, event_summary,
        "triggered" if session_id else "received",
        goal=goal, session_id=session_id, payload=payload,
    )
    await db.commit()

    logger.info(
        f"[WEBHOOK] {provider}/{event_type} → agent {agent_id} "
        f"(trigger={trigger_id[:8]}, session={session_id[:8] if session_id else 'none'})"
    )

    if session_id:
        return WebhookResponse(status="triggered", session_id=session_id)
    return WebhookResponse(
        status="received",
        message="Webhook received. Agent execution queue unavailable.",
    )


# ============================================
# Auto-create helper (called from tool_executor)
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
