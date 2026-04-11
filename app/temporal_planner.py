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
TEMPORAL PLANNER - LONG-HORIZON GOALS
=====================================

TRUE AUTONOMY COMPONENT: Temporal commitment

Agents with long-term planning, deferred execution, and
opportunity cost reasoning across time.

This is what separates reactive agents from agentic life.

Key capabilities:
- Multi-day/multi-week goal persistence
- Deferred execution queues
- Opportunity cost reasoning
- Time-aware prioritization
- Deadline management
- Goal dependency chains
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
import json
import heapq

from sqlalchemy import Column, String, Float, Integer, DateTime, JSON, Boolean, Text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, or_

from .db import Base

logger = logging.getLogger(__name__)


class GoalHorizon(str, Enum):
    IMMEDIATE = "immediate"     # < 1 hour
    SHORT_TERM = "short_term"   # 1 hour - 1 day
    MEDIUM_TERM = "medium_term" # 1 day - 1 week
    LONG_TERM = "long_term"     # 1 week - 1 month
    STRATEGIC = "strategic"     # > 1 month


class ExecutionWindow(str, Enum):
    ANYTIME = "anytime"
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    NIGHT = "night"
    WEEKDAY = "weekday"
    WEEKEND = "weekend"


class DeferralReason(str, Enum):
    DEPENDENCY = "dependency"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    BETTER_TIME = "better_time"
    COST_OPTIMIZATION = "cost_optimization"
    EXTERNAL_WAIT = "external_wait"
    STRATEGY_CHANGE = "strategy_change"


# ============== Database Models ==============

class LongTermGoal(Base):
    """A goal with temporal commitment."""
    __tablename__ = "long_term_goals"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    agent_id = Column(String, nullable=False, index=True)
    
    description = Column(Text, nullable=False)
    horizon = Column(String, nullable=False)
    
    priority = Column(Float, default=0.5)
    importance = Column(Float, default=0.5)
    urgency = Column(Float, default=0.5)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    target_completion = Column(DateTime, nullable=True)
    actual_completion = Column(DateTime, nullable=True)
    
    parent_goal_id = Column(String, nullable=True)
    dependencies = Column(JSON, default=list)  # List of goal IDs
    
    progress = Column(Float, default=0.0)
    status = Column(String, default="active")  # active, paused, completed, abandoned
    
    context = Column(JSON, nullable=True)
    success_criteria = Column(JSON, nullable=True)


class DeferredExecution(Base):
    """An execution deferred to a future time."""
    __tablename__ = "deferred_executions"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    agent_id = Column(String, nullable=False, index=True)
    goal_id = Column(String, nullable=True)
    
    action_type = Column(String, nullable=False)
    action_data = Column(JSON, nullable=False)
    
    scheduled_time = Column(DateTime, nullable=False)
    execution_window = Column(String, default="anytime")
    
    deferral_reason = Column(String, nullable=True)
    deferral_count = Column(Integer, default=0)
    
    is_executed = Column(Boolean, default=False)
    is_cancelled = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    executed_at = Column(DateTime, nullable=True)


class OpportunityCostRecord(Base):
    """Record of opportunity cost decisions."""
    __tablename__ = "opportunity_costs"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    agent_id = Column(String, nullable=False, index=True)
    
    chosen_action = Column(String, nullable=False)
    foregone_actions = Column(JSON, nullable=False)  # List of alternatives
    
    expected_value_chosen = Column(Float, nullable=True)
    expected_value_foregone = Column(JSON, nullable=True)
    
    actual_outcome = Column(JSON, nullable=True)
    regret_score = Column(Float, nullable=True)
    
    reasoning = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ============== Temporal Planner ==============

@dataclass
class TemporalGoal:
    """In-memory representation of a temporal goal."""
    id: str
    description: str
    horizon: GoalHorizon
    priority: float
    importance: float
    urgency: float
    target_completion: Optional[datetime]
    dependencies: List[str]
    progress: float = 0.0
    
    @property
    def score(self) -> float:
        """Calculate priority score."""
        urgency_weight = 1.5 if self.urgency > 0.7 else 1.0
        return (self.priority * 0.4 + self.importance * 0.3 + self.urgency * 0.3) * urgency_weight


@dataclass
class DeferredAction:
    """An action to be executed later."""
    id: str
    action_type: str
    action_data: Dict[str, Any]
    scheduled_time: datetime
    goal_id: Optional[str]
    reason: DeferralReason
    
    def __lt__(self, other):
        return self.scheduled_time < other.scheduled_time


