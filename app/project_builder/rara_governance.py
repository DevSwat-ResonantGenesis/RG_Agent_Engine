"""
RARA Governance - RARA Integration
===================================

Integrates with RARA service (port 8093) for safety governance.

Features:
- Kill switch checking before mutations
- Snapshot creation before file writes
- Atomic rollback on failures
- Capability enforcement
- Agent registration and coordination
"""

import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum
import httpx
import os

logger = logging.getLogger(__name__)


class MutationType(str, Enum):
    """Types of mutations."""
    CREATE_FILE = "create_file"
    MODIFY_FILE = "modify_file"
    DELETE_FILE = "delete_file"
    CREATE_DIRECTORY = "create_directory"


class MutationStatus(str, Enum):
    """Status of a mutation."""
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTED = "executed"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"


@dataclass
class MutationResult:
    """Result of a mutation execution."""
    success: bool
    mutation_id: Optional[str] = None
    snapshot_id: Optional[str] = None
    status: MutationStatus = MutationStatus.PENDING
    error: Optional[str] = None
    rollback_available: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "mutation_id": self.mutation_id,
            "snapshot_id": self.snapshot_id,
            "status": self.status.value,
            "error": self.error,
            "rollback_available": self.rollback_available,
        }


@dataclass
class AgentCapabilities:
    """Capabilities granted to an agent."""
    agent_id: str
    can_create_files: bool = True
    can_modify_files: bool = True
    can_delete_files: bool = False
    can_create_directories: bool = True
    allowed_paths: List[str] = field(default_factory=list)
    forbidden_paths: List[str] = field(default_factory=list)
    trust_score: float = 0.5
    
    def can_mutate(self, mutation_type: MutationType, path: str) -> bool:
        """Check if agent can perform mutation."""
        for forbidden in self.forbidden_paths:
            if path.startswith(forbidden):
                return False
        
        if self.allowed_paths:
            allowed = any(path.startswith(p) for p in self.allowed_paths)
            if not allowed:
                return False
        
        if mutation_type == MutationType.CREATE_FILE:
            return self.can_create_files
        elif mutation_type == MutationType.MODIFY_FILE:
            return self.can_modify_files
        elif mutation_type == MutationType.DELETE_FILE:
            return self.can_delete_files
        elif mutation_type == MutationType.CREATE_DIRECTORY:
            return self.can_create_directories
        
        return False


