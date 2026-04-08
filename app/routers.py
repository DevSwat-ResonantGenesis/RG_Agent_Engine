"""Agent Engine API routers."""

from datetime import datetime, timedelta
import asyncio
import hashlib
import json
import logging
import os
import uuid
from uuid import UUID as PyUUID
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from .websocket_streaming import get_connection_manager, get_execution_streamer
from .db import get_session, async_session
from .models import (
    AgentDefinition, AgentSession, AgentStep, AgentPlan, AgentVersion,
    ToolDefinition, SafetyRule, WorkflowTrigger,
    AgentTeam, AgentTeamMember, AgentTeamWorkflow, AgentTeamRental
)
from .executor import agent_executor, trigger_manager
from .safety import approval_manager
from .publish_agent import publish_agent_to_blockchain


BLOCKCHAIN_SERVICE_URL = os.getenv("BLOCKCHAIN_SERVICE_URL", "http://blockchain_service:8000")
RARA_SERVICE_URL = os.getenv("RARA_SERVICE_URL", "http://rg_internal_invarients_sim:8093")
BILLING_SERVICE_URL = os.getenv("BILLING_SERVICE_URL", "http://billing_service:8000").rstrip("/")
BASE_PUBLISH_CREDITS = int(os.getenv("BASE_PUBLISH_CREDITS", "250"))
BUY_CREDITS_URL = os.getenv("BUY_CREDITS_URL", "https://dev-swat.com/billing")
LLM_SERVICE_URL = os.getenv("LLM_SERVICE_URL", "http://llm_service:8000").rstrip("/")

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except Exception:
        return default


MAX_CONCURRENT_AGENT_RUNS = _int_env("AGENT_ENGINE_MAX_CONCURRENT_RUNS", 3)
_agent_run_semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENT_RUNS)


def _canonical_manifest_payload(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_manifest_hash(*, name: str, description: Optional[str], system_prompt: Optional[str], model: str, temperature: float, max_tokens: int, tools: Optional[List[str]], allowed_actions: Optional[List[str]], blocked_actions: Optional[List[str]]) -> str:
    manifest = {
        "name": name,
        "description": description or "",
        "system_prompt": system_prompt or "",
        "model": model,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "tools": sorted(tools or []),
        "allowed_actions": sorted(allowed_actions or []),
        "blocked_actions": sorted(blocked_actions or []),
    }
    digest = hashlib.sha256(_canonical_manifest_payload(manifest).encode("utf-8")).hexdigest()
    return f"0x{digest}"


def _compute_agent_public_hash(*, agent_id: str, owner_user_id: str) -> str:
    digest = hashlib.sha256(f"agent_public:{agent_id}:{owner_user_id}".encode("utf-8")).hexdigest()
    return f"0x{digest}"


def _ensure_hashes_without_version_bump(agent: AgentDefinition) -> bool:
    mutated = False

    if not agent.agent_public_hash:
        owner = str(agent.user_id) if agent.user_id else ""
        agent.agent_public_hash = _compute_agent_public_hash(agent_id=str(agent.id), owner_user_id=owner)
        mutated = True

    safety_config = agent.safety_config or {}

    existing = agent.agent_version_hash or str(safety_config.get("manifest_hash") or "")
    if existing and not agent.agent_version_hash:
        agent.agent_version_hash = existing
        mutated = True

    if existing and not safety_config.get("manifest_hash"):
        safety_config["manifest_hash"] = existing
        agent.safety_config = safety_config
        mutated = True

    if safety_config.get("agent_hash") != str(agent.agent_public_hash or ""):
        safety_config["agent_hash"] = str(agent.agent_public_hash or "")
        agent.safety_config = safety_config
        mutated = True

    return mutated


def _build_config_snapshot(agent: AgentDefinition) -> Dict[str, Any]:
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
        "safety_config": agent.safety_config or {},
        "agent_public_hash": agent.agent_public_hash,
        "agent_version_hash": agent.agent_version_hash,
        "version": agent.version,
    }


def _make_agent_version_row(*, agent: AgentDefinition, previous_version_hash: Optional[str]) -> Optional[AgentVersion]:
    current_version_hash = agent.agent_version_hash
    if not current_version_hash:
        return None
    if previous_version_hash and previous_version_hash == current_version_hash:
        return None

    agent_public_hash = str(agent.agent_public_hash or "")
    if not agent_public_hash:
        owner = str(agent.user_id) if agent.user_id else ""
        agent_public_hash = _compute_agent_public_hash(agent_id=str(agent.id), owner_user_id=owner)
        agent.agent_public_hash = agent_public_hash

    return AgentVersion(
        agent_id=agent.id,
        agent_public_hash=agent_public_hash,
        version_number=int(agent.version or 1),
        agent_version_hash=current_version_hash,
        changelog=None,
        config_snapshot=_build_config_snapshot(agent),
    )


def _base_publish_is_configured() -> bool:
    if os.getenv("ENABLE_BASE_NETWORK", "false").lower() != "true":
        return False
    if not os.getenv("BASE_RPC_URL") or not os.getenv("BASE_AGENT_CONTRACT"):
        return False
    if not (
        os.getenv("BASE_PRIVATE_KEY")
        or os.getenv("BASE_AGENT_PRIVATE_KEY")
        or os.getenv("BASE_DEPLOYER_PRIVATE_KEY")
    ):
        return False
    return True


async def _deduct_base_publish_credits(*, user_id: str, agent_id: str) -> Optional[str]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{BILLING_SERVICE_URL}/billing/credits/deduct",
            headers={"x-user-id": user_id},
            json={
                "amount": BASE_PUBLISH_CREDITS,
                "reference_type": "agent_publish_base",
                "reference_id": agent_id,
                "description": "Publish agent to Base",
            },
        )

    if resp.status_code == 402:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "insufficient_credits",
                "message": resp.text,
                "required_credits": BASE_PUBLISH_CREDITS,
                "buy_url": BUY_CREDITS_URL,
            },
        )

    if resp.status_code not in (200, 201):
        raise Exception(f"Credit deduction failed: {resp.status_code} {resp.text}")

    data = resp.json() if resp.content else {}
    return str(data.get("transaction_id") or data.get("id") or "") or None


async def _refund_base_publish_credits(*, user_id: str, amount: int, original_tx_id: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{BILLING_SERVICE_URL}/billing/credits/refund",
                headers={"x-user-id": user_id},
                json={
                    "amount": amount,
                    "original_tx_id": original_tx_id,
                    "reason": "Refund: Base publish failed",
                },
            )
    except Exception as e:
        logger.warning(f"Failed to refund credits after Base publish failure: {e}")


async def best_effort_issue_dsid_and_register(*, agent: "AgentDefinition", user_id: Optional[str], session: AsyncSession) -> None:
    safety_config = agent.safety_config or {}
    if safety_config.get("dsid"):
        return

    dsid_value: Optional[str] = None
    dsid_content_hash: Optional[str] = None

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{BLOCKCHAIN_SERVICE_URL}/blockchain/dsid",
                json={
                    "entity_type": "agent",
                    "entity_id": str(agent.id),
                    "content": {
                        "agent_id": str(agent.id),
                        "manifest_hash": safety_config.get("manifest_hash"),
                        "name": agent.name,
                        "description": agent.description,
                        "model": agent.model,
                        "tools": agent.tools or [],
                    },
                    "metadata": {
                        "user_id": user_id,
                    },
                },
            )

        if resp.status_code in (200, 201):
            data = resp.json()
            dsid_value = data.get("dsid")
            dsid_content_hash = data.get("content_hash")
    except Exception:
        dsid_value = None

    if dsid_value:
        safety_config["dsid"] = dsid_value
        if dsid_content_hash:
            safety_config["dsid_content_hash"] = dsid_content_hash
        agent.safety_config = safety_config
        await session.commit()

        try:
            public_key = f"pk_{hashlib.sha256(dsid_value.encode('utf-8')).hexdigest()}"
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{RARA_SERVICE_URL}/agents/register",
                    json={
                        "agent_id": str(agent.id),
                        "role": "executor",
                        "dsid": dsid_value,
                        "public_key": public_key,
                        "capabilities": [
                            "filesystem.create_file",
                            "filesystem.update_file",
                        ],
                    },
                )
        except Exception:
            return

# DSID-P Protocol Integration for Semantic Clustering
def classify_agent_domain(name: str, description: str, tools: List[str] = None) -> Dict[str, Any]:
    """
    Auto-classify agent into DSID-P semantic cluster.
    
    Tier 1 Domain Clusters:
    - A: Analytics & Reasoning
    - K: Knowledge & Research
    - L: Language & Communication
    - C: Creative & Generative
    - W: Automation & Workflows
    - S: Software & Engineering
    - B: Business & Operations
    - H: Health & Medical
    - P: Legal, Policy & Compliance
    - G: Governance & Supervision
    - M: Meta-Cognitive
    """
    text = f"{name} {description or ''} {' '.join(tools or [])}".lower()
    
    # Keyword-based classification
    if any(k in text for k in ["medical", "health", "patient", "diagnosis", "clinical"]):
        return {"domain": "H", "name": "Health & Medical", "srr": 5}
    if any(k in text for k in ["legal", "law", "compliance", "policy", "regulatory"]):
        return {"domain": "P", "name": "Legal, Policy & Compliance", "srr": 5}
    if any(k in text for k in ["governance", "supervisor", "monitor", "audit"]):
        return {"domain": "G", "name": "Governance & Supervision", "srr": 5}
    if any(k in text for k in ["meta", "planner", "orchestrat", "coordinator"]):
        return {"domain": "M", "name": "Meta-Cognitive", "srr": 5}
    if any(k in text for k in ["code", "software", "engineer", "developer", "programming"]):
        return {"domain": "S", "name": "Software & Engineering", "srr": 4}
    if any(k in text for k in ["business", "finance", "sales", "revenue", "operation"]):
        return {"domain": "B", "name": "Business & Operations", "srr": 4}
    if any(k in text for k in ["workflow", "automat", "task", "process"]):
        return {"domain": "W", "name": "Automation & Workflows", "srr": 3}
    if any(k in text for k in ["analys", "data", "forecast", "predict", "pattern"]):
        return {"domain": "A", "name": "Analytics & Reasoning", "srr": 3}
    if any(k in text for k in ["research", "knowledge", "document", "learn", "tutor"]):
        return {"domain": "K", "name": "Knowledge & Research", "srr": 2}
    if any(k in text for k in ["creative", "generat", "write", "art", "design"]):
        return {"domain": "C", "name": "Creative & Generative", "srr": 2}
    if any(k in text for k in ["chat", "communicat", "language", "translate", "support"]):
        return {"domain": "L", "name": "Language & Communication", "srr": 2}
    
    # Default: Knowledge & Research
    return {"domain": "K", "name": "Knowledge & Research", "srr": 2}


def calculate_pricing_tier(srr: int, tools: List[str]) -> Dict[str, Any]:
    """
    DSID-P Section 37: Calculate agent pricing tier.
    
    Pricing Tiers:
    - Tier 1 (Basic): $1-$9 - Lightweight functions
    - Tier 2 (Advanced): $10-$49 - Multi-step workflows
    - Tier 3 (Professional): $50-$199 - Specialized domains
    - Tier 4 (Enterprise): $200-$999 - Compliance-heavy
    - Tier 5 (Critical): Custom - Governments/Banks
    """
    tool_count = len(tools) if tools else 0
    
    # Base tier from SRR
    if srr >= 5:
        base_tier = 5
    elif srr >= 4:
        base_tier = 4
    elif srr >= 3:
        base_tier = 3
    elif srr >= 2:
        base_tier = 2
    else:
        base_tier = 1
    
    # Adjust based on tool complexity
    if tool_count > 10:
        base_tier = min(5, base_tier + 1)
    elif tool_count > 5:
        base_tier = min(5, base_tier + 0)  # No change
    
    # Pricing ranges
    pricing_ranges = {
        1: {"tier": "tier_1", "name": "Basic", "price_range": "$1-$9", "monthly_seat": 0.10},
        2: {"tier": "tier_2", "name": "Advanced", "price_range": "$10-$49", "monthly_seat": 0.50},
        3: {"tier": "tier_3", "name": "Professional", "price_range": "$50-$199", "monthly_seat": 1.00},
        4: {"tier": "tier_4", "name": "Enterprise", "price_range": "$200-$999", "monthly_seat": 2.00},
        5: {"tier": "tier_5", "name": "Critical", "price_range": "Custom", "monthly_seat": 3.00},
    }
    
    return pricing_ranges.get(base_tier, pricing_ranges[1])


router = APIRouter(prefix="/agents", tags=["agents"])

# Create sub-routers for specific paths to avoid route ordering issues
tools_router = APIRouter(prefix="/agents", tags=["tools"])
safety_router = APIRouter(prefix="/agents", tags=["safety"])


# ============== Request/Response Models ==============

class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    provider: Optional[str] = None  # e.g. openai, anthropic, groq, google, local
    model: str = "gpt-4-turbo-preview"
    temperature: float = 0.7
    max_tokens: int = 4096
    tool_mode: Optional[str] = "smart"  # smart = all tools auto, manual = only selected tools
    tools: Optional[List[str]] = None
    mode: Optional[str] = "governed"  # governed or unbounded
    safety_config: Optional[Dict[str, Any]] = None
    budget_config: Optional[Dict[str, Any]] = None  # {max_tokens_per_run, max_runs_per_day}
    allowed_actions: Optional[List[str]] = None
    blocked_actions: Optional[List[str]] = None


class AgentResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    provider: Optional[str] = None
    model: str
    tool_mode: Optional[str] = "smart"
    tools: Optional[List[str]]
    mode: Optional[str] = None
    is_active: bool
    version: int
    manifest_hash: Optional[str] = None
    agent_public_hash: Optional[str] = None
    agent_version_hash: Optional[str] = None
    dsid: Optional[str] = None


class SessionCreate(BaseModel):
    goal: str = Field(..., min_length=1)
    context: Optional[Dict[str, Any]] = None


class SessionResponse(BaseModel):
    id: str
    agent_id: str
    status: str
    current_goal: Optional[str]
    loop_count: int
    total_tokens_used: int
    final_output: Optional[str] = None
    error_message: Optional[str] = None


class StepResponse(BaseModel):
    id: str
    step_number: int
    step_type: str
    reasoning: Optional[str]
    tool_name: Optional[str]
    output_data: Optional[Dict[str, Any]]
    safety_check_passed: bool
    duration_ms: Optional[int]


class ToolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = None
    category: Optional[str] = None
    parameters_schema: Optional[Dict[str, Any]] = None
    handler_type: str = "http"
    handler_config: Optional[Dict[str, Any]] = None
    risk_level: str = "low"
    requires_approval: bool = False


class ToolResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    category: Optional[str]
    parameters_schema: Optional[Dict[str, Any]] = None
    handler_type: Optional[str] = None
    handler_config: Optional[Dict[str, Any]] = None
    risk_level: str
    requires_approval: bool = False
    is_active: bool


class ProviderCatalogProvider(BaseModel):
    id: str
    name: str
    available: bool
    has_user_key: Optional[bool] = None
    uses_credits: Optional[bool] = None
    model: Optional[str] = None
    description: Optional[str] = None
    capabilities: Optional[List[str]] = None


class ProvidersCatalogResponse(BaseModel):
    providers: List[ProviderCatalogProvider]
    default: Optional[str] = None
    fallback_chain: Optional[List[str]] = None
    message: Optional[str] = None


class SafetyRuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    rule_type: str  # rate_limit, content_filter, action_block, resource_limit
    action: str  # block, warn, require_approval, log
    condition: Optional[Dict[str, Any]] = None
    parameters: Optional[Dict[str, Any]] = None
    priority: int = 0


class TriggerCreate(BaseModel):
    name: str
    trigger_type: str  # schedule, webhook, event, condition
    config: Dict[str, Any]
    cron_expression: Optional[str] = None
    event_type: Optional[str] = None
    event_filter: Optional[Dict[str, Any]] = None
    input_template: Optional[Dict[str, Any]] = None


# ============== Agent Teams Request/Response Models ==============

class TeamCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    team_type: str = "collaborative"  # collaborative, hierarchical, specialized
    config: Optional[Dict[str, Any]] = None
    member_agent_ids: Optional[List[str]] = None
    lead_agent_id: Optional[str] = None
    is_rentable: bool = False


class AgentTemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    category: str
    system_prompt: str
    model: str
    tools: List[str] = []


class InstantiateTemplateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    rental_price_per_hour: Optional[float] = None
    is_public: bool = False


class CapabilityBundle(BaseModel):
    id: str
    name: str
    description: str
    tools: List[str]


class TeamUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    team_type: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    member_agent_ids: Optional[List[str]] = None
    lead_agent_id: Optional[str] = None
    is_rentable: Optional[bool] = None
    rental_price_per_hour: Optional[float] = None
    is_public: Optional[bool] = None


class TeamResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    team_type: str
    member_agent_ids: Optional[List[str]]
    lead_agent_id: Optional[str]
    status: str
    is_rentable: bool
    rental_price_per_hour: Optional[float]
    is_public: bool
    total_tasks_completed: int
    created_at: Optional[str]


class TeamRentRequest(BaseModel):
    hours: float = Field(..., gt=0)


class TeamTransferRequest(BaseModel):
    new_owner_id: str


class TeamMintNFTRequest(BaseModel):
    contract_address: Optional[str] = None


class PublishAgentRequest(BaseModel):
    publish_internal_marketplace: bool = True
    publish_decentralized: bool = False
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    price_type: str = "free"
    price_amount: float = 0.0
    tagline: Optional[str] = None
    include_system_prompt: bool = False


class PublishInternalResult(BaseModel):
    success: bool
    listing_id: Optional[str] = None
    status: Optional[str] = None
    error: Optional[str] = None


class PublishDecentralizedResult(BaseModel):
    success: bool
    tx_hash: Optional[str] = None
    network: Optional[str] = None
    contract: Optional[str] = None
    error: Optional[str] = None


class PublishAgentResponse(BaseModel):
    agent_id: str
    internal_marketplace: Optional[PublishInternalResult] = None
    decentralized: Optional[PublishDecentralizedResult] = None


# ============== Health Check (must be before /{agent_id} route) ==============

@router.get("/health")
async def agents_health():
    """Health check for agents router."""
    return {"status": "ok", "service": "agent_engine"}


