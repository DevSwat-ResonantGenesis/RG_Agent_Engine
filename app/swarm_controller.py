"""
AGENT SWARM CONTROLLER
======================

Controls agent swarms for massive parallel execution.
Enables hundreds of agents to work together autonomously.

Features:
- Dynamic swarm scaling
- Load balancing across agents
- Emergent behavior coordination
- Collective intelligence aggregation
- Self-healing swarm recovery
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
import random

logger = logging.getLogger(__name__)


class SwarmMode(Enum):
    PARALLEL = "parallel"       # All agents work independently
    PIPELINE = "pipeline"       # Agents work in sequence
    CONSENSUS = "consensus"     # Agents vote on decisions
    COMPETITIVE = "competitive" # Agents compete for best result
    COLLABORATIVE = "collaborative"  # Agents build on each other


@dataclass
class SwarmAgent:
    """An agent in the swarm."""
    id: str
    swarm_id: str
    role: str = "worker"
    status: str = "idle"
    current_task: Optional[str] = None
    contributions: int = 0
    last_active: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Swarm:
    """A swarm of coordinated agents."""
    id: str
    name: str
    mode: SwarmMode
    goal: str
    agents: Dict[str, SwarmAgent] = field(default_factory=dict)
    status: str = "initializing"
    results: List[Dict[str, Any]] = field(default_factory=list)
    consensus_votes: Dict[str, Dict[str, int]] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SwarmController:
    """
    Controls multiple agent swarms for massive parallel autonomous execution.
    """
    
    def __init__(self):
        self.swarms: Dict[str, Swarm] = {}
        self.agent_pool: Dict[str, SwarmAgent] = {}
        self._running = False
        self._control_task = None
    
    async def start(self):
        """Start the swarm controller."""
        self._running = True
        self._control_task = asyncio.create_task(self._control_loop())
        logger.info("Swarm Controller started")
    
    async def stop(self):
        """Stop the swarm controller."""
        self._running = False
        if self._control_task:
            self._control_task.cancel()
        logger.info("Swarm Controller stopped")
    
    async def create_swarm(
        self,
        name: str,
        goal: str,
        mode: SwarmMode = SwarmMode.PARALLEL,
        agent_count: int = 5,
    ) -> str:
        """Create a new agent swarm."""
        swarm_id = str(uuid4())
        
        swarm = Swarm(
            id=swarm_id,
            name=name,
            mode=mode,
            goal=goal,
        )
        
        # Spawn agents for swarm
        for i in range(agent_count):
            agent = await self._spawn_swarm_agent(swarm_id, f"agent_{i}")
            swarm.agents[agent.id] = agent
        
        swarm.status = "active"
        self.swarms[swarm_id] = swarm
        
        logger.info(f"Created swarm {name} ({swarm_id}) with {agent_count} agents in {mode.value} mode")
        
        # Start swarm execution
        asyncio.create_task(self._execute_swarm(swarm))
        
        return swarm_id
    
    async def _spawn_swarm_agent(self, swarm_id: str, name: str) -> SwarmAgent:
        """Spawn a new agent for the swarm."""
        agent_id = str(uuid4())
        
        agent = SwarmAgent(
            id=agent_id,
            swarm_id=swarm_id,
            role="worker",
        )
        
        self.agent_pool[agent_id] = agent
        
        # Register with autonomous daemon
        try:
            from .autonomous_daemon import get_daemon
            daemon = await get_daemon()
            await daemon.register_autonomous_agent(
                agent_id=agent_id,
                initial_goal=f"Swarm agent for {swarm_id}",
            )
        except Exception as e:
            logger.debug(f"Could not register with daemon: {e}")
        
        return agent
    
    async def _execute_swarm(self, swarm: Swarm):
        """Execute swarm based on mode."""
        try:
            if swarm.mode == SwarmMode.PARALLEL:
                await self._execute_parallel(swarm)
            elif swarm.mode == SwarmMode.PIPELINE:
                await self._execute_pipeline(swarm)
            elif swarm.mode == SwarmMode.CONSENSUS:
                await self._execute_consensus(swarm)
            elif swarm.mode == SwarmMode.COMPETITIVE:
                await self._execute_competitive(swarm)
            elif swarm.mode == SwarmMode.COLLABORATIVE:
                await self._execute_collaborative(swarm)
                
        except Exception as e:
            logger.error(f"Swarm execution error: {e}")
            swarm.status = "failed"
    
    async def _execute_parallel(self, swarm: Swarm):
        """Execute all agents in parallel on the same goal."""
        tasks = []
        
        for agent in swarm.agents.values():
            tasks.append(self._agent_work(agent, swarm.goal))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if not isinstance(result, Exception):
                swarm.results.append(result)
        
        swarm.status = "completed"
        logger.info(f"Parallel swarm {swarm.id} completed with {len(swarm.results)} results")
    
    async def _execute_pipeline(self, swarm: Swarm):
        """Execute agents in sequence, each building on previous."""
        agents = list(swarm.agents.values())
        current_context = {"goal": swarm.goal}
        
        for agent in agents:
            result = await self._agent_work(agent, swarm.goal, current_context)
            if result:
                swarm.results.append(result)
                current_context = {**current_context, "previous_result": result}
        
        swarm.status = "completed"
        logger.info(f"Pipeline swarm {swarm.id} completed")
    
    async def _execute_consensus(self, swarm: Swarm):
        """Execute agents and reach consensus on result."""
        # Each agent proposes a solution
        proposals = []
        
        for agent in swarm.agents.values():
            result = await self._agent_work(agent, swarm.goal)
            if result:
                proposals.append({"agent": agent.id, "proposal": result})
        
        # Voting round
        votes: Dict[str, int] = {}
        for proposal in proposals:
            # Each agent votes (simplified: random vote for now)
            for agent in swarm.agents.values():
                if agent.id != proposal["agent"]:
                    vote_key = str(proposals.index(proposal))
                    votes[vote_key] = votes.get(vote_key, 0) + 1
        
        # Winner
        if votes:
            winner_idx = max(votes.keys(), key=lambda k: votes[k])
            swarm.results = [proposals[int(winner_idx)]["proposal"]]
        
        swarm.status = "completed"
        logger.info(f"Consensus swarm {swarm.id} reached agreement")
    
    async def _execute_competitive(self, swarm: Swarm):
        """Agents compete, best result wins."""
        results_with_scores = []
        
        for agent in swarm.agents.values():
            result = await self._agent_work(agent, swarm.goal)
            if result:
                # Score result (simplified)
                score = random.random()  # Would be real evaluation
                results_with_scores.append({"result": result, "score": score, "agent": agent.id})
        
        # Best result wins
        if results_with_scores:
            winner = max(results_with_scores, key=lambda x: x["score"])
            swarm.results = [winner["result"]]
            logger.info(f"Agent {winner['agent']} won competition")
        
        swarm.status = "completed"
    
    async def _execute_collaborative(self, swarm: Swarm):
        """Agents collaborate, combining their work."""
        partial_results = []
        
        # Split goal into parts
        agents = list(swarm.agents.values())
        parts = await self._split_goal(swarm.goal, len(agents))
        
        # Each agent works on their part
        for agent, part in zip(agents, parts):
            result = await self._agent_work(agent, part)
            if result:
                partial_results.append(result)
        
        # Combine results
        combined = await self._combine_results(partial_results, swarm.goal)
        swarm.results = [combined]
        
        swarm.status = "completed"
        logger.info(f"Collaborative swarm {swarm.id} combined {len(partial_results)} contributions")
    
    async def _agent_work(
        self,
        agent: SwarmAgent,
        goal: str,
        context: Dict[str, Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """Execute work for a single agent."""
        agent.status = "working"
        agent.current_task = goal
        
        try:
            import httpx
            
            prompt = f"""You are an autonomous agent in a swarm. Complete this task:

TASK: {goal}

CONTEXT: {context or {}}

Respond with your result in JSON format."""

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "http://llm_service:8000/llm/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.7,
                    },
                )
                
                if response.status_code == 200:
                    result = response.json()
                    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                    
                    agent.contributions += 1
                    agent.status = "idle"
                    agent.last_active = datetime.now(timezone.utc).isoformat()
                    
                    return {"agent": agent.id, "output": content}
                    
        except Exception as e:
            logger.error(f"Agent {agent.id} work failed: {e}")
            agent.status = "error"
        
        return None
    
    async def _split_goal(self, goal: str, parts: int) -> List[str]:
        """Split a goal into parts for collaborative work."""
        # Simple split - would use LLM in production
        return [f"Part {i+1} of: {goal}" for i in range(parts)]
    
    async def _combine_results(self, results: List[Dict], goal: str) -> Dict[str, Any]:
        """Combine multiple results into one."""
        return {
            "combined": True,
            "goal": goal,
            "contributions": results,
        }
    
    async def _control_loop(self):
        """Main control loop for managing swarms."""
        while self._running:
            try:
                # Health check swarms
                for swarm in list(self.swarms.values()):
                    await self._check_swarm_health(swarm)
                
                await asyncio.sleep(5)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Control loop error: {e}")
    
    async def _check_swarm_health(self, swarm: Swarm):
        """Check health of a swarm and recover if needed."""
        if swarm.status != "active":
            return
        
        # Check for stuck agents
        for agent in swarm.agents.values():
            if agent.status == "error":
                # Replace failed agent
                new_agent = await self._spawn_swarm_agent(swarm.id, f"replacement_{agent.id[:8]}")
                swarm.agents[new_agent.id] = new_agent
                del swarm.agents[agent.id]
                logger.info(f"Replaced failed agent {agent.id} with {new_agent.id}")
    
    async def scale_swarm(self, swarm_id: str, new_count: int):
        """Scale a swarm to a new agent count."""
        swarm = self.swarms.get(swarm_id)
        if not swarm:
            return
        
        current = len(swarm.agents)
        
        if new_count > current:
            # Add agents
            for i in range(new_count - current):
                agent = await self._spawn_swarm_agent(swarm_id, f"scaled_{i}")
                swarm.agents[agent.id] = agent
        elif new_count < current:
            # Remove agents
            to_remove = list(swarm.agents.keys())[new_count:]
            for agent_id in to_remove:
                del swarm.agents[agent_id]
                del self.agent_pool[agent_id]
        
        logger.info(f"Scaled swarm {swarm_id} from {current} to {new_count} agents")
    
    def get_swarm_status(self, swarm_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a swarm."""
        swarm = self.swarms.get(swarm_id)
        if not swarm:
            return None
        
        return {
            "id": swarm.id,
            "name": swarm.name,
            "mode": swarm.mode.value,
            "status": swarm.status,
            "agents": len(swarm.agents),
            "results": len(swarm.results),
        }
    
    def get_all_swarms(self) -> List[Dict[str, Any]]:
        """Get all swarms."""
        return [self.get_swarm_status(sid) for sid in self.swarms]


# Global instance
_controller: Optional[SwarmController] = None


async def get_swarm_controller() -> SwarmController:
    """Get or create swarm controller."""
    global _controller
    if _controller is None:
        _controller = SwarmController()
        await _controller.start()
    return _controller
