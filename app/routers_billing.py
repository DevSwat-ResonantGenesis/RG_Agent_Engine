"""Billing and Usage API Endpoints."""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models_billing import PlanTier, UsageType
from .services.usage_service import UsageService
from .services.billing_service import BillingService, process_stripe_webhook

router = APIRouter(prefix="/billing", tags=["billing"])


# Request/Response Models
class CreateCheckoutRequest(BaseModel):
    plan_tier: str
    success_url: str
    cancel_url: str
    email: Optional[str] = None  # Optional - will be fetched from session if not provided


class CreateCheckoutResponse(BaseModel):
    session_id: str
    checkout_url: str


class TokenPurchaseRequest(BaseModel):
    pack_id: str  # 100k, 500k, 1m, 5m
    success_url: str
    cancel_url: str
    email: EmailStr


class ChangePlanRequest(BaseModel):
    new_plan: str


class CancelSubscriptionRequest(BaseModel):
    at_period_end: bool = True


class UsageSummaryResponse(BaseModel):
    org_id: str
    period_start: str
    period_end: str
    plan_tier: str
    token_limit: Any
    tokens_used: int
    tokens_remaining: Any
    usage_percent: float
    overage_enabled: bool
    overage_tokens: int
    breakdown: dict
    total_tokens: int


class RecordUsageRequest(BaseModel):
    usage_type: str
    quantity: int = 1
    resource_id: Optional[str] = None
    resource_name: Optional[str] = None
    metadata: Optional[dict] = None


class CreditEstimateRequest(BaseModel):
    action: str  # agent_session, workflow_run, chat_conversation, etc.
    params: Optional[dict] = None  # estimated_steps, model, etc.


class CreditEstimateResponse(BaseModel):
    estimated_credits: int
    breakdown: dict
    usd_equivalent: float


# Credit cost constants for estimation (aligned with Credit Calculator spec)
CREDIT_COSTS = {
    # Tier 1: LLM
    "llm_input_1k": 10,
    "llm_output_1k": 30,
    "chat_message": 20,
    # Tier 2: Agent
    "agent_session": 100,
    "agent_step": 50,
    "agent_goal": 200,
    "multi_agent_team": 500,
    # Tier 3: Compute
    "compute_second": 1,
    "code_execution": 5,
    "preview_hour": 300,
    # Tier 4: Workflow
    "workflow_run": 50,
    "workflow_step": 20,
    # Tier 5: Storage
    "storage_mb": 1,
    "memory_write": 2,
    "rag_upload": 10,
    # Tier 6: Blockchain
    "blockchain_audit": 100,
    "blockchain_verify": 10,
    "compliance_report": 500,
    # Tier 7: Hash Sphere
    "hash_sphere_identity": 50,
    "hash_sphere_transaction": 20,
    # Tier 8: Code Visualizer
    "code_analysis": 200,
    "governance_check": 50,
    # State Physics API
    "state_physics_simulation": 1,
    "state_physics_generate": 10,
    "state_physics_invariant": 2,
    "state_physics_agent": 5,
}


# Endpoints

@router.post("/checkout", response_model=CreateCheckoutResponse)
async def create_checkout_session(
    request: CreateCheckoutRequest,
    org_id: str = Header(..., alias="X-Org-ID"),
    db: AsyncSession = Depends(get_session),
):
    """Create Stripe Checkout session for subscription."""
    try:
        plan_tier = PlanTier(request.plan_tier)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid plan tier: {request.plan_tier}")
    
    service = BillingService(db)
    try:
        result = await service.create_checkout_session(
            org_id=UUID(org_id),
            plan_tier=plan_tier,
            success_url=request.success_url,
            cancel_url=request.cancel_url,
            email=request.email,
        )
        return CreateCheckoutResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/checkout/subscription", response_model=CreateCheckoutResponse)
async def create_checkout_subscription_alias(
    request: CreateCheckoutRequest,
    org_id: str = Header(..., alias="X-Org-ID"),
    db: AsyncSession = Depends(get_session),
):
    """Create Stripe Checkout session for subscription (alias)."""
    return await create_checkout_session(request, org_id, db)


@router.post("/stripe/checkout", response_model=CreateCheckoutResponse)
async def create_stripe_checkout_alias(
    request: CreateCheckoutRequest,
    org_id: str = Header(..., alias="X-Org-ID"),
    db: AsyncSession = Depends(get_session),
):
    """Create Stripe Checkout session (alias for /checkout)."""
    return await create_checkout_session(request, org_id, db)


