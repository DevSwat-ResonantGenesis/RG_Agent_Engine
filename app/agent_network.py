"""
Agent Network - Network of interconnected agents.

STATUS: GRADUATED
CREATED: 2025-12-21
GRADUATED: 2025-12-21
GOVERNANCE: Network topology for agent interconnection and communication.

INVARIANTS:
  - connections are bidirectional
  - agent IDs are unique in network
  - disconnected agents are removed from all connections
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any, Set
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Governance: This module is GRADUATED
_IS_STUB = False


class AgentRole(Enum):
    """Roles an agent can have in the network."""
    LEADER = "leader"
    WORKER = "worker"
    COORDINATOR = "coordinator"
    SPECIALIST = "specialist"
    OBSERVER = "observer"


@dataclass
class NetworkNode:
    """A node in the agent network."""
    agent_id: str
    connections: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentNetwork:
    """Network of interconnected agents."""
    
    def __init__(self):
        self.nodes: Dict[str, NetworkNode] = {}
        
    def add_agent(self, agent_id: str) -> NetworkNode:
        if agent_id not in self.nodes:
            self.nodes[agent_id] = NetworkNode(agent_id=agent_id)
        return self.nodes[agent_id]
        
    def connect(self, agent1: str, agent2: str) -> bool:
        self.add_agent(agent1)
        self.add_agent(agent2)
        self.nodes[agent1].connections.add(agent2)
        self.nodes[agent2].connections.add(agent1)
        return True
        
    def disconnect(self, agent1: str, agent2: str) -> bool:
        if agent1 in self.nodes:
            self.nodes[agent1].connections.discard(agent2)
        if agent2 in self.nodes:
            self.nodes[agent2].connections.discard(agent1)
        return True
        
    def get_connections(self, agent_id: str) -> Set[str]:
        node = self.nodes.get(agent_id)
        return node.connections if node else set()
        
    def get_stats(self) -> Dict[str, Any]:
        total_connections = sum(len(n.connections) for n in self.nodes.values()) // 2
        return {"total_agents": len(self.nodes), "total_connections": total_connections}


_network: Optional[AgentNetwork] = None

def get_agent_network() -> AgentNetwork:
    global _network
    if _network is None:
        _network = AgentNetwork()
    return _network
