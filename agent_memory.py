"""Agent Memory Module"""
from typing import Dict, Any, Optional
from datetime import datetime

class AgentMemory:
    """Agent memory management"""
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.memory: Dict[str, Any] = {}
        self.created_at = datetime.now()
    
    def store(self, key: str, value: Any) -> None:
        """Store value in memory"""
        self.memory[key] = {
            "value": value,
            "timestamp": datetime.now()
        }
    
    def retrieve(self, key: str) -> Optional[Any]:
        """Retrieve value from memory"""
        if key in self.memory:
            return self.memory[key]["value"]
        return None
    
    def clear(self) -> None:
        """Clear memory"""
        self.memory.clear()