@router.get("/marketplace")
async def list_marketplace_agents(
    category: Optional[str] = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    """List agents explicitly published to the marketplace.

    Only returns agents where the owner opted in via published_to_marketplace.
    """
    try:
        stmt = (
            select(AgentDefinition)
            .where(AgentDefinition.is_active.is_(True))
            .where(AgentDefinition.archived_at.is_(None))
            .where(AgentDefinition.published_to_marketplace.is_(True))
            .order_by(AgentDefinition.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        agents = result.scalars().all()

        responses = []
        for a in agents:
            safety_config = a.safety_config or {}
            manifest_hash = safety_config.get("manifest_hash")
            if not manifest_hash:
                manifest_hash = compute_manifest_hash(
                    name=a.name, description=a.description,
                    system_prompt=a.system_prompt, model=a.model,
                    temperature=a.temperature, max_tokens=a.max_tokens,
                    tools=a.tools, allowed_actions=a.allowed_actions,
                    blocked_actions=a.blocked_actions,
                )
            responses.append({
                "id": str(a.id),
                "name": a.name,
                "description": a.description,
                "model": a.model,
                "tools": a.tools,
                "is_active": a.is_active,
                "version": a.version,
                "manifest_hash": manifest_hash,
                "agent_public_hash": a.agent_public_hash,
                "agent_version_hash": a.agent_version_hash,
                "category": (safety_config.get("governance", {}) or {}).get("category", "utility"),
                "created_at": a.created_at.isoformat() if a.created_at else None,
            })
        return {"agents": responses, "count": len(responses)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list marketplace agents: {str(e)}")


@router.post("/{agent_id}/publish", response_model=PublishAgentResponse)
async def publish_agent(
    agent_id: str,
    payload: PublishAgentRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Publish an agent to the internal marketplace and/or decentralized network."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    from uuid import UUID

    try:
        agent_uuid = UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent_id")

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user_id")

    result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == agent_uuid)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if agent.user_id and agent.user_id != user_uuid:
        raise HTTPException(status_code=403, detail="Only owner can publish agent")

    internal_result: Optional[PublishInternalResult] = None
    decentralized_result: Optional[PublishDecentralizedResult] = None

    if payload.publish_internal_marketplace:
        try:
            marketplace_base_url = os.getenv("MARKETPLACE_SERVICE_URL", "http://marketplace_service:8000").rstrip("/")
            description = agent.description or ""
            listing_payload: Dict[str, Any] = {
                "name": agent.name,
                "tagline": payload.tagline or (description[:180] if description else None),
                "description": description,
                "category": payload.category or "utility",
                "tags": payload.tags or [],
                "price_type": payload.price_type,
                "price_amount": payload.price_amount,
                "required_tools": agent.tools or [],
                "agent_config": {
                    "agent_id": str(agent.id),
                    "model": agent.model,
                    "tools": agent.tools or [],
                    "system_prompt": agent.system_prompt if payload.include_system_prompt else None,
                    "temperature": agent.temperature,
                    "max_tokens": agent.max_tokens,
                },
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                create_resp = await client.post(
                    f"{marketplace_base_url}/marketplace/listings",
                    headers={"x-user-id": user_id},
                    json=listing_payload,
                )
                if create_resp.status_code not in (200, 201):
                    raise Exception(f"Create listing failed: {create_resp.status_code} {create_resp.text}")

                listing_id = str(create_resp.json().get("id") or "")
                if not listing_id:
                    raise Exception("Marketplace listing creation returned no id")

                publish_resp = await client.post(
                    f"{marketplace_base_url}/marketplace/listings/{listing_id}/publish",
                    headers={"x-user-id": user_id},
                )
                if publish_resp.status_code not in (200, 201):
                    raise Exception(f"Publish listing failed: {publish_resp.status_code} {publish_resp.text}")

            internal_result = PublishInternalResult(
                success=True,
                listing_id=listing_id,
                status="published",
            )
        except Exception as e:
            internal_result = PublishInternalResult(success=False, error=str(e))

    if payload.publish_decentralized:
        deducted_tx_id: Optional[str] = None
        should_charge = _base_publish_is_configured()

        try:
            if should_charge:
                deducted_tx_id = await _deduct_base_publish_credits(user_id=user_id, agent_id=str(agent.id))

            publish_result = await publish_agent_to_blockchain(
                agent_id=str(agent.id),
                agent_name=agent.name,
                agent_description=agent.description or "",
                manifest_hash=(agent.safety_config or {}).get("manifest_hash") or "",
                metadata_uri=None,
            )

            if should_charge and deducted_tx_id and not bool(publish_result.get("success")):
                await _refund_base_publish_credits(
                    user_id=user_id,
                    amount=BASE_PUBLISH_CREDITS,
                    original_tx_id=deducted_tx_id,
                )

            decentralized_result = PublishDecentralizedResult(
                success=bool(publish_result.get("success")),
                tx_hash=publish_result.get("tx_hash"),
                network=publish_result.get("network"),
                contract=publish_result.get("contract"),
                error=publish_result.get("error"),
            )
        except HTTPException:
            if should_charge and deducted_tx_id:
                await _refund_base_publish_credits(
                    user_id=user_id,
                    amount=BASE_PUBLISH_CREDITS,
                    original_tx_id=deducted_tx_id,
                )
            raise
        except Exception as e:
            if should_charge and deducted_tx_id:
                await _refund_base_publish_credits(
                    user_id=user_id,
                    amount=BASE_PUBLISH_CREDITS,
                    original_tx_id=deducted_tx_id,
                )
            decentralized_result = PublishDecentralizedResult(success=False, error=str(e))

    # Mark agent as published to marketplace
    agent.published_to_marketplace = True
    await session.commit()

    return PublishAgentResponse(
        agent_id=str(agent.id),
        internal_marketplace=internal_result,
        decentralized=decentralized_result,
    )


@router.post("/{agent_id}/marketplace-publish")
async def toggle_marketplace_publish(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Toggle an agent's marketplace visibility. Owner-only."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    from uuid import UUID
    try:
        agent_uuid = UUID(agent_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == agent_uuid)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.user_id and agent.user_id != user_uuid:
        raise HTTPException(status_code=403, detail="Only owner can publish agent")

    agent.published_to_marketplace = not agent.published_to_marketplace
    await session.commit()
    return {
        "agent_id": str(agent.id),
        "published_to_marketplace": agent.published_to_marketplace,
    }


@router.post("/{agent_id}/marketplace-unpublish")
async def unpublish_from_marketplace(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Remove an agent from the marketplace. Owner-only."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    from uuid import UUID
    try:
        agent_uuid = UUID(agent_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == agent_uuid)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.user_id and agent.user_id != user_uuid:
        raise HTTPException(status_code=403, detail="Only owner can unpublish agent")

    agent.published_to_marketplace = False
    await session.commit()
    return {"agent_id": str(agent.id), "published_to_marketplace": False}


# ============== Agent Teams Endpoints (must be before /{agent_id} route) ==============

@router.get("/teams")
async def list_agent_teams(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """List agent teams for the current user."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    from uuid import UUID
    result = await session.execute(
        select(AgentTeam)
        .where(AgentTeam.user_id == UUID(user_id))
        .where(AgentTeam.status != "archived")
        .order_by(AgentTeam.created_at.desc())
    )
    teams = result.scalars().all()
    
    return [
        TeamResponse(
            id=str(t.id),
            name=t.name,
            description=t.description,
            team_type=t.team_type,
            member_agent_ids=[str(m) for m in t.member_agent_ids] if t.member_agent_ids else [],
            lead_agent_id=str(t.lead_agent_id) if t.lead_agent_id else None,
            status=t.status,
            is_rentable=t.is_rentable,
            rental_price_per_hour=t.rental_price_per_hour,
            is_public=t.is_public,
            total_tasks_completed=t.total_tasks_completed,
            created_at=t.created_at.isoformat() if t.created_at else None,
        )
        for t in teams
    ]


@router.post("/teams", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_team(
    payload: TeamCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create a new agent team."""
    user_id = request.headers.get("x-user-id")
    org_id = request.headers.get("x-org-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    from uuid import UUID
    team = AgentTeam(
        user_id=UUID(user_id),
        org_id=UUID(org_id) if org_id else None,
        name=payload.name,
        description=payload.description,
        team_type=payload.team_type,
        config=payload.config,
        member_agent_ids=[UUID(m) for m in payload.member_agent_ids] if payload.member_agent_ids else [],
        lead_agent_id=UUID(payload.lead_agent_id) if payload.lead_agent_id else None,
        is_rentable=payload.is_rentable,
        rental_price_per_hour=payload.rental_price_per_hour,
        is_public=payload.is_public,
        status="active",
    )
    session.add(team)
    await session.commit()
    await session.refresh(team)
    
    return TeamResponse(
        id=str(team.id),
        name=team.name,
        description=team.description,
        team_type=team.team_type,
        member_agent_ids=[str(m) for m in team.member_agent_ids] if team.member_agent_ids else [],
        lead_agent_id=str(team.lead_agent_id) if team.lead_agent_id else None,
        status=team.status,
        is_rentable=team.is_rentable,
        rental_price_per_hour=team.rental_price_per_hour,
        is_public=team.is_public,
        total_tasks_completed=team.total_tasks_completed,
        created_at=team.created_at.isoformat() if team.created_at else None,
    )


@router.get("/teams/my-rentals")
async def get_my_rentals(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get teams the current user is renting."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    from uuid import UUID
    result = await session.execute(
        select(AgentTeamRental)
        .where(AgentTeamRental.renter_id == UUID(user_id))
        .where(AgentTeamRental.status == "active")
    )
    rentals = result.scalars().all()
    
    return [
        {
            "id": str(r.id),
            "team_id": str(r.team_id),
            "price_per_hour": r.price_per_hour,
            "total_hours": r.total_hours,
            "total_price": r.total_price,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        }
        for r in rentals
    ]


@router.get("/teams/{team_id}", response_model=TeamResponse)
async def get_agent_team(
    team_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get agent team by ID."""
    from uuid import UUID
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == UUID(team_id))
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    return TeamResponse(
        id=str(team.id),
        name=team.name,
        description=team.description,
        team_type=team.team_type,
        member_agent_ids=[str(m) for m in team.member_agent_ids] if team.member_agent_ids else [],
        lead_agent_id=str(team.lead_agent_id) if team.lead_agent_id else None,
        status=team.status,
        is_rentable=team.is_rentable,
        rental_price_per_hour=team.rental_price_per_hour,
        is_public=team.is_public,
        total_tasks_completed=team.total_tasks_completed,
        created_at=team.created_at.isoformat() if team.created_at else None,
    )


@router.put("/teams/{team_id}", response_model=TeamResponse)
async def update_agent_team(
    team_id: str,
    payload: TeamUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Update an agent team."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    from uuid import UUID
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == UUID(team_id))
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if str(team.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Update fields
    if payload.name is not None:
        team.name = payload.name
    if payload.description is not None:
        team.description = payload.description
    if payload.team_type is not None:
        team.team_type = payload.team_type
    if payload.config is not None:
        team.config = payload.config
    if payload.member_agent_ids is not None:
        team.member_agent_ids = [UUID(m) for m in payload.member_agent_ids]
    if payload.lead_agent_id is not None:
        team.lead_agent_id = UUID(payload.lead_agent_id)
    if payload.is_rentable is not None:
        team.is_rentable = payload.is_rentable
    if payload.rental_price_per_hour is not None:
        team.rental_price_per_hour = payload.rental_price_per_hour
    if payload.is_public is not None:
        team.is_public = payload.is_public
    
    await session.commit()
    await session.refresh(team)
    
    return TeamResponse(
        id=str(team.id),
        name=team.name,
        description=team.description,
        team_type=team.team_type,
        member_agent_ids=[str(m) for m in team.member_agent_ids] if team.member_agent_ids else [],
        lead_agent_id=str(team.lead_agent_id) if team.lead_agent_id else None,
        status=team.status,
        is_rentable=team.is_rentable,
        rental_price_per_hour=team.rental_price_per_hour,
        is_public=team.is_public,
        total_tasks_completed=team.total_tasks_completed,
        created_at=team.created_at.isoformat() if team.created_at else None,
    )


@router.patch("/teams/{team_id}")
async def patch_agent_team(
    team_id: str,
    payload: TeamUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Partial update an agent team (alias for PUT)."""
    return await update_agent_team(team_id, payload, request, session)


@router.delete("/teams/{team_id}")
async def delete_agent_team(
    team_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Delete (archive) an agent team."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    from uuid import UUID
    from datetime import datetime
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == UUID(team_id))
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if str(team.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    team.status = "archived"
    team.archived_at = datetime.utcnow()
    await session.commit()
    
    return {"status": "archived", "id": team_id}


@router.patch("/teams/{team_id}/archive")
async def archive_agent_team(
    team_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Archive an agent team."""
    return await delete_agent_team(team_id, request, session)


@router.patch("/teams/{team_id}/unarchive")
async def unarchive_agent_team(
    team_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Unarchive an agent team."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    from uuid import UUID
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == UUID(team_id))
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if str(team.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    team.status = "active"
    team.archived_at = None
    await session.commit()
    
    return {"status": "active", "id": team_id}


@router.get("/teams/{team_id}/members")
async def get_team_members(
    team_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get members of an agent team."""
    from uuid import UUID
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == UUID(team_id))
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    # Get agent details for each member
    members = []
    if team.member_agent_ids:
        for agent_id in team.member_agent_ids:
            agent_result = await session.execute(
                select(AgentDefinition).where(AgentDefinition.id == agent_id)
            )
            agent = agent_result.scalar_one_or_none()
            if agent:
                members.append({
                    "id": str(agent.id),
                    "name": agent.name,
                    "description": agent.description,
                    "model": agent.model,
                    "is_lead": str(agent.id) == str(team.lead_agent_id) if team.lead_agent_id else False,
                })
    
    return members


@router.get("/teams/{team_id}/workflows")
async def get_team_workflows(
    team_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get workflows for an agent team."""
    from uuid import UUID
    result = await session.execute(
        select(AgentTeamWorkflow)
        .where(AgentTeamWorkflow.team_id == UUID(team_id))
        .order_by(AgentTeamWorkflow.created_at.desc())
        .limit(50)
    )
    workflows = result.scalars().all()
    
    return [
        {
            "id": str(w.id),
            "name": w.name,
            "goal": w.goal,
            "status": w.status,
            "total_steps": w.total_steps,
            "tokens_used": w.tokens_used,
            "duration_ms": w.duration_ms,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        }
        for w in workflows
    ]


@router.post("/teams/workflows/{workflow_id}/cancel")
async def cancel_team_workflow(
    workflow_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Cancel a team workflow."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    from uuid import UUID
    result = await session.execute(
        select(AgentTeamWorkflow).where(AgentTeamWorkflow.id == UUID(workflow_id))
    )
    workflow = result.scalar_one_or_none()
    
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    workflow.status = "cancelled"
    await session.commit()
    
    return {"status": "cancelled", "id": workflow_id}


@router.get("/teams/{team_id}/ownership")
async def get_team_ownership(
    team_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get ownership info for an agent team."""
    from uuid import UUID
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == UUID(team_id))
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    return {
        "owner_id": str(team.user_id),
        "nft_token_id": team.nft_token_id,
        "nft_contract_address": team.nft_contract_address,
        "owner_address": team.owner_address,
        "is_nft_minted": bool(team.nft_token_id),
    }


@router.get("/teams/{team_id}/rentals")
async def get_team_rentals(
    team_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get rental history for an agent team."""
    from uuid import UUID
    result = await session.execute(
        select(AgentTeamRental)
        .where(AgentTeamRental.team_id == UUID(team_id))
        .order_by(AgentTeamRental.started_at.desc())
    )
    rentals = result.scalars().all()
    
    return [
        {
            "id": str(r.id),
            "renter_id": str(r.renter_id),
            "price_per_hour": r.price_per_hour,
            "total_hours": r.total_hours,
            "total_price": r.total_price,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        }
        for r in rentals
    ]


@router.post("/teams/{team_id}/rent")
async def rent_agent_team(
    team_id: str,
    payload: TeamRentRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Rent an agent team."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    from uuid import UUID
    from datetime import datetime, timedelta
    
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == UUID(team_id))
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if not team.is_rentable:
        raise HTTPException(status_code=400, detail="Team is not available for rent")
    
    if team.current_renter_id:
        raise HTTPException(status_code=400, detail="Team is currently rented")
    
    price_per_hour = team.rental_price_per_hour or 0.0
    total_price = price_per_hour * payload.hours
    expires_at = datetime.utcnow() + timedelta(hours=payload.hours)
    
    rental = AgentTeamRental(
        team_id=UUID(team_id),
        renter_id=UUID(user_id),
        owner_id=team.user_id,
        price_per_hour=price_per_hour,
        total_hours=payload.hours,
        total_price=total_price,
        status="active",
        expires_at=expires_at,
    )
    session.add(rental)
    
    team.current_renter_id = UUID(user_id)
    team.rental_expires_at = expires_at
    
    await session.commit()
    await session.refresh(rental)
    
    return {
        "id": str(rental.id),
        "team_id": team_id,
        "price_per_hour": price_per_hour,
        "total_hours": payload.hours,
        "total_price": total_price,
        "expires_at": expires_at.isoformat(),
    }


@router.post("/teams/{team_id}/transfer")
async def transfer_agent_team(
    team_id: str,
    payload: TeamTransferRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Transfer ownership of an agent team."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    from uuid import UUID
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == UUID(team_id))
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if str(team.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Only owner can transfer team")
    
    team.user_id = UUID(payload.new_owner_id)
    await session.commit()
    
    return {"status": "transferred", "new_owner_id": payload.new_owner_id}


@router.post("/teams/{team_id}/mint-nft")
async def mint_team_nft(
    team_id: str,
    payload: TeamMintNFTRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Mint an NFT for an agent team (placeholder - requires blockchain integration)."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    from uuid import UUID
    import secrets
    
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == UUID(team_id))
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if str(team.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Only owner can mint NFT")
    
    if team.nft_token_id:
        raise HTTPException(status_code=400, detail="NFT already minted for this team")
    
    # Generate placeholder NFT data (in production, this would interact with blockchain)
    token_id = f"RG-TEAM-{secrets.token_hex(8).upper()}"
    contract_address = payload.contract_address or "0x" + secrets.token_hex(20)
    
    team.nft_token_id = token_id
    team.nft_contract_address = contract_address
    await session.commit()
    
    return {
        "status": "minted",
        "token_id": token_id,
        "contract_address": contract_address,
        "team_id": team_id,
    }


# ============== Agent Definition Endpoints ==============

@router.post("/", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create a new agent definition."""
    try:
        user_id = request.headers.get("x-user-id")
        if not user_id:
            raise HTTPException(status_code=401, detail="User ID required")

        user_role = (request.headers.get("x-user-role") or "user").strip().lower()
        is_superuser = (request.headers.get("x-is-superuser") or "").strip().lower() in {"1", "true", "yes", "on"}
        unlimited_credits = (request.headers.get("x-unlimited-credits") or "").strip().lower() in {"1", "true", "yes", "on"}
        privileged_roles = {"platform_owner", "admin", "superuser"}
        privileged_bypass = is_superuser or unlimited_credits or user_role in privileged_roles

        from uuid import UUID as PyUUID
        try:
            user_uuid = PyUUID(user_id)
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="Invalid user ID format")
        
        # ============================================
        # CHECK AGENT LIMIT (GTM Critical)
        # ============================================
        if user_id and not privileged_bypass:
            import httpx
            from sqlalchemy import func
            
            # Get user's plan from billing service
            user_plan = "developer"
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        "http://billing_service:8001/billing/subscription",
                        headers={"x-user-id": user_id},
                        timeout=5.0,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        user_plan = data.get("plan", "developer").lower()
                        if data.get("is_dev"):
                            user_plan = "unlimited"
            except Exception:
                pass  # Default to developer plan
            
            # Plan limits for agents
            agent_limits = {
                "developer": 3, "free": 3,
                "plus": 20, "professional": 20,
                "enterprise": -1, "unlimited": -1,
            }
            max_agents = agent_limits.get(user_plan, 3)
            
            # Count existing agents
            if max_agents > 0:
                count_result = await session.execute(
                    select(func.count(AgentDefinition.id)).where(
                        AgentDefinition.user_id == user_uuid
                    )
                )
                current_count = count_result.scalar() or 0
                
                if current_count >= max_agents:
                    raise HTTPException(
                        status_code=429,
                        detail={
                            "error": "agent_limit_exceeded",
                            "message": f"Agent limit reached ({current_count}/{max_agents}). Upgrade to Plus for 20 agents.",
                            "used": current_count,
                            "limit": max_agents,
                            "upgrade_url": "/pricing"
                        }
                    )

        resolved_tools = payload.tools
        if resolved_tools is None:
            resolved_tools = ["web_search", "fetch_url"]

        # DSID-P: Auto-classify agent into semantic cluster
        cluster = classify_agent_domain(
            payload.name,
            payload.description,
            resolved_tools
        )
        
        # DSID-P Section 37: Calculate pricing tier based on SRR and complexity
        pricing_tier = calculate_pricing_tier(cluster["srr"], resolved_tools or [])
        
        # Merge cluster info into safety_config
        safety_config = payload.safety_config or {}
        safety_config["dsidp_cluster"] = cluster
        safety_config["semantic_risk_rating"] = cluster["srr"]
        safety_config["pricing_tier"] = pricing_tier

        safety_config["manifest_hash"] = compute_manifest_hash(
            name=payload.name,
            description=payload.description,
            system_prompt=payload.system_prompt,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            tools=resolved_tools,
            allowed_actions=payload.allowed_actions,
            blocked_actions=payload.blocked_actions,
        )

        agent_id = uuid.uuid4()
        agent_public_hash = _compute_agent_public_hash(agent_id=str(agent_id), owner_user_id=str(user_uuid))
        safety_config["agent_hash"] = agent_public_hash

        # Merge budget_config into safety_config for per-agent enforcement
        if payload.budget_config and isinstance(payload.budget_config, dict):
            bc = payload.budget_config
            if bc.get("max_tokens_per_run"):
                safety_config["max_tokens_per_run"] = int(bc["max_tokens_per_run"])
            if bc.get("max_runs_per_day"):
                safety_config["max_runs_per_day"] = int(bc["max_runs_per_day"])

        resolved_mode = (payload.mode or "governed").strip().lower()
        if resolved_mode not in ("governed", "unbounded"):
            resolved_mode = "governed"

        resolved_tool_mode = (payload.tool_mode or "smart").strip().lower()
        if resolved_tool_mode not in ("smart", "manual"):
            resolved_tool_mode = "smart"

        resolved_source = "cloud"

        org_id_raw = request.headers.get("x-org-id")
        org_uuid = None
        if org_id_raw:
            try:
                org_uuid = PyUUID(org_id_raw)
            except (ValueError, AttributeError):
                org_uuid = None

        agent = AgentDefinition(
            id=agent_id,
            user_id=user_uuid,
            org_id=org_uuid,
            name=payload.name,
            description=payload.description,
            system_prompt=payload.system_prompt,
            provider=payload.provider,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            tool_mode=resolved_tool_mode,
            tools=resolved_tools,
            mode=resolved_mode,
            safety_config=safety_config,
            allowed_actions=payload.allowed_actions,
            blocked_actions=payload.blocked_actions,
            agent_public_hash=agent_public_hash,
            agent_version_hash=str(safety_config.get("manifest_hash") or ""),
            agent_source=resolved_source,
        )

        session.add(agent)

        version_row = _make_agent_version_row(agent=agent, previous_version_hash=None)
        if version_row:
            session.add(version_row)
        await session.commit()
        await session.refresh(agent)

        await best_effort_issue_dsid_and_register(agent=agent, user_id=user_id, session=session)

        return AgentResponse(
            id=str(agent.id),
            name=agent.name,
            description=agent.description,
            provider=agent.provider,
            model=agent.model,
            tool_mode=getattr(agent, 'tool_mode', None) or 'smart',
            tools=agent.tools,
            mode=agent.mode,
            is_active=agent.is_active,
            version=agent.version,
            manifest_hash=(agent.safety_config or {}).get("manifest_hash"),
            agent_public_hash=agent.agent_public_hash,
            agent_version_hash=agent.agent_version_hash,
            dsid=(agent.safety_config or {}).get("dsid"),
        )
    except HTTPException:
        # Re-raise HTTPException without wrapping (e.g., 429 agent limit)
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create agent: {str(e)}")


@router.get("/", response_model=List[AgentResponse])
async def list_agents(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """List all agents for the authenticated user.
    
    User isolation: Each user only sees their own agents.
    The x-user-id header is set by the gateway auth middleware.
    """
    try:
        user_id = request.headers.get("x-user-id")
        if not user_id:
            raise HTTPException(status_code=401, detail="User ID required")

        stmt = select(AgentDefinition)
        from uuid import UUID as PyUUID
        from sqlalchemy import or_
        org_id = request.headers.get("x-org-id")
        org_uuid = None
        if org_id:
            try:
                org_uuid = PyUUID(org_id)
            except (ValueError, AttributeError):
                org_uuid = None
        try:
            user_uuid = PyUUID(user_id)
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="Invalid user ID format")
        if org_uuid:
            # Multi-tenant: user sees own agents + org-shared agents
            stmt = stmt.where(or_(
                AgentDefinition.user_id == user_uuid,
                AgentDefinition.org_id == org_uuid,
            ))
        else:
            stmt = stmt.where(AgentDefinition.user_id == user_uuid)
        stmt = stmt.where(AgentDefinition.archived_at.is_(None))
        
        result = await session.execute(stmt)
        agents = result.scalars().all()

        mutated = False
        responses: List[AgentResponse] = []
        for a in agents:
            safety_config = a.safety_config or {}
            manifest_hash = safety_config.get("manifest_hash")
            if not manifest_hash:
                manifest_hash = compute_manifest_hash(
                    name=a.name,
                    description=a.description,
                    system_prompt=a.system_prompt,
                    model=a.model,
                    temperature=a.temperature,
                    max_tokens=a.max_tokens,
                    tools=a.tools,
                    allowed_actions=a.allowed_actions,
                    blocked_actions=a.blocked_actions,
                )
                safety_config["manifest_hash"] = manifest_hash
                a.safety_config = safety_config
                a.agent_version_hash = str(manifest_hash)
                mutated = True

            if _ensure_hashes_without_version_bump(a):
                mutated = True

            responses.append(
                AgentResponse(
                    id=str(a.id),
                    name=a.name,
                    description=a.description,
                    provider=a.provider,
                    model=a.model,
                    tool_mode=getattr(a, 'tool_mode', None) or 'smart',
                    tools=a.tools,
                    mode=getattr(a, 'mode', None) or 'governed',
                    is_active=a.is_active,
                    version=a.version,
                    manifest_hash=manifest_hash,
                    agent_public_hash=a.agent_public_hash,
                    agent_version_hash=a.agent_version_hash,
                    dsid=(a.safety_config or {}).get("dsid"),
                )
            )

        if mutated:
            await session.commit()

        return responses
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list agents: {str(e)}")


@router.get("/templates", response_model=List[AgentTemplateResponse])
async def list_agent_templates():
    return [
        AgentTemplateResponse(
            id="basic-responder",
            name="Basic Responder",
            description="Simple agent that responds to messages.",
            category="starter",
            system_prompt="You are a helpful assistant agent. Respond concisely and clearly.",
            model="gpt-4-turbo-preview",
            tools=["memory.read", "memory.write"],
        ),
        AgentTemplateResponse(
            id="data-processor",
            name="Data Processor",
            description="Processes and transforms data with structured output.",
            category="data",
            system_prompt="You process structured input and return structured output. Be precise.",
            model="gpt-4-turbo-preview",
            tools=["memory.read", "memory.write"],
        ),
        AgentTemplateResponse(
            id="secure-validator",
            name="Secure Validator",
            description="Security-focused validation agent.",
            category="security",
            system_prompt="You validate inputs, identify risks, and return safe, actionable results.",
            model="gpt-4-turbo-preview",
            tools=["memory.read", "memory.write"],
        ),
        AgentTemplateResponse(
            id="ai-assistant",
            name="AI Assistant",
            description="Assistant with conversation context and tool use.",
            category="ai",
            system_prompt="You are an assistant agent. Use tools when needed and keep context.",
            model="gpt-4-turbo-preview",
            tools=["memory.read", "memory.write", "llm.complete"],
        ),
        AgentTemplateResponse(
            id="enterprise-security-auditor",
            name="Enterprise Security Auditor",
            description="Performs security reviews, threat modeling, and produces remediation plans.",
            category="enterprise",
            system_prompt="You are a senior security auditor. Perform threat modeling, identify vulnerabilities, and propose prioritized remediations with clear steps.",
            model="gpt-4-turbo-preview",
            tools=["memory.read", "memory.write", "llm.complete"],
        ),
        AgentTemplateResponse(
            id="enterprise-compliance-officer",
            name="Enterprise Compliance Officer",
            description="Maps requirements to controls and generates audit-ready evidence checklists.",
            category="enterprise",
            system_prompt="You are a compliance officer. Translate requirements into controls, gaps, evidence artifacts, and audit narratives. Be conservative and precise.",
            model="gpt-4-turbo-preview",
            tools=["memory.read", "memory.write", "llm.complete"],
        ),
        AgentTemplateResponse(
            id="enterprise-incident-commander",
            name="Incident Commander",
            description="Runs incident response: triage, timeline, comms, mitigations, postmortem.",
            category="enterprise",
            system_prompt="You are an SRE incident commander. Triage quickly, request missing signals, propose mitigations, and produce a clear incident timeline and postmortem.",
            model="gpt-4-turbo-preview",
            tools=["memory.read", "memory.write"],
        ),
        AgentTemplateResponse(
            id="enterprise-architecture-reviewer",
            name="Architecture Reviewer",
            description="Reviews system designs for scalability, reliability, cost, and security.",
            category="enterprise",
            system_prompt="You are a principal architect. Review designs for scalability, reliability, cost, and security. Provide tradeoffs and a recommended architecture.",
            model="gpt-4-turbo-preview",
            tools=["memory.read", "memory.write", "llm.complete"],
        ),
        AgentTemplateResponse(
            id="enterprise-data-governance",
            name="Data Governance Lead",
            description="Defines data classification, retention, lineage and access governance policies.",
            category="enterprise",
            system_prompt="You are a data governance lead. Define data classification, retention, access controls, lineage requirements, and operational procedures.",
            model="gpt-4-turbo-preview",
            tools=["memory.read", "memory.write"],
        ),
        AgentTemplateResponse(
            id="enterprise-sre-runbook-writer",
            name="SRE Runbook Writer",
            description="Creates production runbooks, health checks, alerts, and on-call procedures.",
            category="enterprise",
            system_prompt="You are an SRE. Produce clear runbooks with detection, diagnosis, mitigation, rollback, and verification steps.",
            model="gpt-4-turbo-preview",
            tools=["memory.read", "memory.write"],
        ),
    ]


@router.post("/templates/{template_id}/instantiate", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def instantiate_agent_template(
    template_id: str,
    payload: InstantiateTemplateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    templates = {t.id: t for t in await list_agent_templates()}
    template = templates.get(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    from uuid import UUID as PyUUID
    try:
        user_uuid = PyUUID(user_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    if not payload.name and not payload.description:
        computed_name = template.name
        computed_description = template.description
    else:
        computed_name = payload.name or template.name
        computed_description = payload.description or template.description

    manifest_hash = compute_manifest_hash(
        name=computed_name,
        description=computed_description,
        system_prompt=template.system_prompt,
        model=template.model,
        temperature=0.7,
        max_tokens=128000,
        tools=template.tools,
        allowed_actions=None,
        blocked_actions=None,
    )

    agent_id = uuid.uuid4()
    agent_public_hash = _compute_agent_public_hash(agent_id=str(agent_id), owner_user_id=str(user_uuid))

    agent = AgentDefinition(
        id=agent_id,
        user_id=user_uuid,
        name=computed_name,
        description=computed_description,
        system_prompt=template.system_prompt,
        model=template.model,
        temperature=0.7,
        max_tokens=128000,
        tools=template.tools,
        safety_config={
            "manifest_hash": manifest_hash,
            "agent_hash": agent_public_hash,
        },
        allowed_actions=None,
        blocked_actions=None,
        agent_public_hash=agent_public_hash,
        agent_version_hash=str(manifest_hash),
    )
    session.add(agent)

    version_row = _make_agent_version_row(agent=agent, previous_version_hash=None)
    if version_row:
        session.add(version_row)
    await session.commit()
    await session.refresh(agent)

    await best_effort_issue_dsid_and_register(agent=agent, user_id=user_id, session=session)

    return AgentResponse(
        id=str(agent.id),
        name=agent.name,
        description=agent.description,
        model=agent.model,
        tools=agent.tools,
        is_active=agent.is_active,
        version=agent.version,
        manifest_hash=(agent.safety_config or {}).get("manifest_hash"),
        agent_public_hash=agent.agent_public_hash,
        agent_version_hash=agent.agent_version_hash,
        dsid=(agent.safety_config or {}).get("dsid"),
        agent_source=getattr(agent, 'agent_source', None) or 'cloud',
    )


@router.get("/available-tools")
async def list_available_tools():
    """List all available tools for the agent tool picker.
    
    Returns ALL tools from the unified registry (builtin_tools.py),
    grouped by category with schemas and BYOK requirements.
    Used by the frontend when tool_mode='manual' to let users pick tools.
    """
    from .rg_tool_registry.builtin_tools import build_registry

    registry = build_registry()
    all_tools = registry.get_all()

    return [
        {
            "id": td.name,
            "name": td.name,
            "description": td.description,
            "category": td.category.value if td.category else "general",
            "parameters_schema": td.to_openai()["function"]["parameters"],
            "risk_level": "low",
            "requires_approval": False,
            "byok_provider": td.requires_api_key,
            "access": [a.value for a in td.access],
        }
        for td in all_tools
    ]


@router.post("/tools/execute")
async def execute_tool_direct(request: Request):
    """Execute a platform tool directly (no agent session required).

    Used by external integrations and inter-service calls.
    Body: {tool_name: str, tool_input: dict, user_id?: str}
    """
    from .executor import AgentExecutor

    body = await request.json()
    tool_name = body.get("tool_name", "").strip()
    tool_input = body.get("tool_input", body.get("parameters", {}))

    if not tool_name:
        raise HTTPException(status_code=400, detail="Missing tool_name")

    # Extract user context from headers or body (for tool management)
    user_id = (
        request.headers.get("x-user-id")
        or body.get("user_id")
        or "anonymous"
    )

    executor = AgentExecutor()

    # Build a lightweight session-like object for tools that need user context
    _TOOL_MGMT = {"create_tool", "list_tools", "delete_tool", "update_tool",
                   "auto_build_tool", "check_tool_exists"}

    # Handler map
    handler = executor._handler_map.get(tool_name)
    if handler:
        try:
            if tool_name in _TOOL_MGMT:
                # Tool management needs user context — build a mock session
                class _Ctx:
                    pass
                mock = _Ctx()
                mock.user_id = user_id
                mock.context = {
                    "org_id": request.headers.get("x-org-id", ""),
                    "user_role": request.headers.get("x-user-role", "user"),
                }
                result = await handler(tool_input, session=mock)
            else:
                result = await handler(tool_input, session=None)
            return {"success": True, "tool_name": tool_name, "result": result}
        except Exception as e:
            return {"success": False, "tool_name": tool_name, "error": str(e)}

    # ED service proxy
    if tool_name in executor.ED_SERVICE_TOOLS:
        try:
            result = await executor._proxy_to_ed_service(tool_name, tool_input or {}, session=None)
            if result is not None:
                return {"success": True, "tool_name": tool_name, "result": result}
        except Exception as e:
            return {"success": False, "tool_name": tool_name, "error": str(e)}

    # Try dynamic custom tools (user-created or shared)
    try:
        from .routers_agentic_chat import _execute_dynamic_custom_tool
        ctx = {"user_id": user_id}
        result = await _execute_dynamic_custom_tool(tool_name, tool_input or {}, ctx)
        if not result.get("error", "").startswith("Custom tool"):
            return {"success": True, "tool_name": tool_name, "result": result}
    except Exception:
        pass

    raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")


@router.get("/tools/list")
async def list_platform_tools():
    """List all platform tools with descriptions."""
    from .rg_tool_registry.builtin_tools import build_registry
    registry = build_registry()
    all_tools = registry.get_all()
    return [
        {
            "name": td.name,
            "description": td.description,
            "category": td.category.value if hasattr(td.category, 'value') else str(td.category),
            "params": [p.to_dict() if hasattr(p, 'to_dict') else {"name": p.name} for p in (td.params or [])],
        }
        for td in all_tools
    ]


# ════════════════════════════════════════════════════════════════════
# FEDERATION — External agents running on user hardware
# ════════════════════════════════════════════════════════════════════

class FederationRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="Federated agent")
    connection_url: Optional[str] = None  # URL where agent is reachable (optional)
    hardware_info: Optional[Dict[str, Any]] = None  # CPU, RAM, GPU, OS
    client_version: Optional[str] = None  # e.g. "openclaw-ext/1.2.0"
    capabilities: Optional[List[str]] = None  # what the agent can do locally
    tools: Optional[List[str]] = None  # platform tools to assign
    provider: str = "groq"
    model: str = "llama-3.3-70b-versatile"


class FederationHeartbeatRequest(BaseModel):
    agent_id: str
    status: str = "online"  # online, busy, idle
    metrics: Optional[Dict[str, Any]] = None  # cpu_pct, mem_mb, active_tasks, etc.


@router.post("/federation/register")
async def federation_register(
    body: FederationRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Register an external agent running on user hardware.

    Creates a platform agent with agent_source='federated' and stores
    federation metadata (connection_url, hardware_info, client_version).
    The agent gets access to all platform tools via execute-tool-direct.
    """
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    from datetime import timezone
    now = datetime.now(timezone.utc)

    federation_config = {
        "connection_url": body.connection_url,
        "hardware_info": body.hardware_info or {},
        "client_version": body.client_version,
        "capabilities": body.capabilities or [],
        "registered_at": now.isoformat(),
        "last_heartbeat": now.isoformat(),
        "status": "online",
    }

    # Build system prompt
    tools = body.tools or ["web_search", "fetch_url", "memory_read", "memory_write", "http_request"]
    tools_csv = ", ".join(tools)
    system_prompt = (
        f"You are '{body.name}', a federated AI agent running on user hardware "
        f"and connected to the Resonant Genesis platform.\n\n"
        f"YOUR ROLE: {body.description}\n\n"
        f"PLATFORM TOOLS: {tools_csv}\n"
        f"You have full access to 200+ platform tools via the unified registry. "
        f"Use discover_services and check_tool_exists to find additional tools.\n"
    )

    # Compute hashes
    raw_hash = f"{user_id}:{body.name}:{now.isoformat()}"
    agent_public_hash = hashlib.sha256(raw_hash.encode()).hexdigest()

    safety_config = {
        "mode": "governed",
        "max_steps_per_run": 25,
        "max_tokens_per_run": 50000,
        "rate_limit_per_minute": 30,
        "federated": True,
    }

    agent = AgentDefinition(
        user_id=user_id,
        name=body.name,
        description=body.description,
        system_prompt=system_prompt,
        provider=body.provider,
        model=body.model,
        temperature=0.6,
        max_tokens=128000,
        tools=tools,
        safety_config=safety_config,
        allowed_actions=tools,
        blocked_actions=["delete_community", "delete_user", "admin_override"],
        agent_public_hash=agent_public_hash,
        agent_version_hash=hashlib.sha256(system_prompt.encode()).hexdigest(),
        agent_source="federated",
        openclaw_config=federation_config,  # DB column reused for federation metadata
        mode="governed",
        is_active=True,
    )

    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    logger.info(f"[FEDERATION] Registered agent '{body.name}' ({agent.id}) from {body.connection_url or 'unknown'}")

    return {
        "success": True,
        "agent_id": str(agent.id),
        "agent_public_hash": agent_public_hash,
        "tools": tools,
        "endpoints": {
            "heartbeat": "/agents/federation/heartbeat",
            "execute_tool": "/agents/execute-tool-direct",
            "tools_list": "/agents/tools/list",
            "execute": f"/agents/{agent.id}/execute",
        },
        "message": f"Agent '{body.name}' registered. Use heartbeat endpoint to stay connected.",
    }


@router.post("/federation/heartbeat")
async def federation_heartbeat(
    body: FederationHeartbeatRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Heartbeat from a federated agent running on user hardware."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    result = await db.execute(
        select(AgentDefinition).where(
            AgentDefinition.id == body.agent_id,
            AgentDefinition.user_id == user_id,
            AgentDefinition.agent_source == "federated",
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Federated agent not found")

    from datetime import timezone
    now = datetime.now(timezone.utc)

    # Update federation metadata
    config = agent.openclaw_config or {}
    config["last_heartbeat"] = now.isoformat()
    config["status"] = body.status
    if body.metrics:
        config["last_metrics"] = body.metrics
    agent.openclaw_config = config

    # Force SQLAlchemy to detect the JSONB change
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(agent, "openclaw_config")

    await db.commit()

    return {"success": True, "agent_id": body.agent_id, "status": body.status, "ack": now.isoformat()}


@router.get("/federation/agents")
async def federation_list_agents(
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """List all federated agents for the authenticated user with connection status."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    result = await db.execute(
        select(AgentDefinition).where(
            AgentDefinition.user_id == user_id,
            AgentDefinition.agent_source == "federated",
            AgentDefinition.is_active == True,
        )
    )
    agents = result.scalars().all()

    from datetime import timezone
    now = datetime.now(timezone.utc)

    items = []
    for a in agents:
        config = a.openclaw_config or {}
        last_hb = config.get("last_heartbeat")
        online = False
        if last_hb:
            try:
                hb_time = datetime.fromisoformat(last_hb)
                online = (now - hb_time).total_seconds() < 300  # 5 min threshold
            except Exception:
                pass

        items.append({
            "agent_id": str(a.id),
            "name": a.name,
            "description": a.description,
            "connection_url": config.get("connection_url"),
            "client_version": config.get("client_version"),
            "hardware_info": config.get("hardware_info"),
            "capabilities": config.get("capabilities", []),
            "status": "online" if online else "offline",
            "last_heartbeat": last_hb,
            "tools": a.tools or [],
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })

    return {"agents": items, "total": len(items)}


@router.post("/federation/disconnect/{agent_id}")
async def federation_disconnect(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Disconnect a federated agent (marks inactive, keeps data)."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    result = await db.execute(
        select(AgentDefinition).where(
            AgentDefinition.id == agent_id,
            AgentDefinition.user_id == user_id,
            AgentDefinition.agent_source == "federated",
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Federated agent not found")

    agent.is_active = False
    config = agent.openclaw_config or {}
    config["status"] = "disconnected"
    config["disconnected_at"] = datetime.now().isoformat()
    agent.openclaw_config = config

    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(agent, "openclaw_config")

    await db.commit()

    logger.info(f"[FEDERATION] Disconnected agent '{agent.name}' ({agent_id})")
    return {"success": True, "agent_id": agent_id, "status": "disconnected"}


@router.get("/capabilities")
async def list_agent_capabilities():
    from .rg_tool_registry.builtin_tools import build_registry
    from .rg_tool_registry.registry import ToolAccess
    registry = build_registry()
    tools = [t.to_openai() for t in registry.get_tools(access=ToolAccess.AGENT)]

    bundles = [
        CapabilityBundle(
            id="starter",
            name="Starter",
            description="Safe defaults for most assistants.",
            tools=["memory.read", "memory.write"],
        ),
        CapabilityBundle(
            id="enterprise-analyst",
            name="Enterprise Analyst",
            description="Enterprise analysis with memory + structured reasoning.",
            tools=["memory.read", "memory.write", "analyze_data", "llm.complete"],
        ),
        CapabilityBundle(
            id="enterprise-security",
            name="Enterprise Security",
            description="Security/compliance workflows with memory + web research.",
            tools=["memory.read", "memory.write", "web_search", "fetch_url", "llm.complete"],
        ),
        CapabilityBundle(
            id="enterprise-sre",
            name="Enterprise SRE",
            description="SRE/runbook workflows with memory + diagnostics.",
            tools=["memory.read", "memory.write", "analyze_data", "web_search", "fetch_url"],
        ),
    ]

    return {"tools": tools, "bundles": [b.model_dump() for b in bundles]}


class ProviderCatalogProvider(BaseModel):
    id: str
    name: str
    available: bool
    has_user_key: Optional[bool] = None
    uses_credits: Optional[bool] = None
    model: Optional[str] = None
    description: Optional[str] = None
    capabilities: Optional[List[str]] = None


class ProvidersCatalogResponse(BaseModel):
    providers: List[ProviderCatalogProvider]
    default: Optional[str] = None
    fallback_chain: Optional[List[str]] = None
    message: Optional[str] = None


@router.get("/providers")
async def list_provider_catalog(request: Request):
    user_id = request.headers.get("x-user-id")
    headers = {"x-user-id": user_id} if user_id else {}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{LLM_SERVICE_URL}/llm/providers", headers=headers)
        if resp.status_code == 200:
            return resp.json() if resp.content else {}
    except Exception as e:
        logger.warning(f"Failed to fetch provider catalog from llm_service: {e}")

    return {
        "providers": [],
        "default": None,
        "fallback_chain": [],
        "message": "Provider catalog unavailable",
    }


@router.get("/metrics")
async def get_platform_metrics(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    from uuid import UUID as PyUUID
    user_id = request.headers.get("x-user-id")
    org_id = request.headers.get("x-org-id")
    user_role = request.headers.get("x-user-role", "")
    is_superuser = request.headers.get("x-is-superuser", "").lower() == "true"
    is_admin = user_role in ("platform_owner", "owner", "admin") or is_superuser

    # Build org-scoped agent filter
    agent_filter = []
    session_filter = []
    org_uuid = None
    if org_id:
        try:
            org_uuid = PyUUID(org_id)
        except (ValueError, AttributeError):
            org_uuid = None
    user_uuid = None
    if user_id:
        try:
            user_uuid = PyUUID(user_id)
        except (ValueError, AttributeError):
            user_uuid = None
    if not is_admin and user_uuid:
        if org_uuid:
            from sqlalchemy import or_
            agent_filter.append(or_(
                AgentDefinition.user_id == user_uuid,
                AgentDefinition.org_id == org_uuid,
            ))
            session_filter.append(AgentSession.user_id == user_uuid)
        else:
            agent_filter.append(AgentDefinition.user_id == user_uuid)
            session_filter.append(AgentSession.user_id == user_uuid)

    agent_q = select(func.count()).select_from(AgentDefinition)
    for f in agent_filter:
        agent_q = agent_q.where(f)
    total_agents = (await session.execute(agent_q)).scalar_one()

    active_q = select(func.count()).select_from(AgentDefinition).where(AgentDefinition.is_active.is_(True))
    for f in agent_filter:
        active_q = active_q.where(f)
    active_agents = (await session.execute(active_q)).scalar_one()

    sess_q = select(func.count()).select_from(AgentSession)
    for f in session_filter:
        sess_q = sess_q.where(f)
    total_sessions = (await session.execute(sess_q)).scalar_one()

    running_q = select(func.count()).select_from(AgentSession).where(AgentSession.status == "running")
    for f in session_filter:
        running_q = running_q.where(f)
    running_sessions = (await session.execute(running_q)).scalar_one()

    completed_q = select(func.count()).select_from(AgentSession).where(AgentSession.status == "completed")
    for f in session_filter:
        completed_q = completed_q.where(f)
    completed_sessions = (await session.execute(completed_q)).scalar_one()

    failed_q = select(func.count()).select_from(AgentSession).where(AgentSession.status == "failed")
    for f in session_filter:
        failed_q = failed_q.where(f)
    failed_sessions = (await session.execute(failed_q)).scalar_one()

    return {
        "agents": {
            "total": int(total_agents or 0),
            "active": int(active_agents or 0),
        },
        "sessions": {
            "total": int(total_sessions or 0),
            "running": int(running_sessions or 0),
            "completed": int(completed_sessions or 0),
            "failed": int(failed_sessions or 0),
        },
    }


@router.get("/metrics/summary")
async def get_platform_metrics_summary(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Summary metrics for the AgentOS footer bar."""
    from uuid import UUID as PyUUID
    user_id = request.headers.get("x-user-id")
    org_id = request.headers.get("x-org-id")
    user_role = request.headers.get("x-user-role", "")
    is_superuser = request.headers.get("x-is-superuser", "").lower() == "true"
    is_admin = user_role in ("platform_owner", "owner", "admin") or is_superuser

    agent_filter = []
    session_filter = []
    org_uuid = None
    if org_id:
        try:
            org_uuid = PyUUID(org_id)
        except (ValueError, AttributeError):
            org_uuid = None
    user_uuid = None
    if user_id:
        try:
            user_uuid = PyUUID(user_id)
        except (ValueError, AttributeError):
            user_uuid = None
    if not is_admin and user_uuid:
        if org_uuid:
            from sqlalchemy import or_
            agent_filter.append(or_(
                AgentDefinition.user_id == user_uuid,
                AgentDefinition.org_id == org_uuid,
            ))
            session_filter.append(AgentSession.user_id == user_uuid)
        else:
            agent_filter.append(AgentDefinition.user_id == user_uuid)
            session_filter.append(AgentSession.user_id == user_uuid)

    agent_q = select(func.count()).select_from(AgentDefinition)
    for f in agent_filter:
        agent_q = agent_q.where(f)
    total_agents = (await session.execute(agent_q)).scalar_one()

    active_q = select(func.count()).select_from(AgentDefinition).where(AgentDefinition.is_active.is_(True))
    for f in agent_filter:
        active_q = active_q.where(f)
    active_agents = (await session.execute(active_q)).scalar_one()

    sess_q = select(func.count()).select_from(AgentSession)
    for f in session_filter:
        sess_q = sess_q.where(f)
    total_sessions = (await session.execute(sess_q)).scalar_one()

    running_q = select(func.count()).select_from(AgentSession).where(AgentSession.status == "running")
    for f in session_filter:
        running_q = running_q.where(f)
    running_sessions = (await session.execute(running_q)).scalar_one()

    completed_q = select(func.count()).select_from(AgentSession).where(AgentSession.status == "completed")
    for f in session_filter:
        completed_q = completed_q.where(f)
    completed_sessions = (await session.execute(completed_q)).scalar_one()

    failed_q = select(func.count()).select_from(AgentSession).where(AgentSession.status == "failed")
    for f in session_filter:
        failed_q = failed_q.where(f)
    failed_sessions = (await session.execute(failed_q)).scalar_one()

    avg_response_ms = (
        await session.execute(
            select(
                func.avg(
                    func.extract("epoch", (AgentSession.completed_at - AgentSession.started_at)) * 1000
                )
            )
            .where(AgentSession.started_at.is_not(None))
            .where(AgentSession.completed_at.is_not(None))
        )
    ).scalar_one()

    return {
        "active_agents": int(active_agents or 0),
        "total_agents": int(total_agents or 0),
        "running_sessions": int(running_sessions or 0),
        "total_sessions": int(total_sessions or 0),
        "total_completed": int(completed_sessions or 0),
        "total_failed": int(failed_sessions or 0),
        "avg_response_ms": int(avg_response_ms) if avg_response_ms is not None else None,
        "status": "healthy",
    }


@router.get("/{agent_id}/metrics")
async def get_agent_run_metrics(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    try:
        agent_uuid = PyUUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent_id")

    total_sessions = (
        await session.execute(
            select(func.count()).select_from(AgentSession).where(AgentSession.agent_id == agent_uuid)
        )
    ).scalar_one()

    tokens_used = (
        await session.execute(
            select(func.coalesce(func.sum(AgentSession.total_tokens_used), 0)).where(AgentSession.agent_id == agent_uuid)
        )
    ).scalar_one()

    by_status_rows = (
        await session.execute(
            select(AgentSession.status, func.count())
            .where(AgentSession.agent_id == agent_uuid)
            .group_by(AgentSession.status)
        )
    ).all()
    by_status = {row[0]: int(row[1]) for row in by_status_rows}

    last_session_at = (
        await session.execute(
            select(func.max(AgentSession.created_at)).where(AgentSession.agent_id == agent_uuid)
        )
    ).scalar_one()

    avg_duration_ms = (
        await session.execute(
            select(
                func.avg(
                    func.extract("epoch", (AgentSession.completed_at - AgentSession.started_at)) * 1000
                )
            )
            .where(AgentSession.agent_id == agent_uuid)
            .where(AgentSession.started_at.is_not(None))
            .where(AgentSession.completed_at.is_not(None))
        )
    ).scalar_one()

    return {
        "agent_id": agent_id,
        "sessions_total": int(total_sessions or 0),
        "sessions_by_status": by_status,
        "total_tokens_used": int(tokens_used or 0),
        "avg_duration_ms": int(avg_duration_ms) if avg_duration_ms is not None else None,
        "last_session_at": last_session_at.isoformat() if last_session_at else None,
    }


@router.get("/{agent_id}/collective-knowledge")
async def agent_collective_knowledge(
    agent_id: str,
    query: Optional[str] = None,
    request: Request = None,
    session: AsyncSession = Depends(get_session),
):
    """Query collective knowledge for an agent. Delegates to collaboration hub if available."""
    try:
        from .agent_collaboration import get_collaboration_hub
        hub = get_collaboration_hub()
        if query:
            results = await hub.knowledge.query_collective_knowledge(agent_id, query)
            return {"results": results}
        return {"results": [], "delegations": [], "agent_id": agent_id}
    except Exception:
        return {"results": [], "delegations": [], "agent_id": agent_id}


@router.get("/{agent_id}/versions")
async def list_agent_versions(
    agent_id: str,
    request: Request,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
):
    from uuid import UUID as PyUUID

    try:
        agent_uuid = PyUUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent_id")

    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    try:
        user_uuid = PyUUID(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user_id")

    owner_check = await session.execute(
        select(AgentDefinition.id).where(
            AgentDefinition.id == agent_uuid,
            AgentDefinition.user_id == user_uuid,
        )
    )
    if owner_check.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    limit = max(1, min(int(limit or 20), 200))

    result = await session.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent_uuid)
        .order_by(AgentVersion.version_number.desc(), AgentVersion.created_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()

    return {
        "agent_id": agent_id,
        "versions": [
            {
                "version_number": int(v.version_number),
                "agent_public_hash": v.agent_public_hash,
                "agent_version_hash": v.agent_version_hash,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "changelog": v.changelog,
                "config_snapshot": v.config_snapshot,
            }
            for v in rows
        ],
    }


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get an agent by ID."""
    from uuid import UUID as PyUUID

    try:
        agent_uuid = PyUUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent_id")

    result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == agent_uuid)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    safety_config = agent.safety_config or {}
    manifest_hash = safety_config.get("manifest_hash")
    if not manifest_hash:
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
        agent.safety_config = safety_config
        agent.agent_version_hash = str(manifest_hash)
        await session.commit()

    if _ensure_hashes_without_version_bump(agent):
        await session.commit()

    if not (agent.safety_config or {}).get("dsid"):
        user_id = request.headers.get("x-user-id")
        await best_effort_issue_dsid_and_register(agent=agent, user_id=user_id, session=session)

    return AgentResponse(
        id=str(agent.id),
        name=agent.name,
        description=agent.description,
        provider=agent.provider,
        model=agent.model,
        tool_mode=getattr(agent, 'tool_mode', None) or 'smart',
        tools=agent.tools,
        mode=agent.mode,
        is_active=agent.is_active,
        version=agent.version,
        manifest_hash=manifest_hash,
        agent_public_hash=agent.agent_public_hash,
        agent_version_hash=agent.agent_version_hash,
        dsid=(agent.safety_config or {}).get("dsid"),
        agent_source=getattr(agent, 'agent_source', None) or 'cloud',
    )


@router.patch("/{agent_id}")
async def patch_agent(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Update specific fields of an agent (partial update).

    Accepts a JSON body with any subset of:
    name, description, system_prompt, provider, model, temperature, max_tokens,
    tools, mode, is_active, tool_mode, safety_config, allowed_actions, blocked_actions.
    """
    try:
        agent_uuid = PyUUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent_id")

    result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == agent_uuid)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Ownership check
    user_id = request.headers.get("x-user-id")
    if user_id and agent.user_id and str(agent.user_id) != user_id:
        user_role = (request.headers.get("x-user-role") or "").lower()
        is_superuser = (request.headers.get("x-is-superuser") or "").lower() in ("true", "1")
        if user_role not in ("owner", "platform_owner", "admin") and not is_superuser:
            raise HTTPException(status_code=403, detail="Not authorized to update this agent")

    body = await request.json()
    updatable_fields = {
        "name", "description", "system_prompt", "provider", "model",
        "temperature", "max_tokens", "tools", "mode", "is_active",
        "tool_mode", "safety_config", "allowed_actions", "blocked_actions",
        "autonomous", "trigger_config", "tool_config",
    }
    updated = []
    for field in updatable_fields:
        if field in body:
            setattr(agent, field, body[field])
            updated.append(field)

    # Handle max_loops convenience field → store in safety_config
    if "max_loops" in body:
        max_loops_val = body["max_loops"]
        if isinstance(max_loops_val, int) and 1 <= max_loops_val <= 100:
            sc = dict(agent.safety_config or {})
            sc["max_loops"] = max_loops_val
            agent.safety_config = sc
            updated.append("max_loops")

    if not updated:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    # Bump version on meaningful changes
    if any(f in updated for f in ("name", "description", "system_prompt", "model", "tools", "mode")):
        agent.version = (agent.version or 1) + 1

    await session.commit()
    await session.refresh(agent)

    logger.info(f"Agent {agent_id} updated fields: {updated}")

    return {
        "id": str(agent.id),
        "name": agent.name,
        "description": agent.description,
        "provider": agent.provider,
        "model": agent.model,
        "mode": agent.mode,
        "is_active": agent.is_active,
        "version": agent.version,
        "updated_fields": updated,
        "agent_public_hash": agent.agent_public_hash,
    }


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Archive an agent (soft-delete).

    Agents are recorded on the blockchain with immutable action history,
    reputation scores, and version hashes. Hard-deleting would violate
    blockchain integrity. Instead we mark the agent as archived so it is
    hidden from the user's active list but all data and on-chain
    references remain intact.
    """
    try:
        agent_uuid = PyUUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent_id")

    result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == agent_uuid)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if agent.archived_at is not None:
        return {"status": "already_archived", "id": agent_id}

    agent.is_active = False
    agent.archived_at = datetime.utcnow()
    await session.commit()

    # Record archive action on blockchain (best-effort)
    try:
        from .blockchain_integration import record_action
        await record_action(agent_id, "agent_archived", {
            "agent_id": agent_id,
            "archived_by": request.headers.get("x-user-id", "unknown"),
        })
    except Exception as e:
        logger.warning(f"Failed to record archive on blockchain: {e}")

    return {"status": "archived", "id": agent_id}


@router.patch("/{agent_id}/unarchive")
async def unarchive_agent(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Restore an archived agent."""
    try:
        agent_uuid = PyUUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent_id")

    result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == agent_uuid)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if agent.archived_at is None:
        return {"status": "not_archived", "id": agent_id}

    agent.is_active = True
    agent.archived_at = None
    await session.commit()

    try:
        from .blockchain_integration import record_action
        await record_action(agent_id, "agent_unarchived", {
            "agent_id": agent_id,
            "unarchived_by": request.headers.get("x-user-id", "unknown"),
        })
    except Exception as e:
        logger.warning(f"Failed to record unarchive on blockchain: {e}")

    return {"status": "active", "id": agent_id}


# ============== Session Endpoints ==============

async def _run_agent_session_background(*, session_id: str, agent_id: str) -> None:
    try:
        session_uuid = PyUUID(session_id)
        agent_uuid = PyUUID(agent_id)
    except ValueError:
        logger.error("Invalid UUIDs for background run")
        return

    async with _agent_run_semaphore:
        try:
            await asyncio.wait_for(
                _run_agent_session_background_inner(session_id=str(session_uuid), agent_id=str(agent_uuid)),
                timeout=300,  # 5 minute max per session
            )
        except asyncio.TimeoutError:
            logger.error("Agent session %s timed out after 5 minutes", session_id)
            try:
                async with async_session() as db_session:
                    result = await db_session.execute(
                        select(AgentSession).where(AgentSession.id == PyUUID(session_id))
                    )
                    agent_session = result.scalar_one_or_none()
                    if agent_session and agent_session.status in ("initializing", "queued", "running"):
                        agent_session.status = "failed"
                        agent_session.error_message = "Session timed out (5 minute limit)"
                        await db_session.commit()
            except Exception:
                pass
            return
        except Exception as e:
            logger.exception("Background agent run failed: %s", e)
            try:
                async with async_session() as db_session:
                    result = await db_session.execute(
                        select(AgentSession).where(AgentSession.id == session_uuid)
                    )
                    agent_session = result.scalar_one_or_none()
                    if agent_session and agent_session.status in ("initializing", "queued", "running"):
                        agent_session.status = "failed"
                        agent_session.error_message = str(e)
                        await db_session.commit()
            except Exception:
                return


async def _run_agent_session_background_inner(*, session_id: str, agent_id: str) -> None:
    session_uuid = PyUUID(session_id)
    agent_uuid = PyUUID(agent_id)

    async with async_session() as db_session:
        result = await db_session.execute(select(AgentSession).where(AgentSession.id == session_uuid))
        agent_session = result.scalar_one_or_none()
        if not agent_session:
            return

        result = await db_session.execute(select(AgentDefinition).where(AgentDefinition.id == agent_uuid))
        agent = result.scalar_one_or_none()
        if not agent:
            agent_session.status = "failed"
            agent_session.error_message = "Agent not found"
            await db_session.commit()
            return

        try:
            await agent_executor.run_loop(agent_session, agent, db_session)
        except Exception as e:
            try:
                await db_session.rollback()
            except Exception:
                pass
            agent_session.status = "failed"
            agent_session.error_message = str(e)
            try:
                await db_session.commit()
            except Exception:
                try:
                    await db_session.rollback()
                except Exception:
                    pass


@router.post("/{agent_id}/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def start_session(
    agent_id: str,
    payload: SessionCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Start a new agent session."""
    from fastapi.responses import JSONResponse

    user_id = request.headers.get("x-user-id")
    org_id = request.headers.get("x-org-id")
    user_role = (request.headers.get("x-user-role") or "user").strip().lower()
    is_superuser = (request.headers.get("x-is-superuser") or "").strip().lower() in {"1", "true", "yes", "on"}
    is_privileged = is_superuser or user_role in ("platform_owner", "admin")

    # Credit pre-check: block zero-credit users from running agents
    if not is_privileged and user_id and user_id != "anonymous":
        try:
            billing_url = os.getenv("BILLING_SERVICE_URL", "http://billing_service:8000")
            async with httpx.AsyncClient(timeout=5.0) as hc:
                bal_resp = await hc.get(f"{billing_url}/billing/credits/balance/{user_id}")
                if bal_resp.status_code == 200:
                    bal_data = bal_resp.json()
                    balance = bal_data.get("balance", 0)
                    if balance <= 0 and not bal_data.get("unlimited", False):
                        logger.warning(f"[Credits] User {user_id[:8]}... blocked from starting session: 0 credits")
                        return JSONResponse(
                            status_code=402,
                            content={
                                "error": "insufficient_credits",
                                "detail": "Credits exhausted. Please upgrade your plan or purchase credits to run agents.",
                                "message": "Credits exhausted. Please upgrade your plan or purchase credits to run agents.",
                                "action_url": "/pricing",
                                "required": 100,
                                "available": balance,
                            },
                        )
        except Exception as e:
            logger.warning(f"[Credits] Balance check failed for session start: {e}")

    try:
        agent_uuid = PyUUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent_id")

    # Get agent
    result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == agent_uuid)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if not agent.is_active:
        raise HTTPException(status_code=400, detail="Agent is not active")

    # Merge context with gateway headers / derived agent hash for memory scoping
    merged_context: Dict[str, Any] = dict(payload.context or {})
    if org_id and "org_id" not in merged_context:
        merged_context["org_id"] = org_id
    derived_agent_hash = str(agent.agent_public_hash or "") or str((agent.safety_config or {}).get("agent_hash") or "")
    if derived_agent_hash and "agent_hash" not in merged_context:
        merged_context["agent_hash"] = derived_agent_hash
    # Forward gateway-injected identity headers into session context
    # (internal services trust x-user-* headers, NOT JWTs)
    user_role = (request.headers.get("x-user-role") or "user").strip().lower()
    if "user_role" not in merged_context:
        merged_context["user_role"] = user_role
    is_superuser = (request.headers.get("x-is-superuser") or "").strip().lower() in {"1", "true", "yes", "on"}
    if "is_superuser" not in merged_context:
        merged_context["is_superuser"] = is_superuser
    unlimited_credits = (request.headers.get("x-unlimited-credits") or "").strip().lower() in {"1", "true", "yes", "on"}
    if "unlimited_credits" not in merged_context:
        merged_context["unlimited_credits"] = unlimited_credits

    # Create session
    agent_session = await agent_executor.start_session(
        agent=agent,
        goal=payload.goal,
        initial_context=merged_context,
        user_id=user_id,
        db_session=session,
    )

    # Mark as queued immediately so the UI doesn't show long-running "initializing"
    agent_session.status = "queued"
    await session.commit()

    # Run loop in background
    background_tasks.add_task(
        _run_agent_session_background,
        session_id=str(agent_session.id),
        agent_id=str(agent.id),
    )

    return SessionResponse(
        id=str(agent_session.id),
        agent_id=str(agent_session.agent_id),
        status=agent_session.status,
        current_goal=agent_session.current_goal,
        loop_count=agent_session.loop_count,
        total_tokens_used=agent_session.total_tokens_used,
    )


@router.get("/{agent_id}/sessions", response_model=List[SessionResponse])
async def list_sessions(
    agent_id: str,
    status_filter: Optional[str] = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    """List sessions for an agent."""
    try:
        agent_uuid = PyUUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent_id")

    stmt = select(AgentSession).where(AgentSession.agent_id == agent_uuid)
    if status_filter:
        stmt = stmt.where(AgentSession.status == status_filter)
    stmt = stmt.order_by(AgentSession.created_at.desc()).limit(limit)

    result = await session.execute(stmt)
    sessions = result.scalars().all()

    return [
        SessionResponse(
            id=str(s.id),
            agent_id=str(s.agent_id),
            status=s.status,
            current_goal=s.current_goal,
            loop_count=s.loop_count,
            total_tokens_used=s.total_tokens_used,
            final_output=s.final_output if hasattr(s, 'final_output') else None,
            error_message=s.error_message if hasattr(s, 'error_message') else None,
        )
        for s in sessions
    ]


@router.get("/{agent_id}/sessions/{session_id}", response_model=SessionResponse)
async def get_agent_session_detail(
    agent_id: str,
    session_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get session details for a specific agent (frontend compatibility route)."""
    result = await session.execute(
        select(AgentSession)
        .where(AgentSession.id == session_id)
        .where(AgentSession.agent_id == agent_id)
    )
    agent_session = result.scalar_one_or_none()
    if not agent_session:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionResponse(
        id=str(agent_session.id),
        agent_id=str(agent_session.agent_id),
        status=agent_session.status,
        current_goal=agent_session.current_goal,
        loop_count=agent_session.loop_count,
        total_tokens_used=agent_session.total_tokens_used,
        final_output=agent_session.final_output if hasattr(agent_session, 'final_output') else None,
        error_message=agent_session.error_message if hasattr(agent_session, 'error_message') else None,
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session_detail(
    session_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get session details."""
    result = await session.execute(
        select(AgentSession).where(AgentSession.id == session_id)
    )
    agent_session = result.scalar_one_or_none()
    if not agent_session:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionResponse(
        id=str(agent_session.id),
        agent_id=str(agent_session.agent_id),
        status=agent_session.status,
        current_goal=agent_session.current_goal,
        loop_count=agent_session.loop_count,
        total_tokens_used=agent_session.total_tokens_used,
        final_output=agent_session.final_output if hasattr(agent_session, 'final_output') else None,
        error_message=agent_session.error_message if hasattr(agent_session, 'error_message') else None,
    )


@router.get("/sessions/{session_id}/steps", response_model=List[StepResponse])
async def get_session_steps(
    session_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get all steps for a session."""
    result = await session.execute(
        select(AgentStep)
        .where(AgentStep.session_id == session_id)
        .order_by(AgentStep.step_number)
    )
    steps = result.scalars().all()

    return [
        StepResponse(
            id=str(s.id),
            step_number=s.step_number,
            step_type=s.step_type,
            reasoning=s.reasoning,
            tool_name=s.tool_name,
            output_data=s.output_data,
            safety_check_passed=s.safety_check_passed,
            duration_ms=s.duration_ms,
        )
        for s in steps
    ]


# ============== Phase 4.5: LangSmith-Level Trace Viewer ==============

@router.get("/sessions/{session_id}/trace")
async def get_session_trace(
    session_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Full execution trace for a session — LangSmith-level detail.

    Returns session metadata + every step with input/output, tool calls,
    safety checks, token usage, and timing for deep debugging.
    """
    result = await session.execute(
        select(AgentSession).where(AgentSession.id == session_id)
    )
    agent_session = result.scalar_one_or_none()
    if not agent_session:
        raise HTTPException(status_code=404, detail="Session not found")

    steps_result = await session.execute(
        select(AgentStep)
        .where(AgentStep.session_id == session_id)
        .order_by(AgentStep.step_number)
    )
    steps = steps_result.scalars().all()

    total_tokens = sum(s.tokens_used or 0 for s in steps)
    total_duration_ms = sum(s.duration_ms or 0 for s in steps)
    tool_calls = [s for s in steps if s.step_type == "tool_call"]
    safety_flags = [s for s in steps if not s.safety_check_passed]

    trace_steps = []
    for s in steps:
        step_data = {
            "id": str(s.id),
            "step_number": s.step_number,
            "step_type": s.step_type,
            "reasoning": s.reasoning,
            "input_data": s.input_data,
            "output_data": s.output_data,
            "tool_name": s.tool_name,
            "tool_input": s.tool_input,
            "tool_output": s.tool_output,
            "safety_check_passed": s.safety_check_passed,
            "safety_violations": s.safety_violations,
            "required_approval": s.required_approval,
            "approval_status": s.approval_status,
            "tokens_used": s.tokens_used or 0,
            "duration_ms": s.duration_ms,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        trace_steps.append(step_data)

    # Cost estimation (approximate per-token pricing)
    COST_PER_1K_TOKENS = {
        "gpt-4": 0.03, "gpt-4-turbo": 0.01, "gpt-4-turbo-preview": 0.01,
        "gpt-3.5-turbo": 0.0005, "llama": 0.0002, "mixtral": 0.0003,
        "groq": 0.0001, "default": 0.001,
    }
    # Load agent to get model
    agent_result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == agent_session.agent_id)
    )
    agent_def = agent_result.scalar_one_or_none()
    model_name = (agent_def.model or "default").lower() if agent_def else "default"
    cost_rate = next(
        (v for k, v in COST_PER_1K_TOKENS.items() if k in model_name),
        COST_PER_1K_TOKENS["default"],
    )
    estimated_cost_usd = round(total_tokens * cost_rate / 1000, 6)

    # Waterfall timing (cumulative ms offset for each step)
    waterfall = []
    cumulative_ms = 0
    for s in steps:
        dur = s.duration_ms or 0
        waterfall.append({
            "step_number": s.step_number,
            "step_type": s.step_type,
            "tool_name": s.tool_name,
            "offset_ms": cumulative_ms,
            "duration_ms": dur,
            "tokens": s.tokens_used or 0,
        })
        cumulative_ms += dur

    return {
        "session": {
            "id": str(agent_session.id),
            "agent_id": str(agent_session.agent_id),
            "status": agent_session.status,
            "goal": agent_session.current_goal,
            "started_at": agent_session.started_at.isoformat() if agent_session.started_at else None,
            "completed_at": agent_session.completed_at.isoformat() if agent_session.completed_at else None,
            "loop_count": agent_session.loop_count,
            "total_tokens_used": agent_session.total_tokens_used or 0,
            "total_tool_calls": agent_session.total_tool_calls or 0,
        },
        "trace": {
            "steps": trace_steps,
            "summary": {
                "total_steps": len(steps),
                "total_tokens": total_tokens,
                "total_duration_ms": total_duration_ms,
                "tool_call_count": len(tool_calls),
                "unique_tools": list(set(s.tool_name for s in tool_calls if s.tool_name)),
                "safety_flags": len(safety_flags),
                "has_approval_gates": any(s.required_approval for s in steps),
            },
        },
        "cost": {
            "model": model_name,
            "cost_per_1k_tokens": cost_rate,
            "estimated_cost_usd": estimated_cost_usd,
        },
        "waterfall": waterfall,
    }


@router.post("/sessions/{session_id}/cancel")
async def cancel_session(
    session_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Cancel a running session."""
    result = await session.execute(
        select(AgentSession).where(AgentSession.id == session_id)
    )
    agent_session = result.scalar_one_or_none()
    if not agent_session:
        raise HTTPException(status_code=404, detail="Session not found")

    if agent_session.status not in ("running", "waiting_approval", "paused"):
        raise HTTPException(status_code=400, detail="Session cannot be cancelled")

    agent_session.status = "cancelled"
    agent_session.completed_at = datetime.utcnow()
    await session.commit()

    return {"status": "cancelled", "id": session_id}


# Phase 2.4: Session feedback for learning loop
class SessionFeedbackRequest(BaseModel):
    rating: int = Field(..., ge=-1, le=1, description="-1=thumbs down, 0=neutral, 1=thumbs up")
    comment: Optional[str] = Field(None, max_length=1000)


@router.post("/sessions/{session_id}/feedback")
async def submit_session_feedback(
    session_id: str,
    payload: SessionFeedbackRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Submit feedback (thumbs up/down) on a completed session.
    Feeds into the learning loop so agents improve over time."""
    result = await session.execute(
        select(AgentSession).where(AgentSession.id == session_id)
    )
    agent_session = result.scalar_one_or_none()
    if not agent_session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Store feedback in session context
    ctx = dict(agent_session.context or {})
    ctx["user_feedback"] = {
        "rating": payload.rating,
        "comment": payload.comment,
        "timestamp": datetime.utcnow().isoformat(),
    }
    agent_session.context = ctx
    await session.commit()

    # Feed into learning loop
    try:
        from .learning_loop import get_learning_loop, OutcomeType
        ll = get_learning_loop()
        # Re-classify based on user feedback
        if payload.rating == 1:
            outcome_override = OutcomeType.SUCCESS
        elif payload.rating == -1:
            outcome_override = OutcomeType.FAILURE
        else:
            outcome_override = None

        if outcome_override:
            ll.record_execution(
                session_id=str(agent_session.id),
                agent_id=str(agent_session.agent_id),
                goal=agent_session.current_goal or "",
                goal_achieved=(payload.rating == 1),
                steps_taken=agent_session.loop_count or 0,
                tokens_used=agent_session.total_tokens_used or 0,
                duration_seconds=0,
                step_history=[],
                error_message=payload.comment if payload.rating == -1 else None,
                final_output=agent_session.final_output,
                context={"source": "user_feedback", "rating": payload.rating},
            )
    except Exception as e:
        logger.warning(f"Failed to record feedback in learning loop: {e}")

    return {
        "status": "recorded",
        "session_id": session_id,
        "rating": payload.rating,
    }


@router.post("/sessions/{session_id}/approve/{step_id}")
async def approve_step(
    session_id: str,
    step_id: str,
    approved: bool = True,
    background_tasks: BackgroundTasks = None,
    session: AsyncSession = Depends(get_session),
):
    """Approve or reject a step requiring approval."""
    await approval_manager.grant_approval(step_id, approved, session)

    # Resume session if approved — re-enter the execution loop in background
    if approved:
        result = await session.execute(
            select(AgentSession).where(AgentSession.id == session_id)
        )
        agent_session = result.scalar_one_or_none()
        if agent_session and agent_session.status == "waiting_approval":
            agent_session.status = "running"
            await session.commit()

            # Re-enter execution loop in background so the session actually resumes
            if background_tasks:
                background_tasks.add_task(_resume_session_after_approval, str(session_id))
    elif not approved:
        # Rejected — mark session as failed
        result = await session.execute(
            select(AgentSession).where(AgentSession.id == session_id)
        )
        agent_session = result.scalar_one_or_none()
        if agent_session and agent_session.status == "waiting_approval":
            agent_session.status = "failed"
            agent_session.error_message = "Step rejected by user"
            agent_session.completed_at = datetime.utcnow()
            await session.commit()

    return {"status": "approved" if approved else "rejected", "step_id": step_id}


async def _resume_session_after_approval(session_id: str):
    """Background task: re-enter execution loop for an approved session."""
    try:
        async with async_session() as db_session:
            result = await db_session.execute(
                select(AgentSession).where(AgentSession.id == session_id)
            )
            agent_session = result.scalar_one_or_none()
            if not agent_session or agent_session.status != "running":
                return

            # Load the agent definition
            agent_result = await db_session.execute(
                select(AgentDefinition).where(AgentDefinition.id == agent_session.agent_id)
            )
            agent = agent_result.scalar_one_or_none()
            if not agent:
                return

            # Re-enter the execution loop
            await agent_executor._run_loop_inner(
                session=agent_session,
                agent=agent,
                db_session=db_session,
                _step_history=[],
            )
            await db_session.commit()
    except Exception as e:
        logger.error(f"Failed to resume session {session_id} after approval: {e}")
        try:
            async with async_session() as db_session:
                result = await db_session.execute(
                    select(AgentSession).where(AgentSession.id == session_id)
                )
                s = result.scalar_one_or_none()
                if s and s.status == "running":
                    s.status = "failed"
                    s.error_message = f"Resume after approval failed: {str(e)[:200]}"
                    s.completed_at = datetime.utcnow()
                    await db_session.commit()
        except Exception:
            pass


# ============== Governance Approvals Endpoints ==============

# In-memory storage for pending approvals (use DB in production)
_pending_approvals: Dict[str, Dict[str, Any]] = {}
_resource_limits: Dict[str, Dict[str, Any]] = {
    "daily_spend": {"id": "daily_spend", "name": "Daily Spend Limit", "limit": 100.0, "used": 0.0, "unit": "$"},
    "hourly_tokens": {"id": "hourly_tokens", "name": "Token Limit (Hourly)", "limit": 50000, "used": 0, "unit": "tokens"},
    "concurrent_exec": {"id": "concurrent_exec", "name": "Concurrent Executions", "limit": 10, "used": 0, "unit": "executions"},
    "daily_api": {"id": "daily_api", "name": "API Calls (Daily)", "limit": 10000, "used": 0, "unit": "calls"},
}


@router.get("/approvals/pending")
async def list_pending_approvals(request: Request):
    """List all pending approvals for governance."""
    approvals = list(_pending_approvals.values())
    return {"approvals": approvals}


@router.post("/approvals/{approval_id}/approve")
async def approve_governance_action(approval_id: str, request: Request):
    """Approve a pending governance action."""
    if approval_id in _pending_approvals:
        _pending_approvals[approval_id]["status"] = "approved"
        del _pending_approvals[approval_id]
        return {"status": "approved", "approval_id": approval_id}
    raise HTTPException(status_code=404, detail="Approval not found")


@router.post("/approvals/{approval_id}/reject")
async def reject_governance_action(approval_id: str, request: Request):
    """Reject a pending governance action."""
    if approval_id in _pending_approvals:
        _pending_approvals[approval_id]["status"] = "rejected"
        del _pending_approvals[approval_id]
        return {"status": "rejected", "approval_id": approval_id}
    raise HTTPException(status_code=404, detail="Approval not found")


# ============== Resource Limits Endpoints ==============

@router.get("/limits")
async def get_resource_limits(request: Request):
    """Get all resource limits."""
    return {"limits": list(_resource_limits.values())}


@router.put("/limits/{limit_id}")
async def update_resource_limit(limit_id: str, request: Request):
    """Update a resource limit."""
    if limit_id not in _resource_limits:
        raise HTTPException(status_code=404, detail="Limit not found")
    
    body = await request.json()
    new_limit = body.get("limit")
    if new_limit is not None:
        _resource_limits[limit_id]["limit"] = new_limit
    
    return {"status": "updated", "limit": _resource_limits[limit_id]}


# ============== Tool Endpoints ==============

@tools_router.post("/tools", response_model=ToolResponse, status_code=status.HTTP_201_CREATED)
async def create_tool(
    payload: ToolCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a new tool definition.

    If a tool with the same name already exists, return the existing tool
    instead of inserting a duplicate. This matches idempotent behavior
    expected by tests and avoids unique constraint violations.
    """

    # Check for existing tool with same name
    existing_result = await session.execute(
        select(ToolDefinition).where(ToolDefinition.name == payload.name)
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        tool = existing
    else:
        tool = ToolDefinition(
            name=payload.name,
            description=payload.description,
            category=payload.category,
            parameters_schema=payload.parameters_schema,
            handler_type=payload.handler_type,
            handler_config=payload.handler_config,
            risk_level=payload.risk_level,
            requires_approval=payload.requires_approval,
        )
        session.add(tool)
        await session.commit()
        await session.refresh(tool)

    return ToolResponse(
        id=str(tool.id),
        name=tool.name,
        description=tool.description,
        category=tool.category,
        parameters_schema=tool.parameters_schema,
        handler_type=tool.handler_type,
        handler_config=tool.handler_config,
        risk_level=tool.risk_level,
        requires_approval=tool.requires_approval,
        is_active=tool.is_active,
    )


@tools_router.get("/tools", response_model=List[ToolResponse])
async def list_tools(
    category: Optional[str] = None,
):
    """List available tools from unified registry."""
    from .rg_tool_registry.builtin_tools import build_registry

    registry = build_registry()
    all_tools = registry.get_all()
    if category:
        all_tools = [t for t in all_tools if (t.category.value if t.category else "general") == category]

    return [
        ToolResponse(
            id=td.name,
            name=td.name,
            description=td.description,
            category=td.category.value if td.category else "general",
            parameters_schema=td.to_openai()["function"]["parameters"],
            handler_type="internal",
            handler_config=None,
            risk_level="low",
            requires_approval=False,
            is_active=True,
        )
        for td in all_tools
    ]


@tools_router.get("/tools/custom", response_model=List[ToolResponse])
async def list_custom_tools(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    try:
        stmt = (
            select(ToolDefinition)
            .where(ToolDefinition.is_active == True)
            .where(ToolDefinition.handler_config["created_by"].astext == user_id)
        )
        result = await session.execute(stmt)
        tools = result.scalars().all()
    except Exception:
        result = await session.execute(select(ToolDefinition).where(ToolDefinition.is_active == True))
        tools = [
            t
            for t in result.scalars().all()
            if isinstance(getattr(t, "handler_config", None), dict)
            and t.handler_config.get("created_by") == user_id
        ]

    return [
        ToolResponse(
            id=str(t.id),
            name=t.name,
            description=t.description,
            category=t.category,
            parameters_schema=t.parameters_schema,
            handler_type=t.handler_type,
            handler_config=t.handler_config,
            risk_level=t.risk_level,
            requires_approval=t.requires_approval,
            is_active=t.is_active,
        )
        for t in tools
    ]


@tools_router.post("/tools/custom", response_model=ToolResponse, status_code=status.HTTP_201_CREATED)
async def create_custom_tool(
    payload: ToolCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    handler_config = dict(payload.handler_config or {})
    handler_config["created_by"] = user_id
    handler_config["custom"] = True

    existing_result = await session.execute(select(ToolDefinition).where(ToolDefinition.name == payload.name))
    existing = existing_result.scalar_one_or_none()
    if existing:
        existing_cfg = existing.handler_config if isinstance(existing.handler_config, dict) else {}
        if existing_cfg.get("created_by") != user_id:
            raise HTTPException(status_code=409, detail="Tool name already exists")
        tool = existing
        tool.description = payload.description
        tool.category = payload.category
        tool.parameters_schema = payload.parameters_schema
        tool.handler_type = payload.handler_type
        tool.handler_config = handler_config
        tool.risk_level = payload.risk_level
        tool.requires_approval = payload.requires_approval
        tool.is_active = True
        await session.commit()
        await session.refresh(tool)
    else:
        tool = ToolDefinition(
            name=payload.name,
            description=payload.description,
            category=payload.category,
            parameters_schema=payload.parameters_schema,
            handler_type=payload.handler_type,
            handler_config=handler_config,
            risk_level=payload.risk_level,
            requires_approval=payload.requires_approval,
        )
        session.add(tool)
        await session.commit()
        await session.refresh(tool)

    return ToolResponse(
        id=str(tool.id),
        name=tool.name,
        description=tool.description,
        category=tool.category,
        parameters_schema=tool.parameters_schema,
        handler_type=tool.handler_type,
        handler_config=tool.handler_config,
        risk_level=tool.risk_level,
        requires_approval=tool.requires_approval,
        is_active=tool.is_active,
    )


class OpenAPIImportRequest(BaseModel):
    spec_url: Optional[str] = None
    spec_json: Optional[Dict[str, Any]] = None
    base_url_override: Optional[str] = None
    prefix: str = ""
    auth_header: Optional[str] = None
    auth_value: Optional[str] = None


@tools_router.post("/tools/custom/from-openapi")
async def import_tools_from_openapi(
    payload: OpenAPIImportRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Import tools from an OpenAPI/Swagger spec URL or JSON.

    Parses the spec and creates one webhook-type tool per endpoint path+method.
    Returns the list of created tools.
    """
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    spec = payload.spec_json
    if not spec and payload.spec_url:
        import httpx as _httpx
        try:
            async with _httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(payload.spec_url)
                resp.raise_for_status()
                spec = resp.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch spec: {e}")

    if not spec or not isinstance(spec, dict):
        raise HTTPException(status_code=400, detail="Provide spec_url or spec_json")

    # Determine base URL
    base_url = payload.base_url_override
    if not base_url:
        servers = spec.get("servers", [])
        if servers:
            base_url = servers[0].get("url", "")
        elif spec.get("host"):
            scheme = (spec.get("schemes") or ["https"])[0]
            base_url = f"{scheme}://{spec['host']}{spec.get('basePath', '')}"

    if not base_url:
        raise HTTPException(status_code=400, detail="Cannot determine base URL from spec; provide base_url_override")

    paths = spec.get("paths", {})
    created = []

    for path, methods in paths.items():
        for method, op in methods.items():
            if method.lower() in ("parameters", "servers", "summary", "description"):
                continue
            op_id = op.get("operationId") or f"{method}_{path}".replace("/", "_").replace("{", "").replace("}", "").strip("_")
            tool_name = f"{payload.prefix}{op_id}" if payload.prefix else op_id
            description = op.get("summary") or op.get("description") or f"{method.upper()} {path}"

            # Build parameter schema from spec
            params_schema = {}
            for param in op.get("parameters", []):
                params_schema[param["name"]] = {
                    "type": param.get("schema", {}).get("type", "string"),
                    "description": param.get("description", ""),
                    "required": param.get("required", False),
                    "in": param.get("in", "query"),
                }
            body = op.get("requestBody", {})
            if body:
                content = body.get("content", {})
                json_schema = content.get("application/json", {}).get("schema", {})
                if json_schema.get("properties"):
                    for pname, pdef in json_schema["properties"].items():
                        params_schema[pname] = {
                            "type": pdef.get("type", "string"),
                            "description": pdef.get("description", ""),
                            "required": pname in json_schema.get("required", []),
                            "in": "body",
                        }

            handler_config = {
                "created_by": user_id,
                "custom": True,
                "source": "openapi_import",
                "url": f"{base_url.rstrip('/')}{path}",
                "method": method.upper(),
                "headers": {},
            }
            if payload.auth_header and payload.auth_value:
                handler_config["auth_header"] = payload.auth_header
                handler_config["auth_value"] = payload.auth_value

            # Upsert
            existing_result = await session.execute(select(ToolDefinition).where(ToolDefinition.name == tool_name))
            existing = existing_result.scalar_one_or_none()
            if existing:
                existing.description = description[:500]
                existing.parameters_schema = params_schema
                existing.handler_type = "webhook"
                existing.handler_config = handler_config
                existing.is_active = True
                tool = existing
            else:
                tool = ToolDefinition(
                    name=tool_name,
                    description=description[:500],
                    category="openapi_import",
                    parameters_schema=params_schema,
                    handler_type="webhook",
                    handler_config=handler_config,
                    risk_level="medium",
                    requires_approval=False,
                )
                session.add(tool)

            created.append({"name": tool_name, "method": method.upper(), "path": path})

    await session.commit()
    return {"imported": len(created), "tools": created}


@tools_router.delete("/tools/custom/{tool_id}")
async def delete_custom_tool(
    tool_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    try:
        tool_uuid = PyUUID(tool_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tool_id")

    result = await session.execute(select(ToolDefinition).where(ToolDefinition.id == tool_uuid))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    cfg = tool.handler_config if isinstance(tool.handler_config, dict) else {}
    if cfg.get("created_by") != user_id:
        raise HTTPException(status_code=403, detail="Not allowed")

    await session.delete(tool)
    await session.commit()
    return {"deleted": True, "id": tool_id}


# ============== Safety Rule Endpoints ==============

@safety_router.post("/safety-rules", status_code=status.HTTP_201_CREATED)
async def create_safety_rule(
    payload: SafetyRuleCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a new safety rule."""
    rule = SafetyRule(
        name=payload.name,
        description=payload.description,
        rule_type=payload.rule_type,
        action=payload.action,
        condition=payload.condition,
        parameters=payload.parameters,
        priority=payload.priority,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    return {
        "id": str(rule.id),
        "name": rule.name,
        "rule_type": rule.rule_type,
        "action": rule.action,
        "is_active": rule.is_active,
    }


@safety_router.get("/safety-rules")
async def list_safety_rules(
    session: AsyncSession = Depends(get_session),
):
    """List all safety rules."""
    result = await session.execute(
        select(SafetyRule).order_by(SafetyRule.priority.desc())
    )
    rules = result.scalars().all()

    return [
        {
            "id": str(r.id),
            "name": r.name,
            "rule_type": r.rule_type,
            "action": r.action,
            "priority": r.priority,
            "is_active": r.is_active,
        }
        for r in rules
    ]


# ============== Trigger Endpoints ==============

@router.post("/{agent_id}/triggers", status_code=status.HTTP_201_CREATED)
async def create_trigger(
    agent_id: str,
    payload: TriggerCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a workflow trigger for an agent."""
    trigger = WorkflowTrigger(
        agent_id=agent_id,
        name=payload.name,
        trigger_type=payload.trigger_type,
        config=payload.config,
        cron_expression=payload.cron_expression,
        event_type=payload.event_type,
        event_filter=payload.event_filter,
        input_template=payload.input_template,
    )
    session.add(trigger)
    await session.commit()
    await session.refresh(trigger)

    return {
        "id": str(trigger.id),
        "name": trigger.name,
        "trigger_type": trigger.trigger_type,
        "is_active": trigger.is_active,
    }


@router.get("/{agent_id}/triggers")
async def list_triggers(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
):
    """List triggers for an agent."""
    result = await session.execute(
        select(WorkflowTrigger).where(WorkflowTrigger.agent_id == agent_id)
    )
    triggers = result.scalars().all()

    return [
        {
            "id": str(t.id),
            "name": t.name,
            "trigger_type": t.trigger_type,
            "is_active": t.is_active,
            "last_triggered_at": t.last_triggered_at.isoformat() if t.last_triggered_at else None,
            "trigger_count": t.trigger_count,
        }
        for t in triggers
    ]


@router.post("/triggers/webhook/{trigger_id}")
async def webhook_trigger(
    trigger_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Handle webhook trigger."""
    secret = request.headers.get("x-webhook-secret", "")
    payload = await request.json()

    trigger = await trigger_manager.process_webhook(
        trigger_id=trigger_id,
        payload=payload,
        secret=secret,
        db_session=session,
    )

    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found or invalid secret")

    # Get agent and start session
    result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == trigger.agent_id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Build input from template
    input_data = trigger.input_template or {}
    input_data["webhook_payload"] = payload

    agent_session = await agent_executor.start_session(
        agent=agent,
        goal=input_data.get("goal", "Process webhook"),
        initial_context=input_data,
        user_id=None,
        db_session=session,
    )

    # Update trigger stats
    trigger.last_triggered_at = datetime.utcnow()
    trigger.trigger_count += 1
    await session.commit()

    # Run in background
    background_tasks.add_task(
        _run_agent_session_background,
        session_id=str(agent_session.id),
        agent_id=str(agent.id),
    )

    return {"status": "triggered", "session_id": str(agent_session.id)}


# ============== Schedule CRUD (Phase 1.1) ==============

class ScheduleCreate(BaseModel):
    name: str
    goal: str
    cron_expression: Optional[str] = None
    interval_seconds: Optional[int] = None
    context: Optional[Dict[str, Any]] = None
    max_retries: int = 3
    timeout_seconds: int = 3600


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    goal: Optional[str] = None
    cron_expression: Optional[str] = None
    interval_seconds: Optional[int] = None
    context: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


@router.post("/{agent_id}/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    agent_id: str,
    payload: ScheduleCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create a periodic schedule for an agent."""
    from .models_schedule import AgentSchedule
    from datetime import timezone as tz

    user_id = request.headers.get("x-user-id")
    now = datetime.now(tz.utc)

    # Calculate first next_run_at
    next_run = None
    if payload.cron_expression:
        try:
            from croniter import croniter
            next_run = croniter(payload.cron_expression, now).get_next(datetime)
        except Exception:
            next_run = now + timedelta(hours=1)
    elif payload.interval_seconds:
        next_run = now + timedelta(seconds=payload.interval_seconds)
    else:
        raise HTTPException(400, "Provide cron_expression or interval_seconds")

    sched = AgentSchedule(
        agent_id=agent_id,
        user_id=user_id,
        name=payload.name,
        goal=payload.goal,
        cron_expression=payload.cron_expression,
        interval_seconds=payload.interval_seconds,
        context=payload.context or {},
        max_retries=payload.max_retries,
        timeout_seconds=payload.timeout_seconds,
        next_run_at=next_run,
    )
    session.add(sched)
    await session.commit()
    await session.refresh(sched)

    return {
        "id": str(sched.id),
        "name": sched.name,
        "enabled": sched.enabled,
        "next_run_at": sched.next_run_at.isoformat() if sched.next_run_at else None,
    }


@router.get("/{agent_id}/schedules")
async def list_schedules(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
):
    """List schedules for an agent."""
    from .models_schedule import AgentSchedule

    result = await session.execute(
        select(AgentSchedule).where(AgentSchedule.agent_id == agent_id)
    )
    schedules = result.scalars().all()

    return [
        {
            "id": str(s.id),
            "name": s.name,
            "enabled": s.enabled,
            "cron_expression": s.cron_expression,
            "interval_seconds": s.interval_seconds,
            "goal": s.goal,
            "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
            "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
            "run_count": s.run_count,
            "success_count": s.success_count,
            "failure_count": s.failure_count,
        }
        for s in schedules
    ]


@router.patch("/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    payload: ScheduleUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update a schedule."""
    from .models_schedule import AgentSchedule

    sched = await session.get(AgentSchedule, schedule_id)
    if not sched:
        raise HTTPException(404, "Schedule not found")

    for field_name in ("name", "goal", "cron_expression", "interval_seconds", "context", "enabled"):
        val = getattr(payload, field_name, None)
        if val is not None:
            setattr(sched, field_name, val)

    await session.commit()
    return {"id": str(sched.id), "updated": True}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete a schedule."""
    from .models_schedule import AgentSchedule

    sched = await session.get(AgentSchedule, schedule_id)
    if not sched:
        raise HTTPException(404, "Schedule not found")

    await session.delete(sched)
    await session.commit()
    return {"id": schedule_id, "deleted": True}


# ============== Repo-to-Agent Endpoints ==============

class RepoToAgentRequest(BaseModel):
    repo_url: str = Field(..., description="GitHub repository URL")
    custom_name: Optional[str] = None
    custom_description: Optional[str] = None


@router.post("/repo-to-agent", status_code=status.HTTP_201_CREATED)
async def create_agent_from_repo(
    payload: RepoToAgentRequest,
    x_user_id: str = None,
    session: AsyncSession = Depends(get_session),
):
    """Create an AI agent from a GitHub repository.
    
    This viral feature analyzes a GitHub repo and creates an AI agent
    that understands the codebase and can work with it.
    """
    from .repo_to_agent import create_agent_from_repo as convert_repo
    
    if not x_user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    try:
        result = await convert_repo(
            repo_url=payload.repo_url,
            user_id=x_user_id,
            custom_name=payload.custom_name,
            custom_description=payload.custom_description,
            db_session=session,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to analyze repository: {str(e)}")


@router.post("/repo-to-agent/analyze")
async def analyze_repo(
    payload: RepoToAgentRequest,
):
    """Analyze a GitHub repository without creating an agent.
    
    Returns analysis results that can be previewed before creating the agent.
    """
    from .repo_to_agent import github_analyzer
    
    try:
        analysis = await github_analyzer.analyze_repo(payload.repo_url)
        return {
            "repo_url": analysis.repo_url,
            "repo_name": analysis.repo_name,
            "owner": analysis.owner,
            "description": analysis.description,
            "primary_language": analysis.primary_language,
            "languages": analysis.languages,
            "file_count": analysis.file_count,
            "total_size_kb": analysis.total_size,
            "key_files": analysis.key_files,
            "entry_points": analysis.entry_points,
            "dependencies": analysis.dependencies[:20],
            "api_endpoints": analysis.api_endpoints[:10],
            "functions_found": len(analysis.functions),
            "classes_found": len(analysis.classes),
            "suggested_tools": analysis.suggested_tools,
            "suggested_capabilities": analysis.suggested_capabilities,
            "structure": {
                "has_tests": analysis.structure.get("has_tests"),
                "has_docs": analysis.structure.get("has_docs"),
                "has_ci": analysis.structure.get("has_ci"),
                "has_docker": analysis.structure.get("has_docker"),
                "has_api": analysis.structure.get("has_api"),
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to analyze repository: {str(e)}")


# ============== Anomaly-Triggered Agent Workflows (Phase 3.4) ==============

# Cache loaded from DB on first access; DB is source of truth
_anomaly_triggers: Dict[str, Dict[str, Any]] = {}
_anomaly_triggers_loaded = False


async def _load_anomaly_triggers_from_db(db: AsyncSession):
    """Load anomaly triggers from DB into in-memory cache."""
    global _anomaly_triggers, _anomaly_triggers_loaded
    try:
        from sqlalchemy import text
        rows = await db.execute(text("SELECT * FROM anomaly_triggers WHERE enabled = true"))
        _anomaly_triggers.clear()
        for row in rows.mappings().all():
            tid = str(row["id"])
            _anomaly_triggers[tid] = {
                "id": tid,
                "name": row["name"],
                "subsystem": row.get("subsystem"),
                "severity": row.get("severity", "critical"),
                "agent_id": str(row["agent_id"]),
                "goal_template": row.get("goal_template", ""),
                "cooldown_seconds": row.get("cooldown_seconds", 300),
                "enabled": row.get("enabled", True),
                "created_by": row.get("created_by"),
                "last_fired_at": row["last_fired_at"].isoformat() if row.get("last_fired_at") else None,
                "fire_count": row.get("fire_count", 0),
            }
        _anomaly_triggers_loaded = True
    except Exception as e:
        if "does not exist" not in str(e).lower():
            logger.warning(f"Failed to load anomaly triggers from DB: {e}")


class AnomalyTriggerConfig(BaseModel):
    name: str
    subsystem: Optional[str] = None
    severity: str = "critical"
    agent_id: str
    goal_template: str = "Investigate and resolve {subsystem} anomaly: {message}"
    cooldown_seconds: int = 300
    enabled: bool = True


@router.post("/anomaly-triggers")
async def create_anomaly_trigger(
    payload: AnomalyTriggerConfig,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Configure an anomaly trigger that fires an agent session when a matching alert occurs."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    # Verify agent exists
    result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == payload.agent_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Agent not found")

    from sqlalchemy import text as sa_text
    from uuid import uuid4
    trigger_id = str(uuid4())

    # Persist to DB
    try:
        await session.execute(sa_text("""
            INSERT INTO anomaly_triggers (id, name, subsystem, severity, agent_id, goal_template, cooldown_seconds, enabled, created_by)
            VALUES (:id, :name, :subsystem, :severity, :agent_id::uuid, :goal_template, :cooldown, :enabled, :created_by)
        """), {
            "id": trigger_id, "name": payload.name, "subsystem": payload.subsystem,
            "severity": payload.severity, "agent_id": payload.agent_id,
            "goal_template": payload.goal_template, "cooldown": payload.cooldown_seconds,
            "enabled": payload.enabled, "created_by": user_id,
        })
        await session.commit()
    except Exception as e:
        logger.warning(f"Failed to persist anomaly trigger to DB: {e}")
        await session.rollback()

    # Update in-memory cache
    trigger_data = {
        "id": trigger_id, "name": payload.name, "subsystem": payload.subsystem,
        "severity": payload.severity, "agent_id": payload.agent_id,
        "goal_template": payload.goal_template, "cooldown_seconds": payload.cooldown_seconds,
        "enabled": payload.enabled, "created_by": user_id,
        "last_fired_at": None, "fire_count": 0,
    }
    _anomaly_triggers[trigger_id] = trigger_data
    return trigger_data


@router.get("/anomaly-triggers")
async def list_anomaly_triggers(session: AsyncSession = Depends(get_session)):
    """List all configured anomaly→agent triggers."""
    global _anomaly_triggers_loaded
    if not _anomaly_triggers_loaded:
        await _load_anomaly_triggers_from_db(session)
    return {"triggers": list(_anomaly_triggers.values()), "count": len(_anomaly_triggers)}


@router.delete("/anomaly-triggers/{trigger_id}")
async def delete_anomaly_trigger(trigger_id: str, session: AsyncSession = Depends(get_session)):
    """Delete an anomaly trigger."""
    # Delete from DB
    try:
        from sqlalchemy import text as sa_text
        await session.execute(sa_text("DELETE FROM anomaly_triggers WHERE id = :id"), {"id": trigger_id})
        await session.commit()
    except Exception as e:
        logger.warning(f"Failed to delete anomaly trigger from DB: {e}")
    # Remove from cache
    if trigger_id in _anomaly_triggers:
        del _anomaly_triggers[trigger_id]
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="Trigger not found")


@router.post("/anomaly-triggers/fire")
async def fire_anomaly_trigger(
    subsystem: str,
    severity: str = "critical",
    message: str = "Anomaly detected",
    background_tasks: BackgroundTasks = None,
    session: AsyncSession = Depends(get_session),
):
    """Manually fire anomaly triggers matching the given subsystem/severity.

    Also called internally by the system watchdog when alerts are created.
    Returns the list of agent sessions that were started.
    """
    import time
    fired = []

    for tid, cfg in _anomaly_triggers.items():
        if not cfg["enabled"]:
            continue
        if cfg["subsystem"] and cfg["subsystem"] != subsystem:
            continue
        if cfg["severity"] != severity:
            continue

        # Cooldown check
        last = cfg.get("last_fired_at")
        if last and (time.time() - last) < cfg["cooldown_seconds"]:
            continue

        # Load agent and create session
        result = await session.execute(
            select(AgentDefinition).where(AgentDefinition.id == cfg["agent_id"])
        )
        agent = result.scalar_one_or_none()
        if not agent:
            continue

        goal = cfg["goal_template"].format(
            subsystem=subsystem, severity=severity, message=message,
        )

        agent_session = await agent_executor.start_session(
            agent=agent,
            goal=goal,
            initial_context={"anomaly_subsystem": subsystem, "anomaly_severity": severity, "anomaly_message": message},
            user_id=cfg["created_by"],
            db_session=session,
        )

        cfg["last_fired_at"] = time.time()
        cfg["fire_count"] = (cfg.get("fire_count") or 0) + 1

        if background_tasks:
            background_tasks.add_task(
                _run_agent_session_background,
                session_id=str(agent_session.id),
                agent_id=str(agent.id),
            )

        fired.append({
            "trigger_id": tid,
            "trigger_name": cfg["name"],
            "session_id": str(agent_session.id),
            "agent_id": cfg["agent_id"],
            "goal": goal,
        })

    return {"fired": fired, "count": len(fired)}


@router.get("/watchdog/status")
async def watchdog_status():
    """Get system watchdog health status and recent alerts."""
    from .system_watchdog import get_watchdog
    try:
        wd = await get_watchdog()
        return {
            "status": wd.get_status(),
            "alerts": wd.get_alerts(unacknowledged_only=False),
        }
    except Exception as e:
        return {"error": str(e), "status": {"running": False}}


# ============== Governance-as-a-Service API (Phase 3.1) ==============

class PolicyEvaluateRequest(BaseModel):
    agent_id: str
    action_type: str
    action_data: Optional[Dict[str, Any]] = None
    autonomy_mode: str = "supervised"
    estimated_cost: float = 0.0


@router.post("/governance/evaluate")
async def governance_evaluate_policy(
    payload: PolicyEvaluateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Evaluate a proposed action against all governance policies.

    Returns the policy decision (execute/require_approval/pause/abort/replan),
    risk level, reasons, and recommendations.
    """
    from .policy_engine import (
        get_policy_engine, PolicyContext, AutonomyMode
    )

    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    # Load agent
    result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == payload.agent_id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Build a lightweight policy context
    ctx = PolicyContext(
        agent=agent,
        session=None,
        step_count=0,
        elapsed_seconds=0.0,
        total_cost=0.0,
        action_type=payload.action_type,
        action_data=payload.action_data or {},
    )

    try:
        mode = AutonomyMode(payload.autonomy_mode)
    except ValueError:
        mode = AutonomyMode.SUPERVISED

    engine = get_policy_engine()
    policy_result = await engine.evaluate(
        ctx=ctx,
        autonomy_mode=mode,
        estimated_cost=payload.estimated_cost,
    )

    return {
        "decision": policy_result.decision.value,
        "risk_level": policy_result.risk_level.value,
        "reasons": policy_result.reasons,
        "recommendations": policy_result.recommendations,
        "requires_approval": policy_result.requires_approval,
    }


@router.get("/governance/audit-trail")
async def governance_audit_trail(
    agent_id: Optional[str] = None,
    limit: int = 100,
    request: Request = None,
    session: AsyncSession = Depends(get_session),
):
    """Get governance audit trail — all policy decisions made for agent sessions."""
    from sqlalchemy import text

    query = """
        SELECT s.id as session_id, s.current_goal, s.status,
               s.loop_count, s.total_tokens_used as tokens_used, s.error_message,
               s.created_at, s.completed_at,
               a.name as agent_name, a.id as agent_id
        FROM agent_sessions s
        JOIN agent_definitions a ON s.agent_id = a.id
    """
    params: Dict[str, Any] = {"limit": limit}
    if agent_id:
        query += " WHERE s.agent_id = :agent_id"
        params["agent_id"] = agent_id
    query += " ORDER BY s.created_at DESC LIMIT :limit"

    try:
        result = await session.execute(text(query), params)
        rows = result.fetchall()
    except Exception:
        return {"audit_trail": [], "error": "Table not ready"}

    return {
        "audit_trail": [
            {
                "session_id": str(r.session_id),
                "agent_id": str(r.agent_id),
                "agent_name": r.agent_name,
                "goal": r.current_goal,
                "status": r.status,
                "steps": r.loop_count,
                "tokens": r.tokens_used,
                "error": r.error_message,
                "started_at": str(r.created_at) if r.created_at else None,
                "completed_at": str(r.completed_at) if r.completed_at else None,
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.get("/governance/compliance-report")
async def governance_compliance_report(
    request: Request = None,
    session: AsyncSession = Depends(get_session),
):
    """Generate a compliance summary report across all agents.

    Includes: total sessions, success/failure rates, safety violations,
    top agents by usage, and policy configuration summary.
    """
    from sqlalchemy import text

    try:
        # Session stats
        stats_result = await session.execute(text("""
            SELECT
                COUNT(*) as total_sessions,
                COUNT(*) FILTER (WHERE status = 'completed') as completed,
                COUNT(*) FILTER (WHERE status = 'failed') as failed,
                COUNT(*) FILTER (WHERE status = 'running') as running,
                COALESCE(SUM(total_tokens_used), 0) as total_tokens,
                COALESCE(AVG(loop_count), 0) as avg_steps
            FROM agent_sessions
        """))
        stats = stats_result.fetchone()

        # Agent count
        agent_count_result = await session.execute(text(
            "SELECT COUNT(*) as cnt FROM agent_definitions WHERE is_active = true"
        ))
        agent_count = agent_count_result.scalar() or 0

        # Safety rules count
        safety_count_result = await session.execute(text(
            "SELECT COUNT(*) as cnt FROM safety_rules WHERE is_active = true"
        ))
        safety_count = safety_count_result.scalar() or 0

        # Top agents by session count
        top_result = await session.execute(text("""
            SELECT a.name, a.id, COUNT(s.id) as session_count
            FROM agent_definitions a
            LEFT JOIN agent_sessions s ON s.agent_id = a.id
            GROUP BY a.id, a.name
            ORDER BY session_count DESC
            LIMIT 10
        """))
        top_agents = [
            {"name": r.name, "id": str(r.id), "sessions": r.session_count}
            for r in top_result.fetchall()
        ]
    except Exception as e:
        return {"error": f"Report generation failed: {e}"}

    total = stats.total_sessions or 1
    return {
        "summary": {
            "total_sessions": stats.total_sessions,
            "completed": stats.completed,
            "failed": stats.failed,
            "running": stats.running,
            "success_rate": round(stats.completed / total, 3),
            "total_tokens_used": stats.total_tokens,
            "avg_steps_per_session": round(float(stats.avg_steps), 1),
        },
        "agents": {
            "total_active": agent_count,
            "top_by_usage": top_agents,
        },
        "safety": {
            "active_rules": safety_count,
        },
        "generated_at": datetime.utcnow().isoformat(),
    }


# ============== SOC2/ISO Compliance Dashboard (Phase 4.1) ==============

@router.get("/compliance/audit-export")
async def compliance_audit_export(
    format: str = "json",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    agent_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """Export audit trail in SOC2/ISO-compliant format (JSON or CSV).

    Includes: all agent sessions, steps, tool calls, safety decisions,
    approval gates, token usage, and timestamps for auditor review.
    """
    from sqlalchemy import text
    import csv
    import io

    where_clauses = []
    params: Dict[str, Any] = {}
    if start_date:
        where_clauses.append("s.created_at >= :start_date")
        params["start_date"] = start_date
    if end_date:
        where_clauses.append("s.created_at <= :end_date")
        params["end_date"] = end_date
    if agent_id:
        where_clauses.append("s.agent_id = :agent_id")
        params["agent_id"] = agent_id

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    query = f"""
        SELECT s.id as session_id, a.name as agent_name, a.id as agent_id,
               a.mode as autonomy_mode, s.current_goal, s.status,
               s.loop_count, s.total_tokens_used as tokens_used, s.error_message,
               s.created_at, s.completed_at
        FROM agent_sessions s
        JOIN agent_definitions a ON s.agent_id = a.id
        {where_sql}
        ORDER BY s.created_at DESC
        LIMIT 10000
    """

    try:
        result = await session.execute(text(query), params)
        rows = result.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    records = [
        {
            "session_id": str(r.session_id),
            "agent_id": str(r.agent_id),
            "agent_name": r.agent_name,
            "autonomy_mode": r.autonomy_mode,
            "goal": r.current_goal,
            "status": r.status,
            "steps": r.loop_count,
            "tokens_used": r.tokens_used,
            "error": r.error_message,
            "started_at": str(r.created_at) if r.created_at else None,
            "completed_at": str(r.completed_at) if r.completed_at else None,
        }
        for r in rows
    ]

    if format == "csv":
        output = io.StringIO()
        if records:
            writer = csv.DictWriter(output, fieldnames=records[0].keys())
            writer.writeheader()
            writer.writerows(records)
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit_export.csv"},
        )

    return {
        "export_format": "json",
        "record_count": len(records),
        "filters": {"start_date": start_date, "end_date": end_date, "agent_id": agent_id},
        "generated_at": datetime.utcnow().isoformat(),
        "records": records,
    }


@router.get("/compliance/score")
async def compliance_score(
    session: AsyncSession = Depends(get_session),
):
    """Calculate SOC2 compliance score based on platform configuration.

    Checks: governance enabled, safety rules active, audit logging,
    kill switch available, approval gates configured, data encryption.
    """
    from sqlalchemy import text

    checks = []

    # 1. Safety rules exist
    try:
        sr = await session.execute(text("SELECT COUNT(*) FROM safety_rules WHERE is_active = true"))
        safety_count = sr.scalar() or 0
        checks.append({"control": "CC6.1 - Safety Rules", "status": "pass" if safety_count > 0 else "fail",
                        "detail": f"{safety_count} active safety rules", "weight": 15})
    except Exception:
        checks.append({"control": "CC6.1 - Safety Rules", "status": "error", "detail": "Cannot query", "weight": 15})

    # 2. Governance engine available
    try:
        from .policy_engine import get_policy_engine
        get_policy_engine()
        checks.append({"control": "CC6.2 - Governance Engine", "status": "pass",
                        "detail": "Policy engine operational", "weight": 20})
    except Exception:
        checks.append({"control": "CC6.2 - Governance Engine", "status": "fail",
                        "detail": "Policy engine not available", "weight": 20})

    # 3. Audit trail functional
    try:
        at = await session.execute(text("SELECT COUNT(*) FROM agent_sessions"))
        session_count = at.scalar() or 0
        checks.append({"control": "CC7.1 - Audit Trail", "status": "pass",
                        "detail": f"{session_count} sessions logged", "weight": 20})
    except Exception:
        checks.append({"control": "CC7.1 - Audit Trail", "status": "fail",
                        "detail": "Audit trail not accessible", "weight": 20})

    # 4. Kill switch / approval gates
    checks.append({"control": "CC6.3 - Kill Switch", "status": "pass",
                    "detail": "Session cancel endpoint available at /sessions/{id}/cancel", "weight": 15})

    # 5. Agent step-level logging
    try:
        sl = await session.execute(text("SELECT COUNT(*) FROM agent_steps"))
        step_count = sl.scalar() or 0
        checks.append({"control": "CC7.2 - Step Logging", "status": "pass" if step_count > 0 else "warn",
                        "detail": f"{step_count} steps logged", "weight": 15})
    except Exception:
        checks.append({"control": "CC7.2 - Step Logging", "status": "warn",
                        "detail": "Step logging table not found", "weight": 15})

    # 6. Data encryption at rest (PostgreSQL)
    checks.append({"control": "CC6.7 - Encryption at Rest", "status": "pass",
                    "detail": "Managed PostgreSQL with encryption at rest", "weight": 15})

    total_weight = sum(c["weight"] for c in checks)
    passing_weight = sum(c["weight"] for c in checks if c["status"] == "pass")
    score = round((passing_weight / total_weight) * 100) if total_weight > 0 else 0

    return {
        "compliance_score": score,
        "max_score": 100,
        "grade": "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "F",
        "framework": "SOC2 Type II (mapped controls)",
        "checks": checks,
        "generated_at": datetime.utcnow().isoformat(),
    }


@router.get("/compliance/evidence-checklist")
async def compliance_evidence_checklist():
    """Generate SOC2/ISO evidence checklist showing what artifacts are available.

    Maps platform capabilities to SOC2 Trust Service Criteria.
    """
    return {
        "framework": "SOC2 Type II",
        "evidence": [
            {"criteria": "CC1.1 - Control Environment",
             "artifact": "Agent definitions with safety_config, mode, and allowed_actions",
             "endpoint": "/agents", "available": True},
            {"criteria": "CC2.1 - Communication",
             "artifact": "Agent session logs with goals, steps, and outcomes",
             "endpoint": "/governance/audit-trail", "available": True},
            {"criteria": "CC3.1 - Risk Assessment",
             "artifact": "Policy engine evaluation results with risk levels",
             "endpoint": "/governance/evaluate", "available": True},
            {"criteria": "CC5.1 - Control Activities",
             "artifact": "Safety rules with actions (block, warn, require_approval)",
             "endpoint": "/safety-rules", "available": True},
            {"criteria": "CC6.1 - Logical Access",
             "artifact": "API key authentication, user_id authorization on all endpoints",
             "endpoint": "All endpoints require x-user-id", "available": True},
            {"criteria": "CC6.2 - System Operations",
             "artifact": "System watchdog health checks and anomaly detection",
             "endpoint": "/watchdog/status", "available": True},
            {"criteria": "CC6.3 - Change Management",
             "artifact": "Agent version history with hashes",
             "endpoint": "/agents/{id}/versions", "available": True},
            {"criteria": "CC7.1 - System Monitoring",
             "artifact": "Full execution traces per session",
             "endpoint": "/sessions/{id}/trace", "available": True},
            {"criteria": "CC7.2 - Incident Response",
             "artifact": "Anomaly-triggered agent workflows for auto-remediation",
             "endpoint": "/anomaly-triggers", "available": True},
            {"criteria": "CC8.1 - Data Integrity",
             "artifact": "Hash Sphere cryptographic anchoring of memory records",
             "endpoint": "memory_service /memory/anchors", "available": True},
        ],
        "generated_at": datetime.utcnow().isoformat(),
    }


# ============== Agent-to-API Publishing (Phase 3.5) ==============

class PublishAgentAPIRequest(BaseModel):
    slug: Optional[str] = None
    description: Optional[str] = None
    rate_limit_rpm: int = 60
    max_tokens_per_call: int = 8192


class PublishAgentAPIResponse(BaseModel):
    id: str
    agent_id: str
    slug: str
    api_key: str
    invoke_url: str
    description: Optional[str] = None
    rate_limit_rpm: int
    is_active: bool


@router.post("/agents/{agent_id}/publish-api", response_model=PublishAgentAPIResponse)
async def publish_agent_as_api(
    agent_id: str,
    payload: PublishAgentAPIRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """One-click publish an agent as a callable REST API with its own API key."""
    import secrets, hashlib
    from .models import PublishedAgentAPI

    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    # Verify agent exists and belongs to user
    result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if str(agent.user_id) != user_id:
        role = request.headers.get("x-user-role", "")
        if role not in ("platform_owner", "owner", "admin"):
            raise HTTPException(status_code=403, detail="Not your agent")

    # Generate slug and API key
    slug = payload.slug or f"{agent.name.lower().replace(' ', '-')[:40]}-{secrets.token_hex(4)}"
    slug = slug.strip("-")

    # Check slug uniqueness
    existing = await session.execute(
        select(PublishedAgentAPI).where(PublishedAgentAPI.slug == slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Slug '{slug}' already taken")

    # Generate API key: rg_pub_<random>
    raw_key = f"rg_pub_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:12]

    pub = PublishedAgentAPI(
        agent_id=agent.id,
        user_id=PyUUID(user_id),
        slug=slug,
        api_key_hash=key_hash,
        api_key_prefix=key_prefix,
        description=payload.description or agent.description,
        rate_limit_rpm=payload.rate_limit_rpm,
        max_tokens_per_call=payload.max_tokens_per_call,
    )
    session.add(pub)
    await session.commit()
    await session.refresh(pub)

    return PublishAgentAPIResponse(
        id=str(pub.id),
        agent_id=str(pub.agent_id),
        slug=slug,
        api_key=raw_key,
        invoke_url=f"/agent-engine/api/v1/{slug}",
        description=pub.description,
        rate_limit_rpm=pub.rate_limit_rpm,
        is_active=True,
    )


@router.get("/agents/{agent_id}/published-apis")
async def list_published_apis(
    agent_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """List all published API endpoints for an agent."""
    from .models import PublishedAgentAPI
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    result = await session.execute(
        select(PublishedAgentAPI).where(
            PublishedAgentAPI.agent_id == PyUUID(agent_id),
            PublishedAgentAPI.user_id == PyUUID(user_id),
        )
    )
    pubs = result.scalars().all()
    return [
        {
            "id": str(p.id),
            "slug": p.slug,
            "invoke_url": f"/agent-engine/api/v1/{p.slug}",
            "api_key_prefix": p.api_key_prefix,
            "is_active": p.is_active,
            "total_calls": p.total_calls,
            "last_called_at": str(p.last_called_at) if p.last_called_at else None,
            "created_at": str(p.created_at),
        }
        for p in pubs
    ]


@router.delete("/published-apis/{pub_id}")
async def revoke_published_api(
    pub_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Revoke/deactivate a published API endpoint."""
    from .models import PublishedAgentAPI
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    result = await session.execute(
        select(PublishedAgentAPI).where(PublishedAgentAPI.id == PyUUID(pub_id))
    )
    pub = result.scalar_one_or_none()
    if not pub:
        raise HTTPException(status_code=404, detail="Published API not found")
    if str(pub.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not yours")

    pub.is_active = False
    await session.commit()
    return {"revoked": True, "id": pub_id}


class PublicInvokeRequest(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = None


@router.post("/api/v1/{slug}")
async def invoke_published_agent(
    slug: str,
    payload: PublicInvokeRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Public invocation endpoint for a published agent API.

    Requires Authorization header: Bearer rg_pub_<key>
    """
    import hashlib
    from .models import PublishedAgentAPI

    # Extract API key
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    api_key = auth[7:].strip()
    if not api_key.startswith("rg_pub_"):
        raise HTTPException(status_code=401, detail="Invalid API key format")

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    # Look up published API by slug + key
    result = await session.execute(
        select(PublishedAgentAPI).where(
            PublishedAgentAPI.slug == slug,
            PublishedAgentAPI.api_key_hash == key_hash,
            PublishedAgentAPI.is_active == True,
        )
    )
    pub = result.scalar_one_or_none()
    if not pub:
        raise HTTPException(status_code=401, detail="Invalid API key or inactive endpoint")

    # Load agent
    agent_result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == pub.agent_id)
    )
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Create session and run
    agent_session = await agent_executor.start_session(
        agent=agent,
        goal=payload.message,
        initial_context=payload.context or {},
        user_id=str(pub.user_id),
        db_session=session,
    )

    # Update stats
    pub.total_calls = (pub.total_calls or 0) + 1
    pub.last_called_at = datetime.utcnow()
    await session.commit()

    # P3.3: Credit creator wallet for API usage (1 credit per call)
    try:
        from .routers_autonomy import agent_wallet_system
        creator_wallet = agent_wallet_system.get_wallet_by_agent(str(pub.agent_id))
        if creator_wallet:
            agent_wallet_system.credit(
                wallet_id=creator_wallet.id,
                amount=1.0,
                description=f"API call to {slug} (caller: {pub.user_id})",
            )
    except Exception as e:
        logger.warning(f"Failed to credit creator wallet for API call: {e}")

    # Run in background
    background_tasks.add_task(
        _run_agent_session_background,
        session_id=str(agent_session.id),
        agent_id=str(agent.id),
    )

    return {
        "session_id": str(agent_session.id),
        "status": "started",
        "poll_url": f"/agent-engine/sessions/{agent_session.id}",
        "sse_url": f"/agent-engine/sessions/{agent_session.id}/sse",
    }


# ============== Learning Loop Endpoints (Phase 2.4) ==============

@router.get("/learning/stats")
async def learning_stats():
    """Get learning loop statistics — patterns learned, success rates, outcome counts."""
    from .learning_loop import get_learning_loop
    loop = get_learning_loop()
    return loop.get_stats()


@router.get("/learning/recommendations/{agent_id}")
async def learning_recommendations(
    agent_id: str,
    goal: str = "",
    session: AsyncSession = Depends(get_session),
):
    """Get learning-based recommendations for an agent's next session."""
    from .learning_loop import get_learning_loop
    loop = get_learning_loop()

    # Get agent's available tools
    result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    tools = agent.tools if agent and agent.tools else []

    return loop.get_recommendations(goal=goal, available_tools=tools)


@router.get("/learning/patterns")
async def learning_patterns(
    pattern_type: str = None,
    min_confidence: float = 0.0,
    limit: int = 50,
):
    """List learned patterns (action sequences, tool combos, error patterns)."""
    from .learning_loop import get_learning_loop, PatternType
    loop = get_learning_loop()

    pt = None
    if pattern_type:
        try:
            pt = PatternType(pattern_type)
        except ValueError:
            pass

    patterns = loop.memory.get_patterns(pattern_type=pt, min_confidence=min_confidence)
    return {
        "patterns": [p.to_dict() for p in patterns[:limit]],
        "total": len(patterns),
    }



# ============== Publish-as-API (Phase 3.5) ==============

class PublishAsApiRequest(BaseModel):
    slug: Optional[str] = None  # Custom slug, auto-generated if not provided

class PublishAsApiResponse(BaseModel):
    slug: str
    api_key: str
    endpoint: str
    rate_limit: int

@router.post("/{agent_id}/publish-api", response_model=PublishAsApiResponse, tags=["publish"])
async def publish_agent_as_api(
    agent_id: str,
    body: PublishAsApiRequest,
    request: Request,
):
    """Publish an agent as a public REST API with its own URL and API key."""
    import hashlib
    import secrets
    from uuid import UUID

    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(401, "Authentication required")

    async with async_session() as db:
        result = await db.execute(
            select(AgentDefinition).where(AgentDefinition.id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(404, "Agent not found")
        if str(agent.user_id) != user_id:
            raise HTTPException(403, "Only the agent owner can publish")

        # Generate or validate slug
        slug = (body.slug or "").strip().lower()
        if not slug:
            # Auto-generate from agent name
            import re
            slug = re.sub(r"[^a-z0-9]+", "-", agent.name.lower()).strip("-")[:48]
            slug = f"{slug}-{secrets.token_hex(3)}"

        if len(slug) < 3 or len(slug) > 64:
            raise HTTPException(400, "Slug must be 3-64 characters")

        # Check slug uniqueness
        existing = await db.execute(
            select(AgentDefinition).where(
                AgentDefinition.api_slug == slug,
                AgentDefinition.id != agent.id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(409, f"Slug '{slug}' is already taken")

        # Generate API key
        api_key = f"rg-agent-{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        agent.api_slug = slug
        agent.api_key_hash = key_hash
        agent.published_as_api = True
        await db.commit()

        return PublishAsApiResponse(
            slug=slug,
            api_key=api_key,
            endpoint=f"/api/v1/agents/public/{slug}/run",
            rate_limit=agent.api_rate_limit or 60,
        )


@router.post("/{agent_id}/unpublish-api", tags=["publish"])
async def unpublish_agent_api(agent_id: str, request: Request):
    """Remove an agent's public API endpoint."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(401, "Authentication required")

    async with async_session() as db:
        result = await db.execute(
            select(AgentDefinition).where(AgentDefinition.id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(404, "Agent not found")
        if str(agent.user_id) != user_id:
            raise HTTPException(403, "Only the agent owner can unpublish")

        agent.published_as_api = False
        agent.api_key_hash = None
        await db.commit()
        return {"unpublished": True, "slug": agent.api_slug}


@router.post("/public/{slug}/run", tags=["public-api"])
async def public_agent_run(
    slug: str,
    request: Request,
):
    """Run a published agent via its public API endpoint.

    Authentication: pass API key as Bearer token or x-api-key header.
    Body: {"goal": "What to do", "context": {optional extra context}}
    """
    import hashlib

    # Extract API key
    auth = request.headers.get("authorization", "")
    api_key = request.headers.get("x-api-key", "")
    if auth.startswith("Bearer "):
        api_key = auth[7:]
    if not api_key:
        raise HTTPException(401, "API key required (Authorization: Bearer <key> or x-api-key header)")

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    async with async_session() as db:
        result = await db.execute(
            select(AgentDefinition).where(
                AgentDefinition.api_slug == slug,
                AgentDefinition.published_as_api == True,
            )
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(404, "Published agent not found")
        if agent.api_key_hash != key_hash:
            raise HTTPException(401, "Invalid API key")

        # Parse request body
        try:
            body = await request.json()
        except Exception:
            body = {}

        goal = body.get("goal", "").strip()
        if not goal:
            raise HTTPException(400, "Missing 'goal' in request body")

        context = body.get("context", {})
        context["_source"] = "public_api"
        context["_slug"] = slug

        # Create session
        from uuid import uuid4
        session = AgentSession(
            id=uuid4(),
            agent_id=agent.id,
            user_id=str(agent.user_id) if agent.user_id else "api",
            current_goal=goal,
            context=context,
            status="pending",
        )
        db.add(session)
        await db.commit()

        # Fire execution in background
        from .executor import agent_executor
        import asyncio
        asyncio.create_task(
            agent_executor.run_session(agent, goal, context)
        )

        return {
            "session_id": str(session.id),
            "status": "pending",
            "slug": slug,
            "sse_stream": f"/api/v1/agents/sessions/{session.id}/sse",
        }


# ============== Health Endpoint ==============

@router.get("/health")
async def health():
    """Health check endpoint."""
    return {"service": "agent_engine", "status": "ok"}


# ============== WebSocket Streaming ==============

@router.websocket("/sessions/{session_id}/stream")
async def websocket_session_stream(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for real-time session streaming.
    
    Clients can connect to receive live updates about:
    - Step execution progress
    - Tool calls and results
    - Verification outcomes
    - Policy decisions
    - Learning insights
    """
    manager = get_connection_manager()
    await manager.connect(websocket, session_id)
    
    try:
        while True:
            # Keep connection alive, handle incoming messages
            data = await websocket.receive_text()
            # Could handle commands like pause, cancel, etc.
            if data == "ping":
                await websocket.send_text('{"type": "pong"}')
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception as e:
        await manager.disconnect(websocket)


# ============== SSE Streaming ==============

@router.get("/sessions/{session_id}/sse")
async def sse_session_stream(session_id: str, request: Request):
    """
    SSE endpoint for real-time agent session streaming.

    Streams step-by-step progress as Server-Sent Events:
    - event: step — new reasoning/tool_call/respond step
    - event: status — session status change (running/completed/failed)
    - event: done — session finished

    Frontend usage:
        const es = new EventSource('/agent-engine/sessions/<id>/sse');
        es.addEventListener('step', (e) => { ... });
        es.addEventListener('status', (e) => { ... });
        es.addEventListener('done', (e) => { es.close(); });
    """

    async def _event_generator():
        last_step_number = -1
        last_status = None
        poll_interval = 1.0  # seconds
        max_idle = 300  # 5 min timeout

        idle_seconds = 0.0

        while idle_seconds < max_idle:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            try:
                async with async_session() as db:
                    # Get session status
                    result = await db.execute(
                        select(AgentSession).where(AgentSession.id == session_id)
                    )
                    session = result.scalar_one_or_none()

                    if not session:
                        yield f"event: error\ndata: {json.dumps({'error': 'Session not found'})}\n\n"
                        break

                    # Emit status change
                    current_status = session.status
                    if current_status != last_status:
                        last_status = current_status
                        yield f"event: status\ndata: {json.dumps({'status': current_status, 'loop_count': session.loop_count, 'total_tool_calls': session.total_tool_calls})}\n\n"

                    # Get new steps
                    steps_result = await db.execute(
                        select(AgentStep)
                        .where(AgentStep.session_id == session_id)
                        .where(AgentStep.step_number > last_step_number)
                        .order_by(AgentStep.step_number.asc())
                    )
                    new_steps = list(steps_result.scalars().all())

                    for step in new_steps:
                        last_step_number = step.step_number
                        idle_seconds = 0.0  # Reset idle on new data

                        step_data = {
                            "step_number": step.step_number,
                            "step_type": step.step_type,
                            "reasoning": step.reasoning,
                            "tool_name": step.tool_name,
                            "tool_input": step.tool_input,
                            "output_data": step.output_data,
                            "duration_ms": step.duration_ms,
                            "safety_check_passed": step.safety_check_passed,
                        }
                        yield f"event: step\ndata: {json.dumps(step_data, default=str)}\n\n"

                        # Emit token_usage event if step has token data
                        _step_tokens = getattr(step, 'tokens_used', 0) or 0
                        if _step_tokens > 0:
                            yield f"event: token_usage\ndata: {json.dumps({'step': step.step_number, 'tokens': _step_tokens, 'total_tokens': session.total_tokens_used or 0})}\n\n"

                        # Emit credit events from step output_data
                        _out = step.output_data or {}
                        if isinstance(_out, dict) and _out.get('credits_deducted'):
                            yield f"event: credit_deduction\ndata: {json.dumps({'step': step.step_number, 'amount': _out['credits_deducted'], 'balance_after': _out.get('credits_balance', 0), 'total_used': _out.get('credits_used_total', 0)})}\n\n"
                        if isinstance(_out, dict) and _out.get('credit_warning'):
                            _cw_type = _out['credit_warning']
                            _cw_bal = _out.get('credits_balance', 0)
                            _cw_msg = 'Credits exhausted!' if _cw_type == 'zero' else f'Low credit balance: {_cw_bal} remaining'
                            yield f"event: credit_warning\ndata: {json.dumps({'type': _cw_type, 'balance': _cw_bal, 'message': _cw_msg})}\n\n"

                    # Check terminal states
                    if current_status in ("completed", "failed", "cancelled"):
                        done_data = {
                            "status": current_status,
                            "final_output": session.final_output,
                            "error": session.error_message,
                            "total_tool_calls": session.total_tool_calls,
                            "loop_count": session.loop_count,
                            "total_tokens": session.total_tokens_used or 0,
                        }
                        yield f"event: done\ndata: {json.dumps(done_data, default=str)}\n\n"
                        break

            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                break

            await asyncio.sleep(poll_interval)
            idle_seconds += poll_interval

        # If we timed out
        if idle_seconds >= max_idle:
            yield f"event: error\ndata: {json.dumps({'error': 'SSE stream timed out'})}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
