"""
AUTONOMOUS WORLD MODEL
======================

The BEST autonomous agents platform: Agents understand their environment.
Complete world modeling, perception, and environmental awareness.

Features:
- Environment understanding
- Entity tracking
- Relationship mapping
- Predictive modeling
- Causal reasoning
- Temporal awareness
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
import json

import httpx

logger = logging.getLogger(__name__)


class EntityType(Enum):
    AGENT = "agent"
    FILE = "file"
    API = "api"
    SERVICE = "service"
    DATA = "data"
    USER = "user"
    RESOURCE = "resource"
    TASK = "task"
    GOAL = "goal"


class RelationshipType(Enum):
    OWNS = "owns"
    USES = "uses"
    CREATES = "creates"
    MODIFIES = "modifies"
    DEPENDS_ON = "depends_on"
    COLLABORATES = "collaborates"
    COMPETES = "competes"
    SUPERVISES = "supervises"


@dataclass
class Entity:
    """An entity in the world model."""
    id: str
    entity_type: EntityType
    name: str
    properties: Dict[str, Any]
    state: str = "active"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_observed: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    confidence: float = 1.0


@dataclass
class Relationship:
    """A relationship between entities."""
    id: str
    source_id: str
    target_id: str
    relationship_type: RelationshipType
    strength: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Event:
    """An event in the world."""
    id: str
    event_type: str
    entities_involved: List[str]
    description: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    impact: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Prediction:
    """A prediction about the future."""
    id: str
    prediction: str
    probability: float
    timeframe: str
    basis: List[str]  # IDs of events/entities this is based on
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    verified: Optional[bool] = None


class EntityTracker:
    """Tracks entities in the environment."""
    
    def __init__(self):
        self.entities: Dict[str, Entity] = {}
    
    def register_entity(
        self,
        entity_type: EntityType,
        name: str,
        properties: Dict[str, Any] = None,
    ) -> Entity:
        """Register a new entity."""
        entity = Entity(
            id=str(uuid4()),
            entity_type=entity_type,
            name=name,
            properties=properties or {},
        )
        self.entities[entity.id] = entity
        return entity
    
    def update_entity(self, entity_id: str, properties: Dict[str, Any]):
        """Update entity properties."""
        if entity_id in self.entities:
            self.entities[entity_id].properties.update(properties)
            self.entities[entity_id].last_observed = datetime.now(timezone.utc).isoformat()
    
    def observe_entity(self, entity_id: str):
        """Mark entity as observed."""
        if entity_id in self.entities:
            self.entities[entity_id].last_observed = datetime.now(timezone.utc).isoformat()
    
    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get an entity by ID."""
        return self.entities.get(entity_id)
    
    def find_entities(
        self,
        entity_type: EntityType = None,
        name_contains: str = None,
    ) -> List[Entity]:
        """Find entities matching criteria."""
        results = []
        for entity in self.entities.values():
            if entity_type and entity.entity_type != entity_type:
                continue
            if name_contains and name_contains.lower() not in entity.name.lower():
                continue
            results.append(entity)
        return results
    
    def decay_confidence(self, decay_rate: float = 0.99):
        """Decay confidence of entities not recently observed."""
        now = datetime.now(timezone.utc)
        for entity in self.entities.values():
            last_observed = datetime.fromisoformat(entity.last_observed.replace('Z', '+00:00'))
            age = (now - last_observed).total_seconds()
            if age > 60:  # More than a minute old
                entity.confidence *= decay_rate


class RelationshipGraph:
    """Graph of relationships between entities."""
    
    def __init__(self):
        self.relationships: Dict[str, Relationship] = {}
        self.entity_relationships: Dict[str, List[str]] = {}  # entity_id -> relationship_ids
    
    def add_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship_type: RelationshipType,
        strength: float = 1.0,
        metadata: Dict[str, Any] = None,
    ) -> Relationship:
        """Add a relationship."""
        relationship = Relationship(
            id=str(uuid4()),
            source_id=source_id,
            target_id=target_id,
            relationship_type=relationship_type,
            strength=strength,
            metadata=metadata or {},
        )
        
        self.relationships[relationship.id] = relationship
        
        # Index by entity
        for entity_id in [source_id, target_id]:
            if entity_id not in self.entity_relationships:
                self.entity_relationships[entity_id] = []
            self.entity_relationships[entity_id].append(relationship.id)
        
        return relationship
    
    def get_relationships(self, entity_id: str) -> List[Relationship]:
        """Get all relationships for an entity."""
        rel_ids = self.entity_relationships.get(entity_id, [])
        return [self.relationships[rid] for rid in rel_ids if rid in self.relationships]
    
    def find_path(self, source_id: str, target_id: str, max_depth: int = 5) -> List[str]:
        """Find a path between two entities."""
        visited = set()
        queue = [(source_id, [source_id])]
        
        while queue and len(visited) < 1000:
            current, path = queue.pop(0)
            
            if current == target_id:
                return path
            
            if current in visited or len(path) > max_depth:
                continue
            
            visited.add(current)
            
            for rel in self.get_relationships(current):
                next_id = rel.target_id if rel.source_id == current else rel.source_id
                if next_id not in visited:
                    queue.append((next_id, path + [next_id]))
        
        return []


