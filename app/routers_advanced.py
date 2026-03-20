import sys
from pathlib import Path

# Add shared modules to path
SHARED_PATH = Path(__file__).resolve().parents[2] / "shared"
if str(SHARED_PATH) not in sys.path:
    sys.path.insert(0, str(SHARED_PATH))

# Add service root to path
SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

"""
ADVANCED AUTONOMOUS AGENT API
=============================

API endpoints for goal decomposition, memory, learning, and self-improvement.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from .goal_engine import get_goal_engine, get_adaptive_planner, GoalDecompositionEngine, AdaptivePlanner
from .agent_memory import get_agent_memory, get_agent_learning, AgentMemory, AgentLearning, MemoryType
from .self_improvement import get_improvement_loop, get_evolution, SelfImprovementLoop, AutonomousEvolution

router = APIRouter(prefix="/advanced", tags=["advanced-autonomy"])


# === REQUEST MODELS ===

class DecomposeGoalRequest(BaseModel):
    goal: str
    context: Optional[Dict[str, Any]] = None
    available_capabilities: Optional[List[str]] = None


class RememberRequest(BaseModel):
    content: Dict[str, Any]
    memory_type: str = "working"
    importance: float = 0.5


class RecallRequest(BaseModel):
    query: Optional[str] = None
    memory_type: Optional[str] = None
    limit: int = 10


class LearnPatternRequest(BaseModel):
    pattern_type: str
    trigger: Dict[str, Any]
    outcome: Dict[str, Any]
    confidence: float = 0.5


class RecordExperienceRequest(BaseModel):
    task_type: str
    context: Dict[str, Any]
    action: Dict[str, Any]
    result: Dict[str, Any]
    success: bool


class RegisterForImprovementRequest(BaseModel):
    config: Dict[str, Any]


# === DEPENDENCIES ===

async def get_goal_eng() -> GoalDecompositionEngine:
    return await get_goal_engine()


async def get_planner() -> AdaptivePlanner:
    return await get_adaptive_planner()


async def get_loop() -> SelfImprovementLoop:
    return await get_improvement_loop()


async def get_evo() -> AutonomousEvolution:
    return await get_evolution()


# === GOAL DECOMPOSITION ===

@router.post("/goals/decompose")
async def decompose_goal(
    request: DecomposeGoalRequest,
    engine: GoalDecompositionEngine = Depends(get_goal_eng),
):
    """Decompose a goal into executable tasks."""
    plan = await engine.decompose(
        goal=request.goal,
        context=request.context,
        available_capabilities=request.available_capabilities,
    )
    
    return {
        "plan_id": plan.id,
        "goal": plan.original_goal,
        "tasks": [
            {
                "id": t.id,
                "description": t.description,
                "type": t.task_type.value,
                "complexity": t.complexity.value,
                "dependencies": t.dependencies,
                "estimated_minutes": t.estimated_duration_minutes,
                "priority": t.priority,
            }
            for t in plan.tasks
        ],
        "critical_path": plan.critical_path,
        "estimated_total_minutes": plan.estimated_total_minutes,
    }


@router.post("/goals/plan-adaptive")
async def plan_with_learning(
    request: DecomposeGoalRequest,
    planner: AdaptivePlanner = Depends(get_planner),
):
    """Create a plan using learned estimates."""
    plan = await planner.plan_with_learning(
        goal=request.goal,
        context=request.context,
    )
    
    return {
        "plan_id": plan.id,
        "tasks": len(plan.tasks),
        "estimated_minutes": plan.estimated_total_minutes,
    }


# === AGENT MEMORY ===

@router.post("/agents/{agent_id}/memory/remember")
async def remember(
    agent_id: str,
    request: RememberRequest,
):
    """Store a memory for an agent."""
    memory = get_agent_memory(agent_id)
    
    memory_type = MemoryType.WORKING
    try:
        memory_type = MemoryType(request.memory_type)
    except ValueError:
        pass
    
    memory_id = memory.remember(
        content=request.content,
        memory_type=memory_type,
        importance=request.importance,
    )
    
    return {"memory_id": memory_id, "type": memory_type.value}


@router.post("/agents/{agent_id}/memory/recall")
async def recall(
    agent_id: str,
    request: RecallRequest,
):
    """Recall memories for an agent."""
    memory = get_agent_memory(agent_id)
    
    memory_type = None
    if request.memory_type:
        try:
            memory_type = MemoryType(request.memory_type)
        except ValueError:
            pass
    
    memories = memory.recall(
        query=request.query,
        memory_type=memory_type,
        limit=request.limit,
    )
    
    return {
        "count": len(memories),
        "memories": [m.to_dict() for m in memories],
    }


@router.get("/agents/{agent_id}/memory/summary")
async def get_memory_summary(agent_id: str):
    """Get memory summary for an agent."""
    memory = get_agent_memory(agent_id)
    return memory.get_summary()


@router.post("/agents/{agent_id}/memory/pattern")
async def learn_pattern(
    agent_id: str,
    request: LearnPatternRequest,
):
    """Learn a pattern for an agent."""
    memory = get_agent_memory(agent_id)
    
    pattern_id = memory.learn_pattern(
        pattern_type=request.pattern_type,
        trigger=request.trigger,
        outcome=request.outcome,
        confidence=request.confidence,
    )
    
    return {"pattern_id": pattern_id}


@router.post("/agents/{agent_id}/memory/apply-patterns")
async def apply_patterns(
    agent_id: str,
    context: Dict[str, Any],
):
    """Apply learned patterns to a context."""
    memory = get_agent_memory(agent_id)
    patterns = memory.apply_learned_patterns(context)
    return {"patterns": patterns}


# === AGENT LEARNING ===

@router.post("/agents/{agent_id}/learning/experience")
async def record_experience(
    agent_id: str,
    request: RecordExperienceRequest,
):
    """Record an experience for learning."""
    learning = get_agent_learning(agent_id)
    
    learning.record_experience(
        task_type=request.task_type,
        context=request.context,
        action=request.action,
        result=request.result,
        success=request.success,
    )
    
    return {"recorded": True, "success_rate": learning.success_rate}


@router.get("/agents/{agent_id}/learning/recommendations")
async def get_recommendations(
    agent_id: str,
    task_type: str,
    context: Optional[str] = None,
):
    """Get recommendations based on learning."""
    learning = get_agent_learning(agent_id)
    
    ctx = {}
    if context:
        import json
        try:
            ctx = json.loads(context)
        except:
            pass
    
    recommendations = learning.get_recommendations(task_type, ctx)
    return {"recommendations": recommendations}


@router.get("/agents/{agent_id}/learning/skill/{skill}")
async def get_skill_level(
    agent_id: str,
    skill: str,
):
    """Get skill level for an agent."""
    learning = get_agent_learning(agent_id)
    level = learning.get_skill_level(skill)
    return {"skill": skill, "level": level}


@router.get("/agents/{agent_id}/learning/summary")
async def get_learning_summary(agent_id: str):
    """Get learning summary for an agent."""
    learning = get_agent_learning(agent_id)
    return learning.get_learning_summary()


# === SELF-IMPROVEMENT ===

@router.post("/agents/{agent_id}/improvement/register")
async def register_for_improvement(
    agent_id: str,
    request: RegisterForImprovementRequest,
    loop: SelfImprovementLoop = Depends(get_loop),
):
    """Register an agent for self-improvement."""
    await loop.register_agent(agent_id, request.config)
    return {"registered": True, "agent_id": agent_id}


@router.get("/agents/{agent_id}/improvement/actions")
async def get_improvements(
    agent_id: str,
    loop: SelfImprovementLoop = Depends(get_loop),
):
    """Get improvement actions for an agent."""
    return {"improvements": loop.get_agent_improvements(agent_id)}


@router.get("/agents/{agent_id}/improvement/metrics")
async def get_metrics_history(
    agent_id: str,
    loop: SelfImprovementLoop = Depends(get_loop),
):
    """Get metrics history for an agent."""
    return {"metrics": loop.get_metrics_history(agent_id)}


# === AUTONOMOUS EVOLUTION ===

@router.post("/agents/{agent_id}/evolve")
async def evolve_agent(
    agent_id: str,
    evolution: AutonomousEvolution = Depends(get_evo),
):
    """Trigger evolution for an agent."""
    result = await evolution.evolve_agent(agent_id)
    return result


@router.get("/agents/{agent_id}/evolution")
async def get_evolution_status(
    agent_id: str,
    evolution: AutonomousEvolution = Depends(get_evo),
):
    """Get evolution status for an agent."""
    capabilities = evolution.evolved_capabilities.get(agent_id, [])
    return {
        "agent_id": agent_id,
        "evolved_capabilities": capabilities,
        "total": len(capabilities),
    }
