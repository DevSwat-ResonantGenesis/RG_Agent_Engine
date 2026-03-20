"""Billing and Usage Tracking Models."""

from datetime import datetime
from enum import Enum
import uuid

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, Boolean, JSON, ForeignKey, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from .db import Base


class PlanTier(str, Enum):
    # Frontend-aligned plan tiers (source of truth: config/pricing.ts)
    DEVELOPER = "developer"  # Free forever
    PLUS = "plus"            # $49/month
    ENTERPRISE = "enterprise"  # Custom pricing
    # Legacy tiers (kept for backwards compatibility)
    FREE = "free"            # Alias for DEVELOPER
    PRO = "pro"              # Alias for PLUS


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    CANCELED = "canceled"
    PAST_DUE = "past_due"
    TRIALING = "trialing"
    PAUSED = "paused"


class UsageType(str, Enum):
    # LLM Operations (Tier 1)
    LLM_INPUT = "llm_input"
    LLM_OUTPUT = "llm_output"
    CHAT_MESSAGE = "chat_message"
    # Agent Operations (Tier 2)
    AGENT_SESSION = "agent_session"
    AGENT_STEP = "agent_step"
    AGENT_EXECUTION = "agent_execution"  # Legacy alias for AGENT_STEP
    AGENT_GOAL = "agent_goal"
    MULTI_AGENT_TEAM = "multi_agent_team"
    # Compute Operations (Tier 3)
    COMPUTE_SECOND = "compute_second"
    CODE_EXECUTION = "code_execution"
    PREVIEW_HOUR = "preview_hour"
    # Workflow Operations (Tier 4)
    WORKFLOW_RUN = "workflow_run"
    WORKFLOW_STEP = "workflow_step"
    SCHEDULED_TRIGGER = "scheduled_trigger"
    WEBHOOK_TRIGGER = "webhook_trigger"
    # Storage Operations (Tier 5)
    STORAGE = "storage"
    MEMORY_WRITE = "memory_write"
    MEMORY_READ = "memory_read"
    RAG_UPLOAD = "rag_upload"
    # Blockchain Operations (Tier 6)
    BLOCKCHAIN_AUDIT = "blockchain_audit"
    BLOCKCHAIN_VERIFY = "blockchain_verify"
    COMPLIANCE_REPORT = "compliance_report"
    # Hash Sphere Operations (Tier 7)
    HASH_SPHERE_IDENTITY = "hash_sphere_identity"
    HASH_SPHERE_TRANSACTION = "hash_sphere_transaction"
    HASH_SPHERE_TRUST = "hash_sphere_trust"
    HASH_SPHERE_PERTURBATION = "hash_sphere_perturbation"
    # Code Visualizer Operations (Tier 8)
    CODE_ANALYSIS = "code_analysis"
    GOVERNANCE_CHECK = "governance_check"
    GRAPH_EXPORT = "graph_export"
    # State Physics Operations (Tier 9)
    STATE_PHYSICS_SIMULATION = "state_physics_simulation"  # Per simulation step
    STATE_PHYSICS_GENERATE = "state_physics_generate"      # Universe generation
    STATE_PHYSICS_INVARIANT = "state_physics_invariant"    # Invariant check
    STATE_PHYSICS_AGENT = "state_physics_agent"            # Agent spawn/action


