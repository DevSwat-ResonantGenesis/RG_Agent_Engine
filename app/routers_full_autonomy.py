"""
FULL AUTONOMY API
=================

THE MOST IMPORTANT API for Resonant Genesis.
Endpoints to start and manage full autonomous operation.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from .full_autonomy import get_full_autonomy_system, start_full_autonomy, FullAutonomySystem
from .agent_brain import get_brain_manager, BrainManager
from .autonomous_queue import get_autonomous_queue, AutonomousTaskQueue
from .agent_network import get_agent_network, AgentNetwork, AgentRole
from .system_watchdog import get_watchdog, SystemWatchdog
from .auto_startup import get_startup_manager

router = APIRouter(prefix="/autonomy", tags=["full-autonomy"])


# === REQUEST MODELS ===

class CreateAgentRequest(BaseModel):
    name: str
    goal: str
    capabilities: Optional[List[str]] = None


class SpawnAgentRequest(BaseModel):
    name: str
    role: str = "worker"
    capabilities: Optional[List[str]] = None
    parent_id: Optional[str] = None


class SetGoalRequest(BaseModel):
    goal: str


# === DEPENDENCIES ===

async def get_system() -> FullAutonomySystem:
    return await get_full_autonomy_system()


async def get_brains() -> BrainManager:
    return await get_brain_manager()


async def get_queue() -> AutonomousTaskQueue:
    return await get_autonomous_queue()


async def get_network() -> AgentNetwork:
    return await get_agent_network()


# === FULL AUTONOMY CONTROL ===

@router.post("/start")
async def start_autonomy():
    """
    START FULL AUTONOMY.
    
    After this call, agents operate COMPLETELY AUTONOMOUSLY.
    No human intervention required.
    """
    system = await start_full_autonomy()
    return {
        "status": "FULL AUTONOMY ACTIVE",
        "message": "Agents are now fully autonomous. No human intervention required.",
        "system_status": system.get_status(),
    }


@router.post("/stop")
async def stop_autonomy(
    system: FullAutonomySystem = Depends(get_system),
):
    """Stop the full autonomy system."""
    await system.stop()
    return {"status": "stopped"}


@router.get("/status")
async def get_autonomy_status(
    system: FullAutonomySystem = Depends(get_system),
):
    """Get full autonomy system status."""
    return system.get_status()


@router.get("/stats")
async def get_full_stats(
    system: FullAutonomySystem = Depends(get_system),
):
    """Get comprehensive statistics for all autonomous systems."""
    return await system.get_full_stats()


# === AUTONOMOUS AGENT CREATION ===

@router.post("/agents/create")
async def create_autonomous_agent(
    request: CreateAgentRequest,
    system: FullAutonomySystem = Depends(get_system),
):
    """
    Create a fully autonomous agent with a goal.
    The agent will pursue this goal without any human intervention.
    """
    agent_id = await system.create_autonomous_agent(
        name=request.name,
        goal=request.goal,
        capabilities=request.capabilities,
    )
    
    return {
        "agent_id": agent_id,
        "name": request.name,
        "goal": request.goal,
        "status": "AUTONOMOUS - pursuing goal independently",
    }


# === AGENT NETWORK ===

@router.get("/network/hierarchy")
async def get_network_hierarchy(
    network: AgentNetwork = Depends(get_network),
):
    """Get the agent network hierarchy."""
    return network.get_hierarchy()


@router.get("/network/stats")
async def get_network_stats(
    network: AgentNetwork = Depends(get_network),
):
    """Get agent network statistics."""
    return network.get_stats()


@router.post("/network/spawn")
async def spawn_network_agent(
    request: SpawnAgentRequest,
    network: AgentNetwork = Depends(get_network),
):
    """Manually spawn an agent in the network."""
    role_map = {
        "coordinator": AgentRole.COORDINATOR,
        "worker": AgentRole.WORKER,
        "specialist": AgentRole.SPECIALIST,
        "supervisor": AgentRole.SUPERVISOR,
        "scout": AgentRole.SCOUT,
    }
    role = role_map.get(request.role.lower(), AgentRole.WORKER)
    
    agent = await network.spawn_agent(
        name=request.name,
        role=role,
        parent_id=request.parent_id or network.root_agent_id,
        capabilities=request.capabilities or ["execute", "learn"],
    )
    
    return {
        "agent_id": agent.id,
        "name": agent.name,
        "role": agent.role.value,
    }


@router.get("/network/agents/{agent_id}")
async def get_network_agent(
    agent_id: str,
    network: AgentNetwork = Depends(get_network),
):
    """Get details of a network agent."""
    agent = network.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role.value,
        "status": agent.status,
        "workload": agent.workload,
        "parent_id": agent.parent_id,
        "children": agent.children_ids,
        "tasks_completed": agent.tasks_completed,
        "agents_spawned": agent.agents_spawned,
    }


# === AGENT BRAINS ===

@router.get("/brains")
async def list_brains(
    brain_mgr: BrainManager = Depends(get_brains),
):
    """List all active agent brains."""
    return {"brains": brain_mgr.get_all_statuses()}


@router.get("/brains/{agent_id}")
async def get_brain_status(
    agent_id: str,
    brain_mgr: BrainManager = Depends(get_brains),
):
    """Get status of a specific agent brain."""
    brain = brain_mgr.get_brain(agent_id)
    if not brain:
        raise HTTPException(status_code=404, detail="Brain not found")
    
    return brain.get_status()


@router.post("/brains/{agent_id}/goal")
async def set_brain_goal(
    agent_id: str,
    request: SetGoalRequest,
    brain_mgr: BrainManager = Depends(get_brains),
):
    """Set a new goal for an agent brain."""
    brain = brain_mgr.get_brain(agent_id)
    if not brain:
        raise HTTPException(status_code=404, detail="Brain not found")
    
    brain.current_goal = request.goal
    
    # Also add to goal pursuit
    from .goal_pursuit import get_goal_pursuit_engine
    engine = await get_goal_pursuit_engine()
    await engine.add_goal(agent_id, request.goal)
    
    return {"agent_id": agent_id, "goal": request.goal}


# === AUTONOMOUS QUEUE ===

@router.get("/queue/stats")
async def get_queue_stats(
    queue: AutonomousTaskQueue = Depends(get_queue),
):
    """Get autonomous task queue statistics."""
    return queue.get_stats()


@router.get("/queue/tasks")
async def list_queue_tasks(
    status: Optional[str] = None,
    limit: int = 50,
    queue: AutonomousTaskQueue = Depends(get_queue),
):
    """List tasks in the queue."""
    tasks = []
    for task in list(queue.tasks.values())[:limit]:
        if status and task.status.value != status:
            continue
        tasks.append({
            "id": task.id,
            "description": task.description[:100],
            "status": task.status.value,
            "priority": task.priority.value,
            "source": task.source.value,
            "assigned_agent": task.assigned_agent,
        })
    
    return {"tasks": tasks, "total": len(queue.tasks)}


# === WATCHDOG ===

@router.get("/watchdog/status")
async def get_watchdog_status():
    """Get system watchdog status."""
    watchdog = await get_watchdog()
    return watchdog.get_status()


@router.get("/watchdog/alerts")
async def get_watchdog_alerts(
    unacknowledged_only: bool = False,
):
    """Get system alerts."""
    watchdog = await get_watchdog()
    return {"alerts": watchdog.get_alerts(unacknowledged_only)}


@router.post("/watchdog/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str):
    """Acknowledge an alert."""
    watchdog = await get_watchdog()
    success = watchdog.acknowledge_alert(alert_id)
    return {"acknowledged": success}


# === STARTUP INFO ===

@router.get("/startup/status")
async def get_startup_status():
    """Get auto-startup status."""
    manager = get_startup_manager()
    return manager.get_status()


# === QUICK START ===

@router.post("/quick-start")
async def quick_start(
    agent_name: str = "ResonantAgent",
    goal: str = "Be helpful and complete tasks autonomously",
):
    """
    QUICK START: Start full autonomy with one agent.
    
    This is the easiest way to start Resonant Genesis in full autonomous mode.
    """
    # Start the system
    system = await start_full_autonomy()
    
    # Create an agent
    agent_id = await system.create_autonomous_agent(
        name=agent_name,
        goal=goal,
        capabilities=["execute", "learn", "communicate", "spawn"],
    )
    
    return {
        "status": "RESONANT GENESIS FULLY AUTONOMOUS",
        "agent_id": agent_id,
        "agent_name": agent_name,
        "goal": goal,
        "message": "System is now running autonomously. The agent will pursue its goal without human intervention.",
        "endpoints": {
            "status": "/autonomy/status",
            "stats": "/autonomy/stats",
            "brains": "/autonomy/brains",
            "network": "/autonomy/network/hierarchy",
        },
    }