class CausalReasoner:
    """Reasons about cause and effect."""
    
    def __init__(self, llm_service_url: str = None):
        self.llm_service_url = llm_service_url or "http://llm_service:8000"
        self.causal_chains: List[Dict[str, Any]] = []
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client
    
    async def analyze_causality(
        self,
        events: List[Event],
    ) -> List[Dict[str, Any]]:
        """Analyze causal relationships between events."""
        client = await self._get_client()
        
        events_desc = [{"type": e.event_type, "desc": e.description, "time": e.timestamp} for e in events]
        
        prompt = f"""Analyze causal relationships between these events:

EVENTS: {json.dumps(events_desc)}

Identify:
1. Which events caused which
2. Common causes
3. Chain reactions
4. Independent events

Respond in JSON:
{{"causal_chains": [{{"cause": "event description", "effect": "event description", "confidence": 0.8}}]}}"""

        try:
            response = await client.post(
                f"{self.llm_service_url}/llm/chat/completions",
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                },
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                data = json.loads(content)
                chains = data.get("causal_chains", [])
                self.causal_chains.extend(chains)
                return chains
                
        except Exception as e:
            logger.error(f"Causal analysis failed: {e}")
        
        return []


class PredictiveEngine:
    """Makes predictions about the future."""
    
    def __init__(self, llm_service_url: str = None):
        self.llm_service_url = llm_service_url or "http://llm_service:8000"
        self.predictions: Dict[str, Prediction] = {}
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client
    
    async def predict(
        self,
        context: Dict[str, Any],
        entities: List[Entity],
        events: List[Event],
    ) -> List[Prediction]:
        """Make predictions based on current state."""
        client = await self._get_client()
        
        prompt = f"""Based on current state, predict what will happen next.

CONTEXT: {json.dumps(context)}
ENTITIES: {[{"type": e.entity_type.value, "name": e.name} for e in entities[:10]]}
RECENT EVENTS: {[e.description for e in events[:5]]}

Make 1-3 predictions about what will happen in the next minutes/hours.

Respond in JSON:
{{"predictions": [{{"prediction": "what will happen", "probability": 0.7, "timeframe": "next 10 minutes", "reasoning": "why"}}]}}"""

        try:
            response = await client.post(
                f"{self.llm_service_url}/llm/chat/completions",
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.5,
                    "response_format": {"type": "json_object"},
                },
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                data = json.loads(content)
                
                predictions = []
                for p in data.get("predictions", []):
                    pred = Prediction(
                        id=str(uuid4()),
                        prediction=p.get("prediction", ""),
                        probability=float(p.get("probability", 0.5)),
                        timeframe=p.get("timeframe", "unknown"),
                        basis=[],
                    )
                    self.predictions[pred.id] = pred
                    predictions.append(pred)
                
                return predictions
                
        except Exception as e:
            logger.error(f"Prediction failed: {e}")
        
        return []
    
    def verify_prediction(self, prediction_id: str, outcome: bool):
        """Verify if a prediction came true."""
        if prediction_id in self.predictions:
            self.predictions[prediction_id].verified = outcome


