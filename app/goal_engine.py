"""
GOAL DECOMPOSITION ENGINE
=========================

Intelligent goal decomposition for autonomous agents.
Breaks complex goals into executable sub-tasks with dependencies.

Features:
- Hierarchical goal decomposition
- Dependency graph generation
- Resource estimation
- Priority assignment
- Adaptive replanning
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
import json

import httpx

logger = logging.getLogger(__name__)


class TaskComplexity(Enum):
    TRIVIAL = 1
    SIMPLE = 2
    MODERATE = 3
    COMPLEX = 4
    VERY_COMPLEX = 5


class TaskType(Enum):
    ANALYSIS = "analysis"
    RESEARCH = "research"
    CODING = "coding"
    TESTING = "testing"
    REVIEW = "review"
    COMMUNICATION = "communication"
    DECISION = "decision"
    EXECUTION = "execution"


@dataclass
class DecomposedTask:
    """A task decomposed from a goal."""
    id: str
    description: str
    task_type: TaskType
    complexity: TaskComplexity
    dependencies: List[str] = field(default_factory=list)
    required_capabilities: List[str] = field(default_factory=list)
    estimated_duration_minutes: int = 10
    priority: int = 1
    context: Dict[str, Any] = field(default_factory=dict)
    success_criteria: List[str] = field(default_factory=list)
    fallback_strategy: Optional[str] = None


@dataclass
class GoalPlan:
    """Complete plan for achieving a goal."""
    id: str
    original_goal: str
    tasks: List[DecomposedTask]
    dependency_graph: Dict[str, List[str]]
    critical_path: List[str]
    estimated_total_minutes: int
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class GoalDecompositionEngine:
    """
    Decomposes complex goals into actionable task plans.
    """
    
    def __init__(self, llm_service_url: str = None):
        self.llm_service_url = llm_service_url or "http://llm_service:8000"
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client
    
    async def decompose(
        self,
        goal: str,
        context: Dict[str, Any] = None,
        max_depth: int = 3,
        available_capabilities: List[str] = None,
    ) -> GoalPlan:
        """Decompose a goal into a task plan."""
        plan_id = str(uuid4())
        
        # Get initial decomposition from LLM
        tasks = await self._llm_decompose(goal, context, available_capabilities)
        
        # Build dependency graph
        graph = self._build_dependency_graph(tasks)
        
        # Find critical path
        critical_path = self._find_critical_path(tasks, graph)
        
        # Calculate total time
        total_time = sum(t.estimated_duration_minutes for t in tasks)
        
        plan = GoalPlan(
            id=plan_id,
            original_goal=goal,
            tasks=tasks,
            dependency_graph=graph,
            critical_path=critical_path,
            estimated_total_minutes=total_time,
        )
        
        logger.info(f"Decomposed goal into {len(tasks)} tasks, critical path: {len(critical_path)}")
        
        return plan
    
    async def _llm_decompose(
        self,
        goal: str,
        context: Dict[str, Any] = None,
        capabilities: List[str] = None,
    ) -> List[DecomposedTask]:
        """Use LLM to decompose goal."""
        client = await self._get_client()
        
        prompt = f"""You are a task decomposition expert. Break down this goal into specific, actionable tasks.

GOAL: {goal}

CONTEXT: {json.dumps(context or {})}

AVAILABLE CAPABILITIES: {capabilities or ["general"]}

Create a detailed task breakdown. For each task specify:
1. A unique ID (task_1, task_2, etc.)
2. Clear description
3. Task type (analysis/research/coding/testing/review/communication/decision/execution)
4. Complexity (1-5, where 5 is most complex)
5. Dependencies (IDs of tasks that must complete first)
6. Required capabilities
7. Estimated duration in minutes
8. Priority (1-5, where 5 is highest)
9. Success criteria

Respond in JSON:
{{
    "tasks": [
        {{
            "id": "task_1",
            "description": "...",
            "type": "analysis",
            "complexity": 2,
            "dependencies": [],
            "capabilities": ["research"],
            "duration_minutes": 15,
            "priority": 3,
            "success_criteria": ["criterion 1", "criterion 2"],
            "fallback": "alternative approach if this fails"
        }}
    ]
}}

