"""
Project Builder Module
======================

Autonomous Project Builder Agent that creates complete fullstack projects.

Integrates:
- Code Visualizer (port 8092) - Code validation, broken import detection
- State Physics (port 8091) - Economic tracking, trust dynamics
- RARA (port 8093) - Safety governance, snapshots, rollback

Components:
- workspace_manager: User workspace isolation
- code_validator: Code Visualizer integration
- state_tracker: State Physics integration
- rara_governance: RARA integration
- template_engine: Project scaffolding
- builder_agent: Main Project Builder Agent
- delivery_manager: Final packaging
"""

from .workspace_manager import (
    WorkspaceManager, 
    UserWorkspace, 
    get_workspace_manager,
    WorkspaceStatus,
    ProjectMetadata,
    ProjectState,
    StateTransition,
)
from .code_validator import (
    CodeValidator, 
    ValidationResult, 
    get_code_validator,
    ValidationStatus,
)
from .state_tracker import (
    StateTracker, 
    AgentState, 
    get_state_tracker,
    AgentStatus,
)
from .rara_governance import (
    RARAGovernance, 
    MutationResult, 
    get_rara_governance,
    MutationType,
)
from .template_engine import (
    TemplateEngine, 
    ProjectTemplate, 
    get_template_engine,
    ProjectType,
    FileTemplate,
)
from .builder_agent import (
    ProjectBuilderAgent, 
    BuildResult, 
    get_builder_agent,
    BuildProgress,
    BuildPhase,
    BuildGoal,
)
from .delivery_manager import (
    DeliveryManager, 
    DeliveryPackage, 
    get_delivery_manager,
)

__all__ = [
    # Workspace Manager
    "WorkspaceManager",
    "UserWorkspace",
    "get_workspace_manager",
    "WorkspaceStatus",
    "ProjectMetadata",
    "ProjectState",
    "StateTransition",
    # Code Validator
    "CodeValidator",
    "ValidationResult",
    "get_code_validator",
    "ValidationStatus",
    # State Tracker
    "StateTracker",
    "AgentState",
    "get_state_tracker",
    "AgentStatus",
    # RARA Governance
    "RARAGovernance",
    "MutationResult",
    "get_rara_governance",
    "MutationType",
    # Template Engine
    "TemplateEngine",
    "ProjectTemplate",
    "get_template_engine",
    "ProjectType",
    "FileTemplate",
    # Builder Agent
    "ProjectBuilderAgent",
    "BuildResult",
    "get_builder_agent",
    "BuildProgress",
    "BuildPhase",
    "BuildGoal",
    # Delivery Manager
    "DeliveryManager",
    "DeliveryPackage",
    "get_delivery_manager",
]
