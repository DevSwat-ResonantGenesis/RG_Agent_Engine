"""
SELF-TRIGGER SYSTEM - AGENT-CONTROLLED TIMING
==============================================

TRUE AUTONOMY COMPONENT: Agent decides WHEN to act

Replaces daemon-controlled timing with agent-controlled timing.
The agent decides its own think intervals based on:
- Current goal urgency
- Resource levels
- Opportunity detection
- Internal motivation

This is the difference between orchestration and autonomy.
"""

import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4

logger = logging.getLogger(__name__)


class TriggerReason(str, Enum):
    GOAL_URGENCY = "goal_urgency"
    OPPORTUNITY = "opportunity"
    MESSAGE_RECEIVED = "message_received"
    DEADLINE_APPROACHING = "deadline_approaching"
    RESOURCE_CRITICAL = "resource_critical"
    CURIOSITY = "curiosity"
    SCHEDULED = "scheduled"
    SELF_REFLECTION = "self_reflection"
    ENVIRONMENT_CHANGE = "environment_change"


class ActivityLevel(str, Enum):
    HYPERACTIVE = "hyperactive"   # Think every 1-5 seconds
    ACTIVE = "active"             # Think every 5-30 seconds
    NORMAL = "normal"             # Think every 30-120 seconds
    RELAXED = "relaxed"           # Think every 2-10 minutes
    DORMANT = "dormant"           # Think every 10-60 minutes
    HIBERNATING = "hibernating"   # Think every hour+


@dataclass
class TriggerDecision:
    """Decision about when to trigger next action."""
    should_act_now: bool
    next_trigger_seconds: float
    reason: TriggerReason
    activity_level: ActivityLevel
    confidence: float
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class InternalState:
    """Agent's internal state that influences timing."""
    goal_urgency: float = 0.5
    resource_level: float = 1.0
    curiosity: float = 0.5
    stress: float = 0.0
    motivation: float = 0.5
    last_success_time: Optional[datetime] = None
    consecutive_failures: int = 0


