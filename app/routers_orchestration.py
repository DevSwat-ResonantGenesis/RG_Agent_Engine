"""
ORCHESTRATION & SWARM API ENDPOINTS
====================================

API for multi-agent orchestration and swarm control.
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from .multi_agent_orchestrator import get_orchestrator, MultiAgentOrchestrator, AgentRole
from .swarm_controller import get_swarm_controller, SwarmController, SwarmMode
from .blockchain_integration import get_blockchain_client, AgentBlockchainClient

router = APIRouter(prefix="/orchestration", tags=["orchestration"])


# === REQUEST MODELS ===

class RegisterAgentRequest(BaseModel):
    agent_id: str
    name: str
    role: str = "executor"
    capabilities: List[str] = []


class SubmitGoalRequest(BaseModel):
    description: str
    priority: int = 1


class CreateSwarmRequest(BaseModel):
    name: str
    goal: str
    mode: str = "parallel"
    agent_count: int = 5


class ScaleSwarmRequest(BaseModel):
    new_count: int


class ReportTaskRequest(BaseModel):
    task_id: str
    agent_id: str
    result: Dict[str, Any]
    success: bool = True


# === DEPENDENCIES ===

async def get_orch() -> MultiAgentOrchestrator:
    return await get_orchestrator()


async def get_swarm() -> SwarmController:
    return await get_swarm_controller()


async def get_bc() -> AgentBlockchainClient:
    return await get_blockchain_client()


# === ORCHESTRATOR ENDPOINTS ===

@router.post("/agents/register")
async def register_agent(
    request: RegisterAgentRequest,
    orch: MultiAgentOrchestrator = Depends(get_orch),
):
    """Register an agent with the orchestrator."""
    role = AgentRole[request.role.upper()] if request.role.upper() in AgentRole.__members__ else AgentRole.EXECUTOR
    
    await orch.register_agent(
        agent_id=request.agent_id,
        name=request.name,
        role=role,
        capabilities=request.capabilities,
    )
    
    return {"status": "registered", "agent_id": request.agent_id, "role": role.value}


@router.post("/goals/submit")
async def submit_goal(
    request: SubmitGoalRequest,
    orch: MultiAgentOrchestrator = Depends(get_orch),
    bc: AgentBlockchainClient = Depends(get_bc),
):
    """Submit a high-level goal for the agent swarm."""
    goal_id = await orch.submit_goal(request.description, request.priority)
    
    # Record on blockchain
    await bc.record_agent_action(
        agent_id="orchestrator",
        action_type="goal_submitted",
        action_data={"goal_id": goal_id, "description": request.description},
    )
    
    return {"goal_id": goal_id, "status": "submitted"}


@router.get("/goals/{goal_id}")
async def get_goal_status(
    goal_id: str,
    orch: MultiAgentOrchestrator = Depends(get_orch),
):
    """Get status of a goal."""
    status = orch.get_goal_status(goal_id)
    if not status:
        raise HTTPException(status_code=404, detail="Goal not found")
    return status


@router.post("/tasks/report")
async def report_task_complete(
    request: ReportTaskRequest,
    orch: MultiAgentOrchestrator = Depends(get_orch),
):
    """Report task completion from an agent."""
    await orch.report_task_complete(
        task_id=request.task_id,
        agent_id=request.agent_id,
        result=request.result,
        success=request.success,
    )
    return {"status": "reported"}


@router.get("/stats")
async def get_orchestrator_stats(
    orch: MultiAgentOrchestrator = Depends(get_orch),
):
    """Get orchestrator statistics."""
    return orch.get_stats()


# === SWARM ENDPOINTS ===

@router.post("/swarms/create")
async def create_swarm(
    request: CreateSwarmRequest,
    swarm: SwarmController = Depends(get_swarm),
    bc: AgentBlockchainClient = Depends(get_bc),
):
    """Create a new agent swarm."""
    mode = SwarmMode[request.mode.upper()] if request.mode.upper() in SwarmMode.__members__ else SwarmMode.PARALLEL
    
    swarm_id = await swarm.create_swarm(
        name=request.name,
        goal=request.goal,
        mode=mode,
        agent_count=request.agent_count,
    )
    
    # Record on blockchain
    await bc.record_agent_action(
        agent_id="swarm_controller",
        action_type="swarm_created",
        action_data={
            "swarm_id": swarm_id,
            "name": request.name,
            "mode": mode.value,
            "agents": request.agent_count,
        },
    )
    
    return {"swarm_id": swarm_id, "status": "created", "mode": mode.value}


@router.get("/swarms/{swarm_id}")
async def get_swarm_status(
    swarm_id: str,
    swarm: SwarmController = Depends(get_swarm),
):
    """Get status of a swarm."""
    status = swarm.get_swarm_status(swarm_id)
    if not status:
        raise HTTPException(status_code=404, detail="Swarm not found")
    return status


@router.post("/swarms/{swarm_id}/scale")
async def scale_swarm(
    swarm_id: str,
    request: ScaleSwarmRequest,
    swarm: SwarmController = Depends(get_swarm),
):
    """Scale a swarm to a new agent count."""
    await swarm.scale_swarm(swarm_id, request.new_count)
    return {"status": "scaled", "new_count": request.new_count}


@router.get("/swarms")
async def list_swarms(
    swarm: SwarmController = Depends(get_swarm),
):
    """List all swarms."""
    return {"swarms": swarm.get_all_swarms()}


# === BLOCKCHAIN VERIFICATION ===

@router.get("/agents/{agent_id}/reputation")
async def get_agent_reputation(
    agent_id: str,
    bc: AgentBlockchainClient = Depends(get_bc),
):
    """Get blockchain-verified agent reputation."""
    return await bc.get_agent_reputation(agent_id)


@router.get("/agents/{agent_id}/history")
async def get_agent_history(
    agent_id: str,
    limit: int = 100,
    bc: AgentBlockchainClient = Depends(get_bc),
):
    """Get agent action history from blockchain."""
    history = await bc.get_agent_history(agent_id, limit)
    return {"agent_id": agent_id, "history": history}


@router.get("/verify/{tx_hash}")
async def verify_action(
    tx_hash: str,
    bc: AgentBlockchainClient = Depends(get_bc),
):
    """Verify an action exists on blockchain."""
    verified = await bc.verify_action(tx_hash)
    return {"tx_hash": tx_hash, "verified": verified}
