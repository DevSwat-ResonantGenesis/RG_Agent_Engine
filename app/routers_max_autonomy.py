"""
MAXIMUM AUTONOMY API ENDPOINTS
==============================

API for goal pursuit, resilience, proactive behavior, and personality.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from .goal_pursuit import get_goal_pursuit_engine, GoalPursuitEngine, GoalPriority
from .agent_resilience import get_resilience_system, AgentResilienceSystem
from .proactive_behavior import get_proactive_system, ProactiveBehaviorSystem, InitiativeLevel
from .agent_personality import get_personality_manager, PersonalityManager

router = APIRouter(prefix="/max-autonomy", tags=["max-autonomy"])


# === REQUEST MODELS ===

class AddGoalRequest(BaseModel):
    description: str
    priority: str = "medium"
    deadline: Optional[str] = None


class SetInitiativeRequest(BaseModel):
    level: str  # passive, reactive, proactive, autonomous


class CreatePersonalityRequest(BaseModel):
    name: str
    archetype: Optional[str] = None  # analyst, innovator, executor, collaborator, advisor


class CheckpointRequest(BaseModel):
    state: Dict[str, Any]
    reason: str = "manual"


# === DEPENDENCIES ===

async def get_pursuit() -> GoalPursuitEngine:
    return await get_goal_pursuit_engine()


async def get_resilience() -> AgentResilienceSystem:
    return await get_resilience_system()


async def get_proactive() -> ProactiveBehaviorSystem:
    return await get_proactive_system()


def get_personality() -> PersonalityManager:
    return get_personality_manager()


# === GOAL PURSUIT ===

@router.post("/agents/{agent_id}/goals")
async def add_goal(
    agent_id: str,
    request: AddGoalRequest,
    engine: GoalPursuitEngine = Depends(get_pursuit),
):
    """Add a goal for autonomous pursuit."""
    priority_map = {
        "critical": GoalPriority.CRITICAL,
        "high": GoalPriority.HIGH,
        "medium": GoalPriority.MEDIUM,
        "low": GoalPriority.LOW,
        "background": GoalPriority.BACKGROUND,
    }
    priority = priority_map.get(request.priority.lower(), GoalPriority.MEDIUM)
    
    goal = await engine.add_goal(
        agent_id=agent_id,
        description=request.description,
        priority=priority,
        deadline=request.deadline,
    )
    
    return {
        "goal_id": goal.id,
        "description": goal.description,
        "priority": goal.priority.name,
        "milestones": len(goal.milestones),
        "status": goal.status.value,
    }


@router.get("/agents/{agent_id}/goals")
async def get_agent_goals(
    agent_id: str,
    engine: GoalPursuitEngine = Depends(get_pursuit),
):
    """Get all goals for an agent."""
    goals = engine.get_agent_goals(agent_id)
    
    return {
        "agent_id": agent_id,
        "goals": [
            {
                "id": g.id,
                "description": g.description,
                "status": g.status.value,
                "progress": g.progress_percentage,
                "attempts": g.attempts,
                "milestones_completed": sum(1 for m in g.milestones if m.completed),
                "milestones_total": len(g.milestones),
                "obstacles": len([o for o in g.obstacles if not o.resolved]),
            }
            for g in goals
        ],
    }


@router.get("/goals/{goal_id}")
async def get_goal_details(
    goal_id: str,
    engine: GoalPursuitEngine = Depends(get_pursuit),
):
    """Get detailed goal information."""
    goal = engine.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    
    return {
        "id": goal.id,
        "description": goal.description,
        "status": goal.status.value,
        "priority": goal.priority.name,
        "progress": goal.progress_percentage,
        "attempts": goal.attempts,
        "milestones": [
            {"description": m.description, "completed": m.completed}
            for m in goal.milestones
        ],
        "obstacles": [
            {"description": o.description, "resolved": o.resolved, "attempts": o.attempts_to_overcome}
            for o in goal.obstacles
        ],
        "created_at": goal.created_at,
    }


@router.get("/goals/stats")
async def get_goal_stats(
    engine: GoalPursuitEngine = Depends(get_pursuit),
):
    """Get goal pursuit statistics."""
    return engine.get_stats()


# === RESILIENCE ===

@router.post("/agents/{agent_id}/resilience/register")
async def register_for_resilience(
    agent_id: str,
    resilience: AgentResilienceSystem = Depends(get_resilience),
):
    """Register an agent for resilience monitoring."""
    resilience.register_agent(agent_id)
    return {"registered": True, "agent_id": agent_id}


@router.get("/agents/{agent_id}/health")
async def get_agent_health(
    agent_id: str,
    resilience: AgentResilienceSystem = Depends(get_resilience),
):
    """Get health status for an agent."""
    health = resilience.get_agent_health(agent_id)
    if not health:
        raise HTTPException(status_code=404, detail="Agent not registered")
    
    return {
        "agent_id": agent_id,
        "status": health.status.value,
        "message": health.message,
        "metrics": health.metrics,
    }


@router.post("/agents/{agent_id}/checkpoint")
async def create_checkpoint(
    agent_id: str,
    request: CheckpointRequest,
    resilience: AgentResilienceSystem = Depends(get_resilience),
):
    """Create a checkpoint for an agent."""
    checkpoint = resilience.create_checkpoint(agent_id, request.state, request.reason)
    return {
        "checkpoint_id": checkpoint.id,
        "created_at": checkpoint.created_at,
    }


@router.get("/resilience/stats")
async def get_resilience_stats(
    resilience: AgentResilienceSystem = Depends(get_resilience),
):
    """Get resilience system statistics."""
    return resilience.get_stats()


# === PROACTIVE BEHAVIOR ===

@router.post("/agents/{agent_id}/initiative")
async def set_initiative_level(
    agent_id: str,
    request: SetInitiativeRequest,
    proactive: ProactiveBehaviorSystem = Depends(get_proactive),
):
    """Set initiative level for an agent."""
    level_map = {
        "passive": InitiativeLevel.PASSIVE,
        "reactive": InitiativeLevel.REACTIVE,
        "proactive": InitiativeLevel.PROACTIVE,
        "autonomous": InitiativeLevel.AUTONOMOUS,
    }
    level = level_map.get(request.level.lower(), InitiativeLevel.PROACTIVE)
    
    proactive.set_initiative_level(agent_id, level)
    
    return {"agent_id": agent_id, "initiative_level": level.name}


@router.get("/agents/{agent_id}/proactive-tasks")
async def get_proactive_tasks(
    agent_id: str,
    proactive: ProactiveBehaviorSystem = Depends(get_proactive),
):
    """Get pending proactive tasks for an agent."""
    tasks = proactive.get_pending_tasks(agent_id)
    
    return {
        "agent_id": agent_id,
        "pending_tasks": [
            {
                "id": t.id,
                "description": t.description,
                "reason": t.reason,
                "priority": t.priority,
            }
            for t in tasks
        ],
    }


@router.get("/proactive/stats")
async def get_proactive_stats(
    proactive: ProactiveBehaviorSystem = Depends(get_proactive),
):
    """Get proactive behavior statistics."""
    return proactive.get_stats()


# === PERSONALITY ===

@router.post("/agents/{agent_id}/personality")
async def create_personality(
    agent_id: str,
    request: CreatePersonalityRequest,
    manager: PersonalityManager = Depends(get_personality),
):
    """Create a personality for an agent."""
    personality = manager.create_personality(
        agent_id=agent_id,
        name=request.name,
        archetype=request.archetype,
    )
    
    return {
        "agent_id": agent_id,
        "name": personality.name,
        "traits": personality.traits.to_dict(),
        "decision_style": personality.preferences.decision_style.value,
        "risk_tolerance": personality.preferences.risk_tolerance.value,
    }


@router.get("/agents/{agent_id}/personality")
async def get_personality(
    agent_id: str,
    manager: PersonalityManager = Depends(get_personality),
):
    """Get personality for an agent."""
    personality = manager.get_personality(agent_id)
    if not personality:
        raise HTTPException(status_code=404, detail="Personality not found")
    
    return {
        "agent_id": agent_id,
        "name": personality.name,
        "traits": personality.traits.to_dict(),
        "decision_style": personality.preferences.decision_style.value,
        "risk_tolerance": personality.preferences.risk_tolerance.value,
        "values": personality.values,
        "communication_style": personality.get_communication_style(),
    }


@router.get("/personality/archetypes")
async def list_archetypes():
    """List available personality archetypes."""
    from .agent_personality import PersonalityGenerator
    return {"archetypes": list(PersonalityGenerator.ARCHETYPES.keys())}