# Credit costs per operation - aligned with Credit Calculator spec
# 1 Credit ≈ $0.001 (1/10th of a cent)
TOKEN_COSTS = {
    # Tier 1: LLM Tokens (Most Expensive)
    UsageType.LLM_INPUT: 10,            # 10 credits per 1K input tokens
    UsageType.LLM_OUTPUT: 30,           # 30 credits per 1K output tokens
    UsageType.CHAT_MESSAGE: 20,         # 20 credits per message (avg 500 tokens)
    
    # Tier 2: Agent Execution (High Value)
    UsageType.AGENT_SESSION: 100,       # 100 credits per session start
    UsageType.AGENT_STEP: 50,           # 50 credits per reasoning step
    UsageType.AGENT_EXECUTION: 50,      # Legacy alias for AGENT_STEP
    UsageType.AGENT_GOAL: 200,          # 200 credits per goal completion
    UsageType.MULTI_AGENT_TEAM: 500,    # 500 credits per team run
    
    # Tier 3: Compute (Code Execution)
    UsageType.COMPUTE_SECOND: 1,        # 1 credit per compute second
    UsageType.CODE_EXECUTION: 5,        # 5 credits base per execution
    UsageType.PREVIEW_HOUR: 300,        # 300 credits per preview hour
    
    # Tier 4: Workflow Automation
    UsageType.WORKFLOW_RUN: 50,         # 50 credits per workflow run
    UsageType.WORKFLOW_STEP: 20,        # 20 credits per workflow step
    UsageType.SCHEDULED_TRIGGER: 10,    # 10 credits per scheduled trigger
    UsageType.WEBHOOK_TRIGGER: 5,       # 5 credits per webhook trigger
    
    # Tier 5: Storage & Memory (Cheapest)
    UsageType.STORAGE: 1,               # 1 credit per MB
    UsageType.MEMORY_WRITE: 2,          # 2 credits per memory write
    UsageType.MEMORY_READ: 0,           # Free to read
    UsageType.RAG_UPLOAD: 10,           # 10 credits per RAG document
    
    # Tier 6: Blockchain Audit (Premium)
    UsageType.BLOCKCHAIN_AUDIT: 100,    # 100 credits per audit entry
    UsageType.BLOCKCHAIN_VERIFY: 10,    # 10 credits per verification
    UsageType.COMPLIANCE_REPORT: 500,   # 500 credits per compliance report
    
    # Tier 7: Hash Sphere Operations
    UsageType.HASH_SPHERE_IDENTITY: 50,       # 50 credits per identity add
    UsageType.HASH_SPHERE_TRANSACTION: 20,    # 20 credits per transaction
    UsageType.HASH_SPHERE_TRUST: 10,          # 10 credits per trust relationship
    UsageType.HASH_SPHERE_PERTURBATION: 100,  # 100 credits per perturbation sim
    
    # Tier 8: Code Visualizer
    UsageType.CODE_ANALYSIS: 200,       # 200 credits per codebase analysis
    UsageType.GOVERNANCE_CHECK: 50,     # 50 credits per governance check
    UsageType.GRAPH_EXPORT: 20,         # 20 credits per graph export
    
    # Tier 9: State Physics API (Simulation Units based)
    # 1 SU = 1 step × 1000 nodes × invariant check
    # Dev: 100k SU/mo @ $49, Startup: 2M SU/mo @ $299
    UsageType.STATE_PHYSICS_SIMULATION: 1,   # 1 credit per simulation step per 1k nodes
    UsageType.STATE_PHYSICS_GENERATE: 10,    # 10 credits per universe generation
    UsageType.STATE_PHYSICS_INVARIANT: 2,    # 2 credits per invariant check (x2 multiplier)
    UsageType.STATE_PHYSICS_AGENT: 5,        # 5 credits per agent action
}

# Plan credit allocations (monthly) - aligned with frontend pricing.ts
PLAN_TOKENS = {
    # Primary tiers (frontend-aligned)
    PlanTier.DEVELOPER: 1_000,      # 1,000 credits/month (no rollover, no top-ups)
    PlanTier.PLUS: 50_000,          # 50,000 credits/month (rollover up to 25K, top-ups $8/10K)
    PlanTier.ENTERPRISE: -1,        # Custom (unlimited)
    # Legacy aliases
    PlanTier.FREE: 1_000,           # Same as DEVELOPER
    PlanTier.PRO: 50_000,           # Same as PLUS
}

# Plan prices (monthly in cents) - aligned with frontend pricing.ts
PLAN_PRICES = {
    # Primary tiers (frontend-aligned)
    PlanTier.DEVELOPER: 0,          # Free forever
    PlanTier.PLUS: 4900,            # $49/month
    PlanTier.ENTERPRISE: 0,         # Custom pricing (contact sales)
    # Legacy aliases
    PlanTier.FREE: 0,               # Same as DEVELOPER
    PlanTier.PRO: 4900,             # Same as PLUS
}