class SelfTriggerSystem:
    """
    Agent-controlled timing system.
    
    The agent decides WHEN to think, not the daemon.
    This is TRUE autonomy - self-initiated action.
    """
    
    # Timing bounds by activity level
    TIMING_BOUNDS = {
        ActivityLevel.HYPERACTIVE: (1, 5),
        ActivityLevel.ACTIVE: (5, 30),
        ActivityLevel.NORMAL: (30, 120),
        ActivityLevel.RELAXED: (120, 600),
        ActivityLevel.DORMANT: (600, 3600),
        ActivityLevel.HIBERNATING: (3600, 7200),
    }
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.internal_state = InternalState()
        
        # Trigger history
        self.trigger_history: List[Dict[str, Any]] = []
        self.last_trigger_time: Optional[datetime] = None
        
        # Callbacks
        self._on_trigger: Optional[Callable[[], Awaitable[None]]] = None
        
        # Running state
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    def set_trigger_callback(self, callback: Callable[[], Awaitable[None]]):
        """Set the callback to invoke when agent decides to act."""
        self._on_trigger = callback
    
    async def start(self):
        """Start the self-trigger loop."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._self_trigger_loop())
        logger.info(f"Agent {self.agent_id} started self-trigger system")
    
    async def stop(self):
        """Stop the self-trigger loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"Agent {self.agent_id} stopped self-trigger system")
    
    async def _self_trigger_loop(self):
        """
        The CORE of agent-controlled timing.
        
        The agent decides its own think interval.
        This is what makes it autonomous, not orchestrated.
        """
        while self._running:
            try:
                # Agent decides when to act next
                decision = await self._decide_next_trigger()
                
                if decision.should_act_now:
                    # Trigger immediately
                    await self._trigger_action(decision.reason)
                else:
                    # Wait for the self-determined interval
                    await asyncio.sleep(decision.next_trigger_seconds)
                    
                    # Re-evaluate (something might have changed)
                    re_decision = await self._decide_next_trigger()
                    if re_decision.should_act_now:
                        await self._trigger_action(re_decision.reason)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in self-trigger loop for {self.agent_id}: {e}")
                await asyncio.sleep(10)  # Brief pause on error
    
    async def _decide_next_trigger(self) -> TriggerDecision:
        """
        The agent's INTERNAL decision about when to act.
        
        This is the key autonomy component - the agent
        decides its own timing based on internal state.
        """
        now = datetime.now(timezone.utc)
        
        # Calculate factors
        urgency_factor = self.internal_state.goal_urgency
        resource_factor = self.internal_state.resource_level
        motivation_factor = self.internal_state.motivation
        curiosity_factor = self.internal_state.curiosity
        stress_factor = self.internal_state.stress
        
        # Determine activity level based on internal state
        activity_score = (
            urgency_factor * 0.3 +
            motivation_factor * 0.25 +
            curiosity_factor * 0.2 +
            (1 - resource_factor) * 0.15 +  # Low resources = less active
            stress_factor * 0.1
        )
        
        # Map score to activity level
        if activity_score > 0.8:
            activity_level = ActivityLevel.HYPERACTIVE
        elif activity_score > 0.6:
            activity_level = ActivityLevel.ACTIVE
        elif activity_score > 0.4:
            activity_level = ActivityLevel.NORMAL
        elif activity_score > 0.2:
            activity_level = ActivityLevel.RELAXED
        elif resource_factor < 0.2:
            activity_level = ActivityLevel.HIBERNATING
        else:
            activity_level = ActivityLevel.DORMANT
        
        # Get timing bounds
        min_wait, max_wait = self.TIMING_BOUNDS[activity_level]
        
        # Calculate actual wait time
        wait_time = min_wait + (max_wait - min_wait) * (1 - activity_score)
        
        # Check for immediate triggers
        should_act_now = False
        trigger_reason = TriggerReason.SCHEDULED
        
        # Urgency override
        if urgency_factor > 0.9:
            should_act_now = True
            trigger_reason = TriggerReason.GOAL_URGENCY
        
        # Resource critical
        if resource_factor < 0.1:
            should_act_now = True
            trigger_reason = TriggerReason.RESOURCE_CRITICAL
        
        # Curiosity spike
        if curiosity_factor > 0.85:
            should_act_now = True
            trigger_reason = TriggerReason.CURIOSITY
        
        # Consecutive failures (need to reflect)
        if self.internal_state.consecutive_failures >= 3:
            should_act_now = True
            trigger_reason = TriggerReason.SELF_REFLECTION
        
        return TriggerDecision(
            should_act_now=should_act_now,
            next_trigger_seconds=wait_time,
            reason=trigger_reason,
            activity_level=activity_level,
            confidence=min(0.9, activity_score + 0.3),
            context={
                "urgency": urgency_factor,
                "resources": resource_factor,
                "motivation": motivation_factor,
                "curiosity": curiosity_factor,
            }
        )
    
    async def _trigger_action(self, reason: TriggerReason):
        """Trigger an action based on agent's internal decision."""
        self.last_trigger_time = datetime.now(timezone.utc)
        
        self.trigger_history.append({
            "time": self.last_trigger_time.isoformat(),
            "reason": reason.value,
            "activity_level": (await self._decide_next_trigger()).activity_level.value,
        })
        
        # Keep history bounded
        if len(self.trigger_history) > 100:
            self.trigger_history = self.trigger_history[-50:]
        
        # Invoke callback
        if self._on_trigger:
            await self._on_trigger()
        
        logger.debug(f"Agent {self.agent_id} self-triggered: {reason.value}")
    
    # ============== External Updates ==============
    
    def update_goal_urgency(self, urgency: float):
        """Update goal urgency (called by temporal planner)."""
        self.internal_state.goal_urgency = max(0, min(1, urgency))
    
    def update_resource_level(self, level: float):
        """Update resource level (called by resource manager)."""
        self.internal_state.resource_level = max(0, min(1, level))
    
    def update_motivation(self, motivation: float):
        """Update motivation level."""
        self.internal_state.motivation = max(0, min(1, motivation))
    
    def update_curiosity(self, curiosity: float):
        """Update curiosity level."""
        self.internal_state.curiosity = max(0, min(1, curiosity))
    
    def record_success(self):
        """Record a successful action."""
        self.internal_state.last_success_time = datetime.now(timezone.utc)
        self.internal_state.consecutive_failures = 0
        self.internal_state.motivation = min(1, self.internal_state.motivation + 0.1)
        self.internal_state.stress = max(0, self.internal_state.stress - 0.1)
    
    def record_failure(self):
        """Record a failed action."""
        self.internal_state.consecutive_failures += 1
        self.internal_state.motivation = max(0, self.internal_state.motivation - 0.05)
        self.internal_state.stress = min(1, self.internal_state.stress + 0.1)
    
    def spike_curiosity(self, amount: float = 0.3):
        """Spike curiosity (e.g., when something interesting happens)."""
        self.internal_state.curiosity = min(1, self.internal_state.curiosity + amount)
    
    def force_immediate_trigger(self, reason: TriggerReason = TriggerReason.OPPORTUNITY):
        """Force an immediate trigger (external event)."""
        asyncio.create_task(self._trigger_action(reason))
    
    # ============== Status ==============
    
    def get_status(self) -> Dict[str, Any]:
        """Get self-trigger system status."""
        return {
            "agent_id": self.agent_id,
            "running": self._running,
            "internal_state": {
                "goal_urgency": self.internal_state.goal_urgency,
                "resource_level": self.internal_state.resource_level,
                "motivation": self.internal_state.motivation,
                "curiosity": self.internal_state.curiosity,
                "stress": self.internal_state.stress,
                "consecutive_failures": self.internal_state.consecutive_failures,
            },
            "last_trigger": self.last_trigger_time.isoformat() if self.last_trigger_time else None,
            "recent_triggers": self.trigger_history[-5:],
        }


# Singleton manager
_trigger_systems: Dict[str, SelfTriggerSystem] = {}


def get_self_trigger(agent_id: str) -> SelfTriggerSystem:
    """Get or create a self-trigger system for an agent."""
    if agent_id not in _trigger_systems:
        _trigger_systems[agent_id] = SelfTriggerSystem(agent_id)
    return _trigger_systems[agent_id]
