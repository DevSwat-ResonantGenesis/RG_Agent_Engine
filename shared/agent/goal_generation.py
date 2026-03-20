"""
Goal Generation Engine
======================

UNBOUNDED MODE: Agents can generate their own goals autonomously.
GOVERNED MODE: Agents can decompose assigned goals into sub-goals.

This enables agents to:
- Generate goals from context (UNBOUNDED only)
- Decompose high-level goals into actionable sub-goals
- Prioritize goals autonomously
- Track goal progress and completion
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime
import uuid
import logging

from .autonomy_mode import (
    AutonomyMode,
    AutonomyModeManager,
    get_autonomy_mode_manager,
)

logger = logging.getLogger(__name__)


class GoalType(str, Enum):
    """Types of goals based on origin."""
    ASSIGNED = "assigned"          # Human-assigned goal
    DERIVED = "derived"            # Derived from higher goal
    SELF_GENERATED = "self_generated"  # Agent created (UNBOUNDED only)
    EMERGENT = "emergent"          # Emerged from context (UNBOUNDED only)


class GoalStatus(str, Enum):
    """Status of a goal."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"
    BLOCKED = "blocked"


class GoalPriority(int, Enum):
    """Priority levels for goals."""
    CRITICAL = 1
    HIGH = 3
    MEDIUM = 5
    LOW = 7
    BACKGROUND = 9


@dataclass
class Goal:
    """A goal that an agent is working towards."""
    id: str
    agent_id: str
    description: str
    goal_type: GoalType
    priority: GoalPriority = GoalPriority.MEDIUM
    status: GoalStatus = GoalStatus.PENDING
    
    # Hierarchy
    parent_goal_id: Optional[str] = None
    sub_goal_ids: List[str] = field(default_factory=list)
    
    # Success criteria
    success_criteria: List[str] = field(default_factory=list)
    completion_percentage: float = 0.0
    
    # Timing
    deadline: Optional[str] = None
    estimated_effort_hours: float = 0.0
    actual_effort_hours: float = 0.0
    
    # Metadata
    created_by: str = ""  # agent_id or user_id
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None
    
    # Context
    context: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    
    # Dependencies
    depends_on: List[str] = field(default_factory=list)  # goal_ids
    blocks: List[str] = field(default_factory=list)  # goal_ids


@dataclass
class GoalGenerationRequest:
    """Request to generate goals."""
    agent_id: str
    context: Dict[str, Any]
    max_goals: int = 5
    focus_areas: List[str] = field(default_factory=list)
    constraints: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GoalDecompositionRequest:
    """Request to decompose a goal."""
    goal_id: str
    agent_id: str
    max_sub_goals: int = 5
    depth: int = 1  # How many levels deep to decompose