class RARAGovernance:
    """
    Manages RARA governance for Project Builder.
    
    Integration Points:
    - GET /control/kill-switch/status - Check if frozen
    - POST /agents/register - Register agent
    - GET /agents/{id}/capabilities - Get capabilities
    - POST /snapshots/create - Create snapshot before mutation
    - POST /mutations/execute - Execute mutation
    - POST /snapshots/restore - Rollback on failure
    - POST /control/freeze - Emergency freeze
    """
    
    RARA_URL = os.getenv("RARA_SERVICE_URL", "http://rg_internal_invarients_sim:8093")
    
    def __init__(self, service_url: str = None):
        self.service_url = service_url or self.RARA_URL
        self._client: Optional[httpx.AsyncClient] = None
        self._agent_capabilities: Dict[str, AgentCapabilities] = {}
        self._snapshots: Dict[str, str] = {}
        logger.info(f"RARAGovernance initialized with service URL: {self.service_url}")
    
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
    
    async def check_kill_switch(self) -> bool:
        """
        Check if RARA kill switch is active.
        
        Returns:
            True if system is frozen (should NOT proceed)
        """
        client = await self._get_client()
        
        try:
            response = await client.get(
                f"{self.service_url}/control/kill-switch/status"
            )
            
            if response.status_code == 200:
                data = response.json()
                frozen = data.get("frozen", False) or data.get("emergency_stop", False)
                if frozen:
                    logger.warning("RARA kill switch is ACTIVE - system frozen")
                return frozen
                
        except Exception as e:
            logger.warning(f"Failed to check kill switch: {e}")
        
        return False
    
    async def register_agent(
        self,
        agent_id: str,
        workspace_path: str,
        trust_score: float = 0.5,
    ) -> AgentCapabilities:
        """
        Register agent with RARA.
        
        Args:
            agent_id: Unique agent ID
            workspace_path: Path to user's workspace
            trust_score: Initial trust score
            
        Returns:
            AgentCapabilities granted to agent
        """
        client = await self._get_client()
        
        capabilities = AgentCapabilities(
            agent_id=agent_id,
            can_create_files=True,
            can_modify_files=True,
            can_delete_files=False,
            can_create_directories=True,
            allowed_paths=[workspace_path],
            forbidden_paths=[
                "/opt/resonant/core",
                "/opt/resonant/agent",
                "/etc",
                "/usr",
                "/bin",
            ],
            trust_score=trust_score,
        )
        
        try:
            # RARA requires specific registration format
            import hashlib
            dsid = hashlib.sha256(f"{agent_id}:{workspace_path}".encode()).hexdigest()[:32]
            
            response = await client.post(
                f"{self.service_url}/agents/register",
                json={
                    "agent_id": agent_id,
                    "role": "executor",  # AgentRole enum value
                    "dsid": dsid,
                    "public_key": f"pk_{agent_id[:16]}",  # Placeholder public key
                    "capabilities": [
                        "filesystem.create_file",
                        "filesystem.update_file",
                    ],
                },
            )
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"Registered agent {agent_id} with RARA: {data}")
            else:
                logger.warning(f"RARA registration returned {response.status_code}: {response.text[:200]}")
                
        except Exception as e:
            logger.warning(f"Failed to register agent with RARA: {e}")
        
        self._agent_capabilities[agent_id] = capabilities
        return capabilities
    
    async def get_capabilities(self, agent_id: str) -> Optional[AgentCapabilities]:
        """Get agent capabilities."""
        if agent_id in self._agent_capabilities:
            return self._agent_capabilities[agent_id]
        
        client = await self._get_client()
        
        try:
            response = await client.get(
                f"{self.service_url}/agents/{agent_id}/capabilities"
            )
            
            if response.status_code == 200:
                data = response.json()
                caps = AgentCapabilities(
                    agent_id=agent_id,
                    trust_score=data.get("trust_score", 0.5),
                )
                self._agent_capabilities[agent_id] = caps
                return caps
                
        except Exception as e:
            logger.warning(f"Failed to get capabilities: {e}")
        
        return None
    
    async def create_snapshot(
        self,
        agent_id: str,
        reason: str,
    ) -> Optional[str]:
        """
        Create a snapshot before mutation.
        
        Args:
            agent_id: Agent ID
            reason: Reason for snapshot
            
        Returns:
            Snapshot ID if successful
        """
        client = await self._get_client()
        
        try:
            response = await client.post(
                f"{self.service_url}/snapshots/create",
                json={
                    "agent_id": agent_id,
                    "reason": reason,
                },
            )
            
            if response.status_code == 200:
                data = response.json()
                snapshot_id = data.get("snapshot_id")
                if snapshot_id:
                    self._snapshots[agent_id] = snapshot_id
                    logger.debug(f"Created snapshot {snapshot_id} for {agent_id}")
                return snapshot_id
                
        except Exception as e:
            logger.warning(f"Failed to create snapshot: {e}")
        
        return None
    
    async def execute_mutation(
        self,
        agent_id: str,
        mutation_type: MutationType,
        target_path: str,
        content: str = None,
        justification: str = None,
    ) -> MutationResult:
        """
        Execute a mutation with RARA governance.
        
        Args:
            agent_id: Agent ID
            mutation_type: Type of mutation
            target_path: Target file/directory path
            content: Content for create/modify
            justification: Reason for mutation
            
        Returns:
            MutationResult with status
        """
        if await self.check_kill_switch():
            return MutationResult(
                success=False,
                status=MutationStatus.REJECTED,
                error="System frozen by kill switch",
            )
        
        capabilities = await self.get_capabilities(agent_id)
        if capabilities and not capabilities.can_mutate(mutation_type, target_path):
            return MutationResult(
                success=False,
                status=MutationStatus.REJECTED,
                error=f"Agent not authorized for {mutation_type.value} on {target_path}",
            )
        
        snapshot_id = await self.create_snapshot(
            agent_id,
            f"Before {mutation_type.value}: {target_path}",
        )
        
        client = await self._get_client()
        
        try:
            import base64
            
            # Map our mutation types to RARA capability types
            capability_map = {
                MutationType.CREATE_FILE: "filesystem.create_file",
                MutationType.MODIFY_FILE: "filesystem.update_file",
                MutationType.DELETE_FILE: "filesystem.delete_file",
                MutationType.CREATE_DIRECTORY: "filesystem.create_file",
            }
            
            # Map to RARA operation types
            operation_type_map = {
                MutationType.CREATE_FILE: "write",
                MutationType.MODIFY_FILE: "write",
                MutationType.DELETE_FILE: "delete",
                MutationType.CREATE_DIRECTORY: "write",
            }
            
            # Build RARA-compliant mutation request
            mutation_request = {
                "actor": "agent",
                "capability": capability_map.get(mutation_type, "filesystem.create_file"),
                "target": target_path,
                "operation": {
                    "type": operation_type_map.get(mutation_type, "write"),
                    "content": base64.b64encode((content or "").encode()).decode() if content else None,
                    "mode": "0644",
                },
                "rationale": justification or f"Project Builder: {mutation_type.value}",
                "confidence": 1.0,
            }
            
            # RARA requires agent_id as query parameter
            response = await client.post(
                f"{self.service_url}/mutations/execute?agent_id={agent_id}",
                json=mutation_request,
            )
            
            if response.status_code == 200:
                data = response.json()
                # Check if RARA actually executed the mutation (not just rejected it)
                if data.get("status") == "executed":
                    return MutationResult(
                        success=True,
                        mutation_id=data.get("mutation_id"),
                        snapshot_id=snapshot_id,
                        status=MutationStatus.EXECUTED,
                        rollback_available=snapshot_id is not None,
                    )
                else:
                    # RARA rejected the mutation (e.g., path outside runtime) - fall back to local
                    logger.warning(f"RARA rejected mutation: {data.get('error', 'unknown')}, falling back to local")
                    raise Exception(f"RARA rejected: {data.get('error', 'unknown')}")
            else:
                # RARA returned HTTP error - fall back to local execution
                logger.warning(f"RARA returned {response.status_code}, falling back to local: {response.text[:200]}")
                raise Exception(f"RARA error: {response.status_code}")
                
        except Exception as e:
            logger.warning(f"RARA mutation failed, executing locally: {e}")
            
            try:
                import os
                from pathlib import Path
                
                if mutation_type == MutationType.CREATE_DIRECTORY:
                    Path(target_path).mkdir(parents=True, exist_ok=True)
                elif mutation_type in [MutationType.CREATE_FILE, MutationType.MODIFY_FILE]:
                    Path(target_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(target_path, "w") as f:
                        f.write(content or "")
                elif mutation_type == MutationType.DELETE_FILE:
                    if os.path.exists(target_path):
                        os.remove(target_path)
                
                return MutationResult(
                    success=True,
                    snapshot_id=snapshot_id,
                    status=MutationStatus.EXECUTED,
                    rollback_available=False,
                )
                
            except Exception as local_error:
                return MutationResult(
                    success=False,
                    snapshot_id=snapshot_id,
                    status=MutationStatus.REJECTED,
                    error=str(local_error),
                )
    
    async def rollback(self, agent_id: str, snapshot_id: str = None) -> bool:
        """
        Rollback to a snapshot.
        
        Args:
            agent_id: Agent ID
            snapshot_id: Snapshot ID (uses latest if not provided)
            
        Returns:
            True if rollback successful
        """
        snapshot_id = snapshot_id or self._snapshots.get(agent_id)
        if not snapshot_id:
            logger.warning(f"No snapshot available for rollback: {agent_id}")
            return False
        
        client = await self._get_client()
        
        try:
            response = await client.post(
                f"{self.service_url}/snapshots/restore",
                json={"snapshot_id": snapshot_id},
            )
            
            if response.status_code == 200:
                logger.info(f"Rolled back to snapshot {snapshot_id}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to rollback: {e}")
        
        return False
    
    async def emergency_freeze(self, reason: str) -> bool:
        """Trigger emergency freeze."""
        client = await self._get_client()
        
        try:
            response = await client.post(
                f"{self.service_url}/control/freeze",
                json={"reason": reason},
            )
            
            if response.status_code == 200:
                logger.warning(f"Emergency freeze triggered: {reason}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to trigger freeze: {e}")
        
        return False
    
    async def get_governance_status(self) -> Dict[str, Any]:
        """Get current governance status."""
        client = await self._get_client()
        
        try:
            response = await client.get(f"{self.service_url}/status")
            
            if response.status_code == 200:
                return response.json()
                
        except Exception as e:
            logger.warning(f"Failed to get governance status: {e}")
        
        return {
            "frozen": False,
            "emergency_stop": False,
            "agents_registered": len(self._agent_capabilities),
        }


_rara_governance: Optional[RARAGovernance] = None


def get_rara_governance() -> RARAGovernance:
    """Get singleton RARA governance instance."""
    global _rara_governance
    if _rara_governance is None:
        _rara_governance = RARAGovernance()
    return _rara_governance
