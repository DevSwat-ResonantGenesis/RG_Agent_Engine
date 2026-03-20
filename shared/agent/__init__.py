"""Agent Engine production components for autonomous operation.

Includes dual-mode autonomy system:
- UNBOUNDED: Full autonomy, no restrictions (research/internal)
- GOVERNED: Bounded autonomy, enterprise-safe (production/sellable)
"""

from .concurrency import ConcurrencyManager, TaskGraph, TaskNode
from .scheduler import DeterministicScheduler, SchedulerConfig, TaskPriority
from .sandbox import SandboxBoundary, SandboxConfig, ToolPermission
from .delegation import AgentDelegator, agent_delegator, DelegationRequest, DelegationResponse, AgentRole

# Dual-Mode Autonomy System
from .autonomy_mode import (
    AutonomyMode,
    AutonomyModeManager,
    UnboundedModeConfig,
    GovernedModeConfig,
    RiskLevel,
    get_autonomy_mode_manager,
    autonomy_mode_manager,
)
from .execution_gate import (
    ExecutionGate,
    ExecutionRequest,
    ExecutionDecision,
    DecisionType,
    get_execution_gate,
    execution_gate,
)
from .goal_generation import (
    GoalGenerationEngine,
    Goal,
    GoalType,
    GoalStatus,
    GoalPriority,
    get_goal_generation_engine,
    goal_generation_engine,
)
from .wallet import (
    AgentWalletSystem,
    AgentWallet,
    WalletTransaction,
    TransactionType,
    TransactionStatus,
    SpendRequest,
    SpendResult,
    get_agent_wallet_system,
    agent_wallet_system,
)
from .negotiation import (
    NegotiationEngine,
    Negotiation,
    NegotiationType,
    NegotiationStatus,
    Bid,
    AgentContract,
    ContractStatus,
    get_negotiation_engine,
    negotiation_engine,
)

__all__ = [
    # Existing
    "ConcurrencyManager",
    "TaskGraph",
    "TaskNode",
    "DeterministicScheduler",
    "SchedulerConfig",
    "TaskPriority",
    "SandboxBoundary",
    "SandboxConfig",
    "ToolPermission",
    "AgentDelegator",
    "agent_delegator",
    "DelegationRequest",
    "DelegationResponse",
    "AgentRole",
    # Autonomy Mode
    "AutonomyMode",
    "AutonomyModeManager",
    "UnboundedModeConfig",
    "GovernedModeConfig",
    "RiskLevel",
    "get_autonomy_mode_manager",
    "autonomy_mode_manager",
    # Execution Gate
    "ExecutionGate",
    "ExecutionRequest",
    "ExecutionDecision",
    "DecisionType",
    "get_execution_gate",
    "execution_gate",
    # Goal Generation
    "GoalGenerationEngine",
    "Goal",
    "GoalType",
    "GoalStatus",
    "GoalPriority",
    "get_goal_generation_engine",
    "goal_generation_engine",
    # Wallet
    "AgentWalletSystem",
    "AgentWallet",
    "WalletTransaction",
    "TransactionType",
    "TransactionStatus",
    "SpendRequest",
    "SpendResult",
    "get_agent_wallet_system",
    "agent_wallet_system",
    # Negotiation
    "NegotiationEngine",
    "Negotiation",
    "NegotiationType",
    "NegotiationStatus",
    "Bid",
    "AgentContract",
    "ContractStatus",
    "get_negotiation_engine",
    "negotiation_engine",
]