Create 3-10 well-structured tasks that together achieve the goal."""

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
                
                tasks = []
                for t in data.get("tasks", []):
                    task_type = TaskType.EXECUTION
                    try:
                        task_type = TaskType(t.get("type", "execution"))
                    except ValueError:
                        pass
                    
                    complexity = TaskComplexity(min(5, max(1, t.get("complexity", 2))))
                    
                    tasks.append(DecomposedTask(
                        id=t.get("id", str(uuid4())),
                        description=t.get("description", ""),
                        task_type=task_type,
                        complexity=complexity,
                        dependencies=t.get("dependencies", []),
                        required_capabilities=t.get("capabilities", []),
                        estimated_duration_minutes=t.get("duration_minutes", 10),
                        priority=min(5, max(1, t.get("priority", 1))),
                        success_criteria=t.get("success_criteria", []),
                        fallback_strategy=t.get("fallback"),
                    ))
                
                return tasks
                
        except Exception as e:
            logger.error(f"LLM decomposition failed: {e}")
        
        # Fallback: single task
        return [DecomposedTask(
            id="task_1",
            description=goal,
            task_type=TaskType.EXECUTION,
            complexity=TaskComplexity.MODERATE,
        )]
    
    def _build_dependency_graph(self, tasks: List[DecomposedTask]) -> Dict[str, List[str]]:
        """Build dependency graph from tasks."""
        graph = {}
        for task in tasks:
            graph[task.id] = task.dependencies
        return graph
    
    def _find_critical_path(
        self,
        tasks: List[DecomposedTask],
        graph: Dict[str, List[str]],
    ) -> List[str]:
        """Find the critical path through the task graph."""
        task_map = {t.id: t for t in tasks}
        
        # Calculate longest path to each task
        longest_path: Dict[str, Tuple[int, List[str]]] = {}
        
        def get_longest_path(task_id: str) -> Tuple[int, List[str]]:
            if task_id in longest_path:
                return longest_path[task_id]
            
            task = task_map.get(task_id)
            if not task:
                return (0, [])
            
            deps = graph.get(task_id, [])
            if not deps:
                result = (task.estimated_duration_minutes, [task_id])
            else:
                max_path = (0, [])
                for dep in deps:
                    dep_duration, dep_path = get_longest_path(dep)
                    if dep_duration > max_path[0]:
                        max_path = (dep_duration, dep_path)
                
                result = (
                    max_path[0] + task.estimated_duration_minutes,
                    max_path[1] + [task_id]
                )
            
            longest_path[task_id] = result
            return result
        
        # Find task with longest path
        max_duration = 0
        critical_path = []
        
        for task in tasks:
            duration, path = get_longest_path(task.id)
            if duration > max_duration:
                max_duration = duration
                critical_path = path
        
        return critical_path
    
    async def replan(
        self,
        plan: GoalPlan,
        completed_tasks: List[str],
        failed_task: Optional[str] = None,
        failure_reason: Optional[str] = None,
    ) -> GoalPlan:
        """Replan after progress or failure."""
        remaining_tasks = [t for t in plan.tasks if t.id not in completed_tasks]
        
        if failed_task and failure_reason:
            # Get alternative approach
            failed = next((t for t in plan.tasks if t.id == failed_task), None)
            if failed and failed.fallback_strategy:
                # Create new task with fallback
                new_task = DecomposedTask(
                    id=f"{failed_task}_retry",
                    description=failed.fallback_strategy,
                    task_type=failed.task_type,
                    complexity=failed.complexity,
                    dependencies=[d for d in failed.dependencies if d in completed_tasks],
                    required_capabilities=failed.required_capabilities,
                    estimated_duration_minutes=failed.estimated_duration_minutes,
                    priority=failed.priority + 1,
                )
                remaining_tasks.append(new_task)
        
        # Rebuild plan
        graph = self._build_dependency_graph(remaining_tasks)
        critical_path = self._find_critical_path(remaining_tasks, graph)
        total_time = sum(t.estimated_duration_minutes for t in remaining_tasks)
        
        return GoalPlan(
            id=plan.id,
            original_goal=plan.original_goal,
            tasks=remaining_tasks,
            dependency_graph=graph,
            critical_path=critical_path,
            estimated_total_minutes=total_time,
        )
    
    def get_next_tasks(self, plan: GoalPlan, completed: Set[str]) -> List[DecomposedTask]:
        """Get tasks that can be executed next."""
        ready = []
        
        for task in plan.tasks:
            if task.id in completed:
                continue
            
            # Check if all dependencies are complete
            deps_met = all(d in completed for d in task.dependencies)
            if deps_met:
                ready.append(task)
        
        # Sort by priority
        ready.sort(key=lambda t: t.priority, reverse=True)
        
        return ready


class AdaptivePlanner:
    """
    Adaptive planning with learning from past executions.
    """
    
    def __init__(self):
        self.engine = GoalDecompositionEngine()
        self.execution_history: List[Dict[str, Any]] = []
        self.task_duration_estimates: Dict[str, List[int]] = {}
    
    async def plan_with_learning(
        self,
        goal: str,
        context: Dict[str, Any] = None,
    ) -> GoalPlan:
        """Create a plan using learned estimates."""
        plan = await self.engine.decompose(goal, context)
        
        # Adjust estimates based on history
        for task in plan.tasks:
            task_key = f"{task.task_type.value}_{task.complexity.value}"
            if task_key in self.task_duration_estimates:
                historical = self.task_duration_estimates[task_key]
                avg_duration = sum(historical) / len(historical)
                task.estimated_duration_minutes = int(avg_duration)
        
        return plan
    
    def record_execution(
        self,
        task: DecomposedTask,
        actual_duration_minutes: int,
        success: bool,
    ):
        """Record task execution for learning."""
        task_key = f"{task.task_type.value}_{task.complexity.value}"
        
        if task_key not in self.task_duration_estimates:
            self.task_duration_estimates[task_key] = []
        
        self.task_duration_estimates[task_key].append(actual_duration_minutes)
        
        # Keep only recent history
        if len(self.task_duration_estimates[task_key]) > 100:
            self.task_duration_estimates[task_key] = self.task_duration_estimates[task_key][-50:]
        
        self.execution_history.append({
            "task_type": task.task_type.value,
            "complexity": task.complexity.value,
            "estimated": task.estimated_duration_minutes,
            "actual": actual_duration_minutes,
            "success": success,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


# Global instances
_engine: Optional[GoalDecompositionEngine] = None
_planner: Optional[AdaptivePlanner] = None


async def get_goal_engine() -> GoalDecompositionEngine:
    global _engine
    if _engine is None:
        _engine = GoalDecompositionEngine()
    return _engine


async def get_adaptive_planner() -> AdaptivePlanner:
    global _planner
    if _planner is None:
        _planner = AdaptivePlanner()
    return _planner