class GoalGenerationEngine:
    """
    Engine for goal generation and decomposition.
    
    UNBOUNDED MODE:
    - Full goal generation from context
    - Autonomous prioritization
    - Self-directed goal setting
    
    GOVERNED MODE:
    - Goal decomposition only
    - Prioritization suggestions (human approves)
    - Cannot create top-level goals
    """
    
    def __init__(
        self,
        mode_manager: Optional[AutonomyModeManager] = None,
        llm_client: Optional[Any] = None,
    ):
        self.mode_manager = mode_manager or get_autonomy_mode_manager()
        self.llm_client = llm_client
        
        # Goal storage
        self._goals: Dict[str, Goal] = {}
        self._agent_goals: Dict[str, List[str]] = {}  # agent_id -> goal_ids
    
    async def generate_goals(
        self,
        request: GoalGenerationRequest
    ) -> List[Goal]:
        """
        Generate goals from context.
        
        UNBOUNDED MODE ONLY: Agents can generate their own goals.
        GOVERNED MODE: Raises PermissionError.
        """
        mode = self.mode_manager.get_mode(request.agent_id)
        config = self.mode_manager.get_config(request.agent_id)
        
        if mode == AutonomyMode.GOVERNED:
            if not config.can_set_own_goals:
                raise PermissionError(
                    "Goal generation not allowed in GOVERNED mode. "
                    "Use decompose_goal() for assigned goals."
                )
        
        logger.info(f"Generating goals for agent {request.agent_id} in {mode.value} mode")
        
        # Build goal generation prompt
        prompt = self._build_goal_generation_prompt(request)
        
        # Call LLM to generate goals
        if self.llm_client:
            generated = await self._call_llm_for_goals(prompt, request.max_goals)
        else:
            # Mock generation for testing
            generated = self._mock_generate_goals(request)
        
        # Create Goal objects
        goals = []
        for goal_data in generated:
            goal = Goal(
                id=str(uuid.uuid4()),
                agent_id=request.agent_id,
                description=goal_data.get("description", ""),
                goal_type=GoalType.SELF_GENERATED,
                priority=GoalPriority(goal_data.get("priority", 5)),
                success_criteria=goal_data.get("success_criteria", []),
                estimated_effort_hours=goal_data.get("estimated_hours", 1.0),
                created_by=request.agent_id,
                context=request.context,
                tags=goal_data.get("tags", []),
            )
            
            self._goals[goal.id] = goal
            if request.agent_id not in self._agent_goals:
                self._agent_goals[request.agent_id] = []
            self._agent_goals[request.agent_id].append(goal.id)
            
            goals.append(goal)
        
        logger.info(f"Generated {len(goals)} goals for agent {request.agent_id}")
        return goals
    
    async def decompose_goal(
        self,
        request: GoalDecompositionRequest
    ) -> List[Goal]:
        """
        Decompose a goal into sub-goals.
        
        Available in both UNBOUNDED and GOVERNED modes.
        """
        parent_goal = self._goals.get(request.goal_id)
        if not parent_goal:
            raise ValueError(f"Goal {request.goal_id} not found")
        
        if parent_goal.agent_id != request.agent_id:
            raise PermissionError("Cannot decompose another agent's goal")
        
        logger.info(f"Decomposing goal {request.goal_id} for agent {request.agent_id}")
        
        # Build decomposition prompt
        prompt = self._build_decomposition_prompt(parent_goal, request)
        
        # Call LLM to decompose
        if self.llm_client:
            sub_goal_data = await self._call_llm_for_decomposition(prompt, request.max_sub_goals)
        else:
            # Mock decomposition for testing
            sub_goal_data = self._mock_decompose_goal(parent_goal, request)
        
        # Create sub-goal objects
        sub_goals = []
        for data in sub_goal_data:
            sub_goal = Goal(
                id=str(uuid.uuid4()),
                agent_id=request.agent_id,
                description=data.get("description", ""),
                goal_type=GoalType.DERIVED,
                priority=parent_goal.priority,
                parent_goal_id=parent_goal.id,
                success_criteria=data.get("success_criteria", []),
                estimated_effort_hours=data.get("estimated_hours", 0.5),
                created_by=request.agent_id,
                context=parent_goal.context,
                tags=parent_goal.tags + data.get("tags", []),
            )
            
            self._goals[sub_goal.id] = sub_goal
            parent_goal.sub_goal_ids.append(sub_goal.id)
            
            if request.agent_id not in self._agent_goals:
                self._agent_goals[request.agent_id] = []
            self._agent_goals[request.agent_id].append(sub_goal.id)
            
            sub_goals.append(sub_goal)
        
        # Recursive decomposition if depth > 1
        if request.depth > 1:
            for sub_goal in sub_goals:
                nested_request = GoalDecompositionRequest(
                    goal_id=sub_goal.id,
                    agent_id=request.agent_id,
                    max_sub_goals=request.max_sub_goals,
                    depth=request.depth - 1,
                )
                await self.decompose_goal(nested_request)
        
        logger.info(f"Created {len(sub_goals)} sub-goals for goal {request.goal_id}")
        return sub_goals
    
    async def prioritize_goals(
        self,
        agent_id: str,
        goal_ids: Optional[List[str]] = None
    ) -> List[Goal]:
        """
        Prioritize goals for an agent.
        
        UNBOUNDED: Full autonomy in prioritization
        GOVERNED: Suggests priorities, human approves
        """
        mode = self.mode_manager.get_mode(agent_id)
        
        # Get goals to prioritize
        if goal_ids:
            goals = [self._goals[gid] for gid in goal_ids if gid in self._goals]
        else:
            goal_ids = self._agent_goals.get(agent_id, [])
            goals = [self._goals[gid] for gid in goal_ids if gid in self._goals]
        
        # Filter to pending/in_progress goals
        active_goals = [
            g for g in goals 
            if g.status in [GoalStatus.PENDING, GoalStatus.IN_PROGRESS]
        ]
        
        if not active_goals:
            return []
        
        # Build prioritization prompt
        prompt = self._build_prioritization_prompt(active_goals)
        
        # Call LLM for prioritization
        if self.llm_client:
            priority_order = await self._call_llm_for_prioritization(prompt)
        else:
            # Mock prioritization
            priority_order = self._mock_prioritize_goals(active_goals)
        
        # Apply priorities
        for i, goal_id in enumerate(priority_order):
            if goal_id in self._goals:
                # Map index to priority (1-9)
                priority_value = min(9, max(1, (i // 2) * 2 + 1))
                self._goals[goal_id].priority = GoalPriority(priority_value)
                self._goals[goal_id].updated_at = datetime.utcnow().isoformat()
        
        # Return sorted goals
        sorted_goals = sorted(
            [self._goals[gid] for gid in priority_order if gid in self._goals],
            key=lambda g: g.priority.value
        )
        
        return sorted_goals
    
    def assign_goal(
        self,
        agent_id: str,
        description: str,
        assigner_id: str,
        priority: GoalPriority = GoalPriority.MEDIUM,
        deadline: Optional[str] = None,
        success_criteria: Optional[List[str]] = None,
    ) -> Goal:
        """
        Assign a goal to an agent (human-initiated).
        
        Available in both modes.
        """
        goal = Goal(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            description=description,
            goal_type=GoalType.ASSIGNED,
            priority=priority,
            deadline=deadline,
            success_criteria=success_criteria or [],
            created_by=assigner_id,
        )
        
        self._goals[goal.id] = goal
        if agent_id not in self._agent_goals:
            self._agent_goals[agent_id] = []
        self._agent_goals[agent_id].append(goal.id)
        
        logger.info(f"Assigned goal {goal.id} to agent {agent_id} by {assigner_id}")
        return goal
    
    def update_goal_status(
        self,
        goal_id: str,
        status: GoalStatus,
        completion_percentage: Optional[float] = None,
    ) -> Goal:
        """Update the status of a goal."""
        goal = self._goals.get(goal_id)
        if not goal:
            raise ValueError(f"Goal {goal_id} not found")
        
        goal.status = status
        goal.updated_at = datetime.utcnow().isoformat()
        
        if completion_percentage is not None:
            goal.completion_percentage = completion_percentage
        
        if status == GoalStatus.COMPLETED:
            goal.completion_percentage = 100.0
            goal.completed_at = datetime.utcnow().isoformat()
        
        return goal
    
    def get_goal(self, goal_id: str) -> Optional[Goal]:
        """Get a goal by ID."""
        return self._goals.get(goal_id)
    
    def get_agent_goals(
        self,
        agent_id: str,
        status: Optional[GoalStatus] = None,
        goal_type: Optional[GoalType] = None,
    ) -> List[Goal]:
        """Get all goals for an agent."""
        goal_ids = self._agent_goals.get(agent_id, [])
        goals = [self._goals[gid] for gid in goal_ids if gid in self._goals]
        
        if status:
            goals = [g for g in goals if g.status == status]
        
        if goal_type:
            goals = [g for g in goals if g.goal_type == goal_type]
        
        return sorted(goals, key=lambda g: g.priority.value)
    
    def get_next_goal(self, agent_id: str) -> Optional[Goal]:
        """Get the next goal for an agent to work on."""
        goals = self.get_agent_goals(agent_id, status=GoalStatus.PENDING)
        if not goals:
            goals = self.get_agent_goals(agent_id, status=GoalStatus.IN_PROGRESS)
        
        if goals:
            return goals[0]  # Highest priority
        return None
    
    # Private methods for LLM interaction
    
    def _build_goal_generation_prompt(self, request: GoalGenerationRequest) -> str:
        """Build prompt for goal generation."""
        return f"""
        Based on the following context, generate up to {request.max_goals} goals for the agent.
        
        Context:
        {request.context}
        
        Focus areas: {', '.join(request.focus_areas) if request.focus_areas else 'General'}
        Constraints: {request.constraints}
        
        For each goal, provide:
        - description: Clear, actionable goal description
        - priority: 1-9 (1=critical, 9=background)
        - success_criteria: List of measurable criteria
        - estimated_hours: Estimated effort in hours
        - tags: Relevant tags
        
        Return as JSON array.
        """
    
    def _build_decomposition_prompt(self, goal: Goal, request: GoalDecompositionRequest) -> str:
        """Build prompt for goal decomposition."""
        return f"""
        Decompose the following goal into up to {request.max_sub_goals} sub-goals.
        
        Goal: {goal.description}
        Success criteria: {goal.success_criteria}
        
        For each sub-goal, provide:
        - description: Clear, actionable sub-goal description
        - success_criteria: List of measurable criteria
        - estimated_hours: Estimated effort in hours
        - tags: Relevant tags
        
        Return as JSON array.
        """
    
    def _build_prioritization_prompt(self, goals: List[Goal]) -> str:
        """Build prompt for goal prioritization."""
        goal_list = "\n".join([
            f"- {g.id}: {g.description} (current priority: {g.priority.value})"
            for g in goals
        ])
        return f"""
        Prioritize the following goals from most to least important.
        
        Goals:
        {goal_list}
        
        Return goal IDs in priority order as JSON array.
        """
    
    async def _call_llm_for_goals(self, prompt: str, max_goals: int) -> List[Dict]:
        """Call LLM to generate goals."""
        # Placeholder - implement with actual LLM client
        return self._mock_generate_goals(GoalGenerationRequest(
            agent_id="",
            context={},
            max_goals=max_goals,
        ))
    
    async def _call_llm_for_decomposition(self, prompt: str, max_sub_goals: int) -> List[Dict]:
        """Call LLM to decompose goal."""
        # Placeholder - implement with actual LLM client
        return []
    
    async def _call_llm_for_prioritization(self, prompt: str) -> List[str]:
        """Call LLM to prioritize goals."""
        # Placeholder - implement with actual LLM client
        return []
    
    # Mock methods for testing
    
    def _mock_generate_goals(self, request: GoalGenerationRequest) -> List[Dict]:
        """Mock goal generation for testing."""
        return [
            {
                "description": f"Generated goal {i+1} based on context",
                "priority": 5,
                "success_criteria": ["Criteria 1", "Criteria 2"],
                "estimated_hours": 2.0,
                "tags": ["generated", "autonomous"],
            }
            for i in range(min(3, request.max_goals))
        ]
    
    def _mock_decompose_goal(self, goal: Goal, request: GoalDecompositionRequest) -> List[Dict]:
        """Mock goal decomposition for testing."""
        return [
            {
                "description": f"Sub-goal {i+1} of: {goal.description[:50]}...",
                "success_criteria": ["Sub-criteria 1"],
                "estimated_hours": 0.5,
                "tags": ["sub-goal"],
            }
            for i in range(min(3, request.max_sub_goals))
        ]
    
    def _mock_prioritize_goals(self, goals: List[Goal]) -> List[str]:
        """Mock goal prioritization for testing."""
        # Simple mock: sort by current priority
        sorted_goals = sorted(goals, key=lambda g: g.priority.value)
        return [g.id for g in sorted_goals]


# Global instance
goal_generation_engine = GoalGenerationEngine()


def get_goal_generation_engine() -> GoalGenerationEngine:
    """Get the global goal generation engine."""
    return goal_generation_engine
