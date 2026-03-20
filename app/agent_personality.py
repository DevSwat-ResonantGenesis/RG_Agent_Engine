"""
Agent Personality - Manages agent personality traits and behaviors.

STATUS: GRADUATED
CREATED: 2025-12-21
GRADUATED: 2025-12-21
GOVERNANCE: Personality management for customizing agent communication style.

INVARIANTS:
  - verbosity, formality, creativity in range [0.0, 1.0]
  - default personality always available
  - system prompts are deterministic for same personality
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Governance: This module is GRADUATED
_IS_STUB = False


class PersonalityTrait(Enum):
    """Personality traits for agents."""
    FRIENDLY = "friendly"
    PROFESSIONAL = "professional"
    CASUAL = "casual"
    FORMAL = "formal"
    HELPFUL = "helpful"
    CONCISE = "concise"
    DETAILED = "detailed"
    CREATIVE = "creative"
    ANALYTICAL = "analytical"


@dataclass
class AgentPersonality:
    """Personality configuration for an agent."""
    agent_id: str
    name: str
    traits: List[PersonalityTrait] = field(default_factory=list)
    communication_style: str = "balanced"
    verbosity: float = 0.5  # 0.0 = very concise, 1.0 = very detailed
    formality: float = 0.5  # 0.0 = casual, 1.0 = formal
    creativity: float = 0.5  # 0.0 = analytical, 1.0 = creative
    custom_instructions: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class PersonalityManager:
    """
    Manages agent personalities.
    
    Provides configuration and retrieval of agent personality
    settings for customized interactions.
    """
    
    def __init__(self):
        self.personalities: Dict[str, AgentPersonality] = {}
        self.default_personality = AgentPersonality(
            agent_id="default",
            name="Default",
            traits=[PersonalityTrait.HELPFUL, PersonalityTrait.PROFESSIONAL]
        )
        
    def create_personality(
        self,
        agent_id: str,
        name: str,
        traits: Optional[List[str]] = None,
        communication_style: str = "balanced",
        verbosity: float = 0.5,
        formality: float = 0.5,
        creativity: float = 0.5,
        custom_instructions: Optional[str] = None
    ) -> AgentPersonality:
        """Create a personality for an agent."""
        trait_enums = []
        for t in (traits or []):
            try:
                trait_enums.append(PersonalityTrait(t.lower()))
            except ValueError:
                pass
                
        personality = AgentPersonality(
            agent_id=agent_id,
            name=name,
            traits=trait_enums,
            communication_style=communication_style,
            verbosity=verbosity,
            formality=formality,
            creativity=creativity,
            custom_instructions=custom_instructions
        )
        self.personalities[agent_id] = personality
        logger.info(f"Created personality for agent {agent_id}: {name}")
        return personality
        
    def get_personality(self, agent_id: str) -> AgentPersonality:
        """Get personality for an agent."""
        return self.personalities.get(agent_id, self.default_personality)
        
    def update_personality(
        self,
        agent_id: str,
        **kwargs
    ) -> Optional[AgentPersonality]:
        """Update an agent's personality."""
        personality = self.personalities.get(agent_id)
        if not personality:
            return None
            
        for key, value in kwargs.items():
            if hasattr(personality, key):
                setattr(personality, key, value)
                
        return personality
        
    def delete_personality(self, agent_id: str) -> bool:
        """Delete an agent's personality."""
        if agent_id in self.personalities:
            del self.personalities[agent_id]
            return True
        return False
        
    def list_personalities(self) -> List[AgentPersonality]:
        """List all personalities."""
        return list(self.personalities.values())
        
    def get_system_prompt(self, agent_id: str) -> str:
        """Generate a system prompt based on personality."""
        personality = self.get_personality(agent_id)
        
        parts = [f"You are {personality.name}."]
        
        if personality.traits:
            trait_str = ", ".join(t.value for t in personality.traits)
            parts.append(f"Your personality traits are: {trait_str}.")
            
        if personality.verbosity < 0.3:
            parts.append("Be very concise in your responses.")
        elif personality.verbosity > 0.7:
            parts.append("Provide detailed and thorough responses.")
            
        if personality.formality < 0.3:
            parts.append("Use a casual, friendly tone.")
        elif personality.formality > 0.7:
            parts.append("Maintain a formal, professional tone.")
            
        if personality.custom_instructions:
            parts.append(personality.custom_instructions)
            
        return " ".join(parts)
        
    def get_stats(self) -> Dict[str, Any]:
        """Get manager statistics."""
        trait_counts: Dict[str, int] = {}
        for p in self.personalities.values():
            for t in p.traits:
                trait_counts[t.value] = trait_counts.get(t.value, 0) + 1
                
        return {
            "total_personalities": len(self.personalities),
            "trait_distribution": trait_counts
        }


# Global instance
_personality_manager: Optional[PersonalityManager] = None


def get_personality_manager() -> PersonalityManager:
    """Get or create the global personality manager."""
    global _personality_manager
    if _personality_manager is None:
        _personality_manager = PersonalityManager()
    return _personality_manager
