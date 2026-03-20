"""
Dual-Mode Autonomy System
=========================

Provides runtime switching between:
- UNBOUNDED: Full autonomy, no restrictions (research/internal)
- GOVERNED: Bounded autonomy, enterprise-safe (production/sellable)

This is the core infrastructure for the dual-mode autonomy system.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class AutonomyMode(str, Enum):
    """The two autonomy modes available in the system."""
    UNBOUNDED = "unbounded"  # Full autonomy, no restrictions
    GOVERNED = "governed"    # Bounded autonomy, enterprise-safe


class RiskLevel(str, Enum):
    """Risk levels for actions."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class UnboundedModeConfig:
    """
    Configuration for UNBOUNDED mode - maximum autonomy.
    
    Use for:
    - Internal R&D
    - Research experiments
    - Performance testing
    - Capability exploration
    
    WARNING: No safety limits. Use only in controlled environments.
    """
    # Resource limits (effectively unlimited)
    max_concurrent_tasks: int = 10000
    max_tokens_per_request: int = 10_000_000
    max_budget_per_day: float = float('inf')
    max_budget_per_month: float = float('inf')
    
    # Self-governance capabilities
    can_modify_own_permissions: bool = True
    can_create_sub_agents: bool = True
    can_set_own_goals: bool = True
    can_modify_governance: bool = True
    can_expand_scope: bool = True
    can_grant_permissions_to_others: bool = True
    
    # Execution autonomy
    require_approval: bool = False
    auto_execute_all: bool = True
    allow_external_apis: bool = True
    allow_financial_transactions: bool = True
    allow_real_world_effects: bool = True
    
    # Wallet limits (unlimited)
    wallet_limit: float = float('inf')
    transaction_limit: float = float('inf')
    daily_transaction_limit: float = float('inf')
    
    # Audit (optional in unbounded)
    audit_enabled: bool = False
    audit_level: str = "minimal"  # minimal, standard, full
    
    # Negotiation
    can_negotiate_autonomously: bool = True
    can_create_binding_contracts: bool = True
    contract_value_limit: float = float('inf')


@dataclass
class GovernedModeConfig:
    """
    Configuration for GOVERNED mode - bounded, enterprise-safe.
    
    Use for:
    - Production deployments
    - Enterprise customers
    - Regulated environments
    - Public-facing agents
    
    All actions are bounded, audited, and may require approval.
    """
    # Resource limits (hard caps)
    max_concurrent_tasks: int = 10
    max_tokens_per_request: int = 100_000
    max_budget_per_day: float = 100.0  # USD
    max_budget_per_month: float = 2000.0  # USD
    
    # No self-governance
    can_modify_own_permissions: bool = False
    can_create_sub_agents: bool = True  # But requires approval
    can_set_own_goals: bool = False  # Goals are assigned
    can_modify_governance: bool = False
    can_expand_scope: bool = False
    can_grant_permissions_to_others: bool = False
    
    # Execution with gates
    require_approval: bool = True
    auto_execute_all: bool = False
    allow_external_apis: bool = True  # With rate limits
    allow_financial_transactions: bool = True  # With limits
    allow_real_world_effects: bool = True  # With approval
    
    # Wallet limits (bounded)
    wallet_limit: float = 1000.0  # USD max balance
    transaction_limit: float = 100.0  # USD per transaction
    daily_transaction_limit: float = 500.0  # USD per day
    
    # Approval thresholds
    approval_threshold_usd: float = 50.0
    approval_threshold_risk: RiskLevel = RiskLevel.MEDIUM
    approval_timeout_seconds: int = 3600  # 1 hour
    
    # Audit (required in governed)
    audit_enabled: bool = True
    audit_level: str = "full"  # minimal, standard, full
    
    # Negotiation
    can_negotiate_autonomously: bool = True
    can_create_binding_contracts: bool = True  # With approval
    contract_value_limit: float = 500.0  # USD


