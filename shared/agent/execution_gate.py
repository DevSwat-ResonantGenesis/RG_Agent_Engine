"""
Execution Gate System
=====================

Central gate that decides if an action can execute based on autonomy mode.

UNBOUNDED mode: All actions allowed, no approval required
GOVERNED mode: Actions checked against limits, may require approval

This is the enforcement layer for the dual-mode autonomy system.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
import logging

from .autonomy_mode import (
    AutonomyMode,
    AutonomyModeManager,
    RiskLevel,
    get_autonomy_mode_manager,
)

logger = logging.getLogger(__name__)


class DecisionType(str, Enum):
    """Types of execution decisions."""
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    PENDING_APPROVAL = "pending_approval"
    RATE_LIMITED = "rate_limited"


@dataclass
class ExecutionRequest:
    """Request to execute an action."""
    id: str
    agent_id: str
    action: str
    action_type: str  # tool_call, api_request, financial, governance, etc.
    risk_level: RiskLevel
    estimated_cost: float = 0.0
    requires_external_api: bool = False
    requires_financial: bool = False
    requires_real_world_effect: bool = False
    target_resource: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class ExecutionDecision:
    """Decision from the execution gate."""
    request_id: str
    decision: DecisionType
    allowed: bool
    requires_approval: bool
    mode: AutonomyMode
    reason: str
    limits_applied: Optional[Dict[str, Any]] = None
    approval_id: Optional[str] = None
    expires_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionRecord:
    """Record of an execution for tracking limits."""
    request_id: str
    agent_id: str
    action: str
    cost: float
    timestamp: str
    mode: AutonomyMode
    decision: DecisionType


class ExecutionGate:
    """
    Central gate that decides if an action can execute based on mode.
    
    Responsibilities:
    - Check action against current mode configuration
    - Enforce spending limits
    - Enforce rate limits
    - Determine if approval is required
    - Track execution history for limit enforcement
    """
    
    def __init__(
        self,
        mode_manager: Optional[AutonomyModeManager] = None,
    ):
        self.mode_manager = mode_manager or get_autonomy_mode_manager()
        
        # Execution tracking for limit enforcement
        self._daily_spend: Dict[str, float] = {}  # agent_id -> daily spend
        self._daily_executions: Dict[str, int] = {}  # agent_id -> count
        self._execution_history: List[ExecutionRecord] = []
        
        # Last reset timestamps
        self._last_daily_reset: Dict[str, str] = {}
    
    def evaluate(self, request: ExecutionRequest) -> ExecutionDecision:
        """
        Evaluate an execution request and return a decision.
        
        This is the main entry point for the execution gate.
        """
        mode = self.mode_manager.get_mode(request.agent_id)
        config = self.mode_manager.get_config(request.agent_id)
        
        logger.debug(
            f"Evaluating request: agent={request.agent_id}, "
            f"action={request.action}, mode={mode.value}"
        )
        
        if mode == AutonomyMode.UNBOUNDED:
            return self._evaluate_unbounded(request, config)
        else:
            return self._evaluate_governed(request, config)
    
    def _evaluate_unbounded(self, request: ExecutionRequest, config) -> ExecutionDecision:
        """
        Evaluate in UNBOUNDED mode - always allow, never require approval.
        """
        # Log for audit (optional in unbounded)
        if config.audit_enabled:
            self._record_execution(request, AutonomyMode.UNBOUNDED, DecisionType.ALLOWED)
        
        return ExecutionDecision(
            request_id=request.id,
            decision=DecisionType.ALLOWED,
            allowed=True,
            requires_approval=False,
            mode=AutonomyMode.UNBOUNDED,
            reason="UNBOUNDED mode - full autonomy granted",
            limits_applied=None,
            metadata={
                "audit_enabled": config.audit_enabled,
                "auto_execute": True,
            }
        )
    
    def _evaluate_governed(self, request: ExecutionRequest, config) -> ExecutionDecision:
        """
        Evaluate in GOVERNED mode - apply limits and approval gates.
        """
        # Reset daily limits if needed
        self._maybe_reset_daily_limits(request.agent_id)
        
        # Check budget limit
        current_daily_spend = self._daily_spend.get(request.agent_id, 0.0)
        if current_daily_spend + request.estimated_cost > config.max_budget_per_day:
            self._record_execution(request, AutonomyMode.GOVERNED, DecisionType.BLOCKED)
            return ExecutionDecision(
                request_id=request.id,
                decision=DecisionType.BLOCKED,
                allowed=False,
                requires_approval=False,
                mode=AutonomyMode.GOVERNED,
                reason=f"Daily budget limit exceeded: ${config.max_budget_per_day}",
                limits_applied={
                    "daily_budget": config.max_budget_per_day,
                    "current_spend": current_daily_spend,
                    "requested": request.estimated_cost,
                }
            )
        
        # Check transaction limit
        if request.requires_financial and request.estimated_cost > config.transaction_limit:
            # Requires approval for large transactions
            self._record_execution(request, AutonomyMode.GOVERNED, DecisionType.PENDING_APPROVAL)
            return ExecutionDecision(
                request_id=request.id,
                decision=DecisionType.PENDING_APPROVAL,
                allowed=True,
                requires_approval=True,
                mode=AutonomyMode.GOVERNED,
                reason=f"Transaction ${request.estimated_cost} exceeds limit ${config.transaction_limit}",
                limits_applied={
                    "transaction_limit": config.transaction_limit,
                    "requested": request.estimated_cost,
                },
                metadata={"approval_reason": "transaction_limit_exceeded"}
            )
        
        # Check risk level
        requires_approval = False
        approval_reason = None
        
        risk_threshold = config.approval_threshold_risk
        risk_order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        
        if risk_order.index(request.risk_level) >= risk_order.index(risk_threshold):
            requires_approval = True
            approval_reason = f"Risk level {request.risk_level.value} >= threshold {risk_threshold.value}"
        
        # Check cost threshold
        if request.estimated_cost > config.approval_threshold_usd:
            requires_approval = True
            approval_reason = f"Cost ${request.estimated_cost} > threshold ${config.approval_threshold_usd}"
        
        # Check real-world effects
        if request.requires_real_world_effect and config.require_approval:
            requires_approval = True
            approval_reason = "Real-world effect requires approval"
        
        # Check self-governance actions
        if request.action_type == "governance":
            if not config.can_modify_own_permissions:
                self._record_execution(request, AutonomyMode.GOVERNED, DecisionType.BLOCKED)
                return ExecutionDecision(
                    request_id=request.id,
                    decision=DecisionType.BLOCKED,
                    allowed=False,
                    requires_approval=False,
                    mode=AutonomyMode.GOVERNED,
                    reason="Self-governance not allowed in GOVERNED mode",
                )
        
        # Record execution
        decision_type = DecisionType.PENDING_APPROVAL if requires_approval else DecisionType.ALLOWED
        self._record_execution(request, AutonomyMode.GOVERNED, decision_type)
        
        # Update spend tracking (if allowed)
        if not requires_approval:
            self._daily_spend[request.agent_id] = current_daily_spend + request.estimated_cost
        
        return ExecutionDecision(
            request_id=request.id,
            decision=decision_type,
            allowed=True,
            requires_approval=requires_approval,
            mode=AutonomyMode.GOVERNED,
            reason=approval_reason or "GOVERNED mode - bounded execution",
            limits_applied={
                "daily_budget": config.max_budget_per_day,
                "transaction_limit": config.transaction_limit,
                "approval_threshold_usd": config.approval_threshold_usd,
                "approval_threshold_risk": config.approval_threshold_risk.value,
            },
            metadata={
                "approval_reason": approval_reason,
                "current_daily_spend": self._daily_spend.get(request.agent_id, 0.0),
            }
        )
    
    def _record_execution(
        self,
        request: ExecutionRequest,
        mode: AutonomyMode,
        decision: DecisionType
    ):
        """Record an execution for tracking."""
        record = ExecutionRecord(
            request_id=request.id,
            agent_id=request.agent_id,
            action=request.action,
            cost=request.estimated_cost,
            timestamp=datetime.utcnow().isoformat(),
            mode=mode,
            decision=decision,
        )
        self._execution_history.append(record)
        
        # Increment daily execution count
        self._daily_executions[request.agent_id] = (
            self._daily_executions.get(request.agent_id, 0) + 1
        )
    
    def _maybe_reset_daily_limits(self, agent_id: str):
        """Reset daily limits if a new day has started."""
        today = datetime.utcnow().date().isoformat()
        last_reset = self._last_daily_reset.get(agent_id)
        
        if last_reset != today:
            self._daily_spend[agent_id] = 0.0
            self._daily_executions[agent_id] = 0
            self._last_daily_reset[agent_id] = today
            logger.debug(f"Reset daily limits for agent {agent_id}")
    
    def get_daily_spend(self, agent_id: str) -> float:
        """Get current daily spend for an agent."""
        self._maybe_reset_daily_limits(agent_id)
        return self._daily_spend.get(agent_id, 0.0)
    
    def get_daily_executions(self, agent_id: str) -> int:
        """Get current daily execution count for an agent."""
        self._maybe_reset_daily_limits(agent_id)
        return self._daily_executions.get(agent_id, 0)
    
    def get_remaining_budget(self, agent_id: str) -> float:
        """Get remaining daily budget for an agent."""
        config = self.mode_manager.get_config(agent_id)
        mode = self.mode_manager.get_mode(agent_id)
        
        if mode == AutonomyMode.UNBOUNDED:
            return float('inf')
        
        current_spend = self.get_daily_spend(agent_id)
        return max(0, config.max_budget_per_day - current_spend)
    
    def get_execution_history(
        self,
        agent_id: Optional[str] = None,
        limit: int = 100
    ) -> List[ExecutionRecord]:
        """Get execution history."""
        history = self._execution_history
        if agent_id:
            history = [r for r in history if r.agent_id == agent_id]
        return history[-limit:]
    
    def can_execute(self, request: ExecutionRequest) -> bool:
        """Quick check if an action can execute (without approval)."""
        decision = self.evaluate(request)
        return decision.allowed and not decision.requires_approval
    
    def mark_approved(self, request_id: str, approver_id: str):
        """Mark a pending request as approved and update spend tracking."""
        # Find the request in history
        for record in self._execution_history:
            if record.request_id == request_id:
                record.decision = DecisionType.ALLOWED
                # Update spend
                self._daily_spend[record.agent_id] = (
                    self._daily_spend.get(record.agent_id, 0.0) + record.cost
                )
                logger.info(f"Request {request_id} approved by {approver_id}")
                return True
        return False
    
    def mark_rejected(self, request_id: str, rejector_id: str, reason: str):
        """Mark a pending request as rejected."""
        for record in self._execution_history:
            if record.request_id == request_id:
                record.decision = DecisionType.BLOCKED
                logger.info(f"Request {request_id} rejected by {rejector_id}: {reason}")
                return True
        return False


# Global instance
execution_gate = ExecutionGate()


def get_execution_gate() -> ExecutionGate:
    """Get the global execution gate."""
    return execution_gate
