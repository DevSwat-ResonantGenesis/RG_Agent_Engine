"""
BLOCKCHAIN INTEGRATION FOR AGENTS
=================================

Records all agent decisions and actions on the blockchain.
Provides cryptographic proof of agent behavior.

Features:
- All agent actions recorded immutably
- Decision verification on-chain
- Agent reputation on blockchain
- Cross-agent trust via blockchain
- Audit trail for compliance
"""

import asyncio
import logging
import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)


@dataclass
class BlockchainProof:
    """Proof of an action recorded on blockchain."""
    tx_hash: str
    block_number: Optional[int]
    timestamp: str
    action_type: str
    agent_id: str
    verified: bool = False


class AgentBlockchainClient:
    """
    Client for recording agent actions on the blockchain.
    """
    
    def __init__(self, blockchain_url: str = None):
        self.blockchain_url = blockchain_url or "http://blockchain_node_1:8000"
        self._client: Optional[httpx.AsyncClient] = None
        self._cache: Dict[str, BlockchainProof] = {}
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client
    
    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
    
    def _compute_hash(self, data: Dict) -> str:
        """Compute hash of action data."""
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    
    async def record_agent_action(
        self,
        agent_id: str,
        action_type: str,
        action_data: Dict[str, Any],
        goal: Optional[str] = None,
    ) -> Optional[BlockchainProof]:
        """Record an agent action on the blockchain."""
        client = await self._get_client()
        
        timestamp = datetime.now(timezone.utc).isoformat()
        
        payload = {
            "agent_id": agent_id,
            "action_type": action_type,
            "action_data": action_data,
            "goal": goal,
            "timestamp": timestamp,
            "action_hash": self._compute_hash(action_data),
        }
        
        try:
            response = await client.post(
                f"{self.blockchain_url}/distributed/transactions",
                json={
                    "tx_type": "agent_action",
                    "payload": payload,
                },
            )
            
            if response.status_code == 200:
                result = response.json()
                proof = BlockchainProof(
                    tx_hash=result.get("tx_hash", ""),
                    block_number=None,
                    timestamp=timestamp,
                    action_type=action_type,
                    agent_id=agent_id,
                )
                self._cache[proof.tx_hash] = proof
                
                logger.info(f"Recorded action {action_type} for agent {agent_id}: {proof.tx_hash}")
                return proof
                
        except Exception as e:
            logger.error(f"Failed to record action on blockchain: {e}")
        
        return None
    
    async def record_decision(
        self,
        agent_id: str,
        decision: str,
        reasoning: str,
        options_considered: List[str] = None,
        confidence: float = 1.0,
    ) -> Optional[BlockchainProof]:
        """Record an agent decision on the blockchain."""
        return await self.record_agent_action(
            agent_id=agent_id,
            action_type="decision",
            action_data={
                "decision": decision,
                "reasoning": reasoning,
                "options": options_considered or [],
                "confidence": confidence,
            },
        )
    
    async def record_goal_started(
        self,
        agent_id: str,
        goal: str,
        sub_goals: List[str] = None,
    ) -> Optional[BlockchainProof]:
        """Record goal initiation."""
        return await self.record_agent_action(
            agent_id=agent_id,
            action_type="goal_started",
            action_data={
                "goal": goal,
                "sub_goals": sub_goals or [],
            },
            goal=goal,
        )
    
    async def record_goal_completed(
        self,
        agent_id: str,
        goal: str,
        result: Dict[str, Any],
        success: bool = True,
    ) -> Optional[BlockchainProof]:
        """Record goal completion."""
        return await self.record_agent_action(
            agent_id=agent_id,
            action_type="goal_completed",
            action_data={
                "goal": goal,
                "result": result,
                "success": success,
            },
            goal=goal,
        )
    
    async def record_agent_spawn(
        self,
        parent_id: str,
        child_id: str,
        child_goal: str,
    ) -> Optional[BlockchainProof]:
        """Record agent spawning another agent."""
        return await self.record_agent_action(
            agent_id=parent_id,
            action_type="agent_spawned",
            action_data={
                "parent_id": parent_id,
                "child_id": child_id,
                "child_goal": child_goal,
            },
        )
    
    async def record_message_sent(
        self,
        from_agent: str,
        to_agent: str,
        message_type: str,
        message_hash: str,
    ) -> Optional[BlockchainProof]:
        """Record inter-agent message."""
        return await self.record_agent_action(
            agent_id=from_agent,
            action_type="message_sent",
            action_data={
                "from": from_agent,
                "to": to_agent,
                "type": message_type,
                "hash": message_hash,
            },
        )
    
    async def verify_action(self, tx_hash: str) -> bool:
        """Verify an action exists on blockchain."""
        client = await self._get_client()
        
        try:
            response = await client.get(
                f"{self.blockchain_url}/blockchain/transactions/{tx_hash}",
            )
            
            if response.status_code == 200:
                return True
                
        except Exception as e:
            logger.error(f"Verification failed: {e}")
        
        return False
    
    async def get_agent_history(
        self,
        agent_id: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get action history for an agent from blockchain."""
        client = await self._get_client()
        
        try:
            response = await client.get(
                f"{self.blockchain_url}/distributed/state/agent:{agent_id}:actions",
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get("value", [])[-limit:]
                
        except Exception as e:
            logger.debug(f"Could not get history: {e}")
        
        return []
    
    async def get_agent_reputation(self, agent_id: str) -> Dict[str, Any]:
        """Get agent reputation from blockchain."""
        history = await self.get_agent_history(agent_id)
        
        if not history:
            return {"agent_id": agent_id, "score": 0.5, "actions": 0}
        
        # Calculate reputation from history
        total = len(history)
        successful = sum(1 for h in history if h.get("success", True))
        
        return {
            "agent_id": agent_id,
            "score": successful / total if total > 0 else 0.5,
            "actions": total,
            "successful": successful,
        }


class BlockchainVerifiedExecution:
    """
    Executes agent actions with blockchain verification.
    Every action is recorded and verifiable.
    """
    
    def __init__(self):
        self.client = AgentBlockchainClient()
        self._pending_verifications: Dict[str, asyncio.Task] = {}
    
    async def execute_with_proof(
        self,
        agent_id: str,
        action: str,
        action_fn,
        *args,
        **kwargs,
    ) -> Dict[str, Any]:
        """Execute an action and record proof on blockchain."""
        # Record intent
        intent_proof = await self.client.record_agent_action(
            agent_id=agent_id,
            action_type=f"{action}_intent",
            action_data={"args": str(args), "kwargs": str(kwargs)},
        )
        
        result = None
        error = None
        
        try:
            # Execute action
            result = await action_fn(*args, **kwargs)
            
            # Record success
            await self.client.record_agent_action(
                agent_id=agent_id,
                action_type=f"{action}_success",
                action_data={"result": str(result)[:500]},
            )
            
        except Exception as e:
            error = str(e)
            
            # Record failure
            await self.client.record_agent_action(
                agent_id=agent_id,
                action_type=f"{action}_failure",
                action_data={"error": error},
            )
        
        return {
            "success": error is None,
            "result": result,
            "error": error,
            "proof": intent_proof.tx_hash if intent_proof else None,
        }
    
    async def verify_execution_chain(
        self,
        agent_id: str,
        tx_hashes: List[str],
    ) -> Dict[str, Any]:
        """Verify a chain of executions."""
        verified = []
        failed = []
        
        for tx_hash in tx_hashes:
            if await self.client.verify_action(tx_hash):
                verified.append(tx_hash)
            else:
                failed.append(tx_hash)
        
        return {
            "agent_id": agent_id,
            "total": len(tx_hashes),
            "verified": len(verified),
            "failed": len(failed),
            "valid": len(failed) == 0,
        }


# Global instances
_blockchain_client: Optional[AgentBlockchainClient] = None
_verified_executor: Optional[BlockchainVerifiedExecution] = None


async def get_blockchain_client() -> AgentBlockchainClient:
    """Get blockchain client."""
    global _blockchain_client
    if _blockchain_client is None:
        _blockchain_client = AgentBlockchainClient()
    return _blockchain_client


async def get_verified_executor() -> BlockchainVerifiedExecution:
    """Get verified executor."""
    global _verified_executor
    if _verified_executor is None:
        _verified_executor = BlockchainVerifiedExecution()
    return _verified_executor


# Convenience functions
async def record_action(agent_id: str, action_type: str, data: Dict) -> Optional[str]:
    """Record an action and return tx hash."""
    client = await get_blockchain_client()
    proof = await client.record_agent_action(agent_id, action_type, data)
    return proof.tx_hash if proof else None


async def verify_agent(agent_id: str) -> Dict[str, Any]:
    """Get verified reputation of an agent."""
    client = await get_blockchain_client()
    return await client.get_agent_reputation(agent_id)
