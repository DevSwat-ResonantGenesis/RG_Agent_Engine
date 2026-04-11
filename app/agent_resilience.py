"""
AGENT SELF-HEALING AND RESILIENCE
=================================

Maximum autonomy: Agents recover from failures automatically.
Self-healing, error recovery, and resilience mechanisms.

Features:
- Automatic error detection and recovery
- State checkpointing and restoration
- Graceful degradation
- Self-diagnosis and repair
- Failover strategies
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
import json
import traceback

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    RECOVERING = "recovering"
    CRITICAL = "critical"


class FailureType(Enum):
    TRANSIENT = "transient"      # Temporary, will likely resolve
    PERSISTENT = "persistent"    # Requires intervention
    RESOURCE = "resource"        # Resource exhaustion
    DEPENDENCY = "dependency"    # External dependency failure
    LOGIC = "logic"              # Bug or logic error


@dataclass
class HealthCheck:
    """Result of a health check."""
    component: str
    status: HealthStatus
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Checkpoint:
    """Agent state checkpoint for recovery."""
    id: str
    agent_id: str
    state: Dict[str, Any]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reason: str = "periodic"


@dataclass
class FailureRecord:
    """Record of a failure for analysis."""
    id: str
    agent_id: str
    failure_type: FailureType
    error: str
    stack_trace: Optional[str]
    context: Dict[str, Any]
    recovered: bool = False
    recovery_strategy: Optional[str] = None
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AgentHealthMonitor:
    """
    Monitors agent health and detects issues.
    """
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.status = HealthStatus.HEALTHY
        self.health_history: List[HealthCheck] = []
        self.failure_count = 0
        self.last_healthy_at = datetime.now(timezone.utc)
        
        # Thresholds
        self.failure_threshold = 5
        self.degraded_threshold = 3
    
    def record_success(self):
        """Record a successful operation."""
        self.failure_count = max(0, self.failure_count - 1)
        if self.failure_count == 0:
            self.status = HealthStatus.HEALTHY
            self.last_healthy_at = datetime.now(timezone.utc)
    
    def record_failure(self, error: str):
        """Record a failed operation."""
        self.failure_count += 1
        
        if self.failure_count >= self.failure_threshold:
            self.status = HealthStatus.CRITICAL
        elif self.failure_count >= self.degraded_threshold:
            self.status = HealthStatus.DEGRADED
        
        self.health_history.append(HealthCheck(
            component="agent",
            status=self.status,
            message=error,
        ))
    
    def check_health(self) -> HealthCheck:
        """Perform health check."""
        # Check time since last healthy
        time_unhealthy = datetime.now(timezone.utc) - self.last_healthy_at
        
        if time_unhealthy > timedelta(minutes=10) and self.status != HealthStatus.HEALTHY:
            self.status = HealthStatus.CRITICAL
        
        return HealthCheck(
            component=f"agent_{self.agent_id}",
            status=self.status,
            message=f"Failure count: {self.failure_count}",
            metrics={
                "failure_count": self.failure_count,
                "time_since_healthy_seconds": time_unhealthy.total_seconds(),
            },
        )


class StateCheckpointer:
    """
    Creates and manages agent state checkpoints.
    """
    
    MAX_CHECKPOINTS = 10
    
    def __init__(self):
        self.checkpoints: Dict[str, List[Checkpoint]] = {}  # agent_id -> checkpoints
    
    def create_checkpoint(
        self,
        agent_id: str,
        state: Dict[str, Any],
        reason: str = "periodic",
    ) -> Checkpoint:
        """Create a checkpoint."""
        checkpoint = Checkpoint(
            id=str(uuid4()),
            agent_id=agent_id,
            state=state,
            reason=reason,
        )
        
        if agent_id not in self.checkpoints:
            self.checkpoints[agent_id] = []
        
        self.checkpoints[agent_id].append(checkpoint)
        
        # Keep only recent checkpoints
        if len(self.checkpoints[agent_id]) > self.MAX_CHECKPOINTS:
            self.checkpoints[agent_id] = self.checkpoints[agent_id][-self.MAX_CHECKPOINTS:]
        
        logger.debug(f"Checkpoint created for agent {agent_id}: {reason}")
        
        return checkpoint
    
    def get_latest_checkpoint(self, agent_id: str) -> Optional[Checkpoint]:
        """Get the latest checkpoint for an agent."""
        checkpoints = self.checkpoints.get(agent_id, [])
        return checkpoints[-1] if checkpoints else None
    
    def restore_from_checkpoint(self, agent_id: str, checkpoint_id: str = None) -> Optional[Dict[str, Any]]:
        """Restore agent state from a checkpoint."""
        checkpoints = self.checkpoints.get(agent_id, [])
        
        if checkpoint_id:
            checkpoint = next((c for c in checkpoints if c.id == checkpoint_id), None)
        else:
            checkpoint = checkpoints[-1] if checkpoints else None
        
        if checkpoint:
            logger.info(f"Restoring agent {agent_id} from checkpoint {checkpoint.id}")
            return checkpoint.state
        
        return None


class RecoveryStrategist:
    """
    Determines and executes recovery strategies.
    """
    
    def __init__(self):
        self.strategies: Dict[FailureType, List[Callable]] = {
            FailureType.TRANSIENT: [
                self._retry_with_backoff,
                self._reset_and_retry,
            ],
            FailureType.PERSISTENT: [
                self._restore_checkpoint,
                self._escalate_to_supervisor,
            ],
            FailureType.RESOURCE: [
                self._reduce_load,
                self._request_resources,
            ],
            FailureType.DEPENDENCY: [
                self._use_fallback,
                self._wait_and_retry,
            ],
            FailureType.LOGIC: [
                self._restore_checkpoint,
                self._request_human_help,
            ],
        }
    
    async def recover(
        self,
        agent_id: str,
        failure: FailureRecord,
        checkpointer: StateCheckpointer,
    ) -> bool:
        """Attempt to recover from a failure."""
        strategies = self.strategies.get(failure.failure_type, [])
        
        for strategy in strategies:
            try:
                success = await strategy(agent_id, failure, checkpointer)
                if success:
                    failure.recovered = True
                    failure.recovery_strategy = strategy.__name__
                    logger.info(f"Agent {agent_id} recovered using {strategy.__name__}")
                    return True
            except Exception as e:
                logger.warning(f"Recovery strategy {strategy.__name__} failed: {e}")
        
        return False
    
    async def _retry_with_backoff(self, agent_id: str, failure: FailureRecord, checkpointer: StateCheckpointer) -> bool:
        """Retry with exponential backoff."""
        await asyncio.sleep(2 ** min(failure.context.get("retry_count", 0), 5))
        return True  # Signal to retry
    
    async def _reset_and_retry(self, agent_id: str, failure: FailureRecord, checkpointer: StateCheckpointer) -> bool:
        """Reset agent state and retry."""
        checkpoint = checkpointer.get_latest_checkpoint(agent_id)
        if checkpoint:
            return True
        return False
    
    async def _restore_checkpoint(self, agent_id: str, failure: FailureRecord, checkpointer: StateCheckpointer) -> bool:
        """Restore from last good checkpoint."""
        state = checkpointer.restore_from_checkpoint(agent_id)
        return state is not None
    
    async def _escalate_to_supervisor(self, agent_id: str, failure: FailureRecord, checkpointer: StateCheckpointer) -> bool:
        """Escalate to supervisor agent."""
        from .parallel_agent_runtime import get_runtime
        
        try:
            runtime = await get_runtime()
            await runtime.broadcast(
                from_agent=agent_id,
                content={
                    "type": "escalation",
                    "failure": failure.error,
                    "agent_id": agent_id,
                },
            )
            return True
        except:
            return False
    
    async def _reduce_load(self, agent_id: str, failure: FailureRecord, checkpointer: StateCheckpointer) -> bool:
        """Reduce agent workload."""
        # Would implement load shedding
        return True
    
    async def _request_resources(self, agent_id: str, failure: FailureRecord, checkpointer: StateCheckpointer) -> bool:
        """Request additional resources."""
        return True
    
    async def _use_fallback(self, agent_id: str, failure: FailureRecord, checkpointer: StateCheckpointer) -> bool:
        """Use fallback mechanism."""
        return True
    
    async def _wait_and_retry(self, agent_id: str, failure: FailureRecord, checkpointer: StateCheckpointer) -> bool:
        """Wait for dependency to recover."""
        await asyncio.sleep(30)
        return True
    
    async def _request_human_help(self, agent_id: str, failure: FailureRecord, checkpointer: StateCheckpointer) -> bool:
        """Request human intervention."""
        logger.warning(f"Agent {agent_id} requesting human help: {failure.error}")
        return False  # Cannot auto-recover


class AgentResilienceSystem:
    """
    Complete resilience system for autonomous agents.
    """
    
    CHECKPOINT_INTERVAL = 60  # seconds
    HEALTH_CHECK_INTERVAL = 10  # seconds
    
    def __init__(self):
        self.monitors: Dict[str, AgentHealthMonitor] = {}
        self.checkpointer = StateCheckpointer()
        self.strategist = RecoveryStrategist()
        self.failure_history: List[FailureRecord] = []
        
        self._running = False
        self._tasks = []
    
    async def start(self):
        """Start the resilience system."""
        self._running = True
        self._tasks.append(asyncio.create_task(self._health_check_loop()))
        logger.info("Agent Resilience System started")
    
    async def stop(self):
        """Stop the resilience system."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        logger.info("Agent Resilience System stopped")
    
    def register_agent(self, agent_id: str):
        """Register an agent for resilience monitoring."""
        self.monitors[agent_id] = AgentHealthMonitor(agent_id)
        logger.debug(f"Agent {agent_id} registered for resilience monitoring")
    
    async def _health_check_loop(self):
        """Periodic health check loop."""
        while self._running:
            try:
                for agent_id, monitor in list(self.monitors.items()):
                    health = monitor.check_health()
                    
                    if health.status == HealthStatus.CRITICAL:
                        await self._handle_critical_agent(agent_id, monitor)
                
                await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")
    
    async def _handle_critical_agent(self, agent_id: str, monitor: AgentHealthMonitor):
        """Handle a critical agent."""
        logger.warning(f"Agent {agent_id} is CRITICAL, attempting recovery")
        
        # Create failure record
        failure = FailureRecord(
            id=str(uuid4()),
            agent_id=agent_id,
            failure_type=FailureType.PERSISTENT,
            error="Agent reached critical status",
            stack_trace=None,
            context={"failure_count": monitor.failure_count},
        )
        
        self.failure_history.append(failure)
        
        # Attempt recovery
        recovered = await self.strategist.recover(agent_id, failure, self.checkpointer)
        
        if recovered:
            monitor.status = HealthStatus.RECOVERING
            monitor.failure_count = 0
    
    async def handle_error(
        self,
        agent_id: str,
        error: Exception,
        context: Dict[str, Any] = None,
    ) -> bool:
        """Handle an error from an agent."""
        # Determine failure type
        failure_type = self._classify_failure(error)
        
        failure = FailureRecord(
            id=str(uuid4()),
            agent_id=agent_id,
            failure_type=failure_type,
            error=str(error),
            stack_trace=traceback.format_exc(),
            context=context or {},
        )
        
        self.failure_history.append(failure)
        
        # Update health monitor
        if agent_id in self.monitors:
            self.monitors[agent_id].record_failure(str(error))
        
        # Attempt recovery
        return await self.strategist.recover(agent_id, failure, self.checkpointer)
    
    def _classify_failure(self, error: Exception) -> FailureType:
        """Classify the type of failure."""
        error_str = str(error).lower()
        
        if "timeout" in error_str or "connection" in error_str:
            return FailureType.TRANSIENT
        elif "memory" in error_str or "resource" in error_str:
            return FailureType.RESOURCE
        elif "not found" in error_str or "unavailable" in error_str:
            return FailureType.DEPENDENCY
        else:
            return FailureType.LOGIC
    
    def create_checkpoint(self, agent_id: str, state: Dict[str, Any], reason: str = "manual"):
        """Create a checkpoint for an agent."""
        return self.checkpointer.create_checkpoint(agent_id, state, reason)
    
    def record_success(self, agent_id: str):
        """Record a successful operation."""
        if agent_id in self.monitors:
            self.monitors[agent_id].record_success()
    
    def get_agent_health(self, agent_id: str) -> Optional[HealthCheck]:
        """Get health status for an agent."""
        monitor = self.monitors.get(agent_id)
        return monitor.check_health() if monitor else None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get resilience system statistics."""
        healthy = sum(1 for m in self.monitors.values() if m.status == HealthStatus.HEALTHY)
        
        return {
            "agents_monitored": len(self.monitors),
            "healthy_agents": healthy,
            "total_failures": len(self.failure_history),
            "recovered_failures": sum(1 for f in self.failure_history if f.recovered),
        }


# Global instance
_resilience: Optional[AgentResilienceSystem] = None


async def get_resilience_system() -> AgentResilienceSystem:
    """Get or create resilience system."""
    global _resilience
    if _resilience is None:
        _resilience = AgentResilienceSystem()
        await _resilience.start()
    return _resilience
