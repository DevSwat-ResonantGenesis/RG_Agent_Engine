"""
Autonomy Mode Database Models
=============================

Database models for the dual-mode autonomy system:
- Agent autonomy mode settings
- Agent wallets and transactions
- Goals
- Negotiations and contracts
- Approvals
"""

from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, Boolean, JSON, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum

from .db import Base


# ============== Enums ==============

class AutonomyModeEnum(str, enum.Enum):
    UNBOUNDED = "unbounded"
    GOVERNED = "governed"


class TransactionTypeEnum(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    REFUND = "refund"
    REWARD = "reward"
    PENALTY = "penalty"


class TransactionStatusEnum(str, enum.Enum):
    PENDING = "pending"
    PENDING_APPROVAL = "pending_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERSED = "reversed"
    REJECTED = "rejected"


class GoalTypeEnum(str, enum.Enum):
    ASSIGNED = "assigned"
    DERIVED = "derived"
    SELF_GENERATED = "self_generated"
    EMERGENT = "emergent"


class GoalStatusEnum(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"
    BLOCKED = "blocked"


class NegotiationTypeEnum(str, enum.Enum):
    TASK_BID = "task_bid"
    RESOURCE_TRADE = "resource_trade"
    CAPABILITY_SHARE = "capability_share"
    CONTRACT = "contract"


class NegotiationStatusEnum(str, enum.Enum):
    OPEN = "open"
    BIDDING = "bidding"
    NEGOTIATING = "negotiating"
    AGREED = "agreed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    CANCELLED = "cancelled"


class ContractStatusEnum(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    COMPLETED = "completed"
    BREACHED = "breached"
    CANCELLED = "cancelled"
    DISPUTED = "disputed"


class ApprovalStatusEnum(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


# ============== Autonomy Mode ==============

class AgentAutonomyMode(Base):
    """Agent autonomy mode settings."""
    __tablename__ = "agent_autonomy_modes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), index=True, unique=True, nullable=False)
    
    # Current mode
    mode = Column(SQLEnum(AutonomyModeEnum), default=AutonomyModeEnum.GOVERNED, nullable=False)
    
    # Custom configuration overrides (optional)
    config_overrides = Column(JSON, nullable=True)
    
    # Access control
    can_switch_to_unbounded = Column(Boolean, default=False)
    unbounded_approved_by = Column(UUID(as_uuid=True), nullable=True)
    unbounded_approved_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class AgentModeTransition(Base):
    """Record of autonomy mode transitions."""
    __tablename__ = "agent_mode_transitions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    
    from_mode = Column(SQLEnum(AutonomyModeEnum), nullable=False)
    to_mode = Column(SQLEnum(AutonomyModeEnum), nullable=False)
    
    initiated_by = Column(UUID(as_uuid=True), nullable=False)  # user_id
    reason = Column(Text, nullable=True)
    
    # Approval (for UNBOUNDED transitions)
    approved_by = Column(UUID(as_uuid=True), nullable=True)
    
    # Extra data
    extra_data = Column(JSON, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ============== Wallet ==============

class AgentWallet(Base):
    """Agent wallet for financial operations."""
    __tablename__ = "agent_wallets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), index=True, unique=True, nullable=False)
    
    # Balance
    balance = Column(Float, default=0.0, nullable=False)
    currency = Column(String(8), default="USD", nullable=False)
    
    # Limits
    daily_limit = Column(Float, default=100.0, nullable=False)
    transaction_limit = Column(Float, default=50.0, nullable=False)
    monthly_limit = Column(Float, default=1000.0, nullable=False)
    
    # Tracking
    daily_spent = Column(Float, default=0.0, nullable=False)
    monthly_spent = Column(Float, default=0.0, nullable=False)
    total_spent = Column(Float, default=0.0, nullable=False)
    total_earned = Column(Float, default=0.0, nullable=False)
    
    # Reset tracking
    last_daily_reset = Column(DateTime(timezone=True), nullable=True)
    last_monthly_reset = Column(DateTime(timezone=True), nullable=True)
    
    # Access control
    approved_recipients = Column(ARRAY(UUID(as_uuid=True)), nullable=True)
    blocked_recipients = Column(ARRAY(UUID(as_uuid=True)), nullable=True)
    
    # Status
    is_active = Column(Boolean, default=True, nullable=False)
    is_frozen = Column(Boolean, default=False, nullable=False)
    frozen_reason = Column(Text, nullable=True)
    frozen_by = Column(UUID(as_uuid=True), nullable=True)
    frozen_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class WalletTransaction(Base):
    """Wallet transaction record."""
    __tablename__ = "wallet_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wallet_id = Column(UUID(as_uuid=True), ForeignKey("agent_wallets.id"), index=True, nullable=False)
    
    # Transaction details
    type = Column(SQLEnum(TransactionTypeEnum), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(8), default="USD", nullable=False)
    description = Column(Text, nullable=True)
    
    # For transfers
    counterparty_wallet_id = Column(UUID(as_uuid=True), nullable=True)
    
    # Status
    status = Column(SQLEnum(TransactionStatusEnum), default=TransactionStatusEnum.PENDING, nullable=False)
    
    # Approval
    requires_approval = Column(Boolean, default=False, nullable=False)
    approval_id = Column(UUID(as_uuid=True), nullable=True)
    approved_by = Column(UUID(as_uuid=True), nullable=True)
    
    # Extra data
    extra_data = Column(JSON, nullable=True)
    
    # Audit
    audit_hash = Column(String(128), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)


# ============== Goals ==============

class AgentGoal(Base):
    """Agent goal."""
    __tablename__ = "agent_goals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    
    # Goal details
    description = Column(Text, nullable=False)
    goal_type = Column(SQLEnum(GoalTypeEnum), nullable=False)
    priority = Column(Integer, default=5, nullable=False)  # 1-9
    status = Column(SQLEnum(GoalStatusEnum), default=GoalStatusEnum.PENDING, nullable=False)
    
    # Hierarchy
    parent_goal_id = Column(UUID(as_uuid=True), ForeignKey("agent_goals.id"), nullable=True)
    
    # Success criteria
    success_criteria = Column(ARRAY(String), nullable=True)
    completion_percentage = Column(Float, default=0.0, nullable=False)
    
    # Timing
    deadline = Column(DateTime(timezone=True), nullable=True)
    estimated_effort_hours = Column(Float, default=0.0, nullable=False)
    actual_effort_hours = Column(Float, default=0.0, nullable=False)
    
    # Metadata
    created_by = Column(UUID(as_uuid=True), nullable=False)  # agent_id or user_id
    context = Column(JSON, nullable=True)
    tags = Column(ARRAY(String), nullable=True)
    
    # Dependencies
    depends_on = Column(ARRAY(UUID(as_uuid=True)), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)


# ============== Negotiation ==============

class AgentNegotiation(Base):
    """Negotiation between agents."""
    __tablename__ = "agent_negotiations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Type
    type = Column(SQLEnum(NegotiationTypeEnum), nullable=False)
    
    # Participants
    initiator_agent_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    target_agent_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=False)
    
    # Subject
    subject = Column(JSON, nullable=False)
    description = Column(Text, nullable=True)
    
    # Status
    status = Column(SQLEnum(NegotiationStatusEnum), default=NegotiationStatusEnum.OPEN, nullable=False)
    
    # Winning bid
    winning_bid_id = Column(UUID(as_uuid=True), nullable=True)
    
    # Resulting contract
    contract_id = Column(UUID(as_uuid=True), nullable=True)
    
    # Configuration
    min_bid = Column(Float, default=0.0, nullable=True)
    max_bid = Column(Float, nullable=True)
    
    # Extra data
    extra_data = Column(JSON, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)


class AgentBid(Base):
    """Bid in a negotiation."""
    __tablename__ = "agent_bids"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    negotiation_id = Column(UUID(as_uuid=True), ForeignKey("agent_negotiations.id"), index=True, nullable=False)
    agent_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    
    # Offer
    offer = Column(JSON, nullable=False)
    price = Column(Float, nullable=False)
    currency = Column(String(8), default="USD", nullable=False)
    
    # Agent's assessment
    confidence = Column(Float, default=0.8, nullable=False)
    estimated_completion = Column(DateTime(timezone=True), nullable=True)
    estimated_duration_hours = Column(Float, default=1.0, nullable=False)
    
    # Status
    is_winning = Column(Boolean, default=False, nullable=False)
    is_rejected = Column(Boolean, default=False, nullable=False)
    rejection_reason = Column(Text, nullable=True)
    
    # Extra data
    extra_data = Column(JSON, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentContract(Base):
    """Binding contract between agents."""
    __tablename__ = "agent_contracts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    negotiation_id = Column(UUID(as_uuid=True), ForeignKey("agent_negotiations.id"), index=True, nullable=False)
    
    # Parties
    parties = Column(ARRAY(UUID(as_uuid=True)), nullable=False)
    
    # Terms
    terms = Column(JSON, nullable=False)
    description = Column(Text, nullable=True)
    
    # Financial terms
    total_value = Column(Float, default=0.0, nullable=False)
    currency = Column(String(8), default="USD", nullable=False)
    rewards = Column(JSON, nullable=True)  # agent_id -> reward
    penalties = Column(JSON, nullable=True)  # agent_id -> penalty
    
    # Status
    status = Column(SQLEnum(ContractStatusEnum), default=ContractStatusEnum.DRAFT, nullable=False)
    
    # Approval
    requires_approval = Column(Boolean, default=False, nullable=False)
    approval_id = Column(UUID(as_uuid=True), nullable=True)
    approved_by = Column(UUID(as_uuid=True), nullable=True)
    
    # Audit
    audit_hash = Column(String(128), nullable=True)
    
    # Extra data
    extra_data = Column(JSON, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class ContractObligation(Base):
    """Obligation in a contract."""
    __tablename__ = "contract_obligations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_id = Column(UUID(as_uuid=True), ForeignKey("agent_contracts.id"), index=True, nullable=False)
    agent_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    
    description = Column(Text, nullable=False)
    deadline = Column(DateTime(timezone=True), nullable=True)
    
    is_completed = Column(Boolean, default=False, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    verification_method = Column(String(32), default="self_report", nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ============== Approvals ==============

class ApprovalRequest(Base):
    """Approval request for GOVERNED mode actions."""
    __tablename__ = "approval_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    
    # Request details
    action = Column(String(64), nullable=False)
    action_type = Column(String(32), nullable=True)
    amount = Column(Float, default=0.0, nullable=False)
    description = Column(Text, nullable=True)
    
    # Context
    context = Column(JSON, nullable=True)
    justification = Column(Text, nullable=True)
    
    # Risk assessment
    risk_level = Column(String(16), default="medium", nullable=False)
    
    # Status
    status = Column(SQLEnum(ApprovalStatusEnum), default=ApprovalStatusEnum.PENDING, nullable=False)
    
    # Decision
    decided_by = Column(UUID(as_uuid=True), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decision_reason = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


# ============== Execution Audit ==============

class ExecutionAuditEntry(Base):
    """Audit entry for agent executions."""
    __tablename__ = "execution_audit_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    
    # Execution details
    action = Column(String(128), nullable=False)
    mode = Column(SQLEnum(AutonomyModeEnum), nullable=False)
    
    # Hashes for integrity
    input_hash = Column(String(128), nullable=True)
    output_hash = Column(String(128), nullable=True)
    
    # Decision
    decision = Column(String(32), nullable=False)  # allowed, blocked, pending_approval
    governance_result = Column(String(32), nullable=False)  # passed, flagged, blocked
    
    # Cost
    cost_incurred = Column(Float, default=0.0, nullable=False)
    risk_level = Column(String(16), nullable=True)
    
    # References
    approval_id = Column(UUID(as_uuid=True), nullable=True)
    parent_entry_id = Column(UUID(as_uuid=True), nullable=True)
    
    # Merkle tree for audit chain
    merkle_root = Column(String(128), nullable=True)
    
    # Extra data
    extra_data = Column(JSON, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
