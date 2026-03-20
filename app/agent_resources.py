"""
AGENT RESOURCES - SURVIVAL PRESSURE
====================================

TRUE AUTONOMY COMPONENT: Resource depletion and survival pressure

Agents have finite resources that deplete over time.
This creates genuine survival pressure and self-preservation drive.

Key capabilities:
- Energy depletion per action
- Resource regeneration
- Death from resource exhaustion
- Resource trading between agents
- Efficiency optimization pressure
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4

from sqlalchemy import Column, String, Float, Integer, DateTime, JSON, Boolean, Text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from .db import Base

logger = logging.getLogger(__name__)


class ResourceType(str, Enum):
    ENERGY = "energy"           # Depletes with actions, regenerates slowly
    COMPUTE = "compute"         # Allocated compute budget
    MEMORY = "memory"           # Working memory capacity
    ATTENTION = "attention"     # Focus/context window
    REPUTATION = "reputation"   # Social capital


class DepletionCause(str, Enum):
    ACTION_EXECUTION = "action_execution"
    THINKING = "thinking"
    COMMUNICATION = "communication"
    LEARNING = "learning"
    IDLE_DECAY = "idle_decay"
    FAILURE_PENALTY = "failure_penalty"


class AgentState(str, Enum):
    ALIVE = "alive"
    DORMANT = "dormant"         # Low energy, reduced activity
    CRITICAL = "critical"       # Very low energy, survival mode
    DEAD = "dead"               # Zero energy, inactive


# ============== Database Models ==============

class AgentResourceState(Base):
    """Persistent resource state for an agent."""
    __tablename__ = "agent_resource_states"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    agent_id = Column(String, nullable=False, unique=True, index=True)
    
    energy = Column(Float, default=100.0)
    max_energy = Column(Float, default=100.0)
    energy_regen_rate = Column(Float, default=1.0)  # per minute
    
    compute_budget = Column(Float, default=1000.0)
    compute_used = Column(Float, default=0.0)
    
    memory_capacity = Column(Float, default=100.0)
    memory_used = Column(Float, default=0.0)
    
    attention_span = Column(Float, default=100.0)
    attention_used = Column(Float, default=0.0)
    
    reputation = Column(Float, default=50.0)
    
    state = Column(String, default="alive")
    
    last_action_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_regen_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    total_energy_consumed = Column(Float, default=0.0)
    total_actions_taken = Column(Integer, default=0)
    deaths = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ResourceTransaction(Base):
    """Record of resource changes."""
    __tablename__ = "resource_transactions"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    agent_id = Column(String, nullable=False, index=True)
    
    resource_type = Column(String, nullable=False)
    amount = Column(Float, nullable=False)  # Negative for consumption
    cause = Column(String, nullable=False)
    
    balance_before = Column(Float, nullable=False)
    balance_after = Column(Float, nullable=False)
    
    context = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ============== Resource Manager ==============

@dataclass
class ResourceCost:
    """Cost of an action in resources."""
    energy: float = 0.0
    compute: float = 0.0
    memory: float = 0.0
    attention: float = 0.0


class AgentResourceManager:
    """
    Manages agent resources and survival pressure.
    
    This creates TRUE survival pressure - agents must optimize
    or they will exhaust resources and die.
    """
    
    # Default costs per action type
    ACTION_COSTS = {
        "think": ResourceCost(energy=0.5, compute=1.0, attention=2.0),
        "tool_call": ResourceCost(energy=2.0, compute=5.0, attention=5.0),
        "api_call": ResourceCost(energy=3.0, compute=10.0, attention=3.0),
        "communicate": ResourceCost(energy=1.0, compute=2.0, attention=4.0),
        "learn": ResourceCost(energy=5.0, compute=15.0, memory=5.0),
        "spawn": ResourceCost(energy=20.0, compute=50.0),
        "idle": ResourceCost(energy=0.1),  # Baseline drain
    }
    
    # State thresholds
    DORMANT_THRESHOLD = 20.0
    CRITICAL_THRESHOLD = 5.0
    DEATH_THRESHOLD = 0.0
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self._state: Optional[AgentResourceState] = None
    
    async def load_state(self, db_session: AsyncSession):
        """Load or create resource state."""
        result = await db_session.execute(
            select(AgentResourceState)
            .where(AgentResourceState.agent_id == self.agent_id)
        )
        self._state = result.scalar_one_or_none()
        
        if not self._state:
            self._state = AgentResourceState(agent_id=self.agent_id)
            db_session.add(self._state)
            await db_session.commit()
            logger.info(f"Created resource state for agent {self.agent_id}")
        
        # Apply regeneration
        await self._apply_regeneration(db_session)
    
    async def _apply_regeneration(self, db_session: AsyncSession):
        """Apply energy regeneration based on time elapsed."""
        if not self._state or self._state.state == AgentState.DEAD.value:
            return
        
        now = datetime.now(timezone.utc)
        elapsed_minutes = (now - self._state.last_regen_at).total_seconds() / 60
        
        if elapsed_minutes > 0:
            regen_amount = min(
                self._state.max_energy - self._state.energy,
                elapsed_minutes * self._state.energy_regen_rate
            )
            
            if regen_amount > 0:
                self._state.energy += regen_amount
                self._state.last_regen_at = now
                await db_session.commit()
    
    async def can_afford(
        self,
        db_session: AsyncSession,
        action_type: str,
    ) -> Tuple[bool, str]:
        """Check if agent can afford an action."""
        await self.load_state(db_session)
        
        if self._state.state == AgentState.DEAD.value:
            return False, "Agent is dead"
        
        cost = self.ACTION_COSTS.get(action_type, ResourceCost(energy=1.0))
        
        if self._state.energy < cost.energy:
            return False, f"Insufficient energy: need {cost.energy}, have {self._state.energy:.1f}"
        
        if self._state.compute_budget - self._state.compute_used < cost.compute:
            return False, f"Insufficient compute budget"
        
        return True, "OK"
    
    async def consume(
        self,
        db_session: AsyncSession,
        action_type: str,
        success: bool = True,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Consume resources for an action."""
        await self.load_state(db_session)
        
        if self._state.state == AgentState.DEAD.value:
            return False
        
        cost = self.ACTION_COSTS.get(action_type, ResourceCost(energy=1.0))
        
        # Apply failure penalty
        if not success:
            cost.energy *= 1.5  # 50% extra energy on failure
        
        # Record transaction
        tx = ResourceTransaction(
            agent_id=self.agent_id,
            resource_type=ResourceType.ENERGY.value,
            amount=-cost.energy,
            cause=DepletionCause.ACTION_EXECUTION.value,
            balance_before=self._state.energy,
            balance_after=self._state.energy - cost.energy,
            context={"action_type": action_type, "success": success, **(context or {})},
        )
        db_session.add(tx)
        
        # Update state
        self._state.energy = max(0, self._state.energy - cost.energy)
        self._state.compute_used += cost.compute
        self._state.memory_used += cost.memory
        self._state.attention_used += cost.attention
        
        self._state.total_energy_consumed += cost.energy
        self._state.total_actions_taken += 1
        self._state.last_action_at = datetime.now(timezone.utc)
        
        # Check state transitions
        await self._update_agent_state(db_session)
        
        await db_session.commit()
        
        logger.debug(f"Agent {self.agent_id} consumed {cost.energy} energy, now at {self._state.energy:.1f}")
        return True
    
    async def _update_agent_state(self, db_session: AsyncSession):
        """Update agent state based on resources."""
        if self._state.energy <= self.DEATH_THRESHOLD:
            if self._state.state != AgentState.DEAD.value:
                self._state.state = AgentState.DEAD.value
                self._state.deaths += 1
                logger.warning(f"Agent {self.agent_id} DIED from resource exhaustion")
        
        elif self._state.energy <= self.CRITICAL_THRESHOLD:
            self._state.state = AgentState.CRITICAL.value
            logger.warning(f"Agent {self.agent_id} in CRITICAL state")
        
        elif self._state.energy <= self.DORMANT_THRESHOLD:
            self._state.state = AgentState.DORMANT.value
        
        else:
            self._state.state = AgentState.ALIVE.value
    
    async def revive(
        self,
        db_session: AsyncSession,
        energy_grant: float = 50.0,
    ):
        """Revive a dead agent."""
        await self.load_state(db_session)
        
        if self._state.state != AgentState.DEAD.value:
            return
        
        self._state.energy = energy_grant
        self._state.state = AgentState.ALIVE.value
        self._state.attention_used = 0
        
        await db_session.commit()
        logger.info(f"Agent {self.agent_id} revived with {energy_grant} energy")
    
    async def transfer_energy(
        self,
        db_session: AsyncSession,
        to_agent_id: str,
        amount: float,
    ) -> bool:
        """Transfer energy to another agent."""
        await self.load_state(db_session)
        
        if self._state.energy < amount:
            return False
        
        # Get target agent
        result = await db_session.execute(
            select(AgentResourceState)
            .where(AgentResourceState.agent_id == to_agent_id)
        )
        target = result.scalar_one_or_none()
        
        if not target:
            return False
        
        # Transfer
        self._state.energy -= amount
        target.energy = min(target.max_energy, target.energy + amount)
        
        await db_session.commit()
        logger.info(f"Agent {self.agent_id} transferred {amount} energy to {to_agent_id}")
        return True
    
    async def get_survival_pressure(self) -> float:
        """Get current survival pressure (0-1, higher = more pressure)."""
        if not self._state:
            return 0.0
        
        if self._state.state == AgentState.DEAD.value:
            return 1.0
        
        # Pressure increases as energy decreases
        energy_pressure = 1.0 - (self._state.energy / self._state.max_energy)
        
        # Compute budget pressure
        compute_pressure = self._state.compute_used / self._state.compute_budget if self._state.compute_budget > 0 else 0
        
        return min(1.0, (energy_pressure * 0.7 + compute_pressure * 0.3))
    
    async def get_efficiency_score(self) -> float:
        """Get efficiency score based on actions vs energy."""
        if not self._state or self._state.total_actions_taken == 0:
            return 0.5
        
        # Lower energy per action = more efficient
        energy_per_action = self._state.total_energy_consumed / self._state.total_actions_taken
        
        # Normalize (assuming 2.0 is average)
        efficiency = max(0, min(1, 1 - (energy_per_action - 1.0) / 3.0))
        return efficiency
    
    def get_status(self) -> Dict[str, Any]:
        """Get resource status."""
        if not self._state:
            return {"error": "State not loaded"}
        
        return {
            "agent_id": self.agent_id,
            "state": self._state.state,
            "energy": self._state.energy,
            "max_energy": self._state.max_energy,
            "energy_percent": (self._state.energy / self._state.max_energy) * 100,
            "compute_used": self._state.compute_used,
            "compute_budget": self._state.compute_budget,
            "reputation": self._state.reputation,
            "total_actions": self._state.total_actions_taken,
            "deaths": self._state.deaths,
        }
    
    def is_alive(self) -> bool:
        """Check if agent is alive."""
        return self._state and self._state.state != AgentState.DEAD.value
    
    def is_critical(self) -> bool:
        """Check if agent is in critical state."""
        return self._state and self._state.state == AgentState.CRITICAL.value


# Singleton manager
_resource_managers: Dict[str, AgentResourceManager] = {}


def get_resource_manager(agent_id: str) -> AgentResourceManager:
    """Get or create a resource manager for an agent."""
    if agent_id not in _resource_managers:
        _resource_managers[agent_id] = AgentResourceManager(agent_id)
    return _resource_managers[agent_id]
