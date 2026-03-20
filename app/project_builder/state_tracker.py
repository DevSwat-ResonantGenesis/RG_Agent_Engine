"""
State Tracker - State Physics Integration
==========================================

Integrates with State Physics service (port 8091) to track economic constraints.

Features:
- Register agent as node in Hash Sphere
- Track budget/resource consumption
- Monitor trust dynamics
- Enforce conservation laws
- Visualize build process
"""

import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum
import httpx
import os

logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    """Status of agent in State Physics."""
    ACTIVE = "active"
    BUILDING = "building"
    IDLE = "idle"
    LOW_BUDGET = "low_budget"
    DEAD = "dead"


@dataclass
class AgentState:
    """State of an agent in State Physics."""
    agent_id: str
    dsid: str
    budget: float
    trust_score: float
    temperature: float
    mass: float
    status: AgentStatus
    total_spent: float = 0.0
    files_created: int = 0
    transactions: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "dsid": self.dsid,
            "budget": self.budget,
            "trust_score": self.trust_score,
            "temperature": self.temperature,
            "mass": self.mass,
            "status": self.status.value,
            "total_spent": self.total_spent,
            "files_created": self.files_created,
            "transactions": self.transactions,
        }


@dataclass
class InvariantStatus:
    """Status of conservation law invariants."""
    mass_conservation: bool = True
    energy_conservation: bool = True
    identity_uniqueness: bool = True
    causality: bool = True
    trust_bounds: bool = True
    non_negative_value: bool = True
    violations: List[str] = field(default_factory=list)
    
    @property
    def all_passed(self) -> bool:
        return all([
            self.mass_conservation,
            self.energy_conservation,
            self.identity_uniqueness,
            self.causality,
            self.trust_bounds,
            self.non_negative_value,
        ])


