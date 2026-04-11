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
MULTI-AGENT ORCHESTRATOR
========================

Central orchestration for autonomous agent swarms.
Coordinates multiple agents working together on complex goals.

Features:
- Goal decomposition into sub-tasks
- Agent assignment based on capabilities
- Progress tracking across agents
- Result aggregation
- Failure recovery and reassignment
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Set, Callable
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
import json

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class AgentRole(Enum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    REVIEWER = "reviewer"
    SUPERVISOR = "supervisor"
    SPECIALIST = "specialist"


@dataclass
class SubTask:
    """A sub-task decomposed from main goal."""
    id: str
    description: str
    parent_id: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    assigned_agent: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    priority: int = 1
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None


@dataclass
class SwarmGoal:
    """A high-level goal for agent swarm."""
    id: str
    description: str
    sub_tasks: List[SubTask] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    agents_assigned: Set[str] = field(default_factory=set)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None
    final_result: Optional[Dict[str, Any]] = None


@dataclass 
class AgentProfile:
    """Profile of an agent in the swarm."""
    agent_id: str
    name: str
    role: AgentRole
    capabilities: List[str] = field(default_factory=list)
    current_task: Optional[str] = None
    tasks_completed: int = 0
    tasks_failed: int = 0
    available: bool = True
    performance_score: float = 1.0


class MultiAgentOrchestrator:
    """
    Orchestrates multiple autonomous agents working as a swarm.
    
    This is the brain that:
    - Breaks down complex goals into sub-tasks
    - Assigns tasks to capable agents
    - Tracks progress and handles failures
    - Aggregates results
    - Ensures goal completion
    """
    
    def __init__(self, llm_service_url: str = None):
        self.llm_service_url = llm_service_url or "http://llm_service:8000"
        
        # Agent registry
        self.agents: Dict[str, AgentProfile] = {}
        
        # Active goals
        self.goals: Dict[str, SwarmGoal] = {}
        
        # Task queue
        self.task_queue: asyncio.Queue = asyncio.Queue()
        
        # Result callbacks
        self._on_task_complete: Optional[Callable] = None
        self._on_goal_complete: Optional[Callable] = None
        
        # Blockchain integration
        self._blockchain_client = None
        
        self._running = False
        self._orchestration_task = None
    
    async def start(self):
        """Start the orchestrator."""
        self._running = True
        self._orchestration_task = asyncio.create_task(self._orchestration_loop())
        logger.info("Multi-Agent Orchestrator started")
    
    async def stop(self):
        """Stop the orchestrator."""
        self._running = False
        if self._orchestration_task:
            self._orchestration_task.cancel()
        logger.info("Multi-Agent Orchestrator stopped")
    
    async def register_agent(
        self,
        agent_id: str,
        name: str,
        role: AgentRole,
        capabilities: List[str] = None,
    ):
        """Register an agent with the orchestrator."""
        self.agents[agent_id] = AgentProfile(
            agent_id=agent_id,
            name=name,
            role=role,
            capabilities=capabilities or [],
        )
        logger.info(f"Registered agent {name} ({agent_id}) as {role.value}")
    
    async def submit_goal(self, description: str, priority: int = 1) -> str:
        """Submit a high-level goal for the swarm."""
        goal_id = str(uuid4())
        
        goal = SwarmGoal(
            id=goal_id,
            description=description,
        )
        
        # Decompose into sub-tasks
        sub_tasks = await self._decompose_goal(description)
        goal.sub_tasks = sub_tasks
        
        self.goals[goal_id] = goal
        
        # Queue initial tasks (those with no dependencies)
        for task in sub_tasks:
            if not task.dependencies:
                await self.task_queue.put(task)
        
        logger.info(f"Goal {goal_id} submitted with {len(sub_tasks)} sub-tasks")
        
        # Record on blockchain
        await self._record_on_blockchain("goal_submitted", {
            "goal_id": goal_id,
            "description": description,
            "sub_tasks": len(sub_tasks),
        })
        
        return goal_id
    
    async def _decompose_goal(self, description: str) -> List[SubTask]:
        """Decompose a goal into sub-tasks using LLM."""
        import httpx
        
        prompt = f"""You are a task decomposition expert. Break down this goal into specific, actionable sub-tasks.

GOAL: {description}

Respond in JSON with a list of tasks:
{{
    "tasks": [
        {{
            "id": "task_1",
            "description": "Specific task description",
            "dependencies": [],  // IDs of tasks that must complete first
            "required_capabilities": ["capability1", "capability2"],
            "priority": 1  // 1-5, 5 is highest
        }}
    ]
}}

Break down into 3-10 concrete tasks. Each task should be completable by one agent."""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
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
                    
                    tasks = []
                    for t in data.get("tasks", []):
                        tasks.append(SubTask(
                            id=t.get("id", str(uuid4())),
                            description=t.get("description", ""),
                            dependencies=t.get("dependencies", []),
                            priority=t.get("priority", 1),
                        ))
                    return tasks
                    
        except Exception as e:
            logger.error(f"Goal decomposition failed: {e}")
        
        # Fallback: single task
        return [SubTask(id="task_1", description=description)]
    
    async def _orchestration_loop(self):
        """Main orchestration loop."""
        while self._running:
            try:
                # Get next task
                try:
                    task = await asyncio.wait_for(self.task_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    # Check for blocked tasks that can now proceed
                    await self._check_blocked_tasks()
                    continue
                
                # Find best agent for task
                agent = await self._find_best_agent(task)
                
                if agent:
                    await self._assign_task(task, agent)
                else:
                    # No available agent, requeue
                    task.status = TaskStatus.BLOCKED
                    await asyncio.sleep(1)
                    await self.task_queue.put(task)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Orchestration error: {e}")
    
    async def _find_best_agent(self, task: SubTask) -> Optional[AgentProfile]:
        """Find the best available agent for a task."""
        available_agents = [
            a for a in self.agents.values()
            if a.available and a.current_task is None
        ]
        
        if not available_agents:
            return None
        
        # Score agents by capability match and performance
        scored = []
        for agent in available_agents:
            score = agent.performance_score
            # Prefer executors for tasks, reviewers for review, etc.
            if agent.role == AgentRole.EXECUTOR:
                score += 0.5
            scored.append((score, agent))
        
        scored.sort(reverse=True, key=lambda x: x[0])
        return scored[0][1] if scored else None
    
    async def _assign_task(self, task: SubTask, agent: AgentProfile):
        """Assign a task to an agent."""
        task.assigned_agent = agent.agent_id
        task.status = TaskStatus.ASSIGNED
        agent.current_task = task.id
        agent.available = False
        
        logger.info(f"Assigned task {task.id} to agent {agent.name}")
        
        # Record on blockchain
        await self._record_on_blockchain("task_assigned", {
            "task_id": task.id,
            "agent_id": agent.agent_id,
            "description": task.description,
        })
        
        # Trigger agent execution
        await self._trigger_agent_execution(agent, task)
    
    async def _trigger_agent_execution(self, agent: AgentProfile, task: SubTask):
        """Trigger agent to execute task."""
        from .parallel_agent_runtime import get_runtime
        
        try:
            runtime = await get_runtime()
            
            # Send task to agent
            await runtime.send_message(
                from_agent="orchestrator",
                to_agent=agent.agent_id,
                content={
                    "type": "execute_task",
                    "task_id": task.id,
                    "description": task.description,
                    "priority": task.priority,
                },
            )
            
            task.status = TaskStatus.IN_PROGRESS
            
        except Exception as e:
            logger.error(f"Failed to trigger agent: {e}")
            task.status = TaskStatus.FAILED
            task.error = str(e)
            await self._handle_task_failure(task, agent)
    
    async def report_task_complete(
        self,
        task_id: str,
        agent_id: str,
        result: Dict[str, Any],
        success: bool = True,
    ):
        """Report task completion from an agent."""
        # Find task
        task = None
        goal = None
        for g in self.goals.values():
            for t in g.sub_tasks:
                if t.id == task_id:
                    task = t
                    goal = g
                    break
        
        if not task:
            logger.warning(f"Task {task_id} not found")
            return
        
        agent = self.agents.get(agent_id)
        
        if success:
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.completed_at = datetime.now(timezone.utc).isoformat()
            
            if agent:
                agent.tasks_completed += 1
                agent.performance_score = min(2.0, agent.performance_score + 0.1)
            
            logger.info(f"Task {task_id} completed by {agent_id}")
            
            # Record on blockchain
            await self._record_on_blockchain("task_completed", {
                "task_id": task_id,
                "agent_id": agent_id,
                "success": True,
            })
            
            # Check if dependent tasks can now proceed
            await self._unlock_dependent_tasks(task, goal)
            
            # Check if goal is complete
            await self._check_goal_completion(goal)
            
        else:
            await self._handle_task_failure(task, agent, result.get("error"))
        
        # Free agent
        if agent:
            agent.current_task = None
            agent.available = True
    
    async def _handle_task_failure(
        self,
        task: SubTask,
        agent: Optional[AgentProfile],
        error: str = None,
    ):
        """Handle task failure."""
        task.status = TaskStatus.FAILED
        task.error = error
        
        if agent:
            agent.tasks_failed += 1
            agent.performance_score = max(0.1, agent.performance_score - 0.2)
            agent.current_task = None
            agent.available = True
        
        logger.warning(f"Task {task.id} failed: {error}")
        
        # Record on blockchain
        await self._record_on_blockchain("task_failed", {
            "task_id": task.id,
            "agent_id": agent.agent_id if agent else None,
            "error": error,
        })
        
        # Retry with different agent
        task.status = TaskStatus.PENDING
        task.assigned_agent = None
        await self.task_queue.put(task)
    
    async def _unlock_dependent_tasks(self, completed_task: SubTask, goal: SwarmGoal):
        """Unlock tasks that depended on completed task."""
        for task in goal.sub_tasks:
            if completed_task.id in task.dependencies:
                # Check if all dependencies are met
                all_deps_met = all(
                    any(t.id == dep and t.status == TaskStatus.COMPLETED 
                        for t in goal.sub_tasks)
                    for dep in task.dependencies
                )
                
                if all_deps_met and task.status == TaskStatus.PENDING:
                    await self.task_queue.put(task)
                    logger.info(f"Task {task.id} unlocked")
    
    async def _check_blocked_tasks(self):
        """Check if any blocked tasks can proceed."""
        for goal in self.goals.values():
            for task in goal.sub_tasks:
                if task.status == TaskStatus.BLOCKED:
                    # Check if dependencies are met
                    deps_met = all(
                        any(t.id == dep and t.status == TaskStatus.COMPLETED
                            for t in goal.sub_tasks)
                        for dep in task.dependencies
                    )
                    
                    if deps_met:
                        task.status = TaskStatus.PENDING
                        await self.task_queue.put(task)
    
    async def _check_goal_completion(self, goal: SwarmGoal):
        """Check if goal is complete."""
        all_complete = all(
            t.status == TaskStatus.COMPLETED
            for t in goal.sub_tasks
        )
        
        if all_complete:
            goal.status = TaskStatus.COMPLETED
            goal.completed_at = datetime.now(timezone.utc).isoformat()
            
            # Aggregate results
            goal.final_result = {
                "sub_task_results": [
                    {"id": t.id, "result": t.result}
                    for t in goal.sub_tasks
                ],
            }
            
            logger.info(f"Goal {goal.id} completed!")
            
            # Record on blockchain
            await self._record_on_blockchain("goal_completed", {
                "goal_id": goal.id,
                "description": goal.description,
                "tasks_completed": len(goal.sub_tasks),
            })
            
            if self._on_goal_complete:
                await self._on_goal_complete(goal)
    
    async def _record_on_blockchain(self, event_type: str, data: Dict[str, Any]):
        """Record event on blockchain."""
        try:
            import httpx
            
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    "http://blockchain_node_1:8000/distributed/transactions",
                    json={
                        "tx_type": "agent_orchestration",
                        "payload": {
                            "event": event_type,
                            "data": data,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    },
                )
        except Exception as e:
            logger.debug(f"Blockchain recording failed: {e}")
    
    def get_goal_status(self, goal_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a goal."""
        goal = self.goals.get(goal_id)
        if not goal:
            return None
        
        return {
            "id": goal.id,
            "description": goal.description,
            "status": goal.status.value,
            "sub_tasks": [
                {
                    "id": t.id,
                    "description": t.description,
                    "status": t.status.value,
                    "assigned_agent": t.assigned_agent,
                }
                for t in goal.sub_tasks
            ],
            "completed_at": goal.completed_at,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get orchestrator statistics."""
        return {
            "agents": len(self.agents),
            "available_agents": sum(1 for a in self.agents.values() if a.available),
            "active_goals": sum(1 for g in self.goals.values() if g.status == TaskStatus.IN_PROGRESS),
            "completed_goals": sum(1 for g in self.goals.values() if g.status == TaskStatus.COMPLETED),
            "pending_tasks": self.task_queue.qsize(),
        }


# Global instance
_orchestrator: Optional[MultiAgentOrchestrator] = None


async def get_orchestrator() -> MultiAgentOrchestrator:
    """Get or create orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = MultiAgentOrchestrator()
        await _orchestrator.start()
    return _orchestrator