class Organization(Base):
    """Organization for billing and team management."""
    __tablename__ = "organizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=False)
    
    # Billing
    stripe_customer_id = Column(String(255), nullable=True, unique=True)
    stripe_subscription_id = Column(String(255), nullable=True)
    
    # Plan
    plan_tier = Column(String(32), default=PlanTier.FREE.value)
    subscription_status = Column(String(32), default=SubscriptionStatus.ACTIVE.value)
    billing_period_start = Column(DateTime(timezone=True), nullable=True)
    billing_period_end = Column(DateTime(timezone=True), nullable=True)
    
    # Token allocation
    monthly_token_limit = Column(BigInteger, default=10000)  # From plan
    tokens_used_this_period = Column(BigInteger, default=0)
    overage_tokens_used = Column(BigInteger, default=0)
    
    # Settings
    overage_enabled = Column(Boolean, default=False)  # Allow overage charges
    overage_limit = Column(BigInteger, nullable=True)  # Max overage tokens
    alert_threshold = Column(Integer, default=80)  # Alert at 80% usage
    
    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Subscription(Base):
    """Subscription record for billing history."""
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    
    # Stripe
    stripe_subscription_id = Column(String(255), nullable=True)
    stripe_price_id = Column(String(255), nullable=True)
    
    # Plan details
    plan_tier = Column(String(32), nullable=False)
    status = Column(String(32), default=SubscriptionStatus.ACTIVE.value)
    
    # Billing period
    current_period_start = Column(DateTime(timezone=True), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    
    # Cancellation
    cancel_at_period_end = Column(Boolean, default=False)
    canceled_at = Column(DateTime(timezone=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class UsageRecord(Base):
    """Individual usage record for tracking token consumption."""
    __tablename__ = "usage_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    
    # Usage details
    usage_type = Column(String(32), nullable=False)  # agent_execution, workflow_run, etc.
    tokens_consumed = Column(BigInteger, nullable=False)
    
    # Context
    resource_id = Column(UUID(as_uuid=True), nullable=True)  # Agent ID, workflow ID, etc.
    resource_name = Column(String(255), nullable=True)
    extra_metadata = Column(JSON, nullable=True)  # Additional context
    
    # Billing period reference
    billing_period_start = Column(DateTime(timezone=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class UsageSummary(Base):
    """Daily/monthly usage summary for quick lookups."""
    __tablename__ = "usage_summaries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    
    # Period
    period_type = Column(String(16), nullable=False)  # daily, monthly
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)
    
    # Totals
    total_tokens = Column(BigInteger, default=0)
    agent_executions = Column(Integer, default=0)
    workflow_runs = Column(Integer, default=0)
    storage_gb = Column(Float, default=0)
    llm_input_tokens = Column(BigInteger, default=0)
    llm_output_tokens = Column(BigInteger, default=0)
    
    # Costs
    base_tokens_used = Column(BigInteger, default=0)  # Within plan
    overage_tokens_used = Column(BigInteger, default=0)  # Beyond plan
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Invoice(Base):
    """Invoice record for billing history."""
    __tablename__ = "invoices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    
    # Stripe
    stripe_invoice_id = Column(String(255), nullable=True, unique=True)
    stripe_payment_intent_id = Column(String(255), nullable=True)
    
    # Invoice details
    status = Column(String(32), default="draft")  # draft, open, paid, void, uncollectible
    currency = Column(String(3), default="usd")
    
    # Amounts (in cents)
    subtotal = Column(Integer, default=0)
    tax = Column(Integer, default=0)
    total = Column(Integer, default=0)
    amount_paid = Column(Integer, default=0)
    amount_due = Column(Integer, default=0)
    
    # Period
    period_start = Column(DateTime(timezone=True), nullable=True)
    period_end = Column(DateTime(timezone=True), nullable=True)
    
    # Line items
    line_items = Column(JSON, nullable=True)  # [{description, amount, quantity}]
    
    # URLs
    invoice_pdf = Column(String(512), nullable=True)
    hosted_invoice_url = Column(String(512), nullable=True)
    
    # Dates
    due_date = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PaymentMethod(Base):
    """Stored payment methods."""
    __tablename__ = "payment_methods"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    
    # Stripe
    stripe_payment_method_id = Column(String(255), nullable=False)
    
    # Card details (safe to store)
    card_brand = Column(String(32), nullable=True)  # visa, mastercard, etc.
    card_last4 = Column(String(4), nullable=True)
    card_exp_month = Column(Integer, nullable=True)
    card_exp_year = Column(Integer, nullable=True)
    
    # Status
    is_default = Column(Boolean, default=False)
    is_valid = Column(Boolean, default=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TokenPurchase(Base):
    """Token pack purchases."""
    __tablename__ = "token_purchases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    
    # Purchase details
    tokens_purchased = Column(BigInteger, nullable=False)
    price_paid = Column(Integer, nullable=False)  # In cents
    
    # Stripe
    stripe_payment_intent_id = Column(String(255), nullable=True)
    
    # Status
    status = Column(String(32), default="pending")  # pending, completed, failed, refunded
    
    # Tokens remaining from this purchase
    tokens_remaining = Column(BigInteger, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
