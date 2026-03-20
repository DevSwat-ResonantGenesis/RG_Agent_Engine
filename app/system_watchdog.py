"""
SYSTEM WATCHDOG
===============

Monitors all autonomous systems and ensures continuous operation.
Auto-recovers from failures, detects anomalies, and maintains health.

Features:
- Health monitoring for all subsystems
- Automatic restart on failure
- Anomaly detection
- Performance tracking
- Alert system
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from enum import Enum
from sqlalchemy import select

logger = logging.getLogger(__name__)


class HealthLevel(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    FAILED = "failed"


@dataclass
class SubsystemHealth:
    """Health status of a subsystem."""
    name: str
    level: HealthLevel
    last_check: str
    message: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    consecutive_failures: int = 0


@dataclass
class Alert:
    """System alert."""
    id: str
    severity: str  # info, warning, critical
    subsystem: str
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    acknowledged: bool = False


class HealthChecker:
    """Performs health checks on subsystems."""
    
    async def check_brain_manager(self) -> SubsystemHealth:
        """Check brain manager health."""
        try:
            from .agent_brain import get_brain_manager
            mgr = await get_brain_manager()
            statuses = mgr.get_all_statuses()
            
            running = sum(1 for s in statuses if s.get("running"))
            
            return SubsystemHealth(
                name="brain_manager",
                level=HealthLevel.HEALTHY if running > 0 else HealthLevel.DEGRADED,
                last_check=datetime.now(timezone.utc).isoformat(),
                metrics={"active_brains": len(statuses), "running": running},
            )
        except Exception as e:
            return SubsystemHealth(
                name="brain_manager",
                level=HealthLevel.FAILED,
                last_check=datetime.now(timezone.utc).isoformat(),
                message=str(e),
            )
    
    async def check_autonomous_queue(self) -> SubsystemHealth:
        """Check autonomous queue health."""
        try:
            from .autonomous_queue import get_autonomous_queue
            queue = await get_autonomous_queue()
            stats = queue.get_stats()
            
            pending = stats.get("pending_tasks", 0)
            agents = stats.get("registered_agents", 0)
            
            level = HealthLevel.HEALTHY
            if agents == 0:
                level = HealthLevel.DEGRADED
            
            return SubsystemHealth(
                name="autonomous_queue",
                level=level,
                last_check=datetime.now(timezone.utc).isoformat(),
                metrics=stats,
            )
        except Exception as e:
            return SubsystemHealth(
                name="autonomous_queue",
                level=HealthLevel.FAILED,
                last_check=datetime.now(timezone.utc).isoformat(),
                message=str(e),
            )
    
    async def check_agent_network(self) -> SubsystemHealth:
        """Check agent network health."""
        try:
            from .agent_network import get_agent_network
            network = await get_agent_network()
            stats = network.get_stats()
            
            total = stats.get("total_agents", 0)
            active = stats.get("active_agents", 0)
            
            level = HealthLevel.HEALTHY
            if total == 0:
                level = HealthLevel.DEGRADED
            elif active < total * 0.5:
                level = HealthLevel.DEGRADED
            
            return SubsystemHealth(
                name="agent_network",
                level=level,
                last_check=datetime.now(timezone.utc).isoformat(),
                metrics=stats,
            )
        except Exception as e:
            return SubsystemHealth(
                name="agent_network",
                level=HealthLevel.FAILED,
                last_check=datetime.now(timezone.utc).isoformat(),
                message=str(e),
            )
    
    async def check_goal_pursuit(self) -> SubsystemHealth:
        """Check goal pursuit engine health."""
        try:
            from .goal_pursuit import get_goal_pursuit_engine
            engine = await get_goal_pursuit_engine()
            stats = engine.get_stats()
            
            return SubsystemHealth(
                name="goal_pursuit",
                level=HealthLevel.HEALTHY,
                last_check=datetime.now(timezone.utc).isoformat(),
                metrics=stats,
            )
        except Exception as e:
            return SubsystemHealth(
                name="goal_pursuit",
                level=HealthLevel.FAILED,
                last_check=datetime.now(timezone.utc).isoformat(),
                message=str(e),
            )
    
    async def check_all(self) -> Dict[str, SubsystemHealth]:
        """Run all health checks."""
        checks = await asyncio.gather(
            self.check_brain_manager(),
            self.check_autonomous_queue(),
            self.check_agent_network(),
            self.check_goal_pursuit(),
            return_exceptions=True,
        )
        
        results = {}
        names = ["brain_manager", "autonomous_queue", "agent_network", "goal_pursuit"]
        
        for name, check in zip(names, checks):
            if isinstance(check, Exception):
                results[name] = SubsystemHealth(
                    name=name,
                    level=HealthLevel.FAILED,
                    last_check=datetime.now(timezone.utc).isoformat(),
                    message=str(check),
                )
            else:
                results[name] = check
        
        return results


class SystemWatchdog:
    """
    Monitors system health and auto-recovers from failures.
    """
    
    CHECK_INTERVAL = 30  # seconds
    MAX_CONSECUTIVE_FAILURES = 3
    
    def __init__(self):
        self.checker = HealthChecker()
        self.health_status: Dict[str, SubsystemHealth] = {}
        self.alerts: List[Alert] = []
        
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start the watchdog."""
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("System Watchdog started")
    
    async def stop(self):
        """Stop the watchdog."""
        self._running = False
        if self._task:
            self._task.cancel()
    
    async def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                # Run health checks
                self.health_status = await self.checker.check_all()
                
                # Process results
                for name, health in self.health_status.items():
                    await self._process_health(health)
                
                await asyncio.sleep(self.CHECK_INTERVAL)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Watchdog error: {e}")
    
    async def _process_health(self, health: SubsystemHealth):
        """Process health check result."""
        if health.level == HealthLevel.FAILED:
            health.consecutive_failures += 1
            
            if health.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                await self._create_alert(
                    severity="critical",
                    subsystem=health.name,
                    message=f"Subsystem {health.name} has failed {health.consecutive_failures} times",
                )
                await self._attempt_recovery(health.name)
        
        elif health.level == HealthLevel.DEGRADED:
            await self._create_alert(
                severity="warning",
                subsystem=health.name,
                message=f"Subsystem {health.name} is degraded",
            )
        
        else:
            health.consecutive_failures = 0
    
    async def _create_alert(self, severity: str, subsystem: str, message: str):
        """Create an alert."""
        from uuid import uuid4
        
        alert = Alert(
            id=str(uuid4()),
            severity=severity,
            subsystem=subsystem,
            message=message,
        )
        
        self.alerts.append(alert)
        
        # Keep only recent alerts
        if len(self.alerts) > 100:
            self.alerts = self.alerts[-50:]
        
        logger.warning(f"ALERT [{severity}] {subsystem}: {message}")
        
        # Phase 3.4: Fire anomaly-triggered agent workflows
        await self._fire_anomaly_triggers(subsystem, severity, message)
    
    async def _fire_anomaly_triggers(self, subsystem: str, severity: str, message: str):
        """Fire anomaly-triggered agent workflows matching this alert."""
        try:
            from .routers import _anomaly_triggers, agent_executor
            from .db import async_session as async_session_factory
            from .models import AgentDefinition
            import time

            for tid, cfg in _anomaly_triggers.items():
                if not cfg.get("enabled"):
                    continue
                if cfg.get("subsystem") and cfg["subsystem"] != subsystem:
                    continue
                if cfg.get("severity") and cfg["severity"] != severity:
                    continue

                last = cfg.get("last_fired_at")
                if last and (time.time() - last) < cfg.get("cooldown_seconds", 300):
                    continue

                async with async_session_factory() as db_session:
                    result = await db_session.execute(
                        select(AgentDefinition).where(AgentDefinition.id == cfg["agent_id"])
                    )
                    agent = result.scalar_one_or_none()
                    if not agent:
                        continue

                    goal = cfg.get("goal_template", "Investigate anomaly: {message}").format(
                        subsystem=subsystem, severity=severity, message=message,
                    )

                    await agent_executor.start_session(
                        agent=agent,
                        goal=goal,
                        initial_context={
                            "anomaly_subsystem": subsystem,
                            "anomaly_severity": severity,
                            "anomaly_message": message,
                        },
                        user_id=cfg.get("created_by", "system"),
                        db_session=db_session,
                    )

                    cfg["last_fired_at"] = time.time()
                    cfg["fire_count"] = cfg.get("fire_count", 0) + 1
                    logger.info(f"[ANOMALY] Fired trigger {tid} for {subsystem}/{severity}")

        except Exception as e:
            logger.warning(f"[ANOMALY] Failed to fire triggers: {e}")

    async def _attempt_recovery(self, subsystem: str):
        """Attempt to recover a failed subsystem."""
        logger.info(f"Attempting recovery of {subsystem}")
        
        try:
            if subsystem == "brain_manager":
                from .agent_brain import get_brain_manager
                await get_brain_manager()
            
            elif subsystem == "autonomous_queue":
                from .autonomous_queue import get_autonomous_queue
                await get_autonomous_queue()
            
            elif subsystem == "agent_network":
                from .agent_network import get_agent_network
                await get_agent_network()
            
            elif subsystem == "goal_pursuit":
                from .goal_pursuit import get_goal_pursuit_engine
                await get_goal_pursuit_engine()
            
            logger.info(f"Recovery of {subsystem} successful")
            
        except Exception as e:
            logger.error(f"Recovery of {subsystem} failed: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get watchdog status."""
        healthy = sum(1 for h in self.health_status.values() if h.level == HealthLevel.HEALTHY)
        
        return {
            "running": self._running,
            "subsystems": {
                name: {
                    "level": health.level.value,
                    "last_check": health.last_check,
                    "failures": health.consecutive_failures,
                }
                for name, health in self.health_status.items()
            },
            "healthy_count": healthy,
            "total_count": len(self.health_status),
            "recent_alerts": len([a for a in self.alerts if not a.acknowledged]),
        }
    
    def get_alerts(self, unacknowledged_only: bool = False) -> List[Dict[str, Any]]:
        """Get alerts."""
        alerts = self.alerts
        if unacknowledged_only:
            alerts = [a for a in alerts if not a.acknowledged]
        
        return [
            {
                "id": a.id,
                "severity": a.severity,
                "subsystem": a.subsystem,
                "message": a.message,
                "timestamp": a.timestamp,
                "acknowledged": a.acknowledged,
            }
            for a in alerts[-50:]
        ]
    
    def acknowledge_alert(self, alert_id: str) -> bool:
        """Acknowledge an alert."""
        for alert in self.alerts:
            if alert.id == alert_id:
                alert.acknowledged = True
                return True
        return False


# Global instance
_watchdog: Optional[SystemWatchdog] = None


async def get_watchdog() -> SystemWatchdog:
    """Get or create watchdog."""
    global _watchdog
    if _watchdog is None:
        _watchdog = SystemWatchdog()
        await _watchdog.start()
    return _watchdog
