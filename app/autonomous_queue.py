"""
AUTONOMOUS TASK QUEUE
=====================

CRITICAL for FULL AUTONOMY: Tasks execute without human trigger.
Self-managing queue that agents populate and consume autonomously.

Features:
- Self-generating tasks from goals
- Priority-based execution
- No human intervention required
- Automatic task creation
- Load balancing across agents
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
import heapq

logger = logging.getLogger(__name__)


class TaskPriority(Enum):
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4
    BACKGROUND = 5


class TaskStatus(Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskSource(Enum):
    GOAL = "goal"           # From goal decomposition
    AGENT = "agent"         # Agent-generated
    SYSTEM = "system"       # System-generated
    PROACTIVE = "proactive" # Proactive detection
    SCHEDULED = "scheduled" # Scheduled task


@dataclass
class AutonomousTask:
    """A task that executes autonomously."""
    id: str
    description: str
    source: TaskSource
    priority: TaskPriority
    status: TaskStatus = TaskStatus.PENDING
    
    # Assignment
    assigned_agent: Optional[str] = None
    assigned_at: Optional[str] = None
    
    # Execution
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    # Retry
    attempts: int = 0
    max_attempts: int = 3
    
    # Timing
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    deadline: Optional[str] = None
    
    # Context
    context: Dict[str, Any] = field(default_factory=dict)
    parent_task_id: Optional[str] = None
    sub_task_ids: List[str] = field(default_factory=list)
    
    def __lt__(self, other):
        # For priority queue - lower priority value = higher priority
        return self.priority.value < other.priority.value


class TaskGenerator:
    """Generates tasks autonomously from various sources."""
    
    def __init__(self):
        self.generators: List[Callable] = []
    
    async def generate_from_goals(self, agent_id: str) -> List[AutonomousTask]:
        """Generate tasks from agent goals."""
        from .goal_pursuit import get_goal_pursuit_engine
        
        tasks = []
        try:
            engine = await get_goal_pursuit_engine()
            goals = engine.get_agent_goals(agent_id)
            
            for goal in goals:
                if goal.status.value == "active":
                    # Find incomplete milestones
                    for milestone in goal.milestones:
                        if not milestone.completed:
                            task = AutonomousTask(
                                id=str(uuid4()),
                                description=f"Complete: {milestone.description}",
                                source=TaskSource.GOAL,
                                priority=TaskPriority.MEDIUM,
                                context={
                                    "goal_id": goal.id,
                                    "milestone_id": milestone.id,
                                    "goal_description": goal.description,
                                },
                            )
                            tasks.append(task)
                            break  # One task per goal
        except:
            pass
        
        return tasks
    
    async def generate_from_proactive(self, agent_id: str) -> List[AutonomousTask]:
        """Generate tasks from proactive detection."""
        from .proactive_behavior import get_proactive_system
        
        tasks = []
        try:
            system = await get_proactive_system()
            proactive_tasks = system.get_pending_tasks(agent_id)
            
            for pt in proactive_tasks[:3]:  # Limit
                task = AutonomousTask(
                    id=str(uuid4()),
                    description=pt.description,
                    source=TaskSource.PROACTIVE,
                    priority=TaskPriority.LOW if pt.priority < 0.5 else TaskPriority.MEDIUM,
                    context={"proactive_task_id": pt.id, "reason": pt.reason},
                )
                tasks.append(task)
        except:
            pass
        
        return tasks
    
    async def generate_maintenance_tasks(self) -> List[AutonomousTask]:
        """Generate system maintenance tasks."""
        tasks = []
        
        # Memory consolidation
        tasks.append(AutonomousTask(
            id=str(uuid4()),
            description="Consolidate and optimize agent memories",
            source=TaskSource.SYSTEM,
            priority=TaskPriority.BACKGROUND,
        ))
        
        # Health check
        tasks.append(AutonomousTask(
            id=str(uuid4()),
            description="Perform system health check",
            source=TaskSource.SYSTEM,
            priority=TaskPriority.BACKGROUND,
        ))
        
        return tasks


class AutonomousTaskQueue:
    """
    Self-managing task queue for full autonomy.
    Tasks execute without any human trigger.
    """
    
    GENERATION_INTERVAL = 30  # seconds
    PROCESSING_INTERVAL = 2  # seconds
    
    def __init__(self):
        self.tasks: Dict[str, AutonomousTask] = {}
        self.priority_queue: List[AutonomousTask] = []
        self.generator = TaskGenerator()
        
        # Agent assignments
        self.agent_tasks: Dict[str, List[str]] = {}  # agent_id -> task_ids
        self.available_agents: List[str] = []
        
        # Running state
        self._running = False
        self._generation_task = None
        self._processing_task = None
    
    async def start(self):
        """Start the autonomous queue."""
        self._running = True
        self._generation_task = asyncio.create_task(self._generation_loop())
        self._processing_task = asyncio.create_task(self._processing_loop())
        
        logger.info("Autonomous Task Queue started - NO HUMAN TRIGGER REQUIRED")
    
    async def stop(self):
        """Stop the queue."""
        self._running = False
        if self._generation_task:
            self._generation_task.cancel()
        if self._processing_task:
            self._processing_task.cancel()
    
    def register_agent(self, agent_id: str):
        """Register an agent as available for tasks."""
        if agent_id not in self.available_agents:
            self.available_agents.append(agent_id)
            self.agent_tasks[agent_id] = []
            logger.info(f"Agent {agent_id} registered for autonomous tasks")
    
    def unregister_agent(self, agent_id: str):
        """Unregister an agent."""
        if agent_id in self.available_agents:
            self.available_agents.remove(agent_id)
    
    async def _generation_loop(self):
        """Continuously generate new tasks."""
        while self._running:
            try:
                await self._generate_tasks()
                await asyncio.sleep(self.GENERATION_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Task generation error: {e}")
    
    async def _generate_tasks(self):
        """Generate tasks for all registered agents."""
        for agent_id in self.available_agents:
            # From goals
            goal_tasks = await self.generator.generate_from_goals(agent_id)
            for task in goal_tasks:
                await self.add_task(task)
            
            # From proactive
            proactive_tasks = await self.generator.generate_from_proactive(agent_id)
            for task in proactive_tasks:
                await self.add_task(task)
        
        # System maintenance
        if len(self.tasks) < 10:
            maintenance = await self.generator.generate_maintenance_tasks()
            for task in maintenance:
                await self.add_task(task)
    
    async def _processing_loop(self):
        """Continuously process and assign tasks."""
        while self._running:
            try:
                await self._process_queue()
                await asyncio.sleep(self.PROCESSING_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Task processing error: {e}")
    
    async def _process_queue(self):
        """Process pending tasks."""
        # Get available agents
        available = [
            agent_id for agent_id in self.available_agents
            if len(self.agent_tasks.get(agent_id, [])) < 3
        ]
        
        if not available or not self.priority_queue:
            return
        
        # Assign highest priority task
        task = heapq.heappop(self.priority_queue)
        if task.status != TaskStatus.PENDING:
            return
        
        # Pick agent (round-robin for now)
        agent_id = available[0]
        
        # Assign
        task.status = TaskStatus.ASSIGNED
        task.assigned_agent = agent_id
        task.assigned_at = datetime.now(timezone.utc).isoformat()
        
        self.agent_tasks[agent_id].append(task.id)
        
        # Execute
        asyncio.create_task(self._execute_task(task))
    
    async def _execute_task(self, task: AutonomousTask):
        """Execute a task autonomously."""
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc).isoformat()
        task.attempts += 1
        
        try:
            from .agent_executor import get_agent_executor
            executor = await get_agent_executor()
            
            result = await executor.execute(
                agent_id=task.assigned_agent,
                task=task.description,
                context=task.context,
            )
            
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc).isoformat()
            task.result = {"success": result.success, "output": result.output}
            
            logger.info(f"Task completed autonomously: {task.description[:50]}")
            
        except Exception as e:
            task.error = str(e)
            
            if task.attempts < task.max_attempts:
                task.status = TaskStatus.PENDING
                heapq.heappush(self.priority_queue, task)
            else:
                task.status = TaskStatus.FAILED
        
        finally:
            # Remove from agent's active tasks
            if task.assigned_agent and task.assigned_agent in self.agent_tasks:
                tasks = self.agent_tasks[task.assigned_agent]
                if task.id in tasks:
                    tasks.remove(task.id)
    
    async def add_task(self, task: AutonomousTask) -> str:
        """Add a task to the queue."""
        if task.id in self.tasks:
            return task.id
        
        self.tasks[task.id] = task
        heapq.heappush(self.priority_queue, task)
        
        return task.id
    
    def get_task(self, task_id: str) -> Optional[AutonomousTask]:
        """Get a task by ID."""
        return self.tasks.get(task_id)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        by_status = {}
        for task in self.tasks.values():
            status = task.status.value
            by_status[status] = by_status.get(status, 0) + 1
        
        return {
            "total_tasks": len(self.tasks),
            "pending_tasks": len(self.priority_queue),
            "registered_agents": len(self.available_agents),
            "by_status": by_status,
        }


# Global instance
_queue: Optional[AutonomousTaskQueue] = None


async def get_autonomous_queue() -> AutonomousTaskQueue:
    """Get or create autonomous queue."""
    global _queue
    if _queue is None:
        _queue = AutonomousTaskQueue()
        await _queue.start()
    return _queue
