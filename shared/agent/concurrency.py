"""
Concurrency manager for Agent Engine with task graph support.
Manages parallel execution with dependency tracking.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Set, Callable, Awaitable
from enum import Enum
from collections import defaultdict


class TaskState(Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


@dataclass
class TaskNode:
    """Node in the task dependency graph."""
    task_id: str
    name: str
    handler: Callable[..., Awaitable[Any]]
    args: tuple = field(default_factory=tuple)
    kwargs: Dict[str, Any] = field(default_factory=dict)
    
    # Dependencies
    depends_on: Set[str] = field(default_factory=set)
    dependents: Set[str] = field(default_factory=set)
    
    # State
    state: TaskState = TaskState.PENDING
    result: Any = None
    error: Optional[Exception] = None
    
    # Timing
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    
    # Execution constraints
    timeout_seconds: float = 300.0
    max_retries: int = 3
    retry_count: int = 0
    
    # Priority (lower = higher priority)
    priority: int = 100
    
    @property
    def duration_ms(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at) * 1000
        return None
    
    @property
    def is_terminal(self) -> bool:
        return self.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED)
    
    def can_run(self, completed_tasks: Set[str]) -> bool:
        """Check if all dependencies are satisfied."""
        return self.depends_on.issubset(completed_tasks)


class TaskGraph:
    """
    Directed acyclic graph of tasks with dependency tracking.
    """
    
    def __init__(self, graph_id: Optional[str] = None):
        self.graph_id = graph_id or str(uuid.uuid4())
        self._nodes: Dict[str, TaskNode] = {}
        self._completed: Set[str] = set()
        self._failed: Set[str] = set()
        
    def add_task(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        depends_on: Optional[List[str]] = None,
        args: tuple = (),
        kwargs: Optional[Dict[str, Any]] = None,
        timeout: float = 300.0,
        priority: int = 100,
    ) -> str:
        """Add a task to the graph."""
        task_id = str(uuid.uuid4())
        
        node = TaskNode(
            task_id=task_id,
            name=name,
            handler=handler,
            args=args,
            kwargs=kwargs or {},
            depends_on=set(depends_on or []),
            timeout_seconds=timeout,
            priority=priority,
        )
        
        # Update dependents
        for dep_id in node.depends_on:
            if dep_id in self._nodes:
                self._nodes[dep_id].dependents.add(task_id)
        
        self._nodes[task_id] = node
        return task_id
    
    def get_ready_tasks(self) -> List[TaskNode]:
        """Get tasks ready to execute (dependencies satisfied)."""
        ready = []
        
        for node in self._nodes.values():
            if node.state == TaskState.PENDING and node.can_run(self._completed):
                node.state = TaskState.READY
                ready.append(node)
        
        # Sort by priority
        return sorted(ready, key=lambda n: n.priority)
    
    def mark_completed(self, task_id: str, result: Any) -> None:
        """Mark a task as completed."""
        node = self._nodes.get(task_id)
        if node:
            node.state = TaskState.COMPLETED
            node.result = result
            node.completed_at = time.time()
            self._completed.add(task_id)
    
    def mark_failed(self, task_id: str, error: Exception) -> None:
        """Mark a task as failed."""
        node = self._nodes.get(task_id)
        if node:
            node.state = TaskState.FAILED
            node.error = error
            node.completed_at = time.time()
            self._failed.add(task_id)
            
            # Block dependent tasks
            self._block_dependents(task_id)
    
    def _block_dependents(self, task_id: str) -> None:
        """Block all tasks that depend on a failed task."""
        node = self._nodes.get(task_id)
        if not node:
            return
        
        for dep_id in node.dependents:
            dep_node = self._nodes.get(dep_id)
            if dep_node and dep_node.state == TaskState.PENDING:
                dep_node.state = TaskState.BLOCKED
                self._block_dependents(dep_id)
    
    def is_complete(self) -> bool:
        """Check if all tasks are in terminal state."""
        return all(node.is_terminal for node in self._nodes.values())
    
    def get_results(self) -> Dict[str, Any]:
        """Get results of all completed tasks."""
        return {
            task_id: node.result
            for task_id, node in self._nodes.items()
            if node.state == TaskState.COMPLETED
        }
    
    def get_errors(self) -> Dict[str, Exception]:
        """Get errors from failed tasks."""
        return {
            task_id: node.error
            for task_id, node in self._nodes.items()
            if node.state == TaskState.FAILED and node.error
        }
    
    def validate(self) -> List[str]:
        """Validate graph structure. Returns list of errors."""
        errors = []
        
        # Check for missing dependencies
        for node in self._nodes.values():
            for dep_id in node.depends_on:
                if dep_id not in self._nodes:
                    errors.append(f"Task {node.name} depends on unknown task {dep_id}")
        
        # Check for cycles
        if self._has_cycle():
            errors.append("Task graph contains a cycle")
        
        return errors
    
    def _has_cycle(self) -> bool:
        """Detect cycles using DFS."""
        visited = set()
        rec_stack = set()
        
        def dfs(task_id: str) -> bool:
            visited.add(task_id)
            rec_stack.add(task_id)
            
            node = self._nodes.get(task_id)
            if node:
                for dep_id in node.dependents:
                    if dep_id not in visited:
                        if dfs(dep_id):
                            return True
                    elif dep_id in rec_stack:
                        return True
            
            rec_stack.remove(task_id)
            return False
        
        for task_id in self._nodes:
            if task_id not in visited:
                if dfs(task_id):
                    return True
        
        return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get graph execution statistics."""
        states = defaultdict(int)
        for node in self._nodes.values():
            states[node.state.value] += 1
        
        durations = [n.duration_ms for n in self._nodes.values() if n.duration_ms]
        
        return {
            "graph_id": self.graph_id,
            "total_tasks": len(self._nodes),
            "states": dict(states),
            "completed": len(self._completed),
            "failed": len(self._failed),
            "avg_duration_ms": sum(durations) / len(durations) if durations else 0,
        }


