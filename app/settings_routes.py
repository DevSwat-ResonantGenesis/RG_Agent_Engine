"""Agent Settings API Routes.

These endpoints provide agent configuration, templates, and management.
Migrated from auth_service to proper domain ownership.
"""

from datetime import datetime
import hashlib
import json
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import AgentDefinition, AgentSession, AgentStep, AgentUserSettings, AgentVersion
from .routers import compute_manifest_hash, list_agent_templates


router = APIRouter(prefix="/agents/settings", tags=["agent-settings"])


# ============================================
# Request Models
# ============================================

class AgentCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    model: str = "gpt-4"
    system_prompt: Optional[str] = None
    patches: Optional[List[int]] = None
    tools: Optional[List[str]] = None
    personality_config: Optional[dict] = None
    patch_config: Optional[dict] = None
    memory_config: Optional[dict] = None
    anchor_config: Optional[dict] = None
    isolate_anchors: bool = True


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    patches: Optional[List[int]] = None
    tool_mode: Optional[str] = None  # smart or manual
    tools: Optional[List[str]] = None
    personality_config: Optional[dict] = None
    patch_config: Optional[dict] = None
    memory_config: Optional[dict] = None
    anchor_config: Optional[dict] = None
    isolate_anchors: Optional[bool] = None
    status: Optional[str] = None


def _require_user_id(request: Request) -> str:
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User ID required")
    return user_id


def _agent_safety(agent: AgentDefinition) -> Dict[str, Any]:
    return agent.safety_config or {}


