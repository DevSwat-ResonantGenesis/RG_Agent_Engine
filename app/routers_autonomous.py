"""
AUTONOMOUS AGENT API ENDPOINTS
==============================

API for managing autonomous agents that run without human intervention.
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from uuid import UUID

from .autonomous_daemon import get_daemon, AutonomousDaemon, start_autonomous_daemon
from .parallel_agent_runtime import get_runtime, ParallelAgentRuntime, AgentCapability

router = APIRouter(prefix="/autonomous", tags=["autonomous-agents"])


class RegisterAutonomousRequest(BaseModel):
    agent_id: str
    initial_goal: str
    capabilities: List[str] = []
    teams: List[str] = []


class InjectEventRequest(BaseModel):
    event_type: str
    data: Dict[str, Any]


class SendMessageRequest(BaseModel):
    to_agent: str
    content: Dict[str, Any]


class ServiceRequest(BaseModel):
    capability: str
    input_data: Dict[str, Any]
    target_agent: Optional[str] = None


# Dependencies
async def get_autonomous_daemon() -> AutonomousDaemon:
    return await get_daemon()


async def get_parallel_runtime() -> ParallelAgentRuntime:
    return await get_runtime()


# === DAEMON MANAGEMENT ===

@router.post("/daemon/start")
async def start_daemon(background_tasks: BackgroundTasks):
    """Start the autonomous daemon."""
    background_tasks.add_task(start_autonomous_daemon)
    return {"status": "starting", "message": "Autonomous daemon starting in background"}


@router.get("/daemon/status")
async def get_daemon_status(daemon: AutonomousDaemon = Depends(get_autonomous_daemon)):
    """Get daemon status."""
    return daemon.get_all_status()


# === AUTONOMOUS AGENT MANAGEMENT ===

@router.post("/agents/register")
async def register_autonomous_agent(
    request: RegisterAutonomousRequest,
    daemon: AutonomousDaemon = Depends(get_autonomous_daemon),
    runtime: ParallelAgentRuntime = Depends(get_parallel_runtime),
):
    """Register an agent for autonomous operation."""
    # Register with daemon for self-triggering
    await daemon.register_autonomous_agent(request.agent_id, request.initial_goal)
    
    # Register with runtime for parallel communication
    capabilities = [AgentCapability(name=c, description=c) for c in request.capabilities]
    await runtime.register_agent(
        agent_id=request.agent_id,
        name=request.agent_id,
        capabilities=capabilities,
        teams=request.teams,
    )
    
    return {
        "status": "registered",
        "agent_id": request.agent_id,
        "autonomous": True,
        "capabilities": request.capabilities,
    }


@router.get("/agents/{agent_id}/status")
async def get_autonomous_agent_status(
    agent_id: str,
    daemon: AutonomousDaemon = Depends(get_autonomous_daemon),
):
    """Get status of an autonomous agent."""
    status = daemon.get_agent_status(agent_id)
    if not status:
        raise HTTPException(status_code=404, detail="Agent not found")
    return status


@router.post("/agents/{agent_id}/goal")
async def update_agent_goal(
    agent_id: str,
    goal: str,
    daemon: AutonomousDaemon = Depends(get_autonomous_daemon),
):
    """Update an agent's goal."""
    if agent_id not in daemon._agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    daemon._agents[agent_id].current_goal = goal
    return {"status": "updated", "agent_id": agent_id, "new_goal": goal}


# === EVENT INJECTION ===

@router.post("/events/inject")
async def inject_event(
    request: InjectEventRequest,
    daemon: AutonomousDaemon = Depends(get_autonomous_daemon),
):
    """Inject an external event for agents to observe."""
    await daemon.inject_event({
        "type": request.event_type,
        "data": request.data,
    })
    return {"status": "injected", "event_type": request.event_type}


# === INTER-AGENT COMMUNICATION ===

@router.post("/agents/{agent_id}/message")
async def send_agent_message(
    agent_id: str,
    request: SendMessageRequest,
    runtime: ParallelAgentRuntime = Depends(get_parallel_runtime),
):
    """Send a message from one agent to another."""
    await runtime.send_message(
        from_agent=agent_id,
        to_agent=request.to_agent,
        content=request.content,
    )
    return {"status": "sent", "from": agent_id, "to": request.to_agent}


@router.post("/agents/{agent_id}/broadcast")
async def broadcast_message(
    agent_id: str,
    content: Dict[str, Any],
    channel: str = "broadcast",
    runtime: ParallelAgentRuntime = Depends(get_parallel_runtime),
):
    """Broadcast a message to all agents."""
    await runtime.broadcast(
        from_agent=agent_id,
        content=content,
        channel=channel,
    )
    return {"status": "broadcast", "from": agent_id, "channel": channel}


@router.post("/agents/{agent_id}/service")
async def request_service(
    agent_id: str,
    request: ServiceRequest,
    runtime: ParallelAgentRuntime = Depends(get_parallel_runtime),
):
    """Request a service from another agent."""
    response = await runtime.request_service(
        from_agent=agent_id,
        capability=request.capability,
        input_data=request.input_data,
        target_agent=request.target_agent,
    )
    
    return {
        "success": response.success,
        "output": response.output_data,
        "error": response.error,
    }


# === CAPABILITY DISCOVERY ===

@router.get("/capabilities")
async def list_capabilities(
    runtime: ParallelAgentRuntime = Depends(get_parallel_runtime),
):
    """List all available capabilities."""
    return {
        "capabilities": list(runtime._capabilities.keys()),
    }


@router.get("/capabilities/{capability}/agents")
async def find_agents_by_capability(
    capability: str,
    runtime: ParallelAgentRuntime = Depends(get_parallel_runtime),
):
    """Find agents that provide a capability."""
    agents = runtime.find_agents_by_capability(capability)
    return {"capability": capability, "agents": agents}


# === TEAM MANAGEMENT ===

@router.post("/teams/{team_name}")
async def create_team(
    team_name: str,
    members: List[str],
    runtime: ParallelAgentRuntime = Depends(get_parallel_runtime),
):
    """Create a team of agents."""
    channel = runtime.create_team(team_name, members)
    return {
        "team": team_name,
        "members": list(channel.members),
    }


# === RUNTIME STATS ===

@router.get("/runtime/stats")
async def get_runtime_stats(
    runtime: ParallelAgentRuntime = Depends(get_parallel_runtime),
):
    """Get parallel runtime statistics."""
    return runtime.get_stats()