class StateTracker:
    """
    Tracks agent state using State Physics service.
    
    Integration Points:
    - POST /api/identity - Register agent as node
    - POST /api/transaction - Record file creation cost
    - POST /api/trust - Build trust relationships
    - GET /api/state - Get current state
    - GET /api/metrics - Get agent metrics
    - GET /api/invariants - Check conservation laws
    - POST /api/simulate - Run physics simulation
    """
    
    STATE_PHYSICS_URL = os.getenv("STATE_PHYSICS_URL", "http://rg_users_invarients_sim:8091")
    
    FILE_CREATION_COST = 50.0
    VALIDATION_COST = 25.0
    FIX_COST = 30.0
    TRUST_GAIN_PER_FILE = 0.01
    TRUST_GAIN_ON_SUCCESS = 0.1
    MIN_BUDGET_WARNING = 500.0
    
    def __init__(self, service_url: str = None):
        self.service_url = service_url or self.STATE_PHYSICS_URL
        self._client: Optional[httpx.AsyncClient] = None
        self._agent_states: Dict[str, AgentState] = {}
        logger.info(f"StateTracker initialized with service URL: {self.service_url}")
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client
    
    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def register_agent(
        self,
        user_id: str,
        project_id: str,
        project_name: str,
        initial_budget: float = 10000.0,
    ) -> AgentState:
        """
        Register a Project Builder agent in State Physics.
        
        Args:
            user_id: User ID
            project_id: Project ID
            project_name: Project name
            initial_budget: Initial budget for the build
            
        Returns:
            AgentState with initial values
        """
        client = await self._get_client()
        dsid = f"project_builder_agent_{user_id}_{project_id}"
        
        try:
            response = await client.post(
                f"{self.service_url}/api/identity",
                json={
                    "dsid": dsid,
                    "node_type": "agent",
                    "initial_trust": 0.5,
                    "initial_value": initial_budget,
                    "metadata": {
                        "agent_type": "project_builder",
                        "user_id": user_id,
                        "project_id": project_id,
                        "project_name": project_name,
                    },
                },
            )
            
            if response.status_code == 200:
                data = response.json()
                node = data.get("node", {})
                
                state = AgentState(
                    agent_id=f"{user_id}_{project_id}",
                    dsid=dsid,
                    budget=initial_budget,
                    trust_score=node.get("trust_score", 0.5),
                    temperature=node.get("temperature", 1.0),
                    mass=node.get("mass", 1.0),
                    status=AgentStatus.ACTIVE,
                )
                
                self._agent_states[state.agent_id] = state
                logger.info(f"Registered agent {dsid} with budget {initial_budget}")
                return state
            else:
                logger.error(f"Failed to register agent: {response.text}")
                
        except Exception as e:
            logger.error(f"Error registering agent: {e}")
        
        state = AgentState(
            agent_id=f"{user_id}_{project_id}",
            dsid=dsid,
            budget=initial_budget,
            trust_score=0.5,
            temperature=1.0,
            mass=1.0,
            status=AgentStatus.ACTIVE,
        )
        self._agent_states[state.agent_id] = state
        return state
    
    async def record_file_creation(
        self,
        agent_id: str,
        file_path: str,
        file_size: int = 0,
    ) -> bool:
        """
        Record file creation as a transaction.
        
        Args:
            agent_id: Agent ID
            file_path: Path to created file
            file_size: Size of file in bytes
            
        Returns:
            True if transaction recorded successfully
        """
        state = self._agent_states.get(agent_id)
        if not state:
            logger.warning(f"Agent {agent_id} not found")
            return False
        
        cost = self.FILE_CREATION_COST
        if state.budget < cost:
            logger.warning(f"Agent {agent_id} has insufficient budget: {state.budget}")
            state.status = AgentStatus.LOW_BUDGET
            return False
        
        client = await self._get_client()
        file_dsid = f"file_{hash(file_path) % 10**12}"
        
        try:
            response = await client.post(
                f"{self.service_url}/api/transaction",
                json={
                    "from_dsid": state.dsid,
                    "to_dsid": file_dsid,
                    "amount": cost,
                },
            )
            
            if response.status_code == 200:
                state.budget -= cost
                state.total_spent += cost
                state.files_created += 1
                state.transactions += 1
                state.trust_score = min(1.0, state.trust_score + self.TRUST_GAIN_PER_FILE)
                state.temperature = min(5.0, state.temperature + 0.1)
                
                if state.budget < self.MIN_BUDGET_WARNING:
                    state.status = AgentStatus.LOW_BUDGET
                
                logger.debug(f"Recorded file creation: {file_path}, cost: {cost}")
                return True
                
        except Exception as e:
            logger.warning(f"Failed to record transaction: {e}")
        
        state.budget -= cost
        state.total_spent += cost
        state.files_created += 1
        return True
    
    async def record_validation(self, agent_id: str) -> bool:
        """Record validation cost."""
        state = self._agent_states.get(agent_id)
        if not state:
            return False
        
        cost = self.VALIDATION_COST
        if state.budget >= cost:
            state.budget -= cost
            state.total_spent += cost
            state.transactions += 1
            return True
        return False
    
    async def record_fix(self, agent_id: str) -> bool:
        """Record self-correction fix cost."""
        state = self._agent_states.get(agent_id)
        if not state:
            return False
        
        cost = self.FIX_COST
        if state.budget >= cost:
            state.budget -= cost
            state.total_spent += cost
            state.transactions += 1
            return True
        return False
    
    async def record_success(self, agent_id: str):
        """Record successful build completion."""
        state = self._agent_states.get(agent_id)
        if not state:
            return
        
        state.trust_score = min(1.0, state.trust_score + self.TRUST_GAIN_ON_SUCCESS)
        state.status = AgentStatus.IDLE
        
        client = await self._get_client()
        try:
            await client.post(
                f"{self.service_url}/api/trust",
                json={
                    "from_dsid": f"user_{state.agent_id.split('_')[0]}",
                    "to_dsid": state.dsid,
                    "trust_level": 0.9,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to record trust: {e}")
    
    async def get_agent_state(self, agent_id: str) -> Optional[AgentState]:
        """Get current agent state."""
        return self._agent_states.get(agent_id)
    
    async def check_budget(self, agent_id: str, required: float = None) -> bool:
        """Check if agent has sufficient budget."""
        state = self._agent_states.get(agent_id)
        if not state:
            return False
        
        required = required or self.FILE_CREATION_COST
        return state.budget >= required
    
    async def check_invariants(self) -> InvariantStatus:
        """Check all conservation law invariants."""
        client = await self._get_client()
        status = InvariantStatus()
        
        try:
            response = await client.get(f"{self.service_url}/api/invariants")
            
            if response.status_code == 200:
                data = response.json()
                
                for inv in data.get("invariants", []):
                    inv_id = inv.get("id", "")
                    violated = inv.get("violated", False)
                    
                    if inv_id == "mass_conservation":
                        status.mass_conservation = not violated
                    elif inv_id == "energy_conservation":
                        status.energy_conservation = not violated
                    elif inv_id == "identity_uniqueness":
                        status.identity_uniqueness = not violated
                    elif inv_id == "causality":
                        status.causality = not violated
                    elif inv_id == "trust_bounds":
                        status.trust_bounds = not violated
                    elif inv_id == "non_negative_value":
                        status.non_negative_value = not violated
                    
                    if violated:
                        status.violations.append(inv.get("name", inv_id))
                        
        except Exception as e:
            logger.warning(f"Failed to check invariants: {e}")
        
        return status
    
    async def enforce_invariants(self) -> int:
        """Enforce invariants and fix violations."""
        client = await self._get_client()
        
        try:
            response = await client.post(f"{self.service_url}/api/invariants/enforce")
            
            if response.status_code == 200:
                data = response.json()
                fixed = data.get("nodes_fixed", 0)
                logger.info(f"Enforced invariants, fixed {fixed} nodes")
                return fixed
                
        except Exception as e:
            logger.warning(f"Failed to enforce invariants: {e}")
        
        return 0
    
    async def run_simulation(self, steps: int = 100) -> Dict[str, Any]:
        """Run physics simulation."""
        client = await self._get_client()
        
        try:
            response = await client.post(
                f"{self.service_url}/api/simulate",
                json={"steps": steps},
            )
            
            if response.status_code == 200:
                return response.json()
                
        except Exception as e:
            logger.warning(f"Failed to run simulation: {e}")
        
        return {}
    
    async def get_metrics(self) -> Dict[str, Any]:
        """Get State Physics metrics."""
        client = await self._get_client()
        
        try:
            response = await client.get(f"{self.service_url}/api/metrics")
            
            if response.status_code == 200:
                return response.json()
                
        except Exception as e:
            logger.warning(f"Failed to get metrics: {e}")
        
        return {}
    
    async def get_full_state(self) -> Dict[str, Any]:
        """Get full State Physics state."""
        client = await self._get_client()
        
        try:
            response = await client.get(f"{self.service_url}/api/state")
            
            if response.status_code == 200:
                return response.json()
                
        except Exception as e:
            logger.warning(f"Failed to get state: {e}")
        
        return {}


_state_tracker: Optional[StateTracker] = None


def get_state_tracker() -> StateTracker:
    """Get singleton state tracker instance."""
    global _state_tracker
    if _state_tracker is None:
        _state_tracker = StateTracker()
    return _state_tracker
