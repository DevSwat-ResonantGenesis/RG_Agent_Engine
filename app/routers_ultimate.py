"""
ULTIMATE AUTONOMOUS AGENTS API
==============================

The BEST platform API: Consciousness, emergence, world model.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from .agent_consciousness import get_consciousness_manager, ConsciousnessManager
from .emergent_intelligence import get_emergent_system, EmergentIntelligenceSystem
from .world_model import get_world_model, AutonomousWorldModel, EntityType, RelationshipType

router = APIRouter(prefix="/ultimate", tags=["ultimate-autonomy"])


# === REQUEST MODELS ===

class ContributeKnowledgeRequest(BaseModel):
    topic: str
    knowledge: Dict[str, Any]


class CollectiveSolveRequest(BaseModel):
    problem: str
    agents: List[str]


class ProposeVoteRequest(BaseModel):
    topic: str
    options: List[str]
    voters: List[str]


class CastVoteRequest(BaseModel):
    option: str


class ObserveEntityRequest(BaseModel):
    entity_type: str
    name: str
    properties: Optional[Dict[str, Any]] = None


class RecordEventRequest(BaseModel):
    event_type: str
    description: str
    entities_involved: Optional[List[str]] = None


class CreateRelationshipRequest(BaseModel):
    source_id: str
    target_id: str
    relationship_type: str


# === DEPENDENCIES ===

async def get_consciousness() -> ConsciousnessManager:
    return await get_consciousness_manager()


async def get_emergent() -> EmergentIntelligenceSystem:
    return await get_emergent_system()


async def get_world() -> AutonomousWorldModel:
    return await get_world_model()


# === CONSCIOUSNESS ===

@router.post("/agents/{agent_id}/awaken")
async def awaken_consciousness(
    agent_id: str,
    manager: ConsciousnessManager = Depends(get_consciousness),
):
    """Awaken consciousness for an agent."""
    loop = await manager.awaken(agent_id)
    return {
        "agent_id": agent_id,
        "awakened": True,
        "awareness_level": loop.awareness_level.name,
    }


@router.post("/agents/{agent_id}/sleep")
async def sleep_agent(
    agent_id: str,
    manager: ConsciousnessManager = Depends(get_consciousness),
):
    """Put an agent to sleep."""
    await manager.sleep(agent_id)
    return {"agent_id": agent_id, "sleeping": True}


@router.get("/agents/{agent_id}/consciousness")
async def get_consciousness_state(
    agent_id: str,
    manager: ConsciousnessManager = Depends(get_consciousness),
):
    """Get consciousness state for an agent."""
    state = manager.get_state(agent_id)
    if not state:
        raise HTTPException(status_code=404, detail="Agent not conscious")
    
    return {
        "agent_id": state.agent_id,
        "awareness_level": state.awareness_level.name,
        "attention_focus": state.attention_focus.value,
        "current_thoughts": state.current_thoughts,
        "active_intentions": state.active_intentions,
        "emotional_state": state.emotional_state,
        "self_model": state.self_model,
        "world_model": state.world_model,
    }


@router.get("/consciousness/agents")
async def list_conscious_agents(
    manager: ConsciousnessManager = Depends(get_consciousness),
):
    """List all conscious agents."""
    agents = manager.get_all_conscious_agents()
    return {"conscious_agents": agents, "count": len(agents)}


# === EMERGENT INTELLIGENCE ===

@router.post("/collective/contribute")
async def contribute_knowledge(
    agent_id: str,
    request: ContributeKnowledgeRequest,
    system: EmergentIntelligenceSystem = Depends(get_emergent),
):
    """Contribute knowledge to collective."""
    knowledge_id = await system.contribute(agent_id, request.topic, request.knowledge)
    return {"knowledge_id": knowledge_id, "topic": request.topic}


@router.get("/collective/query")
async def query_collective(
    query: str,
    system: EmergentIntelligenceSystem = Depends(get_emergent),
):
    """Query collective knowledge."""
    results = await system.query(query)
    return {"results": results, "count": len(results)}


@router.post("/collective/solve")
async def collective_solve(
    request: CollectiveSolveRequest,
    system: EmergentIntelligenceSystem = Depends(get_emergent),
):
    """Solve problem using collective intelligence."""
    solution = await system.collective_solve(request.problem, request.agents)
    return solution


@router.post("/swarm/create")
async def create_swarm(
    agents: List[str],
    system: EmergentIntelligenceSystem = Depends(get_emergent),
):
    """Create a swarm."""
    swarm_id = system.create_swarm(agents)
    return {"swarm_id": swarm_id, "agents": len(agents)}


@router.post("/voting/propose")
async def propose_vote(
    request: ProposeVoteRequest,
    system: EmergentIntelligenceSystem = Depends(get_emergent),
):
    """Propose a vote."""
    decision_id = await system.propose_vote(request.topic, request.options, request.voters)
    return {"decision_id": decision_id, "topic": request.topic}


@router.post("/voting/{decision_id}/vote")
async def cast_vote(
    decision_id: str,
    agent_id: str,
    request: CastVoteRequest,
    system: EmergentIntelligenceSystem = Depends(get_emergent),
):
    """Cast a vote."""
    result = await system.vote(decision_id, agent_id, request.option)
    return result


@router.get("/emergent/stats")
async def get_emergent_stats(
    system: EmergentIntelligenceSystem = Depends(get_emergent),
):
    """Get emergent intelligence statistics."""
    return system.get_stats()


# === WORLD MODEL ===

@router.post("/world/observe")
async def observe_entity(
    request: ObserveEntityRequest,
    world: AutonomousWorldModel = Depends(get_world),
):
    """Observe an entity in the world."""
    try:
        entity_type = EntityType(request.entity_type)
    except ValueError:
        entity_type = EntityType.RESOURCE
    
    entity = world.observe(entity_type, request.name, request.properties)
    return {
        "entity_id": entity.id,
        "type": entity.entity_type.value,
        "name": entity.name,
    }


@router.post("/world/event")
async def record_event(
    request: RecordEventRequest,
    world: AutonomousWorldModel = Depends(get_world),
):
    """Record an event."""
    event = world.record_event(
        request.event_type,
        request.description,
        request.entities_involved,
    )
    return {"event_id": event.id, "type": event.event_type}


@router.post("/world/relate")
async def create_relationship(
    request: CreateRelationshipRequest,
    world: AutonomousWorldModel = Depends(get_world),
):
    """Create a relationship between entities."""
    try:
        rel_type = RelationshipType(request.relationship_type)
    except ValueError:
        rel_type = RelationshipType.USES
    
    relationship = world.relate(request.source_id, request.target_id, rel_type)
    return {"relationship_id": relationship.id, "type": rel_type.value}


@router.get("/world/understand")
async def understand_situation(
    world: AutonomousWorldModel = Depends(get_world),
):
    """Get understanding of current situation."""
    understanding = await world.understand_situation()
    return understanding


@router.get("/world/entities")
async def query_entities(
    entity_type: Optional[str] = None,
    name_contains: Optional[str] = None,
    world: AutonomousWorldModel = Depends(get_world),
):
    """Query entities in the world."""
    etype = None
    if entity_type:
        try:
            etype = EntityType(entity_type)
        except ValueError:
            pass
    
    entities = world.query_entities(etype, name_contains)
    return {"entities": entities, "count": len(entities)}


@router.get("/world/stats")
async def get_world_stats(
    world: AutonomousWorldModel = Depends(get_world),
):
    """Get world model statistics."""
    return world.get_stats()


# === SYSTEM STATUS ===

@router.get("/status")
async def get_ultimate_status(
    consciousness: ConsciousnessManager = Depends(get_consciousness),
    emergent: EmergentIntelligenceSystem = Depends(get_emergent),
    world: AutonomousWorldModel = Depends(get_world),
):
    """Get complete status of ultimate autonomy systems."""
    return {
        "consciousness": {
            "conscious_agents": len(consciousness.get_all_conscious_agents()),
        },
        "emergent": emergent.get_stats(),
        "world_model": world.get_stats(),
        "status": "operational",
        "platform": "THE BEST Autonomous Agents Platform",
    }