def _canonical_json(value: Dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _compute_agent_public_hash(*, agent_id: str, owner_user_id: str) -> str:
    digest = hashlib.sha256(f"agent_public:{agent_id}:{owner_user_id}".encode("utf-8")).hexdigest()
    return f"0x{digest}"


def _ensure_agent_public_hash(agent: AgentDefinition) -> str:
    if agent.agent_public_hash:
        return str(agent.agent_public_hash)

    if not agent.id:
        agent.id = uuid4()

    owner = str(agent.user_id) if agent.user_id else ""
    agent.agent_public_hash = _compute_agent_public_hash(agent_id=str(agent.id), owner_user_id=owner)
    return str(agent.agent_public_hash)


def _compute_and_set_version_hash(agent: AgentDefinition) -> str:
    safety_config = _agent_safety(agent)
    manifest_hash = compute_manifest_hash(
        name=agent.name,
        description=agent.description,
        system_prompt=agent.system_prompt,
        model=agent.model,
        temperature=agent.temperature,
        max_tokens=agent.max_tokens,
        tools=agent.tools,
        allowed_actions=agent.allowed_actions,
        blocked_actions=agent.blocked_actions,
    )
    safety_config["manifest_hash"] = manifest_hash
    safety_config["agent_hash"] = str(agent.agent_public_hash or "")
    agent.safety_config = safety_config

    agent.agent_version_hash = str(manifest_hash)
    return str(agent.agent_version_hash)


def _build_config_snapshot(agent: AgentDefinition) -> Dict[str, Any]:
    safety_config = _agent_safety(agent)
    return {
        "name": agent.name,
        "description": agent.description,
        "system_prompt": agent.system_prompt,
        "model": agent.model,
        "temperature": agent.temperature,
        "max_tokens": agent.max_tokens,
        "tools": agent.tools or [],
        "tool_config": agent.tool_config or {},
        "allowed_actions": agent.allowed_actions or [],
        "blocked_actions": agent.blocked_actions or [],
        "safety_config": safety_config,
        "agent_public_hash": agent.agent_public_hash,
        "agent_version_hash": agent.agent_version_hash,
        "version": agent.version,
    }


async def _maybe_write_version_row(
    *,
    agent: AgentDefinition,
    session: AsyncSession,
    previous_version_hash: Optional[str],
    changelog: Optional[str],
) -> None:
    current_version_hash = agent.agent_version_hash
    if not current_version_hash:
        return
    if previous_version_hash and previous_version_hash == current_version_hash:
        return

    public_hash = _ensure_agent_public_hash(agent)

    session.add(
        AgentVersion(
            agent_id=agent.id,
            agent_public_hash=public_hash,
            version_number=int(agent.version or 1),
            agent_version_hash=current_version_hash,
            changelog=changelog,
            config_snapshot=_build_config_snapshot(agent),
        )
    )


def _ensure_hashes_without_version_bump(agent: AgentDefinition) -> bool:
    mutated = False

    if not agent.agent_public_hash:
        _ensure_agent_public_hash(agent)
        mutated = True

    safety_config = _agent_safety(agent)

    existing = agent.agent_version_hash or str(safety_config.get("manifest_hash") or "")
    if not existing:
        computed = _compute_and_set_version_hash(agent)
        agent.agent_version_hash = computed
        mutated = True
    else:
        if not agent.agent_version_hash:
            agent.agent_version_hash = existing
            mutated = True
        if not safety_config.get("manifest_hash"):
            safety_config["manifest_hash"] = existing
            agent.safety_config = safety_config
            mutated = True

    if (agent.safety_config or {}).get("agent_hash") != str(agent.agent_public_hash or ""):
        safety_config = _agent_safety(agent)
        safety_config["agent_hash"] = str(agent.agent_public_hash or "")
        agent.safety_config = safety_config
        mutated = True

    return mutated


async def _get_agent_for_user(*, agent_id: str, request: Request, session: AsyncSession) -> AgentDefinition:
    from uuid import UUID as PyUUID

    user_id = _require_user_id(request)
    try:
        user_uuid = PyUUID(str(user_id))
        agent_uuid = PyUUID(str(agent_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid agent_id")

    result = await session.execute(
        select(AgentDefinition).where(
            AgentDefinition.id == agent_uuid,
            AgentDefinition.user_id == user_uuid,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


async def _get_user_settings(*, request: Request, session: AsyncSession) -> AgentUserSettings:
    from uuid import UUID as PyUUID

    user_id = _require_user_id(request)
    try:
        user_uuid = PyUUID(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    result = await session.execute(
        select(AgentUserSettings).where(AgentUserSettings.user_id == user_uuid)
    )
    settings = result.scalar_one_or_none()
    if settings:
        return settings

    settings = AgentUserSettings(user_id=user_uuid, memory_config=None)
    session.add(settings)
    await session.commit()
    await session.refresh(settings)
    return settings


def _merge_memory_config(user_default: Dict[str, Any], agent_override: Dict[str, Any]) -> Dict[str, Any]:
    merged = {**(user_default or {})}
    merged.update(agent_override or {})
    return merged


def _agent_to_settings_payload(agent: AgentDefinition) -> Dict[str, Any]:
    safety_config = _agent_safety(agent)
    return {
        "id": str(agent.id),
        "name": agent.name,
        "description": agent.description,
        "agent_hash": str(agent.agent_public_hash or ""),
        "agent_public_hash": str(agent.agent_public_hash or ""),
        "agent_version_hash": str(agent.agent_version_hash or safety_config.get("manifest_hash") or ""),
        "system_prompt": agent.system_prompt,
        "personality_config": safety_config.get("personalityConfig") or {},
        "enabled_patches": safety_config.get("enabledPatches") or [],
        "patch_config": safety_config.get("patchConfig") or {},
        "memory_config": safety_config.get("memoryConfig") or {},
        "anchor_config": safety_config.get("anchorConfig") or {},
        "isolate_anchors": bool(safety_config.get("isolate_anchors", True)),
        "tool_mode": getattr(agent, 'tool_mode', None) or 'smart',
        "tools": agent.tools or [],
        "status": "active" if agent.is_active else "inactive",
        "is_template": bool(safety_config.get("is_template", False)),
        "template_id": safety_config.get("template_id"),
        "is_shared": bool(safety_config.get("is_shared", False)),
        "is_public": bool(safety_config.get("is_public", False)),
        "is_imported": bool(safety_config.get("is_imported", False)),
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
    }


class MemoryConfigRequest(BaseModel):
    enabled: bool = True
    max_memories: int = 1000
    retention_days: int = 30


class RestrictionRequest(BaseModel):
    max_tokens_per_request: int = 4000
    max_requests_per_day: int = 1000
    allowed_models: List[str] = ["gpt-4", "gpt-3.5-turbo"]


# ============================================
# SPECIFIC ROUTES FIRST (before catch-all /{agent_id})
# ============================================
# FastAPI matches routes in order, so specific routes must come before catch-all

@router.get("/templates")
async def list_templates(request: Request):
    """List available agent templates."""
    templates = await list_agent_templates()
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "system_prompt": t.system_prompt,
            "model": t.model,
            "tools": t.tools,
            "category": t.category,
        }
        for t in templates
    ]


@router.get("/shared")
async def list_shared_agents(request: Request):
    """List agents shared with the user."""
    raise HTTPException(status_code=501, detail="Shared agents not implemented")


@router.post("/from-template/{template_id}")
async def create_from_template(
    template_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create an agent from a template."""
    from uuid import UUID as PyUUID

    user_id = _require_user_id(request)
    try:
        user_uuid = PyUUID(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    templates = {t.id: t for t in await list_agent_templates()}
    template = templates.get(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    agent = AgentDefinition(
        user_id=user_uuid,
        name=template.name,
        description=template.description,
        system_prompt=template.system_prompt,
        model=template.model,
        temperature=0.7,
        max_tokens=4096,
        tools=template.tools,
        safety_config={
            "template_id": template.id,
            "is_imported": False,
        },
        is_active=True,
    )

    _ensure_agent_public_hash(agent)
    _compute_and_set_version_hash(agent)
    session.add(agent)
    await _maybe_write_version_row(agent=agent, session=session, previous_version_hash=None, changelog=None)
    await session.commit()
    await session.refresh(agent)
    return _agent_to_settings_payload(agent)


@router.post("/import")
async def import_agent(request: Request):
    """Import an agent configuration."""
    raise HTTPException(status_code=501, detail="Import not implemented")


# ============================================
# Agent CRUD (base routes)
# ============================================

@router.get("")
async def list_agents(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """List user's agents."""
    from uuid import UUID as PyUUID

    user_id = _require_user_id(request)
    try:
        user_uuid = PyUUID(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.user_id == user_uuid).order_by(AgentDefinition.created_at.desc())
    )
    agents = result.scalars().all()
    mutated = False
    payload: List[Dict[str, Any]] = []
    for a in agents:
        if _ensure_hashes_without_version_bump(a):
            mutated = True
        payload.append(_agent_to_settings_payload(a))
 
    if mutated:
        await session.commit()
 
    return payload


@router.post("")
async def create_agent(
    payload: AgentCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create a new agent."""
    from uuid import UUID as PyUUID

    user_id = _require_user_id(request)
    try:
        user_uuid = PyUUID(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    safety_config: Dict[str, Any] = {}
    if payload.personality_config is not None:
        safety_config["personalityConfig"] = payload.personality_config
    if payload.patches is not None:
        safety_config["enabledPatches"] = payload.patches
    if payload.patch_config is not None:
        safety_config["patchConfig"] = payload.patch_config
    if payload.memory_config is not None:
        safety_config["memoryConfig"] = payload.memory_config
    if payload.anchor_config is not None:
        safety_config["anchorConfig"] = payload.anchor_config
    safety_config["isolate_anchors"] = bool(payload.isolate_anchors)

    tools = payload.tools
    if tools is None:
        tools = ["web_search", "fetch_url"]

    agent = AgentDefinition(
        user_id=user_uuid,
        name=payload.name,
        description=payload.description,
        system_prompt=payload.system_prompt,
        model=payload.model,
        temperature=0.7,
        max_tokens=4096,
        tools=tools,
        safety_config=safety_config,
        is_active=True,
    )

    _ensure_agent_public_hash(agent)
    _compute_and_set_version_hash(agent)
    session.add(agent)
    await _maybe_write_version_row(agent=agent, session=session, previous_version_hash=None, changelog=None)
    await session.commit()
    await session.refresh(agent)
    return _agent_to_settings_payload(agent)


# ============================================
# Agent CRUD (catch-all routes - MUST BE LAST)
# ============================================

@router.get("/{agent_id:uuid}")
async def get_agent(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get agent details."""
    agent = await _get_agent_for_user(agent_id=agent_id, request=request, session=session)

    if _ensure_hashes_without_version_bump(agent):
        await session.commit()

    return _agent_to_settings_payload(agent)


@router.put("/{agent_id:uuid}")
async def update_agent(
    agent_id: str,
    payload: AgentUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Update an agent."""
    agent = await _get_agent_for_user(agent_id=agent_id, request=request, session=session)
    safety_config = _agent_safety(agent)

    previous_version_hash = agent.agent_version_hash or str(safety_config.get("manifest_hash") or "")

    if payload.status is not None:
        agent.is_active = payload.status == "active"

    if payload.name is not None:
        agent.name = payload.name
    if payload.description is not None:
        agent.description = payload.description
    if payload.model is not None:
        agent.model = payload.model
    if payload.system_prompt is not None:
        agent.system_prompt = payload.system_prompt

    if payload.tool_mode is not None:
        resolved = payload.tool_mode.strip().lower()
        if resolved in ("smart", "manual"):
            agent.tool_mode = resolved
    if payload.tools is not None:
        agent.tools = payload.tools

    if payload.patches is not None:
        safety_config["enabledPatches"] = payload.patches
    if payload.personality_config is not None:
        safety_config["personalityConfig"] = payload.personality_config
    if payload.patch_config is not None:
        safety_config["patchConfig"] = payload.patch_config
    if payload.memory_config is not None:
        safety_config["memoryConfig"] = payload.memory_config
    if payload.anchor_config is not None:
        safety_config["anchorConfig"] = payload.anchor_config
    if payload.isolate_anchors is not None:
        safety_config["isolate_anchors"] = bool(payload.isolate_anchors)

    agent.safety_config = safety_config

    _ensure_agent_public_hash(agent)
    _compute_and_set_version_hash(agent)

    if previous_version_hash and agent.agent_version_hash and previous_version_hash != agent.agent_version_hash:
        agent.version = int(agent.version or 1) + 1

    await _maybe_write_version_row(
        agent=agent,
        session=session,
        previous_version_hash=previous_version_hash,
        changelog=None,
    )
    await session.commit()
    await session.refresh(agent)
    return _agent_to_settings_payload(agent)


@router.delete("/{agent_id:uuid}")
async def delete_agent(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Delete an agent."""
    agent = await _get_agent_for_user(agent_id=agent_id, request=request, session=session)
    await session.delete(agent)
    await session.commit()
    return {
        "status": "deleted",
        "id": agent_id,
    }


# ============================================
# Agent Operations
# ============================================

@router.post("/{agent_id}/export")
async def export_agent(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Export an agent configuration."""
    agent = await _get_agent_for_user(agent_id=agent_id, request=request, session=session)
    safety_config = _agent_safety(agent)
    return {
        "agent_id": agent_id,
        "export_data": {
            "name": agent.name,
            "description": agent.description,
            "system_prompt": agent.system_prompt,
            "model": agent.model,
            "temperature": agent.temperature,
            "max_tokens": agent.max_tokens,
            "tools": agent.tools or [],
            "personality_config": safety_config.get("personalityConfig") or {},
            "enabled_patches": safety_config.get("enabledPatches") or [],
            "patch_config": safety_config.get("patchConfig") or {},
            "memory_config": safety_config.get("memoryConfig") or {},
            "anchor_config": safety_config.get("anchorConfig") or {},
            "isolate_anchors": bool(safety_config.get("isolate_anchors", True)),
        },
        "exported_at": datetime.utcnow().isoformat(),
    }


@router.post("/{agent_id}/share")
async def share_agent(agent_id: str, request: Request):
    """Share an agent with another user."""
    raise HTTPException(status_code=501, detail="Sharing not implemented")


@router.post("/{agent_id}/save-template")
async def save_as_template(agent_id: str, request: Request):
    """Save an agent as a template."""
    raise HTTPException(status_code=501, detail="Save as template not implemented")


@router.post("/{agent_id}/hash")
async def regenerate_hash(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Regenerate agent's hash."""
    agent = await _get_agent_for_user(agent_id=agent_id, request=request, session=session)

    previous_version_hash = agent.agent_version_hash or str((agent.safety_config or {}).get("manifest_hash") or "")
    _ensure_agent_public_hash(agent)
    new_hash = _compute_and_set_version_hash(agent)
    if previous_version_hash and new_hash and previous_version_hash != new_hash:
        agent.version = int(agent.version or 1) + 1
        await _maybe_write_version_row(
            agent=agent,
            session=session,
            previous_version_hash=previous_version_hash,
            changelog=None,
        )
    await session.commit()
    return {
        "agent_id": agent_id,
        "hash": new_hash,
        "regenerated_at": datetime.utcnow().isoformat(),
    }


# ============================================
# Anchors
# ============================================

@router.get("/{agent_id}/anchors")
async def get_agent_anchors(agent_id: str, request: Request):
    """Get agent's memory anchors."""
    raise HTTPException(status_code=501, detail="Anchors not implemented")


@router.delete("/{agent_id}/anchors/{anchor_id}")
async def delete_agent_anchor(agent_id: str, anchor_id: str, request: Request):
    """Delete an agent anchor."""
    raise HTTPException(status_code=501, detail="Anchors not implemented")


# ============================================
# API Keys
# ============================================

@router.get("/{agent_id}/api-keys")
async def get_agent_api_keys(agent_id: str, request: Request):
    """Get agent's API keys."""
    raise HTTPException(status_code=501, detail="Agent API keys not implemented")


@router.post("/{agent_id}/api-keys")
async def create_agent_api_key(agent_id: str, request: Request):
    """Create an API key for an agent."""
    raise HTTPException(status_code=501, detail="Agent API keys not implemented")


@router.delete("/{agent_id}/api-keys/{key_id}")
async def delete_agent_api_key(agent_id: str, key_id: str, request: Request):
    """Delete an agent API key."""
    raise HTTPException(status_code=501, detail="Agent API keys not implemented")


# ============================================
# Memory Configuration
# ============================================

@router.get("/memory-default")
async def get_memory_default(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get user's default memory configuration."""
    settings = await _get_user_settings(request=request, session=session)
    cfg = settings.memory_config or {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "max_memories": int(cfg.get("max_memories", 1000)),
        "retention_days": int(cfg.get("retention_days", 30)),
    }


@router.put("/memory-default")
async def update_memory_default(
    payload: MemoryConfigRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Update user's default memory configuration."""
    settings = await _get_user_settings(request=request, session=session)
    settings.memory_config = {
        "enabled": payload.enabled,
        "max_memories": payload.max_memories,
        "retention_days": payload.retention_days,
    }
    await session.commit()
    return settings.memory_config


@router.get("/{agent_id}/memory")
async def get_memory_config(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get agent's memory configuration."""
    settings = await _get_user_settings(request=request, session=session)
    user_default = settings.memory_config or {}

    agent = await _get_agent_for_user(agent_id=agent_id, request=request, session=session)
    safety_config = _agent_safety(agent)
    agent_override = safety_config.get("memoryConfig") or {}

    memory_cfg = _merge_memory_config(user_default, agent_override)
    enabled = bool(memory_cfg.get("enabled", True))
    max_memories = int(memory_cfg.get("max_memories", 1000))
    retention_days = int(memory_cfg.get("retention_days", 30))
    return {
        "agent_id": agent_id,
        "enabled": enabled,
        "max_memories": max_memories,
        "retention_days": retention_days,
    }


@router.put("/{agent_id}/memory")
async def update_memory_config(
    agent_id: str,
    payload: MemoryConfigRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Update agent's memory configuration."""
    settings = await _get_user_settings(request=request, session=session)
    user_default = settings.memory_config or {}

    agent = await _get_agent_for_user(agent_id=agent_id, request=request, session=session)
    safety_config = _agent_safety(agent)
    safety_config["memoryConfig"] = {
        "enabled": payload.enabled,
        "max_memories": payload.max_memories,
        "retention_days": payload.retention_days,
    }
    agent.safety_config = safety_config
    await session.commit()

    effective = _merge_memory_config(user_default, safety_config["memoryConfig"])
    return {
        "agent_id": agent_id,
        "enabled": bool(effective.get("enabled", True)),
        "max_memories": int(effective.get("max_memories", 1000)),
        "retention_days": int(effective.get("retention_days", 30)),
        "updated_at": datetime.utcnow().isoformat(),
    }


# ============================================
# Metrics
# ============================================

@router.get("/{agent_id}/metrics")
async def get_agent_metrics(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get agent usage metrics."""
    from uuid import UUID as PyUUID

    agent = await _get_agent_for_user(agent_id=agent_id, request=request, session=session)
    agent_uuid = PyUUID(str(agent.id))

    sessions_total = (
        await session.execute(
            select(func.count()).select_from(AgentSession).where(AgentSession.agent_id == agent_uuid)
        )
    ).scalar_one()

    sessions_completed = (
        await session.execute(
            select(func.count()).select_from(AgentSession).where(
                AgentSession.agent_id == agent_uuid,
                AgentSession.status == "completed",
            )
        )
    ).scalar_one()

    sessions_failed = (
        await session.execute(
            select(func.count()).select_from(AgentSession).where(
                AgentSession.agent_id == agent_uuid,
                AgentSession.status == "failed",
            )
        )
    ).scalar_one()

    last_active = (
        await session.execute(
            select(func.max(AgentSession.last_activity_at)).where(AgentSession.agent_id == agent_uuid)
        )
    ).scalar_one()

    sessions_for_agent = select(AgentSession.id).where(AgentSession.agent_id == agent_uuid)

    total_tokens = (
        await session.execute(
            select(func.coalesce(func.sum(AgentStep.tokens_used), 0)).select_from(AgentStep).where(
                AgentStep.session_id.in_(sessions_for_agent)
            )
        )
    ).scalar_one()

    avg_step_duration_ms = (
        await session.execute(
            select(func.avg(AgentStep.duration_ms)).select_from(AgentStep).where(
                AgentStep.duration_ms.isnot(None),
                AgentStep.session_id.in_(sessions_for_agent),
            )
        )
    ).scalar_one()

    denom = int(sessions_completed) + int(sessions_failed)
    success_rate = float(sessions_completed) / float(denom) if denom > 0 else 1.0

    return {
        "agent_id": agent_id,
        "metrics": {
            "total_sessions": int(sessions_total),
            "completed_sessions": int(sessions_completed),
            "failed_sessions": int(sessions_failed),
            "total_tokens": int(total_tokens or 0),
            "avg_step_duration_ms": float(avg_step_duration_ms) if avg_step_duration_ms is not None else None,
            "success_rate": success_rate,
            "last_active": last_active.isoformat() if last_active else None,
        },
    }


# ============================================
# Patches
# ============================================

@router.get("/{agent_id}/patches")
async def get_agent_patches(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get agent's enabled patches."""
    agent = await _get_agent_for_user(agent_id=agent_id, request=request, session=session)
    safety_config = _agent_safety(agent)
    return {
        "enabled_patches": safety_config.get("enabledPatches") or [],
        "patch_config": safety_config.get("patchConfig") or {},
    }


# ============================================
# Restrictions
# ============================================

@router.get("/{agent_id}/restrictions")
async def get_agent_restrictions(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get agent's restrictions."""
    agent = await _get_agent_for_user(agent_id=agent_id, request=request, session=session)
    safety_config = _agent_safety(agent)
    restrictions = safety_config.get("restrictions") or {}
    return {
        "agent_id": agent_id,
        "restrictions": {
            "max_tokens_per_request": int(restrictions.get("max_tokens_per_request", 4000)),
            "max_requests_per_day": int(restrictions.get("max_requests_per_day", 1000)),
            "allowed_models": list(restrictions.get("allowed_models", ["gpt-4", "gpt-3.5-turbo"])),
        },
    }


@router.put("/{agent_id}/restrictions")
async def update_agent_restrictions(
    agent_id: str,
    payload: RestrictionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Update agent's restrictions."""
    agent = await _get_agent_for_user(agent_id=agent_id, request=request, session=session)
    safety_config = _agent_safety(agent)
    safety_config["restrictions"] = {
        "max_tokens_per_request": payload.max_tokens_per_request,
        "max_requests_per_day": payload.max_requests_per_day,
        "allowed_models": payload.allowed_models,
    }
    agent.safety_config = safety_config
    await session.commit()
    return {
        "agent_id": agent_id,
        "restrictions": safety_config["restrictions"],
        "updated_at": datetime.utcnow().isoformat(),
    }
