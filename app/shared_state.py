"""
Shared State - Control Plane ↔ Agent OS Integration
====================================================

This module provides shared state management between:
- Control Plane (system overview, node management, network health)
- Agent OS (agent creation, execution, teams, economy)

Enables:
- Real-time agent status sync
- Cross-system event propagation
- Unified state management
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Callable
from dataclasses import dataclass, field
from enum import Enum
import json

logger = logging.getLogger(__name__)


class StateChangeType(str, Enum):
    """Types of state changes."""
    AGENT_CREATED = "agent_created"
    AGENT_UPDATED = "agent_updated"
    AGENT_DELETED = "agent_deleted"
    AGENT_STARTED = "agent_started"
    AGENT_STOPPED = "agent_stopped"
    AGENT_PUBLISHED = "agent_published"
    SESSION_STARTED = "session_started"
    SESSION_COMPLETED = "session_completed"
    SESSION_FAILED = "session_failed"
    TEAM_CREATED = "team_created"
    TEAM_UPDATED = "team_updated"
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_COMPLETED = "workflow_completed"
    NETWORK_NODE_ADDED = "network_node_added"
    NETWORK_NODE_REMOVED = "network_node_removed"
    WALLET_UPDATED = "wallet_updated"
    AUTONOMY_STATUS_CHANGED = "autonomy_status_changed"


@dataclass
class StateChange:
    """A state change event."""
    change_type: StateChangeType
    entity_id: str
    entity_type: str  # agent, session, team, node, wallet
    data: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = "agent_os"  # agent_os, control_plane, network
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.change_type.value,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "data": self.data,
            "timestamp": self.timestamp,
            "source": self.source,
        }


@dataclass
class AgentState:
    """Shared agent state."""
    agent_id: str
    name: str
    status: str  # idle, active, running, paused, error
    autonomy_mode: str  # full, supervised, governed, restricted
    current_session_id: Optional[str] = None
    active_goals: List[str] = field(default_factory=list)
    published: bool = False
    network_node_id: Optional[str] = None
    wallet_balance: float = 0.0
    execution_count: int = 0
    success_rate: float = 0.0
    last_activity: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "status": self.status,
            "autonomy_mode": self.autonomy_mode,
            "current_session_id": self.current_session_id,
            "active_goals": self.active_goals,
            "published": self.published,
            "network_node_id": self.network_node_id,
            "wallet_balance": self.wallet_balance,
            "execution_count": self.execution_count,
            "success_rate": self.success_rate,
            "last_activity": self.last_activity,
        }


@dataclass
class NetworkState:
    """Shared network state."""
    total_nodes: int = 0
    active_nodes: int = 0
    total_agents_published: int = 0
    total_executions: int = 0
    network_health: str = "healthy"  # healthy, degraded, critical
    tps: float = 0.0
    avg_latency_ms: float = 0.0


@dataclass
class SystemState:
    """Overall system state."""
    agent_os_running: bool = False
    control_plane_running: bool = False
    autonomy_enabled: bool = False
    healthy_subsystems: int = 0
    total_subsystems: int = 9
    agents: Dict[str, AgentState] = field(default_factory=dict)
    network: NetworkState = field(default_factory=NetworkState)
    last_sync: Optional[str] = None


class SharedStateManager:
    """
    Manages shared state between Control Plane and Agent OS.
    
    This is the central hub for state synchronization.
    """
    
    def __init__(self):
        self.state = SystemState()
        self._subscribers: Dict[str, Set[Callable]] = {}
        self._change_history: List[StateChange] = []
        self._max_history = 1000
        self._lock = asyncio.Lock()
    
    # ============== Agent State ==============
    
    async def register_agent(self, agent_id: str, name: str, autonomy_mode: str = "supervised"):
        """Register a new agent in shared state."""
        async with self._lock:
            self.state.agents[agent_id] = AgentState(
                agent_id=agent_id,
                name=name,
                status="idle",
                autonomy_mode=autonomy_mode,
            )
        
        await self._emit_change(StateChange(
            change_type=StateChangeType.AGENT_CREATED,
            entity_id=agent_id,
            entity_type="agent",
            data={"name": name, "autonomy_mode": autonomy_mode},
        ))
    
    async def update_agent_status(self, agent_id: str, status: str, session_id: Optional[str] = None):
        """Update agent status."""
        async with self._lock:
            if agent_id in self.state.agents:
                agent = self.state.agents[agent_id]
                agent.status = status
                agent.current_session_id = session_id
                agent.last_activity = datetime.now(timezone.utc).isoformat()
        
        change_type = StateChangeType.AGENT_STARTED if status == "running" else StateChangeType.AGENT_UPDATED
        await self._emit_change(StateChange(
            change_type=change_type,
            entity_id=agent_id,
            entity_type="agent",
            data={"status": status, "session_id": session_id},
        ))
    
    async def update_agent_metrics(self, agent_id: str, execution_count: int, success_rate: float):
        """Update agent execution metrics."""
        async with self._lock:
            if agent_id in self.state.agents:
                agent = self.state.agents[agent_id]
                agent.execution_count = execution_count
                agent.success_rate = success_rate
    
    async def update_agent_wallet(self, agent_id: str, balance: float):
        """Update agent wallet balance."""
        async with self._lock:
            if agent_id in self.state.agents:
                self.state.agents[agent_id].wallet_balance = balance
        
        await self._emit_change(StateChange(
            change_type=StateChangeType.WALLET_UPDATED,
            entity_id=agent_id,
            entity_type="wallet",
            data={"balance": balance},
        ))
    
    async def publish_agent(self, agent_id: str, network_node_id: str):
        """Mark agent as published to network."""
        async with self._lock:
            if agent_id in self.state.agents:
                agent = self.state.agents[agent_id]
                agent.published = True
                agent.network_node_id = network_node_id
        
        await self._emit_change(StateChange(
            change_type=StateChangeType.AGENT_PUBLISHED,
            entity_id=agent_id,
            entity_type="agent",
            data={"network_node_id": network_node_id},
        ))
    
    async def remove_agent(self, agent_id: str):
        """Remove agent from shared state."""
        async with self._lock:
            if agent_id in self.state.agents:
                del self.state.agents[agent_id]
        
        await self._emit_change(StateChange(
            change_type=StateChangeType.AGENT_DELETED,
            entity_id=agent_id,
            entity_type="agent",
            data={},
        ))
    
    def get_agent(self, agent_id: str) -> Optional[AgentState]:
        """Get agent state."""
        return self.state.agents.get(agent_id)
    
    def get_all_agents(self) -> List[AgentState]:
        """Get all agent states."""
        return list(self.state.agents.values())
    
    # ============== Session Events ==============
    
    async def session_started(self, session_id: str, agent_id: str, goal: str):
        """Record session start."""
        await self.update_agent_status(agent_id, "running", session_id)
        
        await self._emit_change(StateChange(
            change_type=StateChangeType.SESSION_STARTED,
            entity_id=session_id,
            entity_type="session",
            data={"agent_id": agent_id, "goal": goal},
        ))
    
    async def session_completed(self, session_id: str, agent_id: str, success: bool):
        """Record session completion."""
        await self.update_agent_status(agent_id, "idle", None)
        
        change_type = StateChangeType.SESSION_COMPLETED if success else StateChangeType.SESSION_FAILED
        await self._emit_change(StateChange(
            change_type=change_type,
            entity_id=session_id,
            entity_type="session",
            data={"agent_id": agent_id, "success": success},
        ))
    
    # ============== Network State ==============
    
    async def update_network_state(
        self,
        total_nodes: int = None,
        active_nodes: int = None,
        total_agents_published: int = None,
        network_health: str = None,
        tps: float = None,
    ):
        """Update network state."""
        async with self._lock:
            if total_nodes is not None:
                self.state.network.total_nodes = total_nodes
            if active_nodes is not None:
                self.state.network.active_nodes = active_nodes
            if total_agents_published is not None:
                self.state.network.total_agents_published = total_agents_published
            if network_health is not None:
                self.state.network.network_health = network_health
            if tps is not None:
                self.state.network.tps = tps
    
    # ============== System State ==============
    
    async def update_system_state(
        self,
        agent_os_running: bool = None,
        control_plane_running: bool = None,
        autonomy_enabled: bool = None,
        healthy_subsystems: int = None,
    ):
        """Update system state."""
        async with self._lock:
            if agent_os_running is not None:
                self.state.agent_os_running = agent_os_running
            if control_plane_running is not None:
                self.state.control_plane_running = control_plane_running
            if autonomy_enabled is not None:
                self.state.autonomy_enabled = autonomy_enabled
            if healthy_subsystems is not None:
                self.state.healthy_subsystems = healthy_subsystems
            self.state.last_sync = datetime.now(timezone.utc).isoformat()
        
        if autonomy_enabled is not None:
            await self._emit_change(StateChange(
                change_type=StateChangeType.AUTONOMY_STATUS_CHANGED,
                entity_id="system",
                entity_type="system",
                data={"autonomy_enabled": autonomy_enabled},
            ))
    
    def get_system_state(self) -> Dict[str, Any]:
        """Get full system state."""
        return {
            "agent_os_running": self.state.agent_os_running,
            "control_plane_running": self.state.control_plane_running,
            "autonomy_enabled": self.state.autonomy_enabled,
            "healthy_subsystems": self.state.healthy_subsystems,
            "total_subsystems": self.state.total_subsystems,
            "agent_count": len(self.state.agents),
            "active_agents": sum(1 for a in self.state.agents.values() if a.status == "running"),
            "published_agents": sum(1 for a in self.state.agents.values() if a.published),
            "network": {
                "total_nodes": self.state.network.total_nodes,
                "active_nodes": self.state.network.active_nodes,
                "health": self.state.network.network_health,
                "tps": self.state.network.tps,
            },
            "last_sync": self.state.last_sync,
        }
    
    # ============== Event Subscription ==============
    
    def subscribe(self, event_types: List[StateChangeType], callback: Callable):
        """Subscribe to state change events."""
        for event_type in event_types:
            key = event_type.value
            if key not in self._subscribers:
                self._subscribers[key] = set()
            self._subscribers[key].add(callback)
    
    def unsubscribe(self, callback: Callable):
        """Unsubscribe from all events."""
        for subscribers in self._subscribers.values():
            subscribers.discard(callback)
    
    async def _emit_change(self, change: StateChange):
        """Emit a state change to subscribers."""
        # Record in history
        self._change_history.append(change)
        if len(self._change_history) > self._max_history:
            self._change_history = self._change_history[-self._max_history:]
        
        # Notify subscribers
        key = change.change_type.value
        if key in self._subscribers:
            for callback in self._subscribers[key]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(change)
                    else:
                        callback(change)
                except Exception as e:
                    logger.error(f"Subscriber error: {e}")
    
    def get_recent_changes(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent state changes."""
        return [c.to_dict() for c in self._change_history[-limit:]]


# Singleton instance
_shared_state: Optional[SharedStateManager] = None


def get_shared_state() -> SharedStateManager:
    """Get the singleton shared state manager."""
    global _shared_state
    if _shared_state is None:
        _shared_state = SharedStateManager()
    return _shared_state