class TemporalPlanner:
    """
    Long-horizon temporal planning for agents.
    
    This enables TRUE autonomy through temporal commitment.
    """
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        
        # In-memory caches
        self.goals: Dict[str, TemporalGoal] = {}
        self.deferred_queue: List[DeferredAction] = []
        heapq.heapify(self.deferred_queue)
        
        # Planning parameters
        self.max_planning_horizon_days = 30
        self.replan_interval_hours = 6
        self.last_replan: Optional[datetime] = None
    
    async def load_goals(self, db_session: AsyncSession):
        """Load goals from database."""
        result = await db_session.execute(
            select(LongTermGoal)
            .where(and_(
                LongTermGoal.agent_id == self.agent_id,
                LongTermGoal.status == "active",
            ))
        )
        goals = list(result.scalars().all())
        
        for g in goals:
            self.goals[g.id] = TemporalGoal(
                id=g.id,
                description=g.description,
                horizon=GoalHorizon(g.horizon),
                priority=g.priority,
                importance=g.importance,
                urgency=g.urgency,
                target_completion=g.target_completion,
                dependencies=g.dependencies or [],
                progress=g.progress,
            )
        
        logger.info(f"Loaded {len(self.goals)} goals for agent {self.agent_id}")
    
    async def add_goal(
        self,
        db_session: AsyncSession,
        description: str,
        horizon: GoalHorizon,
        target_completion: Optional[datetime] = None,
        dependencies: Optional[List[str]] = None,
        priority: float = 0.5,
        importance: float = 0.5,
        success_criteria: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Add a new long-term goal."""
        
        goal = LongTermGoal(
            agent_id=self.agent_id,
            description=description,
            horizon=horizon.value,
            priority=priority,
            importance=importance,
            urgency=self._calculate_urgency(target_completion),
            target_completion=target_completion,
            dependencies=dependencies or [],
            success_criteria=success_criteria,
        )
        
        db_session.add(goal)
        await db_session.commit()
        
        self.goals[goal.id] = TemporalGoal(
            id=goal.id,
            description=description,
            horizon=horizon,
            priority=priority,
            importance=importance,
            urgency=goal.urgency,
            target_completion=target_completion,
            dependencies=dependencies or [],
        )
        
        logger.info(f"Agent {self.agent_id} added goal: {description[:50]}...")
        return goal.id
    
    def _calculate_urgency(self, target: Optional[datetime]) -> float:
        """Calculate urgency based on deadline."""
        if not target:
            return 0.3  # Default moderate urgency
        
        now = datetime.now(timezone.utc)
        time_remaining = (target - now).total_seconds()
        
        if time_remaining <= 0:
            return 1.0  # Overdue
        elif time_remaining < 3600:  # < 1 hour
            return 0.95
        elif time_remaining < 86400:  # < 1 day
            return 0.8
        elif time_remaining < 604800:  # < 1 week
            return 0.5
        else:
            return 0.3
    
    async def get_next_goal(
        self,
        db_session: AsyncSession,
    ) -> Optional[TemporalGoal]:
        """Get the next goal to work on."""
        
        # Update urgencies
        for goal in self.goals.values():
            goal.urgency = self._calculate_urgency(goal.target_completion)
        
        # Filter to executable goals (dependencies met)
        executable = []
        for goal in self.goals.values():
            if self._dependencies_met(goal):
                executable.append(goal)
        
        if not executable:
            return None
        
        # Sort by score
        executable.sort(key=lambda g: g.score, reverse=True)
        return executable[0]
    
    def _dependencies_met(self, goal: TemporalGoal) -> bool:
        """Check if goal dependencies are met."""
        for dep_id in goal.dependencies:
            dep = self.goals.get(dep_id)
            if dep and dep.progress < 1.0:
                return False
        return True
    
    async def defer_action(
        self,
        db_session: AsyncSession,
        action_type: str,
        action_data: Dict[str, Any],
        defer_until: datetime,
        reason: DeferralReason,
        goal_id: Optional[str] = None,
    ) -> str:
        """Defer an action to a future time."""
        
        deferred = DeferredExecution(
            agent_id=self.agent_id,
            goal_id=goal_id,
            action_type=action_type,
            action_data=action_data,
            scheduled_time=defer_until,
            deferral_reason=reason.value,
        )
        
        db_session.add(deferred)
        await db_session.commit()
        
        # Add to in-memory queue
        action = DeferredAction(
            id=deferred.id,
            action_type=action_type,
            action_data=action_data,
            scheduled_time=defer_until,
            goal_id=goal_id,
            reason=reason,
        )
        heapq.heappush(self.deferred_queue, action)
        
        logger.info(f"Agent {self.agent_id} deferred action until {defer_until}")
        return deferred.id
    
    async def get_due_actions(
        self,
        db_session: AsyncSession,
    ) -> List[DeferredAction]:
        """Get actions that are due for execution."""
        now = datetime.now(timezone.utc)
        due = []
        
        while self.deferred_queue and self.deferred_queue[0].scheduled_time <= now:
            action = heapq.heappop(self.deferred_queue)
            due.append(action)
        
        return due
    
    async def record_opportunity_cost(
        self,
        db_session: AsyncSession,
        chosen_action: str,
        alternatives: List[Dict[str, Any]],
        reasoning: str,
    ) -> str:
        """Record an opportunity cost decision."""
        
        record = OpportunityCostRecord(
            agent_id=self.agent_id,
            chosen_action=chosen_action,
            foregone_actions=[a.get("action") for a in alternatives],
            expected_value_chosen=alternatives[0].get("expected_value") if alternatives else None,
            expected_value_foregone={a.get("action"): a.get("expected_value") for a in alternatives[1:]},
            reasoning=reasoning,
        )
        
        db_session.add(record)
        await db_session.commit()
        
        return record.id
    
    async def update_goal_progress(
        self,
        db_session: AsyncSession,
        goal_id: str,
        progress: float,
    ):
        """Update goal progress."""
        result = await db_session.execute(
            select(LongTermGoal)
            .where(LongTermGoal.id == goal_id)
        )
        goal = result.scalar_one_or_none()
        
        if goal:
            goal.progress = min(1.0, max(0.0, progress))
            if goal.progress >= 1.0:
                goal.status = "completed"
                goal.actual_completion = datetime.now(timezone.utc)
            await db_session.commit()
            
            if goal_id in self.goals:
                self.goals[goal_id].progress = goal.progress
    
    async def evaluate_opportunity_costs(
        self,
        db_session: AsyncSession,
        options: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Evaluate opportunity costs for a set of options."""
        
        # Score each option
        scored = []
        for opt in options:
            expected_value = opt.get("expected_value", 0.5)
            time_cost = opt.get("time_cost", 1.0)
            resource_cost = opt.get("resource_cost", 0.0)
            
            # Opportunity cost = foregone value / time
            score = expected_value / (time_cost + resource_cost + 0.1)
            scored.append({
                **opt,
                "score": score,
            })
        
        # Sort by score
        scored.sort(key=lambda x: x["score"], reverse=True)
        
        # Calculate regret for not choosing best
        best_score = scored[0]["score"] if scored else 0
        for opt in scored:
            opt["regret"] = best_score - opt["score"]
        
        return {
            "recommended": scored[0] if scored else None,
            "alternatives": scored[1:],
            "total_regret": sum(opt["regret"] for opt in scored[1:]),
        }
    
    async def needs_replanning(self) -> bool:
        """Check if replanning is needed."""
        if not self.last_replan:
            return True
        
        hours_since = (datetime.now(timezone.utc) - self.last_replan).total_seconds() / 3600
        return hours_since >= self.replan_interval_hours
    
    async def replan(
        self,
        db_session: AsyncSession,
        context: Dict[str, Any],
    ):
        """Replan goals and priorities."""
        
        # Update urgencies based on deadlines
        for goal in self.goals.values():
            goal.urgency = self._calculate_urgency(goal.target_completion)
        
        # Check for blocked goals
        blocked = []
        for goal in self.goals.values():
            if not self._dependencies_met(goal):
                blocked.append(goal.id)
        
        # Check for overdue goals
        now = datetime.now(timezone.utc)
        overdue = []
        for goal in self.goals.values():
            if goal.target_completion and goal.target_completion < now and goal.progress < 1.0:
                overdue.append(goal.id)
        
        self.last_replan = datetime.now(timezone.utc)
        
        return {
            "total_goals": len(self.goals),
            "blocked_goals": blocked,
            "overdue_goals": overdue,
            "replanned_at": self.last_replan.isoformat(),
        }
    
    async def get_planning_status(
        self,
        db_session: AsyncSession,
    ) -> Dict[str, Any]:
        """Get temporal planning status."""
        
        by_horizon = {}
        for goal in self.goals.values():
            h = goal.horizon.value
            if h not in by_horizon:
                by_horizon[h] = 0
            by_horizon[h] += 1
        
        return {
            "agent_id": self.agent_id,
            "total_goals": len(self.goals),
            "goals_by_horizon": by_horizon,
            "deferred_actions": len(self.deferred_queue),
            "last_replan": self.last_replan.isoformat() if self.last_replan else None,
            "needs_replanning": await self.needs_replanning(),
        }


# Singleton manager
_planners: Dict[str, TemporalPlanner] = {}


def get_temporal_planner(agent_id: str) -> TemporalPlanner:
    """Get or create a temporal planner for an agent."""
    if agent_id not in _planners:
        _planners[agent_id] = TemporalPlanner(agent_id)
    return _planners[agent_id]
