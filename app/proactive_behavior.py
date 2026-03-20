"""
Proactive Behavior System - Enables agents to take initiative.

STATUS: DENIED CAPABILITY
CREATED: 2025-12-21
GOVERNANCE: This capability is DENIED until proper governance framework exists.
            Core methods return 501 NOT_IMPLEMENTED.
            Reason: Proactive behavior requires initiative level governance.
            Risk: Unwanted autonomous actions without proper constraints.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Governance: This capability is DENIED
_IS_STUB = True
_CAPABILITY_DENIED = True
_DENIAL_REASON = "Proactive behavior requires initiative level governance"


class CapabilityDeniedError(Exception):
    """Raised when a denied capability is invoked."""
    def __init__(self, capability: str, reason: str):
        self.capability = capability
        self.reason = reason
        super().__init__(f"501 NOT_IMPLEMENTED: {capability} - {reason}")


class InitiativeLevel(Enum):
    """Level of agent initiative."""
    PASSIVE = "passive"  # Only respond when asked
    REACTIVE = "reactive"  # React to events
    PROACTIVE = "proactive"  # Anticipate and suggest
    AUTONOMOUS = "autonomous"  # Take action independently


@dataclass
class ProactiveAction:
    """A proactive action taken by an agent."""
    action_id: str
    agent_id: str
    action_type: str
    description: str
    initiative_level: InitiativeLevel
    triggered_by: str
    result: Optional[Any] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ProactiveBehaviorSystem:
    """
    System for managing proactive agent behaviors.
    
    Enables agents to anticipate user needs, suggest actions,
    and take initiative based on context and patterns.
    """
    
    def __init__(self, default_level: InitiativeLevel = InitiativeLevel.REACTIVE):
        self.default_level = default_level
        self.agent_levels: Dict[str, InitiativeLevel] = {}
        self.action_history: List[ProactiveAction] = []
        self.triggers: Dict[str, List[Dict[str, Any]]] = {}
        
    def set_initiative_level(self, agent_id: str, level: InitiativeLevel) -> None:
        """Set the initiative level for an agent."""
        self.agent_levels[agent_id] = level
        logger.info(f"Set initiative level for {agent_id} to {level.value}")
        
    def get_initiative_level(self, agent_id: str) -> InitiativeLevel:
        """Get the initiative level for an agent."""
        return self.agent_levels.get(agent_id, self.default_level)
        
    def register_trigger(
        self,
        trigger_type: str,
        condition: Dict[str, Any],
        action: Dict[str, Any]
    ) -> str:
        """Register a trigger for proactive behavior."""
        import uuid
        trigger_id = str(uuid.uuid4())[:8]
        
        if trigger_type not in self.triggers:
            self.triggers[trigger_type] = []
            
        self.triggers[trigger_type].append({
            "trigger_id": trigger_id,
            "condition": condition,
            "action": action
        })
        
        return trigger_id
        
    def evaluate_triggers(
        self,
        trigger_type: str,
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Evaluate triggers. DENIED - returns 501."""
        logger.warning(
            f"GOVERNANCE_DENIED: evaluate_triggers called on denied capability. "
            f"Reason: {_DENIAL_REASON}"
        )
        raise CapabilityDeniedError("ProactiveBehaviorSystem.evaluate_triggers", _DENIAL_REASON)
        
    def _matches_condition(
        self,
        condition: Dict[str, Any],
        context: Dict[str, Any]
    ) -> bool:
        """Check if a condition matches the context."""
        for key, value in condition.items():
            if key not in context:
                return False
            if context[key] != value:
                return False
        return True
        
    def record_action(
        self,
        agent_id: str,
        action_type: str,
        description: str,
        triggered_by: str,
        result: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ProactiveAction:
        """Record a proactive action."""
        import uuid
        action = ProactiveAction(
            action_id=str(uuid.uuid4())[:8],
            agent_id=agent_id,
            action_type=action_type,
            description=description,
            initiative_level=self.get_initiative_level(agent_id),
            triggered_by=triggered_by,
            result=result,
            metadata=metadata or {}
        )
        self.action_history.append(action)
        return action
        
    def get_stats(self) -> Dict[str, Any]:
        """Get system statistics."""
        level_counts = {}
        for level in self.agent_levels.values():
            level_counts[level.value] = level_counts.get(level.value, 0) + 1
            
        return {
            "total_agents": len(self.agent_levels),
            "level_distribution": level_counts,
            "total_triggers": sum(len(t) for t in self.triggers.values()),
            "total_actions": len(self.action_history)
        }


# Global instance
_proactive_system: Optional[ProactiveBehaviorSystem] = None


def get_proactive_system() -> ProactiveBehaviorSystem:
    """Get or create the global proactive behavior system."""
    global _proactive_system
    if _proactive_system is None:
        _proactive_system = ProactiveBehaviorSystem()
    return _proactive_system
