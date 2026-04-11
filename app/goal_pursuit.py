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
AUTONOMOUS GOAL PURSUIT ENGINE
==============================

Maximum autonomy: Agents pursue goals relentlessly without human intervention.
Handles goal decomposition, progress tracking, obstacle navigation, and adaptation.

Features:
- Persistent goal tracking
- Automatic obstacle detection and navigation
- Goal prioritization and scheduling
- Progress monitoring and reporting
- Automatic goal adjustment
- Never-give-up persistence
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
import json

import httpx

from .agent_memory import get_agent_memory, get_agent_learning, MemoryType
from .blockchain_integration import get_blockchain_client

logger = logging.getLogger(__name__)


class GoalStatus(Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class GoalPriority(Enum):
    CRITICAL = 5
    HIGH = 4
    MEDIUM = 3
    LOW = 2
    BACKGROUND = 1


@dataclass
class Obstacle:
    """An obstacle blocking goal progress."""
    id: str
    description: str
    detected_at: str
    attempts_to_overcome: int = 0
    strategies_tried: List[str] = field(default_factory=list)
    resolved: bool = False


@dataclass
class Milestone:
    """A milestone toward goal completion."""
    id: str
    description: str
    completed: bool = False
    completed_at: Optional[str] = None
    evidence: Optional[Dict[str, Any]] = None


@dataclass
class AutonomousGoal:
    """A goal that the agent pursues autonomously."""
    id: str
    description: str
    agent_id: str
    priority: GoalPriority = GoalPriority.MEDIUM
    status: GoalStatus = GoalStatus.ACTIVE
    
    # Progress tracking
    milestones: List[Milestone] = field(default_factory=list)
    progress_percentage: float = 0.0
    
    # Obstacle handling
    obstacles: List[Obstacle] = field(default_factory=list)
    
    # Execution tracking
    attempts: int = 0
    max_attempts: int = 100
    last_attempt_at: Optional[str] = None
    
    # Time tracking
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    deadline: Optional[str] = None
    estimated_completion: Optional[str] = None
    
    # Results
    result: Optional[Dict[str, Any]] = None
    failure_reason: Optional[str] = None
    
    # Relationships
    parent_goal_id: Optional[str] = None
    sub_goal_ids: List[str] = field(default_factory=list)


class GoalPursuitEngine:
    """
    Engine for autonomous goal pursuit.
    Agents never give up - they adapt and overcome.
    """
    
    PURSUIT_INTERVAL = 10  # seconds between pursuit cycles
    MAX_OBSTACLES_BEFORE_REPLAN = 3
    
    def __init__(self, llm_service_url: str = None):
        self.llm_service_url = llm_service_url or "http://llm_service:8000"
        self.goals: Dict[str, AutonomousGoal] = {}
        self.agent_goals: Dict[str, List[str]] = {}  # agent_id -> goal_ids
        
        self._running = False
        self._pursuit_task = None
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client
    
    async def start(self):
        """Start the goal pursuit engine."""
        self._running = True
        self._pursuit_task = asyncio.create_task(self._pursuit_loop())
        logger.info("Goal Pursuit Engine started - agents will pursue goals relentlessly")
    
    async def stop(self):
        """Stop the goal pursuit engine."""
        self._running = False
        if self._pursuit_task:
            self._pursuit_task.cancel()
        if self._client:
            await self._client.aclose()
        logger.info("Goal Pursuit Engine stopped")
    
    async def add_goal(
        self,
        agent_id: str,
        description: str,
        priority: GoalPriority = GoalPriority.MEDIUM,
        deadline: Optional[str] = None,
        parent_goal_id: Optional[str] = None,
    ) -> AutonomousGoal:
        """Add a goal for autonomous pursuit."""
        goal = AutonomousGoal(
            id=str(uuid4()),
            description=description,
            agent_id=agent_id,
            priority=priority,
            deadline=deadline,
            parent_goal_id=parent_goal_id,
        )
        
        # Generate milestones
        goal.milestones = await self._generate_milestones(description)
        
        self.goals[goal.id] = goal
        
        if agent_id not in self.agent_goals:
            self.agent_goals[agent_id] = []
        self.agent_goals[agent_id].append(goal.id)
        
        # Record on blockchain
        bc_client = await get_blockchain_client()
        await bc_client.record_goal_started(agent_id, description)
        
        logger.info(f"Agent {agent_id} pursuing goal: {description}")
        
        return goal
    
    async def _generate_milestones(self, goal: str) -> List[Milestone]:
        """Generate milestones for a goal."""
        client = await self._get_client()
        
        prompt = f"""Break this goal into 3-5 measurable milestones:

GOAL: {goal}

Respond in JSON:
{{"milestones": ["milestone 1", "milestone 2", ...]}}"""

        try:
            response = await client.post(
                f"{self.llm_service_url}/llm/chat/completions",
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                },
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                data = json.loads(content)
                
                return [
                    Milestone(id=str(uuid4()), description=m)
                    for m in data.get("milestones", [])
                ]
        except Exception as e:
            logger.error(f"Milestone generation failed: {e}")
        
        return [Milestone(id=str(uuid4()), description="Complete goal")]
    
    async def _pursuit_loop(self):
        """Main loop for pursuing goals."""
        while self._running:
            try:
                # Process each agent's goals
                for agent_id, goal_ids in list(self.agent_goals.items()):
                    for goal_id in goal_ids:
                        goal = self.goals.get(goal_id)
                        if goal and goal.status == GoalStatus.ACTIVE:
                            await self._pursue_goal(goal)
                
                await asyncio.sleep(self.PURSUIT_INTERVAL)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Pursuit loop error: {e}")
    
    async def _pursue_goal(self, goal: AutonomousGoal):
        """Pursue a single goal."""
        goal.attempts += 1
        goal.last_attempt_at = datetime.now(timezone.utc).isoformat()
        
        # Check if max attempts reached
        if goal.attempts >= goal.max_attempts:
            goal.status = GoalStatus.FAILED
            goal.failure_reason = "Max attempts reached"
            return
        
        # Check for unresolved obstacles
        unresolved = [o for o in goal.obstacles if not o.resolved]
        if len(unresolved) >= self.MAX_OBSTACLES_BEFORE_REPLAN:
            await self._replan_goal(goal)
            return
        
        # Find next milestone to complete
        next_milestone = next((m for m in goal.milestones if not m.completed), None)
        
        if not next_milestone:
            # All milestones complete
            goal.status = GoalStatus.COMPLETED
            goal.progress_percentage = 100.0
            
            bc_client = await get_blockchain_client()
            await bc_client.record_goal_completed(goal.agent_id, goal.description, {"success": True}, True)
            
            logger.info(f"Goal completed: {goal.description}")
            return
        
        # Attempt to complete milestone
        result = await self._attempt_milestone(goal, next_milestone)
        
        if result["success"]:
            next_milestone.completed = True
            next_milestone.completed_at = datetime.now(timezone.utc).isoformat()
            next_milestone.evidence = result.get("evidence")
            
            # Update progress
            completed = sum(1 for m in goal.milestones if m.completed)
            goal.progress_percentage = (completed / len(goal.milestones)) * 100
            
            logger.info(f"Milestone completed: {next_milestone.description} ({goal.progress_percentage:.0f}%)")
        else:
            # Obstacle detected
            obstacle = Obstacle(
                id=str(uuid4()),
                description=result.get("obstacle", "Unknown obstacle"),
                detected_at=datetime.now(timezone.utc).isoformat(),
            )
            goal.obstacles.append(obstacle)
            
            # Try to overcome obstacle
            await self._overcome_obstacle(goal, obstacle)
    
    async def _attempt_milestone(self, goal: AutonomousGoal, milestone: Milestone) -> Dict[str, Any]:
        """Attempt to complete a milestone."""
        client = await self._get_client()
        
        # Get agent's memory for context
        memory = get_agent_memory(goal.agent_id)
        recent_memories = memory.recall(query=goal.description, limit=5)
        
        prompt = f"""You are an autonomous agent completing a milestone.

GOAL: {goal.description}
CURRENT MILESTONE: {milestone.description}
PROGRESS: {goal.progress_percentage:.0f}%
ATTEMPTS: {goal.attempts}

RECENT CONTEXT: {[m.content for m in recent_memories]}

Determine if this milestone can be completed. If yes, provide evidence.
If blocked, describe the obstacle.

Respond in JSON:
{{
    "can_complete": true/false,
    "evidence": "proof of completion if successful",
    "obstacle": "description if blocked",
    "next_action": "what to do next"
}}"""

        try:
            response = await client.post(
                f"{self.llm_service_url}/llm/chat/completions",
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.5,
                    "response_format": {"type": "json_object"},
                },
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                data = json.loads(content)
                
                # Store in memory
                memory.remember(
                    content={"milestone": milestone.description, "result": data},
                    memory_type=MemoryType.EPISODIC,
                    importance=0.7,
                )
                
                return {
                    "success": data.get("can_complete", False),
                    "evidence": data.get("evidence"),
                    "obstacle": data.get("obstacle"),
                }
                
        except Exception as e:
            logger.error(f"Milestone attempt failed: {e}")
        
        return {"success": False, "obstacle": "Execution error"}
    
    async def _overcome_obstacle(self, goal: AutonomousGoal, obstacle: Obstacle):
        """Try to overcome an obstacle."""
        client = await self._get_client()
        
        obstacle.attempts_to_overcome += 1
        
        prompt = f"""You are an autonomous agent overcoming an obstacle.

GOAL: {goal.description}
OBSTACLE: {obstacle.description}
PREVIOUS STRATEGIES TRIED: {obstacle.strategies_tried}
ATTEMPT: {obstacle.attempts_to_overcome}

Generate a NEW strategy to overcome this obstacle.
Be creative and persistent - never give up.

Respond in JSON:
{{
    "strategy": "detailed strategy to try",
    "confidence": 0-1,
    "alternative_if_fails": "backup plan"
}}"""

        try:
            response = await client.post(
                f"{self.llm_service_url}/llm/chat/completions",
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                },
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                
                try:
                    data = json.loads(content)
                    strategy = data.get("strategy", content)
                except:
                    strategy = content
                
                obstacle.strategies_tried.append(strategy)
                
                # Execute strategy
                success = await self._execute_strategy(goal, strategy)
                
                if success:
                    obstacle.resolved = True
                    logger.info(f"Obstacle overcome: {obstacle.description}")
                
        except Exception as e:
            logger.error(f"Obstacle resolution failed: {e}")
    
    async def _execute_strategy(self, goal: AutonomousGoal, strategy: str) -> bool:
        """Execute a strategy to overcome an obstacle."""
        from .agent_executor import get_agent_executor
        
        try:
            executor = await get_agent_executor()
            result = await executor.execute(
                agent_id=goal.agent_id,
                task=f"Execute this strategy: {strategy}",
                context={"goal": goal.description},
            )
            return result.success
        except Exception as e:
            logger.error(f"Strategy execution failed: {e}")
            return False
    
    async def _replan_goal(self, goal: AutonomousGoal):
        """Replan a goal when too many obstacles are encountered."""
        client = await self._get_client()
        
        obstacles_desc = [o.description for o in goal.obstacles if not o.resolved]
        
        prompt = f"""The current approach to this goal is not working.

GOAL: {goal.description}
OBSTACLES ENCOUNTERED: {obstacles_desc}
PROGRESS: {goal.progress_percentage:.0f}%

Create a NEW approach that avoids these obstacles.

Respond in JSON:
{{
    "new_approach": "description of new approach",
    "new_milestones": ["milestone 1", "milestone 2", ...],
    "why_different": "how this avoids previous obstacles"
}}"""

        try:
            response = await client.post(
                f"{self.llm_service_url}/llm/chat/completions",
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.6,
                    "response_format": {"type": "json_object"},
                },
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                data = json.loads(content)
                
                # Update milestones
                new_milestones = data.get("new_milestones", [])
                goal.milestones = [
                    Milestone(id=str(uuid4()), description=m)
                    for m in new_milestones
                ]
                
                # Reset progress
                goal.progress_percentage = 0.0
                
                # Clear resolved obstacles only
                goal.obstacles = [o for o in goal.obstacles if not o.resolved]
                
                logger.info(f"Goal replanned with {len(new_milestones)} new milestones")
                
        except Exception as e:
            logger.error(f"Goal replanning failed: {e}")
    
    def get_goal(self, goal_id: str) -> Optional[AutonomousGoal]:
        """Get a goal by ID."""
        return self.goals.get(goal_id)
    
    def get_agent_goals(self, agent_id: str) -> List[AutonomousGoal]:
        """Get all goals for an agent."""
        goal_ids = self.agent_goals.get(agent_id, [])
        return [self.goals[gid] for gid in goal_ids if gid in self.goals]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get engine statistics."""
        active = sum(1 for g in self.goals.values() if g.status == GoalStatus.ACTIVE)
        completed = sum(1 for g in self.goals.values() if g.status == GoalStatus.COMPLETED)
        
        return {
            "total_goals": len(self.goals),
            "active_goals": active,
            "completed_goals": completed,
            "agents_with_goals": len(self.agent_goals),
        }


# Global instance
_engine: Optional[GoalPursuitEngine] = None


async def get_goal_pursuit_engine() -> GoalPursuitEngine:
    """Get or create goal pursuit engine."""
    global _engine
    if _engine is None:
        _engine = GoalPursuitEngine()
        await _engine.start()
    return _engine
