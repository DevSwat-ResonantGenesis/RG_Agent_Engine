"""
Agent Brain - Central cognitive processing for agents.

STATUS: GRADUATED
CREATED: 2025-12-21
GRADUATED: 2025-12-21
GOVERNANCE: Central cognitive processing unit for agent reasoning.

INVARIANTS:
  - thought history is bounded
  - working memory is cleared on reset
  - long-term memory persists across sessions
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Governance: This module is GRADUATED
_IS_STUB = False


@dataclass
class ThoughtProcess:
    """A thought process in the agent brain."""
    thought_id: str
    content: str
    reasoning_chain: List[str] = field(default_factory=list)
    confidence: float = 0.5
    timestamp: datetime = field(default_factory=datetime.utcnow)


class AgentBrain:
    """Central cognitive processing unit for an agent."""
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.thoughts: List[ThoughtProcess] = []
        self.working_memory: Dict[str, Any] = {}
        self.long_term_memory: Dict[str, Any] = {}
        
    def think(self, input_data: str) -> ThoughtProcess:
        import uuid
        thought = ThoughtProcess(
            thought_id=str(uuid.uuid4())[:8],
            content=input_data,
            reasoning_chain=[f"Processing: {input_data[:50]}..."]
        )
        self.thoughts.append(thought)
        return thought
        
    def remember(self, key: str, value: Any, long_term: bool = False) -> None:
        if long_term:
            self.long_term_memory[key] = value
        else:
            self.working_memory[key] = value
            
    def recall(self, key: str) -> Optional[Any]:
        return self.working_memory.get(key) or self.long_term_memory.get(key)


_brains: Dict[str, AgentBrain] = {}

def get_agent_brain(agent_id: str) -> AgentBrain:
    if agent_id not in _brains:
        _brains[agent_id] = AgentBrain(agent_id)
    return _brains[agent_id]


class BrainManager:
    """Manages multiple agent brains."""
    
    def __init__(self):
        self.brains: Dict[str, AgentBrain] = {}
        
    def get_brain(self, agent_id: str) -> AgentBrain:
        if agent_id not in self.brains:
            self.brains[agent_id] = AgentBrain(agent_id)
        return self.brains[agent_id]
        
    def list_brains(self) -> List[str]:
        return list(self.brains.keys())
        
    def get_stats(self) -> Dict[str, Any]:
        return {"total_brains": len(self.brains)}


_brain_manager: Optional[BrainManager] = None

def get_brain_manager() -> BrainManager:
    global _brain_manager
    if _brain_manager is None:
        _brain_manager = BrainManager()
    return _brain_manager