class ConcurrencyManager:
    """
    Manages concurrent task execution with:
    - Configurable parallelism
    - Task graph execution
    - Resource limits
    - Graceful shutdown
    """
    
    def __init__(
        self,
        max_concurrent: int = 10,
        max_pending: int = 1000,
    ):
        self.max_concurrent = max_concurrent
        self.max_pending = max_pending
        
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._pending_graphs: Dict[str, TaskGraph] = {}
        
        self._running = False
        self._shutdown_event = asyncio.Event()
        
        # Metrics
        self._total_tasks_executed = 0
        self._total_tasks_failed = 0
    
    async def start(self) -> None:
        """Start the concurrency manager."""
        self._running = True
        self._shutdown_event.clear()
    
    async def stop(self, timeout: float = 30.0) -> None:
        """Gracefully stop all running tasks."""
        self._running = False
        self._shutdown_event.set()
        
        if self._running_tasks:
            # Wait for running tasks with timeout
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._running_tasks.values(), return_exceptions=True),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                # Cancel remaining tasks
                for task in self._running_tasks.values():
                    task.cancel()
    
    async def execute_task(self, node: TaskNode) -> Any:
        """Execute a single task with concurrency control."""
        async with self._semaphore:
            if not self._running:
                raise RuntimeError("ConcurrencyManager is stopped")
            
            node.state = TaskState.RUNNING
            node.started_at = time.time()
            
            task_id = node.task_id
            
            try:
                # Create task with timeout
                coro = node.handler(*node.args, **node.kwargs)
                result = await asyncio.wait_for(coro, timeout=node.timeout_seconds)
                
                node.state = TaskState.COMPLETED
                node.result = result
                node.completed_at = time.time()
                
                self._total_tasks_executed += 1
                return result
                
            except asyncio.TimeoutError:
                node.state = TaskState.FAILED
                node.error = TimeoutError(f"Task {node.name} timed out")
                node.completed_at = time.time()
                self._total_tasks_failed += 1
                raise node.error
                
            except Exception as e:
                node.state = TaskState.FAILED
                node.error = e
                node.completed_at = time.time()
                self._total_tasks_failed += 1
                raise
    
    async def execute_graph(self, graph: TaskGraph) -> Dict[str, Any]:
        """Execute a task graph respecting dependencies."""
        errors = graph.validate()
        if errors:
            raise ValueError(f"Invalid task graph: {errors}")
        
        self._pending_graphs[graph.graph_id] = graph
        
        try:
            while not graph.is_complete():
                if not self._running:
                    break
                
                # Get ready tasks
                ready_tasks = graph.get_ready_tasks()
                
                if not ready_tasks:
                    # No tasks ready - wait for running tasks
                    if self._running_tasks:
                        await asyncio.sleep(0.1)
                        continue
                    else:
                        # Deadlock or all blocked
                        break
                
                # Execute ready tasks concurrently
                tasks = []
                for node in ready_tasks:
                    task = asyncio.create_task(self._execute_graph_task(graph, node))
                    self._running_tasks[node.task_id] = task
                    tasks.append(task)
                
                # Wait for at least one to complete
                if tasks:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    
                    # Cleanup completed
                    for task in done:
                        for task_id, t in list(self._running_tasks.items()):
                            if t == task:
                                del self._running_tasks[task_id]
                                break
            
            return graph.get_results()
            
        finally:
            del self._pending_graphs[graph.graph_id]
    
    async def _execute_graph_task(self, graph: TaskGraph, node: TaskNode) -> None:
        """Execute a task within a graph context."""
        try:
            result = await self.execute_task(node)
            graph.mark_completed(node.task_id, result)
        except Exception as e:
            graph.mark_failed(node.task_id, e)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get concurrency manager statistics."""
        return {
            "running": self._running,
            "max_concurrent": self.max_concurrent,
            "current_running": len(self._running_tasks),
            "pending_graphs": len(self._pending_graphs),
            "total_executed": self._total_tasks_executed,
            "total_failed": self._total_tasks_failed,
            "success_rate": (
                self._total_tasks_executed / 
                max(1, self._total_tasks_executed + self._total_tasks_failed)
            ),
        }