@router.post("/tokens/purchase", response_model=CreateCheckoutResponse)
async def create_token_purchase_session(
    request: TokenPurchaseRequest,
    org_id: str = Header(..., alias="X-Org-ID"),
    db: AsyncSession = Depends(get_session),
):
    """Create checkout session for token pack purchase."""
    service = BillingService(db)
    try:
        result = await service.create_token_purchase_session(
            org_id=UUID(org_id),
            pack_id=request.pack_id,
            success_url=request.success_url,
            cancel_url=request.cancel_url,
            email=request.email,
        )
        return CreateCheckoutResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/subscription/cancel")
async def cancel_subscription(
    request: CancelSubscriptionRequest,
    org_id: str = Header(..., alias="X-Org-ID"),
    db: AsyncSession = Depends(get_session),
):
    """Cancel subscription."""
    service = BillingService(db)
    try:
        result = await service.cancel_subscription(
            org_id=UUID(org_id),
            at_period_end=request.at_period_end,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/subscription/change")
async def change_subscription_plan(
    request: ChangePlanRequest,
    org_id: str = Header(..., alias="X-Org-ID"),
    db: AsyncSession = Depends(get_session),
):
    """Change subscription plan."""
    try:
        new_plan = PlanTier(request.new_plan)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {request.new_plan}")
    
    service = BillingService(db)
    try:
        result = await service.change_plan(
            org_id=UUID(org_id),
            new_plan=new_plan,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/subscription/change-plan")
async def change_subscription_plan_alias(
    request: ChangePlanRequest,
    org_id: str = Header(..., alias="X-Org-ID"),
    db: AsyncSession = Depends(get_session),
):
    """Change subscription plan (alias for /subscription/change)."""
    return await change_subscription_plan(request, org_id, db)


@router.get("/info")
async def get_billing_info(
    org_id: str = Header(..., alias="X-Org-ID"),
    db: AsyncSession = Depends(get_session),
):
    """Get billing information for organization."""
    service = BillingService(db)
    try:
        return await service.get_billing_info(UUID(org_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/overview")
async def get_billing_overview(
    request: Request,
    x_org_id: Optional[str] = Header(None, alias="X-Org-ID"),
    rg_org_id: Optional[str] = Header(None, alias="RG-Org-ID"),
    db: AsyncSession = Depends(get_session),
):
    """Get billing overview for organization (combines info + usage)."""
    # Accept either X-Org-ID or RG-Org-ID header
    org_id = x_org_id or rg_org_id
    if not org_id:
        raise HTTPException(status_code=422, detail="Missing X-Org-ID or RG-Org-ID header")
    
    billing_service = BillingService(db)
    usage_service = UsageService(db)
    
    try:
        billing_info = await billing_service.get_billing_info(UUID(org_id))
    except ValueError:
        # Return default for new orgs
        billing_info = {
            "plan": "free",
            "status": "active",
            "billing_cycle": "monthly",
            "current_period_start": None,
            "current_period_end": None,
        }
    
    try:
        usage_summary = await usage_service.get_usage_summary(UUID(org_id))
    except ValueError:
        usage_summary = {
            "tokens_used": 0,
            "token_limit": 10000,
            "usage_percent": 0,
        }
    
    return {
        "subscription": billing_info,
        "usage": usage_summary,
        "payment_methods": [],
        "invoices": [],
    }


@router.get("/usage")
async def get_usage_summary(
    org_id: str = Header(..., alias="X-Org-ID"),
    db: AsyncSession = Depends(get_session),
):
    """Get usage summary for current billing period."""
    service = UsageService(db)
    try:
        return await service.get_usage_summary(UUID(org_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/usage/summary")
async def get_usage_summary_alias(
    org_id: str = Header(..., alias="X-Org-ID"),
    db: AsyncSession = Depends(get_session),
):
    """Get usage summary (alias for /usage)."""
    return await get_usage_summary(org_id, db)


@router.get("/usage/history")
async def get_usage_history(
    days: int = 30,
    usage_type: Optional[str] = None,
    org_id: str = Header(..., alias="X-Org-ID"),
    db: AsyncSession = Depends(get_session),
):
    """Get usage history."""
    service = UsageService(db)
    
    ut = None
    if usage_type:
        try:
            ut = UsageType(usage_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid usage type: {usage_type}")
    
    return await service.get_usage_history(
        org_id=UUID(org_id),
        days=days,
        usage_type=ut,
    )


@router.post("/usage/record")
async def record_usage(
    request: RecordUsageRequest,
    org_id: str = Header(..., alias="X-Org-ID"),
    user_id: Optional[str] = Header(None, alias="X-User-ID"),
    db: AsyncSession = Depends(get_session),
):
    """Record usage (internal API for services)."""
    try:
        usage_type = UsageType(request.usage_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid usage type: {request.usage_type}")
    
    service = UsageService(db)
    result = await service.record_usage(
        org_id=UUID(org_id),
        usage_type=usage_type,
        quantity=request.quantity,
        user_id=UUID(user_id) if user_id else None,
        resource_id=UUID(request.resource_id) if request.resource_id else None,
        resource_name=request.resource_name,
        metadata=request.metadata,
    )
    
    if not result["success"]:
        raise HTTPException(status_code=402, detail=result)
    
    return result


@router.get("/usage/check")
async def check_can_execute(
    usage_type: str,
    quantity: int = 1,
    org_id: str = Header(..., alias="X-Org-ID"),
    db: AsyncSession = Depends(get_session),
):
    """Check if organization has enough tokens for an operation."""
    try:
        ut = UsageType(usage_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid usage type: {usage_type}")
    
    service = UsageService(db)
    result = await service.check_can_execute(
        org_id=UUID(org_id),
        usage_type=ut,
        quantity=quantity,
    )
    return result


@router.post("/webhook/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(..., alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_session),
):
    """Handle Stripe webhook events."""
    payload = await request.body()
    
    try:
        result = await process_stripe_webhook(db, payload, stripe_signature)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/credits/estimate", response_model=CreditEstimateResponse)
async def estimate_credits(request: CreditEstimateRequest):
    """
    Estimate credit cost before performing an action.
    
    Supports:
    - agent_session: params.estimated_steps, params.model
    - workflow_run: params.steps, params.llm_steps
    - chat_conversation: params.messages, params.evidence_lookups
    - code_execution: params.estimated_seconds
    - blockchain_audit: params.entries
    """
    action = request.action
    params = request.params or {}
    breakdown = {}
    total = 0
    
    if action == "agent_session":
        # Agent session cost formula
        steps = params.get("estimated_steps", 10)
        tokens_k = params.get("tokens_k", 5)
        compute_seconds = params.get("compute_seconds", 30)
        
        breakdown["session_start"] = CREDIT_COSTS["agent_session"]
        breakdown["steps"] = steps * CREDIT_COSTS["agent_step"]
        breakdown["tokens"] = tokens_k * CREDIT_COSTS["chat_message"]
        breakdown["compute"] = compute_seconds * CREDIT_COSTS["compute_second"]
        total = sum(breakdown.values())
        
    elif action == "workflow_run":
        # Workflow cost formula
        steps = params.get("steps", 5)
        llm_steps = params.get("llm_steps", 2)
        compute_seconds = params.get("compute_seconds", 10)
        
        breakdown["workflow_start"] = CREDIT_COSTS["workflow_run"]
        breakdown["steps"] = steps * CREDIT_COSTS["workflow_step"]
        breakdown["llm_steps"] = llm_steps * CREDIT_COSTS["agent_step"]
        breakdown["compute"] = compute_seconds * CREDIT_COSTS["compute_second"]
        total = sum(breakdown.values())
        
    elif action == "chat_conversation":
        # Chat cost formula
        messages = params.get("messages", 10)
        evidence_lookups = params.get("evidence_lookups", 0)
        memory_anchors = params.get("memory_anchors", 0)
        
        breakdown["messages"] = messages * CREDIT_COSTS["chat_message"]
        breakdown["evidence_lookups"] = evidence_lookups * CREDIT_COSTS["code_execution"]
        breakdown["memory_anchors"] = memory_anchors * CREDIT_COSTS["memory_write"]
        total = sum(breakdown.values())
        
    elif action == "code_execution":
        # Code execution cost formula
        estimated_seconds = params.get("estimated_seconds", 5)
        
        breakdown["base"] = CREDIT_COSTS["code_execution"]
        breakdown["compute"] = estimated_seconds * CREDIT_COSTS["compute_second"]
        total = sum(breakdown.values())
        
    elif action == "multi_agent_team":
        # Multi-agent team cost formula
        agents = params.get("agents", 3)
        steps_per_agent = params.get("steps_per_agent", 5)
        
        breakdown["team_start"] = CREDIT_COSTS["multi_agent_team"]
        breakdown["agent_sessions"] = agents * CREDIT_COSTS["agent_session"]
        breakdown["total_steps"] = agents * steps_per_agent * CREDIT_COSTS["agent_step"]
        total = sum(breakdown.values())
        
    elif action == "blockchain_audit":
        # Blockchain audit cost
        entries = params.get("entries", 1)
        
        breakdown["audit_entries"] = entries * CREDIT_COSTS["blockchain_audit"]
        total = sum(breakdown.values())
        
    elif action == "code_analysis":
        # Code visualizer analysis
        breakdown["analysis"] = CREDIT_COSTS["code_analysis"]
        total = CREDIT_COSTS["code_analysis"]
        
    elif action == "state_physics_simulation":
        # State Physics simulation cost
        # 1 SU = 1 step × 1000 nodes × invariant check
        steps = params.get("steps", 10)
        nodes = params.get("nodes", 100)
        invariant_checks = params.get("invariant_checks", 1)
        realtime_multiplier = params.get("realtime_multiplier", 1.0)  # 1.0, 1.5, or 3.0
        
        # Calculate simulation units (per 1k nodes)
        su = (steps * (nodes / 1000) * invariant_checks)
        
        breakdown["simulation_steps"] = int(steps * CREDIT_COSTS["state_physics_simulation"])
        breakdown["invariant_checks"] = int(invariant_checks * CREDIT_COSTS["state_physics_invariant"])
        breakdown["realtime_premium"] = int((su * realtime_multiplier) - su) if realtime_multiplier > 1 else 0
        total = int(sum(breakdown.values()) * realtime_multiplier)
        
    elif action == "state_physics_generate":
        # State Physics universe generation
        nodes = params.get("nodes", 100)
        
        breakdown["generation"] = CREDIT_COSTS["state_physics_generate"]
        breakdown["node_setup"] = int((nodes / 100) * CREDIT_COSTS["state_physics_simulation"])
        total = sum(breakdown.values())
        
    else:
        # Default: look up single action cost
        if action in CREDIT_COSTS:
            quantity = params.get("quantity", 1)
            breakdown[action] = CREDIT_COSTS[action] * quantity
            total = breakdown[action]
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
    
    # 1 credit = $0.001
    usd_equivalent = total * 0.001
    
    return CreditEstimateResponse(
        estimated_credits=total,
        breakdown=breakdown,
        usd_equivalent=round(usd_equivalent, 4)
    )


# Token packs info endpoint
@router.get("/tokens/packs")
async def get_token_packs():
    """Get available token packs."""
    return {
        "packs": [
            {"id": "100k", "tokens": 100_000, "price": 9.00, "savings": "10%"},
            {"id": "500k", "tokens": 500_000, "price": 40.00, "savings": "20%"},
            {"id": "1m", "tokens": 1_000_000, "price": 70.00, "savings": "30%"},
            {"id": "5m", "tokens": 5_000_000, "price": 300.00, "savings": "40%"},
        ],
        "pay_as_you_go_rate": 0.10,  # $0.10 per 1K tokens
    }


# Plans info endpoint
@router.get("/plans")
async def get_plans():
    """Get available subscription plans.
    
    Aligned with frontend config/pricing.ts - 3 tiers: Developer, Plus, Enterprise.
    Uses Resonant Credits system (1 credit ≈ $0.001).
    """
    return {
        "plans": [
            {
                "id": "developer",
                "name": "Developer",
                "price": 0,
                "price_yearly": 0,
                "badge": "Free Forever",
                "description": "For solo builders exploring ResonantGenesis.",
                "credits": {
                    "included": 1000,
                    "display": "1,000 / month",
                    "rollover": False,
                    "topups": False,
                    "note": "No rollover • No top-ups",
                },
                "limits": {
                    "agents": 3,
                    "autonomous_mode": False,
                    "teams": 0,
                    "users": 1,
                    "conversations": 10,
                    "messages_per_day": 100,
                    "compute_hours": 10,
                    "storage_mb": 100,
                },
                "features": [
                    "Free forever",
                    "1,000 credits/month",
                    "Up to 3 agents (no autonomous mode)",
                    "10 conversations, 100 messages/day",
                    "100 MB storage, 5 RAG documents",
                    "10 compute hours/month",
                    "Manual kill switch, 5 basic invariants",
                    "Community support only",
                ],
            },
            {
                "id": "plus",
                "name": "Plus",
                "price": 49,
                "price_yearly": 490,
                "badge": "Professional",
                "recommended": True,
                "description": "For serious builders, teams, and power users.",
                "credits": {
                    "included": 50000,
                    "display": "50,000 / month",
                    "rollover": True,
                    "rollover_limit": 25000,
                    "topups": True,
                    "topup_price": 8,
                    "topup_amount": 10000,
                    "note": "Rollover up to 25K • Top-ups: $8/10K",
                },
                "limits": {
                    "agents": 20,
                    "autonomous_mode": True,
                    "teams": 5,
                    "users": 5,
                    "conversations": 1000,
                    "messages_per_day": 1000,
                    "compute_hours": 100,
                    "storage_mb": 5000,
                },
                "features": [
                    "50,000 credits/month",
                    "Rollover up to 25K credits",
                    "Top-ups: $8/10K credits",
                    "Up to 20 agents with autonomous mode",
                    "Agent teams enabled",
                    "1,000 conversations, 1,000 messages/day",
                    "Evidence graph access",
                    "5 GB storage, 100 RAG documents",
                    "100 compute hours/month, unlimited preview",
                    "Full AI assistance",
                    "Automated kill switch, 15 invariants, 10 snapshots",
                    "Hash Sphere Memory: 1 Universe",
                    "Code Visualizer: graphs + dependency analysis",
                    "Email + Slack support",
                ],
            },
            {
                "id": "enterprise",
                "name": "Enterprise",
                "price": 0,
                "price_yearly": 0,
                "badge": "Custom",
                "contact_sales": True,
                "description": "For organizations running AI as critical infrastructure.",
                "credits": {
                    "included": -1,
                    "display": "Custom",
                    "rollover": True,
                    "topups": True,
                    "note": "Tailored to your needs",
                },
                "limits": {
                    "agents": -1,
                    "autonomous_mode": True,
                    "teams": -1,
                    "users": -1,
                    "conversations": -1,
                    "messages_per_day": -1,
                    "compute_hours": -1,
                    "storage_mb": -1,
                },
                "features": [
                    "Custom credits (tailored to your needs)",
                    "Unlimited agents with full autonomous mode",
                    "Agent team hierarchies",
                    "Unlimited conversations, custom models",
                    "100 GB+ storage, unlimited RAG documents",
                    "Unlimited compute hours, custom runtimes",
                    "SLA-backed kill switch, custom invariants, unlimited snapshots",
                    "Hash Sphere Memory: Multi Universe + Multi-layer",
                    "Code Visualizer: full access + CI integration",
                    "Dedicated engineers, architecture guidance",
                    "99.9% SLA guarantee",
                    "SOC2, HIPAA, GDPR compliance",
                    "On-premise/hybrid deployment option",
                ],
            },
        ],
        # Credit exchange rates for frontend display - aligned with Credit Calculator spec
        # 1 Credit ≈ $0.001 (1/10th of a cent)
        "credit_rates": {
            # Tier 1: LLM Tokens
            "llm_input_1k": 10,
            "llm_output_1k": 30,
            "chat_message": 20,
            # Tier 2: Agent Execution
            "agent_session": 100,
            "agent_step": 50,
            "agent_goal": 200,
            "multi_agent_team": 500,
            # Tier 3: Compute
            "compute_second": 1,
            "compute_minute": 60,
            "code_execution": 5,
            "preview_hour": 300,
            # Tier 4: Workflow
            "workflow_run": 50,
            "workflow_step": 20,
            "scheduled_trigger": 10,
            "webhook_trigger": 5,
            # Tier 5: Storage & Memory
            "storage_mb": 1,
            "storage_gb": 1000,
            "memory_write": 2,
            "memory_read": 0,
            "rag_upload": 10,
            # Tier 6: Blockchain Audit
            "blockchain_audit": 100,
            "blockchain_verify": 10,
            "compliance_report": 500,
            "smart_contract_deploy": 1000,
            # Tier 7: Hash Sphere
            "hash_sphere_identity": 50,
            "hash_sphere_transaction": 20,
            "hash_sphere_trust": 10,
            "hash_sphere_perturbation": 100,
            # Tier 8: Code Visualizer
            "code_analysis": 200,
            "governance_check": 50,
            "graph_export": 20,
        },
        # Credit packs for top-up - aligned with Credit Calculator spec
        # Professional: $8 per 10K credits ($0.80 per 1K)
        # Enterprise: $5 per 10K credits ($0.50 per 1K)
        "credit_packs": [
            {"id": "pack-10k", "credits": 10000, "price": 8, "label": "10K credits", "per_k": 0.80, "tier": "professional"},
            {"id": "pack-50k", "credits": 50000, "price": 35, "label": "50K credits", "per_k": 0.70, "tier": "professional"},
            {"id": "pack-100k", "credits": 100000, "price": 60, "label": "100K credits", "per_k": 0.60, "tier": "professional"},
            {"id": "pack-10k-ent", "credits": 10000, "price": 5, "label": "10K credits", "per_k": 0.50, "tier": "enterprise"},
            {"id": "pack-100k-ent", "credits": 100000, "price": 45, "label": "100K credits", "per_k": 0.45, "tier": "enterprise"},
            {"id": "pack-1m-ent", "credits": 1000000, "price": 400, "label": "1M credits", "per_k": 0.40, "tier": "enterprise"},
        ],
        # Feature add-ons available for paid plans
        "addons": [
            # Capacity add-ons
            {"id": "addon-agents-10", "category": "capacity", "name": "+10 Agents", "price": 10, "unit": "month", "description": "Add 10 more agents to your plan"},
            {"id": "addon-agents-50", "category": "capacity", "name": "+50 Agents", "price": 40, "unit": "month", "description": "Add 50 more agents to your plan"},
            {"id": "addon-users-5", "category": "capacity", "name": "+5 Users", "price": 25, "unit": "month", "description": "Add 5 more team members"},
            {"id": "addon-users-20", "category": "capacity", "name": "+20 Users", "price": 80, "unit": "month", "description": "Add 20 more team members"},
            {"id": "addon-teams-5", "category": "capacity", "name": "+5 Teams", "price": 15, "unit": "month", "description": "Add 5 more teams"},
            {"id": "addon-storage-10gb", "category": "capacity", "name": "+10GB Storage", "price": 5, "unit": "month", "description": "Add 10GB storage"},
            {"id": "addon-storage-100gb", "category": "capacity", "name": "+100GB Storage", "price": 40, "unit": "month", "description": "Add 100GB storage"},
            # Feature add-ons
            {"id": "addon-priority-queue", "category": "feature", "name": "Priority Execution", "price": 20, "unit": "month", "description": "Priority execution queue for faster agent runs"},
            {"id": "addon-sso", "category": "feature", "name": "SSO/SAML", "price": 50, "unit": "month", "description": "Single sign-on with your identity provider"},
            {"id": "addon-audit-logs", "category": "feature", "name": "Advanced Audit Logs", "price": 30, "unit": "month", "description": "90-day audit log retention with export"},
            {"id": "addon-custom-domain", "category": "feature", "name": "Custom Domain", "price": 25, "unit": "month", "description": "Use your own domain for agent URLs"},
            {"id": "addon-webhook-pro", "category": "feature", "name": "Advanced Webhooks", "price": 15, "unit": "month", "description": "Webhook retries, filtering, and transformations"},
            {"id": "addon-api-unlimited", "category": "feature", "name": "Unlimited API Rate", "price": 100, "unit": "month", "description": "Remove API rate limits"},
            # Governance add-ons
            {"id": "addon-blockchain-audit", "category": "governance", "name": "Blockchain Audit Trail", "price": 75, "unit": "month", "description": "Immutable audit trail on blockchain"},
            {"id": "addon-compliance-reports", "category": "governance", "name": "Compliance Reports", "price": 50, "unit": "month", "description": "SOC2, HIPAA, GDPR compliance reports"},
            {"id": "addon-rara-enforcement", "category": "governance", "name": "RARA Governance", "price": 100, "unit": "month", "description": "Full RARA governance enforcement"},
        ],
    }