class AutonomousWorldModel:
    """
    Complete world model for autonomous agents.
    The BEST platform for environment understanding.
    """
    
    def __init__(self):
        self.entity_tracker = EntityTracker()
        self.relationship_graph = RelationshipGraph()
        self.causal_reasoner = CausalReasoner()
        self.predictive_engine = PredictiveEngine()
        
        self.events: List[Event] = []
        self.current_context: Dict[str, Any] = {}
        
        self._running = False
        self._task = None
    
    async def start(self):
        """Start the world model."""
        self._running = True
        self._task = asyncio.create_task(self._maintenance_loop())
        logger.info("Autonomous World Model started")
    
    async def stop(self):
        """Stop the world model."""
        self._running = False
        if self._task:
            self._task.cancel()
    
    async def _maintenance_loop(self):
        """Maintenance loop."""
        while self._running:
            try:
                # Decay entity confidence
                self.entity_tracker.decay_confidence()
                
                # Prune old events
                if len(self.events) > 1000:
                    self.events = self.events[-500:]
                
                await asyncio.sleep(30)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"World model maintenance error: {e}")
    
    def observe(
        self,
        entity_type: EntityType,
        name: str,
        properties: Dict[str, Any] = None,
    ) -> Entity:
        """Observe an entity in the environment."""
        # Check if entity exists
        existing = self.entity_tracker.find_entities(entity_type=entity_type, name_contains=name)
        
        if existing:
            entity = existing[0]
            self.entity_tracker.update_entity(entity.id, properties or {})
            return entity
        else:
            return self.entity_tracker.register_entity(entity_type, name, properties)
    
    def record_event(
        self,
        event_type: str,
        description: str,
        entities_involved: List[str] = None,
        impact: Dict[str, Any] = None,
    ) -> Event:
        """Record an event."""
        event = Event(
            id=str(uuid4()),
            event_type=event_type,
            entities_involved=entities_involved or [],
            description=description,
            impact=impact or {},
        )
        self.events.append(event)
        return event
    
    def relate(
        self,
        source_id: str,
        target_id: str,
        relationship_type: RelationshipType,
    ) -> Relationship:
        """Create a relationship between entities."""
        return self.relationship_graph.add_relationship(source_id, target_id, relationship_type)
    
    async def understand_situation(self) -> Dict[str, Any]:
        """Get understanding of current situation."""
        entities = list(self.entity_tracker.entities.values())
        recent_events = self.events[-10:]
        
        # Analyze causality
        causal_chains = await self.causal_reasoner.analyze_causality(recent_events)
        
        # Make predictions
        predictions = await self.predictive_engine.predict(
            self.current_context,
            entities,
            recent_events,
        )
        
        return {
            "entities": len(entities),
            "recent_events": len(recent_events),
            "causal_chains": causal_chains,
            "predictions": [{"prediction": p.prediction, "probability": p.probability} for p in predictions],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    
    def query_entities(
        self,
        entity_type: EntityType = None,
        name_contains: str = None,
    ) -> List[Dict[str, Any]]:
        """Query entities."""
        entities = self.entity_tracker.find_entities(entity_type, name_contains)
        return [
            {
                "id": e.id,
                "type": e.entity_type.value,
                "name": e.name,
                "state": e.state,
                "confidence": e.confidence,
            }
            for e in entities
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get world model statistics."""
        return {
            "entities": len(self.entity_tracker.entities),
            "relationships": len(self.relationship_graph.relationships),
            "events": len(self.events),
            "predictions": len(self.predictive_engine.predictions),
            "causal_chains": len(self.causal_reasoner.causal_chains),
        }


# Global instance
_world_model: Optional[AutonomousWorldModel] = None


async def get_world_model() -> AutonomousWorldModel:
    """Get or create world model."""
    global _world_model
    if _world_model is None:
        _world_model = AutonomousWorldModel()
        await _world_model.start()
    return _world_model


# ============== BELIEF REVISION SYSTEM ==============
# This is the MISSING PIECE for TRUE AUTONOMY
# Action → Environment delta → World model update → Belief revision

@dataclass
class Belief:
    """A belief the agent holds about the world."""
    id: str
    subject: str
    predicate: str
    confidence: float
    evidence: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    revision_count: int = 0


class BeliefRevisionSystem:
    """
    TRUE AUTONOMY: Belief revision from action consequences.
    
    This closes the loop:
    Action → Observation → Belief Update → Strategy Change
    """
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.beliefs: Dict[str, Belief] = {}
        self.revision_history: List[Dict[str, Any]] = []
    
    def add_belief(
        self,
        subject: str,
        predicate: str,
        confidence: float,
        evidence: Optional[List[str]] = None,
    ) -> Belief:
        """Add a new belief."""
        belief_key = f"{subject}:{predicate}"
        
        if belief_key in self.beliefs:
            return self.revise_belief(belief_key, confidence, evidence)
        
        belief = Belief(
            id=str(uuid4()),
            subject=subject,
            predicate=predicate,
            confidence=confidence,
            evidence=evidence or [],
        )
        self.beliefs[belief_key] = belief
        return belief
    
    def revise_belief(
        self,
        belief_key: str,
        new_confidence: float,
        new_evidence: Optional[List[str]] = None,
    ) -> Optional[Belief]:
        """Revise an existing belief based on new evidence."""
        if belief_key not in self.beliefs:
            return None
        
        belief = self.beliefs[belief_key]
        old_confidence = belief.confidence
        
        # Bayesian-style update
        belief.confidence = (belief.confidence * 0.7 + new_confidence * 0.3)
        belief.revision_count += 1
        belief.last_updated = datetime.now(timezone.utc).isoformat()
        
        if new_evidence:
            belief.evidence.extend(new_evidence)
            belief.evidence = belief.evidence[-10:]  # Keep last 10
        
        # Record revision
        self.revision_history.append({
            "belief_key": belief_key,
            "old_confidence": old_confidence,
            "new_confidence": belief.confidence,
            "revision_count": belief.revision_count,
            "time": datetime.now(timezone.utc).isoformat(),
        })
        
        return belief
    
    async def update_from_action_result(
        self,
        action_type: str,
        action_data: Dict[str, Any],
        result: Dict[str, Any],
        success: bool,
    ):
        """
        CORE BELIEF REVISION: Update beliefs based on action outcomes.
        
        This is what was MISSING - the agent now understands consequences.
        """
        # Extract beliefs from action
        tool_name = action_data.get("tool_name", "")
        target = action_data.get("target", action_data.get("url", ""))
        
        # Update belief about tool effectiveness
        tool_belief_key = f"tool:{tool_name}:effective"
        if tool_belief_key in self.beliefs:
            self.revise_belief(
                tool_belief_key,
                0.9 if success else 0.3,
                [f"Action {'succeeded' if success else 'failed'} at {datetime.now(timezone.utc).isoformat()}"]
            )
        else:
            self.add_belief(
                f"tool:{tool_name}",
                "effective",
                0.8 if success else 0.4,
                [f"Initial observation: {'success' if success else 'failure'}"]
            )
        
        # Update belief about target availability
        if target:
            target_belief_key = f"target:{target}:available"
            self.add_belief(
                f"target:{target}",
                "available",
                0.9 if success else 0.5,
                [f"Accessed at {datetime.now(timezone.utc).isoformat()}"]
            )
        
        # Update strategy beliefs
        if not success:
            error = result.get("error", "")
            self.add_belief(
                f"action:{action_type}",
                f"fails_with:{error[:50]}",
                0.7,
                [f"Failure observed"]
            )
    
    def get_belief(self, subject: str, predicate: str) -> Optional[Belief]:
        """Get a specific belief."""
        return self.beliefs.get(f"{subject}:{predicate}")
    
    def get_beliefs_about(self, subject: str) -> List[Belief]:
        """Get all beliefs about a subject."""
        return [b for key, b in self.beliefs.items() if key.startswith(f"{subject}:")]
    
    def get_confident_beliefs(self, min_confidence: float = 0.7) -> List[Belief]:
        """Get beliefs above confidence threshold."""
        return [b for b in self.beliefs.values() if b.confidence >= min_confidence]
    
    def should_revise_strategy(self) -> bool:
        """Check if beliefs suggest strategy should change."""
        # Check for many low-confidence beliefs
        low_conf = [b for b in self.beliefs.values() if b.confidence < 0.4]
        if len(low_conf) > 3:
            return True
        
        # Check for highly revised beliefs (unstable world)
        highly_revised = [b for b in self.beliefs.values() if b.revision_count > 5]
        if len(highly_revised) > 2:
            return True
        
        return False
    
    def get_status(self) -> Dict[str, Any]:
        """Get belief system status."""
        return {
            "agent_id": self.agent_id,
            "total_beliefs": len(self.beliefs),
            "confident_beliefs": len(self.get_confident_beliefs()),
            "recent_revisions": self.revision_history[-5:],
            "should_revise_strategy": self.should_revise_strategy(),
        }


# Singleton manager
_belief_systems: Dict[str, BeliefRevisionSystem] = {}


def get_belief_system(agent_id: str) -> BeliefRevisionSystem:
    """Get or create a belief revision system for an agent."""
    if agent_id not in _belief_systems:
        _belief_systems[agent_id] = BeliefRevisionSystem(agent_id)
    return _belief_systems[agent_id]
