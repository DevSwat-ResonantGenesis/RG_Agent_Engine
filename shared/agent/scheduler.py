"""
Deterministic scheduler for Agent Engine.
Ensures reproducible task execution ordering.
"""

import asyncio
import heapq
import time
import hashlib
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable, Awaitable
from enum import Enum
import uuid


class TaskPriority(Enum):
    CRITICAL = 0
    HIGH = 25
    NORMAL = 50
    LOW = 75
    BACKGROUND = 100


class SchedulingPolicy(Enum):
    FIFO = "fifo"                   # First in, first out
    PRIORITY = "priority"           # Priority-based
    FAIR = "fair"                   # Fair scheduling across users
    DEADLINE = "deadline"           # Earliest deadline first


@dataclass
class SchedulerConfig:
    """Configuration for deterministic scheduling."""
    policy: SchedulingPolicy = SchedulingPolicy.PRIORITY
    
    # Determinism settings
    deterministic: bool = True
    seed: Optional[int] = None
    
    # Execution limits
    max_concurrent: int = 10
    max_queue_size: int = 10000
    default_timeout_seconds: float = 300.0
    
    # Fair scheduling
    user_quota_per_minute: int = 100
    
    # Run-to-completion
    run_to_completion: bool = True      # Don't preempt running tasks
    max_task_duration: float = 3600.0   # 1 hour max
    
    # Retry policy
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    retry_backoff_multiplier: float = 2.0