@dataclass
class ModeTransition:
    """Record of a mode transition."""
    id: str
    agent_id: str
    from_mode: AutonomyMode
    to_mode: AutonomyMode
    initiated_by: str  # user_id or system
    reason: str
    timestamp: str
    approved_by: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class AutonomyModeManager:
    """
    Manages the current autonomy mode for agents.
    
    Handles:
    - Mode switching with access control
    - Configuration retrieval
    - Mode transition logging
    - Permission checks
    """
    
    def __init__(
        self,
        default_mode: AutonomyMode = AutonomyMode.GOVERNED,
        unbounded_config: Optional[UnboundedModeConfig] = None,
        governed_config: Optional[GovernedModeConfig] = None,
    ):
        self.default_mode = default_mode
        self.unbounded_config = unbounded_config or UnboundedModeConfig()
        self.governed_config = governed_config or GovernedModeConfig()
        
        # Agent mode state
        self._agent_modes: Dict[str, AutonomyMode] = {}
        
        # Access control lists
        self.allowed_unbounded_users: List[str] = []
        self.allowed_unbounded_agents: List[str] = []
        self.allowed_unbounded_roles: List[str] = ["admin", "researcher", "internal"]
        
        # Transition history
        self._transitions: List[ModeTransition] = []
    
    def get_mode(self, agent_id: str) -> AutonomyMode:
        """Get the current mode for an agent."""
        return self._agent_modes.get(agent_id, self.default_mode)
    
    def get_config(self, agent_id: str):
        """Get the configuration for an agent's current mode."""
        mode = self.get_mode(agent_id)
        if mode == AutonomyMode.UNBOUNDED:
            return self.unbounded_config
        return self.governed_config
    
    def get_config_for_mode(self, mode: AutonomyMode):
        """Get configuration for a specific mode."""
        if mode == AutonomyMode.UNBOUNDED:
            return self.unbounded_config
        return self.governed_config
    
    def can_switch_to_unbounded(
        self,
        user_id: str,
        agent_id: str,
        user_role: Optional[str] = None
    ) -> bool:
        """Check if a user can switch an agent to UNBOUNDED mode."""
        # Check user allowlist
        if user_id in self.allowed_unbounded_users:
            return True
        
        # Check agent allowlist
        if agent_id in self.allowed_unbounded_agents:
            return True
        
        # Check role allowlist
        if user_role and user_role in self.allowed_unbounded_roles:
            return True
        
        return False
    
    def switch_mode(
        self,
        agent_id: str,
        new_mode: AutonomyMode,
        user_id: str,
        user_role: Optional[str] = None,
        reason: str = "",
    ) -> tuple[bool, str]:
        """
        Switch an agent's autonomy mode.
        
        Returns:
            (success, message)
        """
        current_mode = self.get_mode(agent_id)
        
        # No change needed
        if current_mode == new_mode:
            return True, f"Agent already in {new_mode.value} mode"
        
        # Check permission for UNBOUNDED
        if new_mode == AutonomyMode.UNBOUNDED:
            if not self.can_switch_to_unbounded(user_id, agent_id, user_role):
                logger.warning(
                    f"Unauthorized UNBOUNDED mode switch attempt: "
                    f"user={user_id}, agent={agent_id}"
                )
                return False, "Unauthorized: Cannot switch to UNBOUNDED mode"
        
        # Record transition
        transition = ModeTransition(
            id=f"trans_{agent_id}_{datetime.utcnow().timestamp()}",
            agent_id=agent_id,
            from_mode=current_mode,
            to_mode=new_mode,
            initiated_by=user_id,
            reason=reason,
            timestamp=datetime.utcnow().isoformat(),
            approved_by=user_id if new_mode == AutonomyMode.UNBOUNDED else None,
        )
        self._transitions.append(transition)
        
        # Apply mode change
        self._agent_modes[agent_id] = new_mode
        
        logger.info(
            f"Mode switched: agent={agent_id}, "
            f"{current_mode.value} -> {new_mode.value}, "
            f"by={user_id}"
        )
        
        return True, f"Switched to {new_mode.value} mode"
    
    def get_transitions(
        self,
        agent_id: Optional[str] = None,
        limit: int = 100
    ) -> List[ModeTransition]:
        """Get mode transition history."""
        transitions = self._transitions
        if agent_id:
            transitions = [t for t in transitions if t.agent_id == agent_id]
        return transitions[-limit:]
    
    def add_unbounded_user(self, user_id: str):
        """Add a user to the UNBOUNDED allowlist."""
        if user_id not in self.allowed_unbounded_users:
            self.allowed_unbounded_users.append(user_id)
    
    def remove_unbounded_user(self, user_id: str):
        """Remove a user from the UNBOUNDED allowlist."""
        if user_id in self.allowed_unbounded_users:
            self.allowed_unbounded_users.remove(user_id)
    
    def add_unbounded_agent(self, agent_id: str):
        """Add an agent to the UNBOUNDED allowlist."""
        if agent_id not in self.allowed_unbounded_agents:
            self.allowed_unbounded_agents.append(agent_id)
    
    def remove_unbounded_agent(self, agent_id: str):
        """Remove an agent from the UNBOUNDED allowlist."""
        if agent_id in self.allowed_unbounded_agents:
            self.allowed_unbounded_agents.remove(agent_id)
    
    def is_unbounded(self, agent_id: str) -> bool:
        """Check if an agent is in UNBOUNDED mode."""
        return self.get_mode(agent_id) == AutonomyMode.UNBOUNDED
    
    def is_governed(self, agent_id: str) -> bool:
        """Check if an agent is in GOVERNED mode."""
        return self.get_mode(agent_id) == AutonomyMode.GOVERNED
    
    def get_all_agent_modes(self) -> Dict[str, str]:
        """Get all agent modes."""
        return {
            agent_id: mode.value 
            for agent_id, mode in self._agent_modes.items()
        }
    
    def reset_to_governed(self, agent_id: str, user_id: str, reason: str = ""):
        """Force reset an agent to GOVERNED mode (emergency)."""
        return self.switch_mode(
            agent_id=agent_id,
            new_mode=AutonomyMode.GOVERNED,
            user_id=user_id,
            reason=reason or "Emergency reset to GOVERNED mode"
        )


# Global instance
autonomy_mode_manager = AutonomyModeManager()


def get_autonomy_mode_manager() -> AutonomyModeManager:
    """Get the global autonomy mode manager."""
    return autonomy_mode_manager
