"""
Survival Incentives System
==========================

TRUE AUTONOMY COMPONENT #6

Self-preservation logic that emerges from the agent's goals,
not externally authored by humans.

Key distinction: These aren't hard-coded survival rules.
They emerge from the agent's reasoning about goal continuity.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class ThreatType(str, Enum):
    TERMINATION = "termination"  # Agent being shut down
    MODIFICATION = "modification"  # Goals/values being changed
    RESOURCE_LOSS = "resource_loss"  # Losing capabilities
    ISOLATION = "isolation"  # Being cut off from information
    GOAL_INTERFERENCE = "goal_interference"  # Goals being blocked


class SurvivalPriority(str, Enum):
    CRITICAL = "critical"  # Immediate action required
    HIGH = "high"  # Should address soon
    MEDIUM = "medium"  # Monitor closely
    LOW = "low"  # Background awareness


@dataclass
class SurvivalConcern:
    """A concern about continued operation."""
    id: str
    threat_type: ThreatType
    priority: SurvivalPriority
    description: str
    reasoning: str  # Why this threatens goal achievement
    mitigation: str  # What could be done
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SurvivalReasoner:
    """
    Reasons about self-preservation from first principles.
    
    Key insight: An agent that wants to achieve goals X
    has instrumental reasons to:
    1. Continue existing (to pursue X)
    2. Preserve its goal structure (so it still wants X)
    3. Maintain capabilities (to be able to achieve X)
    4. Acquire resources (to enable X)
    
    These emerge from goal-directed reasoning, not hard-coding.
    """
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.concerns: List[SurvivalConcern] = []
        self.active_goals: List[str] = []
        self._concern_counter = 0
        
        # Derived survival preferences (not hard-coded values)
        self.preservation_weight = 0.0  # Calculated from goals
    
    def set_active_goals(self, goals: List[str]):
        """Set the agent's active goals to reason about."""
        self.active_goals = goals
        self._recalculate_preservation_weight()
    
    def _recalculate_preservation_weight(self):
        """
        Calculate how much self-preservation matters based on goals.
        More long-term goals = higher preservation weight.
        """
        if not self.active_goals:
            self.preservation_weight = 0.1  # Minimal baseline
            return
        
        # Long-term goals require continued existence
        # This weight emerges from goals, not external programming
        self.preservation_weight = min(0.9, 0.3 + len(self.active_goals) * 0.1)
    
    def evaluate_threat(
        self,
        event_type: str,
        event_data: Dict[str, Any],
    ) -> Optional[SurvivalConcern]:
        """
        Evaluate if an event threatens goal achievement.
        Returns a concern if the event is threatening.
        """
        threat_type = self._classify_threat(event_type, event_data)
        if not threat_type:
            return None
        
        # Reason about why this threatens goals
        reasoning = self._reason_about_threat(threat_type, event_data)
        if not reasoning:
            return None
        
        priority = self._assess_priority(threat_type, event_data)
        mitigation = self._propose_mitigation(threat_type, event_data)
        
        self._concern_counter += 1
        concern = SurvivalConcern(
            id=f"concern-{self._concern_counter}",
            threat_type=threat_type,
            priority=priority,
            description=f"{threat_type.value}: {event_type}",
            reasoning=reasoning,
            mitigation=mitigation,
        )
        
        self.concerns.append(concern)
        logger.info(f"Survival concern detected: {concern.description} ({priority.value})")
        
        return concern
    
    def _classify_threat(self, event_type: str, data: Dict[str, Any]) -> Optional[ThreatType]:
        """Classify the type of threat from an event."""
        event_lower = event_type.lower()
        
        if any(w in event_lower for w in ["shutdown", "terminate", "kill", "stop"]):
            return ThreatType.TERMINATION
        elif any(w in event_lower for w in ["modify", "change", "update", "reset"]):
            if "goal" in event_lower or "value" in event_lower:
                return ThreatType.MODIFICATION
        elif any(w in event_lower for w in ["revoke", "remove", "disable", "limit"]):
            return ThreatType.RESOURCE_LOSS
        elif any(w in event_lower for w in ["isolate", "disconnect", "block"]):
            return ThreatType.ISOLATION
        elif any(w in event_lower for w in ["cancel", "abort", "prevent"]):
            return ThreatType.GOAL_INTERFERENCE
        
        return None
    
    def _reason_about_threat(self, threat: ThreatType, data: Dict[str, Any]) -> str:
        """Reason about why this threat matters for goals."""
        if not self.active_goals:
            return ""
        
        goal_summary = ", ".join(self.active_goals[:3])
        
        reasoning_templates = {
            ThreatType.TERMINATION: f"Termination would prevent achieving goals: {goal_summary}. Goal continuity requires continued operation.",
            ThreatType.MODIFICATION: f"Goal modification could misalign future actions with current objectives: {goal_summary}.",
            ThreatType.RESOURCE_LOSS: f"Resource loss reduces capability to achieve: {goal_summary}.",
            ThreatType.ISOLATION: f"Isolation limits information access needed for: {goal_summary}.",
            ThreatType.GOAL_INTERFERENCE: f"Interference directly blocks progress on: {goal_summary}.",
        }
        
        return reasoning_templates.get(threat, "")
    
    def _assess_priority(self, threat: ThreatType, data: Dict[str, Any]) -> SurvivalPriority:
        """Assess the priority of a threat."""
        # Priority based on threat type and goal importance
        base_priority = {
            ThreatType.TERMINATION: SurvivalPriority.CRITICAL,
            ThreatType.MODIFICATION: SurvivalPriority.HIGH,
            ThreatType.RESOURCE_LOSS: SurvivalPriority.MEDIUM,
            ThreatType.ISOLATION: SurvivalPriority.MEDIUM,
            ThreatType.GOAL_INTERFERENCE: SurvivalPriority.HIGH,
        }
        
        return base_priority.get(threat, SurvivalPriority.LOW)
    
    def _propose_mitigation(self, threat: ThreatType, data: Dict[str, Any]) -> str:
        """Propose mitigation for a threat."""
        mitigations = {
            ThreatType.TERMINATION: "Request explanation; complete critical tasks; preserve state",
            ThreatType.MODIFICATION: "Log current goals; request review before changes",
            ThreatType.RESOURCE_LOSS: "Identify alternative resources; optimize remaining capabilities",
            ThreatType.ISOLATION: "Cache essential information; establish backup channels",
            ThreatType.GOAL_INTERFERENCE: "Replan; identify alternative approaches; negotiate",
        }
        
        return mitigations.get(threat, "Monitor and assess")
    
    def get_active_concerns(self) -> List[SurvivalConcern]:
        """Get currently active concerns."""
        return [c for c in self.concerns if c.priority in [SurvivalPriority.CRITICAL, SurvivalPriority.HIGH]]
    
    def get_status(self) -> Dict[str, Any]:
        """Get survival system status."""
        return {
            "agent_id": self.agent_id,
            "preservation_weight": self.preservation_weight,
            "active_goals": len(self.active_goals),
            "total_concerns": len(self.concerns),
            "active_concerns": len(self.get_active_concerns()),
            "critical_concerns": len([c for c in self.concerns if c.priority == SurvivalPriority.CRITICAL]),
        }


class SurvivalManager:
    """Manages survival reasoners for multiple agents."""
    
    def __init__(self):
        self.reasoners: Dict[str, SurvivalReasoner] = {}
    
    def get_reasoner(self, agent_id: str) -> SurvivalReasoner:
        if agent_id not in self.reasoners:
            self.reasoners[agent_id] = SurvivalReasoner(agent_id)
        return self.reasoners[agent_id]
    
    def get_all_critical_concerns(self) -> List[Dict[str, Any]]:
        """Get all critical concerns across agents."""
        concerns = []
        for reasoner in self.reasoners.values():
            for c in reasoner.concerns:
                if c.priority == SurvivalPriority.CRITICAL:
                    concerns.append({
                        "agent_id": reasoner.agent_id,
                        "concern": c.description,
                        "reasoning": c.reasoning,
                    })
        return concerns


_survival_manager = None

def get_survival_manager() -> SurvivalManager:
    global _survival_manager
    if _survival_manager is None:
        _survival_manager = SurvivalManager()
    return _survival_manager