@dataclass(order=True)
class ScheduledTask:
    """Task in the scheduler queue."""
    sort_key: tuple = field(compare=True)
    task_id: str = field(compare=False)
    name: str = field(compare=False)
    handler: Callable[..., Awaitable[Any]] = field(compare=False)
    args: tuple = field(default_factory=tuple, compare=False)
    kwargs: Dict[str, Any] = field(default_factory=dict, compare=False)
    
    # Scheduling metadata
    priority: TaskPriority = field(default=TaskPriority.NORMAL, compare=False)
    user_id: Optional[str] = field(default=None, compare=False)
    deadline: Optional[float] = field(default=None, compare=False)
    
    # State
    enqueued_at: float = field(default_factory=time.time, compare=False)
    started_at: Optional[float] = field(default=None, compare=False)
    completed_at: Optional[float] = field(default=None, compare=False)
    
    # Retry tracking
    attempt: int = field(default=0, compare=False)
    last_error: Optional[str] = field(default=None, compare=False)
    
    @classmethod
    def create(
        cls,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        priority: TaskPriority = TaskPriority.NORMAL,
        user_id: Optional[str] = None,
        deadline: Optional[float] = None,
        args: tuple = (),
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> "ScheduledTask":
        """Create a new scheduled task with deterministic ordering."""
        task_id = str(uuid.uuid4())
        enqueued_at = time.time()
        
        # Create deterministic sort key
        # (priority, deadline or max, enqueue time, task_id for tie-breaking)
        sort_key = (
            priority.value,
            deadline or float('inf'),
            enqueued_at,
            task_id,
        )
        
        return cls(
            sort_key=sort_key,
            task_id=task_id,
            name=name,
            handler=handler,
            args=args,
            kwargs=kwargs or {},
            priority=priority,
            user_id=user_id,
            deadline=deadline,
            enqueued_at=enqueued_at,
        )


class DeterministicScheduler:
    """
    Deterministic task scheduler with:
    - Reproducible ordering
    - Priority-based scheduling
    - Fair user quotas
    - Run-to-completion semantics
    - Deadline support
    """
    
    def __init__(self, config: Optional[SchedulerConfig] = None):
        self.config = config or SchedulerConfig()
        
        self._queue: List[ScheduledTask] = []
        self._running: Dict[str, ScheduledTask] = {}
        self._completed: Dict[str, ScheduledTask] = {}
        self._failed: Dict[str, ScheduledTask] = {}
        
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        self._user_counts: Dict[str, int] = {}
        self._user_last_reset: Dict[str, float] = {}
        
        self._is_running = False
        self._scheduler_task: Optional[asyncio.Task] = None
        
        # Metrics
        self._total_scheduled = 0
        self._total_completed = 0
        self._total_failed = 0
        self._total_retried = 0
    
    async def start(self) -> None:
        """Start the scheduler."""
        self._is_running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
    
    async def stop(self, timeout: float = 30.0) -> None:
        """Stop the scheduler gracefully."""
        self._is_running = False
        
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await asyncio.wait_for(self._scheduler_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        
        # Wait for running tasks
        if self._running:
            await asyncio.sleep(min(timeout, 5.0))
    
    def schedule(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        priority: TaskPriority = TaskPriority.NORMAL,
        user_id: Optional[str] = None,
        deadline: Optional[float] = None,
        args: tuple = (),
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Schedule a task for execution."""
        if len(self._queue) >= self.config.max_queue_size:
            raise RuntimeError("Scheduler queue is full")
        
        task = ScheduledTask.create(
            name=name,
            handler=handler,
            priority=priority,
            user_id=user_id,
            deadline=deadline,
            args=args,
            kwargs=kwargs,
        )
        
        heapq.heappush(self._queue, task)
        self._total_scheduled += 1
        
        return task.task_id
    
    async def _scheduler_loop(self) -> None:
        """Main scheduler loop."""
        while self._is_running:
            try:
                # Check for ready tasks
                if self._queue and self._semaphore._value > 0:
                    task = self._get_next_task()
                    if task:
                        asyncio.create_task(self._execute_task(task))
                
                await asyncio.sleep(0.01)  # Small delay to prevent busy loop
                
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.1)
    
    def _get_next_task(self) -> Optional[ScheduledTask]:
        """Get the next task to execute based on policy."""
        if not self._queue:
            return None
        
        if self.config.policy == SchedulingPolicy.FIFO:
            return heapq.heappop(self._queue)
        
        elif self.config.policy == SchedulingPolicy.PRIORITY:
            return heapq.heappop(self._queue)
        
        elif self.config.policy == SchedulingPolicy.FAIR:
            # Find task from user with lowest recent usage
            return self._get_fair_task()
        
        elif self.config.policy == SchedulingPolicy.DEADLINE:
            # Already sorted by deadline in sort_key
            return heapq.heappop(self._queue)
        
        return heapq.heappop(self._queue)
    
    def _get_fair_task(self) -> Optional[ScheduledTask]:
        """Get task using fair scheduling across users."""
        now = time.time()
        
        # Reset user counts every minute
        for user_id in list(self._user_last_reset.keys()):
            if now - self._user_last_reset[user_id] > 60:
                self._user_counts[user_id] = 0
                self._user_last_reset[user_id] = now
        
        # Find task from user under quota
        for i, task in enumerate(self._queue):
            user_id = task.user_id or "_anonymous"
            count = self._user_counts.get(user_id, 0)
            
            if count < self.config.user_quota_per_minute:
                # Remove from queue
                self._queue.pop(i)
                heapq.heapify(self._queue)
                
                # Update count
                self._user_counts[user_id] = count + 1
                if user_id not in self._user_last_reset:
                    self._user_last_reset[user_id] = now
                
                return task
        
        # All users over quota, just take next
        return heapq.heappop(self._queue) if self._queue else None
    
    async def _execute_task(self, task: ScheduledTask) -> None:
        """Execute a scheduled task."""
        async with self._semaphore:
            task.started_at = time.time()
            task.attempt += 1
            self._running[task.task_id] = task
            
            try:
                # Execute with timeout
                timeout = self.config.default_timeout_seconds
                if task.deadline:
                    timeout = min(timeout, task.deadline - time.time())
                
                if timeout <= 0:
                    raise TimeoutError("Task deadline passed")
                
                await asyncio.wait_for(
                    task.handler(*task.args, **task.kwargs),
                    timeout=min(timeout, self.config.max_task_duration),
                )
                
                task.completed_at = time.time()
                self._completed[task.task_id] = task
                self._total_completed += 1
                
            except Exception as e:
                task.last_error = str(e)
                
                # Check for retry
                if task.attempt < self.config.max_retries:
                    self._total_retried += 1
                    delay = self.config.retry_delay_seconds * (
                        self.config.retry_backoff_multiplier ** (task.attempt - 1)
                    )
                    await asyncio.sleep(delay)
                    
                    # Re-enqueue
                    heapq.heappush(self._queue, task)
                else:
                    task.completed_at = time.time()
                    self._failed[task.task_id] = task
                    self._total_failed += 1
            
            finally:
                self._running.pop(task.task_id, None)
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a task."""
        # Check running
        if task_id in self._running:
            task = self._running[task_id]
            return {"status": "running", "started_at": task.started_at}
        
        # Check completed
        if task_id in self._completed:
            task = self._completed[task_id]
            return {
                "status": "completed",
                "started_at": task.started_at,
                "completed_at": task.completed_at,
            }
        
        # Check failed
        if task_id in self._failed:
            task = self._failed[task_id]
            return {
                "status": "failed",
                "error": task.last_error,
                "attempts": task.attempt,
            }
        
        # Check queue
        for task in self._queue:
            if task.task_id == task_id:
                return {"status": "queued", "enqueued_at": task.enqueued_at}
        
        return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get scheduler statistics."""
        return {
            "is_running": self._is_running,
            "policy": self.config.policy.value,
            "queue_size": len(self._queue),
            "running_count": len(self._running),
            "completed_count": len(self._completed),
            "failed_count": len(self._failed),
            "total_scheduled": self._total_scheduled,
            "total_completed": self._total_completed,
            "total_failed": self._total_failed,
            "total_retried": self._total_retried,
            "success_rate": (
                self._total_completed /
                max(1, self._total_completed + self._total_failed)
            ),
        }
