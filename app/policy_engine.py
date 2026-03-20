import sys
from pathlib import Path

# Add shared modules to path
SHARED_PATH = Path(__file__).resolve().parents[2] / "shared"
if str(SHARED_PATH) not in sys.path:
    sys.path.insert(0, str(SHARED_PATH))

# Add service root to path
SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

"""
Policy Decision Engine - Unified decision-making for agent autonomy.

This is the core component that converts automation into controlled autonomy.
It unifies:
- Safety rules and threat detection
- Approval workflows
- Cost/budget limits  
- Autonomy mode settings
- Risk assessment

Output: EXECUTE | REQUIRE_APPROVAL | PAUSE | ABORT | REPLAN
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AgentDefinition, AgentSession, AgentStep
from .config import settings

logger = logging.getLogger(__name__)


class PolicyDecision(str, Enum):
    """Possible decisions from the policy engine."""
    EXECUTE = "execute"  # Proceed with action
    REQUIRE_APPROVAL = "require_approval"  # Need human approval
    PAUSE = "pause"  # Pause and wait for conditions
    ABORT = "abort"  # Stop execution entirely
    REPLAN = "replan"  # Go back and create new plan


class RiskLevel(str, Enum):
    """Risk classification for actions."""
    CRITICAL = "critical"  # Immediate abort
    HIGH = "high"  # Require approval
    MEDIUM = "medium"  # Log and monitor
    LOW = "low"  # Proceed normally
    MINIMAL = "minimal"  # No concerns


class AutonomyMode(str, Enum):
    """Agent autonomy levels."""
    FULL = "full"  # Agent decides everything
    SUPERVISED = "supervised"  # Human approves high-risk only
    GOVERNED = "governed"  # Human approves all external actions
    RESTRICTED = "restricted"  # Human approves everything
    LOCKED = "locked"  # No autonomous actions allowed


@dataclass
class PolicyContext:
    """Context for policy evaluation."""
    agent: AgentDefinition
    session: AgentSession
    action_type: str
    action_data: Dict[str, Any]
    step_count: int = 0
    total_cost: float = 0.0
    elapsed_seconds: float = 0.0
    previous_decisions: List[str] = field(default_factory=list)


@dataclass
class PolicyResult:
    """Result from policy evaluation."""
    decision: PolicyDecision
    risk_level: RiskLevel
    reasons: List[str]
    recommendations: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def should_proceed(self) -> bool:
        return self.decision == PolicyDecision.EXECUTE
    
    @property
    def needs_human(self) -> bool:
        return self.decision == PolicyDecision.REQUIRE_APPROVAL


class CostPolicy:
    """Policy for cost/budget management."""
    
    def __init__(
        self,
        max_cost_per_session: float = 10.0,
        max_cost_per_step: float = 1.0,
        warn_threshold: float = 0.8,
    ):
        self.max_cost_per_session = max_cost_per_session
        self.max_cost_per_step = max_cost_per_step
        self.warn_threshold = warn_threshold
    
    def evaluate(self, ctx: PolicyContext, estimated_cost: float = 0.0) -> Tuple[bool, str]:
        """Check if cost is within budget."""
        # Check session total
        if ctx.total_cost >= self.max_cost_per_session:
            return False, f"Session budget exceeded (${ctx.total_cost:.2f} >= ${self.max_cost_per_session:.2f})"
        
        # Check step cost
        if estimated_cost > self.max_cost_per_step:
            return False, f"Step cost too high (${estimated_cost:.2f} > ${self.max_cost_per_step:.2f})"
        
        # Warning threshold
        if ctx.total_cost >= self.max_cost_per_session * self.warn_threshold:
            return True, f"Warning: Approaching budget limit (${ctx.total_cost:.2f}/${self.max_cost_per_session:.2f})"
        
        return True, ""


class TimePolicy:
    """Policy for time/duration limits."""
    
    def __init__(
        self,
        max_session_seconds: int = 3600,
        max_step_seconds: int = 300,
        idle_timeout_seconds: int = 60,
    ):
        self.max_session_seconds = max_session_seconds
        self.max_step_seconds = max_step_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
    
    def evaluate(self, ctx: PolicyContext) -> Tuple[bool, str]:
        """Check if within time limits."""
        if ctx.elapsed_seconds >= self.max_session_seconds:
            return False, f"Session timeout ({ctx.elapsed_seconds:.0f}s >= {self.max_session_seconds}s)"
        
        return True, ""


class LoopPolicy:
    """Policy for detecting and preventing loops."""
    
    def __init__(
        self,
        max_iterations: int = 50,
        max_similar_actions: int = 5,
        similarity_threshold: float = 0.9,
    ):
        self.max_iterations = max_iterations
        self.max_similar_actions = max_similar_actions
        self.similarity_threshold = similarity_threshold
        self._action_history: List[str] = []
    
    def evaluate(self, ctx: PolicyContext) -> Tuple[bool, str]:
        """Check for loop conditions."""
        if ctx.step_count >= self.max_iterations:
            return False, f"Max iterations exceeded ({ctx.step_count} >= {self.max_iterations})"
        
        # Check for repeated actions
        action_key = f"{ctx.action_type}:{hash(str(ctx.action_data))}"
        self._action_history.append(action_key)
        
        # Count similar recent actions
        recent = self._action_history[-10:]
        if recent.count(action_key) >= self.max_similar_actions:
            return False, f"Loop detected: same action repeated {self.max_similar_actions} times"
        
        return True, ""
    
    def reset(self):
        """Reset loop tracking."""
        self._action_history.clear()


class AutonomyPolicy:
    """Policy based on autonomy mode settings."""
    
    # Actions that always require approval regardless of mode
    ALWAYS_APPROVE = {
        "delete_file", "delete_database", "send_email", "make_payment",
        "modify_permissions", "create_user", "external_api_call",
    }
    
    # Actions safe in all modes
    ALWAYS_SAFE = {
        "read_file", "search", "calculate", "format_text", "think",
    }
    
    def evaluate(self, ctx: PolicyContext, mode: AutonomyMode) -> Tuple[PolicyDecision, str]:
        """Evaluate action against autonomy mode."""
        action = ctx.action_type.lower()
        
        # Locked mode - nothing autonomous
        if mode == AutonomyMode.LOCKED:
            return PolicyDecision.REQUIRE_APPROVAL, "Locked mode: all actions require approval"
        
        # Always require approval actions
        if action in self.ALWAYS_APPROVE:
            return PolicyDecision.REQUIRE_APPROVAL, f"Action '{action}' always requires approval"
        
        # Always safe actions
        if action in self.ALWAYS_SAFE:
            return PolicyDecision.EXECUTE, ""
        
        # Mode-based decisions
        if mode == AutonomyMode.FULL:
            return PolicyDecision.EXECUTE, ""
        
        if mode == AutonomyMode.SUPERVISED:
            # Only high-risk needs approval
            if self._is_high_risk(ctx):
                return PolicyDecision.REQUIRE_APPROVAL, "High-risk action in supervised mode"
            return PolicyDecision.EXECUTE, ""
        
        if mode == AutonomyMode.GOVERNED:
            # External actions need approval
            if self._is_external(ctx):
                return PolicyDecision.REQUIRE_APPROVAL, "External action in governed mode"
            return PolicyDecision.EXECUTE, ""
        
        if mode == AutonomyMode.RESTRICTED:
            return PolicyDecision.REQUIRE_APPROVAL, "Restricted mode: approval required"
        
        return PolicyDecision.EXECUTE, ""
    
    def _is_high_risk(self, ctx: PolicyContext) -> bool:
        """Check if action is high risk."""
        high_risk_keywords = ["delete", "modify", "update", "create", "execute", "run"]
        return any(kw in ctx.action_type.lower() for kw in high_risk_keywords)
    
    def _is_external(self, ctx: PolicyContext) -> bool:
        """Check if action involves external systems."""
        external_keywords = ["api", "http", "email", "webhook", "external", "network"]
        action_str = f"{ctx.action_type} {ctx.action_data}"
        return any(kw in action_str.lower() for kw in external_keywords)


class PolicyEngine:
    """
    Unified Policy Decision Engine.
    
    Combines all policy checks into a single decision point.
    This is the brain that converts automation into controlled autonomy.
    """
    
    def __init__(
        self,
        cost_policy: Optional[CostPolicy] = None,
        time_policy: Optional[TimePolicy] = None,
        loop_policy: Optional[LoopPolicy] = None,
        autonomy_policy: Optional[AutonomyPolicy] = None,
    ):
        self.cost_policy = cost_policy or CostPolicy()
        self.time_policy = time_policy or TimePolicy()
        self.loop_policy = loop_policy or LoopPolicy()
        self.autonomy_policy = autonomy_policy or AutonomyPolicy()
        
        self._decision_history: List[PolicyResult] = []
    
    async def evaluate(
        self,
        ctx: PolicyContext,
        autonomy_mode: AutonomyMode = AutonomyMode.SUPERVISED,
        estimated_cost: float = 0.0,
        safety_violations: Optional[List[str]] = None,
    ) -> PolicyResult:
        """
        Evaluate all policies and return unified decision.
        
        This is the main entry point for policy decisions.
        """
        reasons: List[str] = []
        recommendations: List[str] = []
        risk_level = RiskLevel.MINIMAL
        decision = PolicyDecision.EXECUTE
        
        # 1. Check safety violations first (highest priority)
        if safety_violations:
            critical = [v for v in safety_violations if "CRITICAL" in v.upper()]
            high = [v for v in safety_violations if "HIGH" in v.upper()]
            
            if critical:
                decision = PolicyDecision.ABORT
                risk_level = RiskLevel.CRITICAL
                reasons.extend(critical)
                recommendations.append("Action blocked due to critical safety violation")
            elif high:
                decision = PolicyDecision.ABORT
                risk_level = RiskLevel.HIGH
                reasons.extend(high)
                recommendations.append("Action blocked due to high-risk safety violation")
            else:
                risk_level = RiskLevel.MEDIUM
                reasons.extend(safety_violations)
                recommendations.append("Review safety warnings before proceeding")
        
        # 2. Check cost policy
        cost_ok, cost_msg = self.cost_policy.evaluate(ctx, estimated_cost)
        if not cost_ok:
            decision = PolicyDecision.PAUSE
            risk_level = max(risk_level, RiskLevel.MEDIUM, key=lambda x: list(RiskLevel).index(x))
            reasons.append(cost_msg)
            recommendations.append("Consider increasing budget or optimizing costs")
        elif cost_msg:
            reasons.append(cost_msg)
        
        # 3. Check time policy
        time_ok, time_msg = self.time_policy.evaluate(ctx)
        if not time_ok:
            decision = PolicyDecision.ABORT
            risk_level = max(risk_level, RiskLevel.MEDIUM, key=lambda x: list(RiskLevel).index(x))
            reasons.append(time_msg)
            recommendations.append("Session has exceeded time limits")
        
        # 4. Check loop policy
        loop_ok, loop_msg = self.loop_policy.evaluate(ctx)
        if not loop_ok:
            decision = PolicyDecision.REPLAN
            risk_level = max(risk_level, RiskLevel.MEDIUM, key=lambda x: list(RiskLevel).index(x))
            reasons.append(loop_msg)
            recommendations.append("Consider replanning with different approach")
        
        # 5. Check autonomy policy (only if not already blocked)
        if decision == PolicyDecision.EXECUTE:
            autonomy_decision, autonomy_msg = self.autonomy_policy.evaluate(ctx, autonomy_mode)
            if autonomy_decision != PolicyDecision.EXECUTE:
                decision = autonomy_decision
                if autonomy_msg:
                    reasons.append(autonomy_msg)
        
        # Build result
        result = PolicyResult(
            decision=decision,
            risk_level=risk_level,
            reasons=reasons,
            recommendations=recommendations,
            metadata={
                "autonomy_mode": autonomy_mode.value,
                "step_count": ctx.step_count,
                "total_cost": ctx.total_cost,
                "elapsed_seconds": ctx.elapsed_seconds,
                "action_type": ctx.action_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        
        # Record decision
        self._decision_history.append(result)
        
        # Log significant decisions
        if decision != PolicyDecision.EXECUTE:
            logger.info(
                f"Policy decision: {decision.value} for {ctx.action_type}. "
                f"Risk: {risk_level.value}. Reasons: {reasons}"
            )
        
        return result
    
    def get_decision_summary(self) -> Dict[str, Any]:
        """Get summary of recent decisions."""
        if not self._decision_history:
            return {"total": 0, "by_decision": {}, "by_risk": {}}
        
        by_decision = {}
        by_risk = {}
        
        for result in self._decision_history[-100:]:  # Last 100 decisions
            d = result.decision.value
            r = result.risk_level.value
            by_decision[d] = by_decision.get(d, 0) + 1
            by_risk[r] = by_risk.get(r, 0) + 1
        
        return {
            "total": len(self._decision_history),
            "by_decision": by_decision,
            "by_risk": by_risk,
        }
    
    def reset(self):
        """Reset all policy states."""
        self.loop_policy.reset()
        self._decision_history.clear()


# Singleton instance
_policy_engine: Optional[PolicyEngine] = None


def get_policy_engine() -> PolicyEngine:
    """Get the singleton policy engine instance."""
    global _policy_engine
    if _policy_engine is None:
        _policy_engine = PolicyEngine()
    return _policy_engine


def init_policy_engine(
    max_cost_per_session: float = 10.0,
    max_session_seconds: int = 3600,
    max_iterations: int = 50,
) -> PolicyEngine:
    """Initialize policy engine with custom settings."""
    global _policy_engine
    _policy_engine = PolicyEngine(
        cost_policy=CostPolicy(max_cost_per_session=max_cost_per_session),
        time_policy=TimePolicy(max_session_seconds=max_session_seconds),
        loop_policy=LoopPolicy(max_iterations=max_iterations),
        autonomy_policy=AutonomyPolicy(),
    )
    return _policy_engine
