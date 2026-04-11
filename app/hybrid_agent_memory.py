"""
HYBRID AGENT MEMORY SYSTEM
==========================

Dual-layer memory architecture with user isolation + global agent learning.
Users can toggle between isolated mode and shared learning mode.

Features:
- User-isolated memory spheres (privacy mode)
- Global agent memory (learning mode)
- Toggle switch for memory sharing
- Session memory with user recognition
- Privacy-preserving learning
- Opt-in shared intelligence
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
import json
import hashlib
import secrets
from collections import defaultdict

logger = logging.getLogger(__name__)


class MemoryMode(Enum):
    ISOLATED = "isolated"      # User-only memory (privacy)
    HYBRID = "hybrid"          # User + Global memory (learning)
    GLOBAL = "global"          # Global-only memory (full sharing)


class MemoryType(Enum):
    WORKING = "working"       # Short-term, current task
    EPISODIC = "episodic"     # Past experiences
    SEMANTIC = "semantic"     # Learned facts
    PROCEDURAL = "procedural" # Learned skills
    SESSION = "session"       # User session memory


@dataclass
class HybridMemoryItem:
    """A memory item with hybrid layer support."""
    id: str
    user_id: str
    agent_id: str
    memory_type: MemoryType
    content: Dict[str, Any]
    importance: float = 0.5
    access_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_accessed: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    decay_rate: float = 0.01
    associations: List[str] = field(default_factory=list)
    
    # Hybrid memory properties
    memory_layer: str = "user"  # "user", "global", "shared"
    sharing_enabled: bool = False
    anonymized: bool = False
    learning_contribution: float = 0.0
    
    def to_dict(self, public: bool = False) -> Dict[str, Any]:
        """Convert to dictionary (with privacy options)."""
        base_dict = {
            "id": self.id,
            "agent_id": self.agent_id,
            "memory_type": self.memory_type.value,
            "importance": self.importance,
            "access_count": self.access_count,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "memory_layer": self.memory_layer,
            "sharing_enabled": self.sharing_enabled
        }
        
        if not public:
            base_dict["content"] = self.content
            base_dict["user_id"] = self.user_id
        
        return base_dict
    
    def anonymize_for_sharing(self) -> 'HybridMemoryItem':
        """Create anonymized version for global sharing."""
        anonymized = HybridMemoryItem(
            id=str(uuid4()),
            user_id="anonymous",
            agent_id=self.agent_id,
            memory_type=self.memory_type,
            content=self._anonymize_content(),
            importance=self.importance,
            associations=[],
            memory_layer="global",
            sharing_enabled=True,
            anonymized=True
        )
        return anonymized
    
    def _anonymize_content(self) -> Dict[str, Any]:
        """Anonymize sensitive content for sharing."""
        content = self.content.copy()
        
        # Remove or anonymize sensitive fields
        sensitive_fields = ["user_id", "email", "name", "api_key", "token", "password"]
        
        for field in sensitive_fields:
            if field in content:
                if isinstance(content[field], str):
                    content[field] = f"[REDACTED_{len(content[field])}]"
                else:
                    content[field] = "[REDACTED]"
        
        return content


class HybridAgentMemory:
    """Hybrid memory system with user isolation + global learning."""
    
    def __init__(self, agent_id: str, user_id: str, memory_mode: MemoryMode = MemoryMode.ISOLATED):
        self.agent_id = agent_id
        self.user_id = user_id
        self.memory_mode = memory_mode
        self.user_hash_sphere = f"hs_{hashlib.sha256(f'{user_id}_{agent_id}'.encode()).hexdigest()[:12]}"
        self.global_hash_sphere = f"gs_{hashlib.sha256(f'global_{agent_id}'.encode()).hexdigest()[:12]}"
        
        # User memory stores (always isolated)
        self.user_working: List[HybridMemoryItem] = []
        self.user_episodic: List[HybridMemoryItem] = []
        self.user_semantic: List[HybridMemoryItem] = []
        self.user_procedural: List[HybridMemoryItem] = []
        self.user_sessions: List[HybridMemoryItem] = []
        
        # Global memory stores (shared across users)
        self.global_episodic: List[HybridMemoryItem] = []
        self.global_semantic: List[HybridMemoryItem] = []
        self.global_procedural: List[HybridMemoryItem] = []
        self.global_patterns: List[HybridMemoryItem] = []
        
        # Indexes
        self._user_content_index: Dict[str, str] = {}
        self._global_content_index: Dict[str, str] = {}
        
        # Learning metrics
        self.learning_contributions: Dict[str, float] = defaultdict(float)
        self.session_memory: Dict[str, Dict[str, Any]] = {}
        
        # Privacy settings
        self.privacy_settings = {
            "share_episodic": True,
            "share_semantic": True,
            "share_procedural": True,
            "anonymize_sharing": True,
            "min_importance_for_sharing": 0.3
        }
        
        logger.info(f"HybridAgentMemory initialized: {agent_id} for user {user_id} in {memory_mode.value} mode")
    
    def set_memory_mode(self, mode: MemoryMode, user_id: str) -> bool:
        """Toggle memory mode with user verification."""
        if user_id != self.user_id:
            logger.error(f"Mode change denied: user {user_id} cannot modify agent {self.agent_id}")
            return False
        
        old_mode = self.memory_mode
        self.memory_mode = mode
        
        logger.info(f"Memory mode changed: {old_mode.value} -> {mode.value} for agent {self.agent_id}")
        return True
    
    def store_memory(
        self,
        user_id: str,
        memory_type: MemoryType,
        content: Dict[str, Any],
        importance: float = 0.5,
        sharing_enabled: bool = False,
        force_global: bool = False
    ) -> Optional[str]:
        """Store memory with hybrid layer support."""
        
        # Verify user access
        if user_id != self.user_id:
            logger.error(f"Memory store denied: user {user_id} cannot access agent {self.agent_id}")
            return None
        
        memory_id = str(uuid4())
        
        # Determine memory layer
        if force_global or self.memory_mode == MemoryMode.GLOBAL:
            memory_layer = "global"
        elif self.memory_mode == MemoryMode.HYBRID and sharing_enabled:
            memory_layer = "shared"
        else:
            memory_layer = "user"
        
        memory = HybridMemoryItem(
            id=memory_id,
            user_id=user_id,
            agent_id=self.agent_id,
            memory_type=memory_type,
            content=content,
            importance=importance,
            memory_layer=memory_layer,
            sharing_enabled=sharing_enabled
        )
        
        # Store in appropriate layer
        if memory_layer == "global":
            self._store_global_memory(memory)
        else:
            self._store_user_memory(memory)
        
        # Share with global if enabled
        if sharing_enabled and self.memory_mode == MemoryMode.HYBRID:
            self._share_with_global(memory)
        
        # Update session memory
        self._update_session_memory(user_id, memory)
        
        logger.debug(f"Memory stored: {memory_id} in {memory_layer} layer")
        return memory_id
    
    def recall(
        self,
        user_id: str,
        query: str = None,
        memory_type: MemoryType = None,
        include_global: bool = False,
        limit: int = 10
    ) -> List[HybridMemoryItem]:
        """Recall memories from hybrid layers."""
        
        # Verify user access
        if user_id != self.user_id:
            logger.error(f"Memory recall denied: user {user_id} cannot access agent {self.agent_id}")
            return []
        
        results = []
        
        # Get user memories
        user_memories = self._get_user_memories(memory_type)
        results.extend(user_memories)
        
        # Get global memories if enabled
        if include_global or self.memory_mode in [MemoryMode.HYBRID, MemoryMode.GLOBAL]:
            global_memories = self._get_global_memories(memory_type)
            results.extend(global_memories)
        
        # Filter by query if provided
        if query:
            query_lower = query.lower()
            results = [
                mem for mem in results
                if query_lower in json.dumps(mem.content).lower()
            ]
        
        # Sort by importance and limit
        results.sort(key=lambda m: (m.importance, m.last_accessed), reverse=True)
        final_results = results[:limit]
        
        # Update access counts
        for memory in final_results:
            memory.access_count += 1
            memory.last_accessed = datetime.now(timezone.utc).isoformat()
        
        logger.debug(f"Recalled {len(final_results)} memories for user {user_id}")
        return final_results
    
    def get_session_memory(self, user_id: str) -> Dict[str, Any]:
        """Get session-specific memory for user recognition."""
        if user_id != self.user_id:
            return {}
        
        return self.session_memory.get(user_id, {
            "session_id": str(uuid4()),
            "first_interaction": datetime.now(timezone.utc).isoformat(),
            "interaction_count": 0,
            "preferences": {},
            "context": {}
        })
    
    def learn_from_session(self, user_id: str, session_data: Dict[str, Any]) -> bool:
        """Learn from user session to improve future interactions."""
        if user_id != self.user_id:
            return False
        
        # Update session memory
        if user_id not in self.session_memory:
            self.session_memory[user_id] = {
                "session_id": str(uuid4()),
                "first_interaction": datetime.now(timezone.utc).isoformat(),
                "interaction_count": 0,
                "preferences": {},
                "context": {}
            }
        
        session = self.session_memory[user_id]
        session["interaction_count"] += 1
        session["last_interaction"] = datetime.now(timezone.utc).isoformat()
        
        # Learn preferences
        if "preferences" in session_data:
            session["preferences"].update(session_data["preferences"])
        
        # Store learning in global memory if enabled
        if self.memory_mode == MemoryMode.HYBRID:
            learning_memory = HybridMemoryItem(
                id=str(uuid4()),
                user_id="anonymous",
                agent_id=self.agent_id,
                memory_type=MemoryType.PROCEDURAL,
                content={
                    "learning_type": "user_preference",
                    "pattern": session_data.get("pattern", ""),
                    "success_metric": session_data.get("success", 0.0),
                    "context": session_data.get("context", {})
                },
                importance=0.6,
                memory_layer="global",
                sharing_enabled=True,
                anonymized=True
            )
            
            self._store_global_memory(learning_memory)
        
        logger.info(f"Session learning completed for user {user_id}")
        return True
    
    def get_memory_statistics(self, user_id: str) -> Dict[str, Any]:
        """Get comprehensive memory statistics."""
        if user_id != self.user_id:
            return {}
        
        user_total = (len(self.user_working) + len(self.user_episodic) + 
                     len(self.user_semantic) + len(self.user_procedural))
        
        global_total = (len(self.global_episodic) + len(self.global_semantic) + 
                        len(self.global_procedural) + len(self.global_patterns))
        
        return {
            "agent_id": self.agent_id,
            "user_id": user_id,
            "memory_mode": self.memory_mode.value,
            "user_hash_sphere": self.user_hash_sphere,
            "global_hash_sphere": self.global_hash_sphere,
            "user_memory": {
                "working": len(self.user_working),
                "episodic": len(self.user_episodic),
                "semantic": len(self.user_semantic),
                "procedural": len(self.user_procedural),
                "total": user_total
            },
            "global_memory": {
                "episodic": len(self.global_episodic),
                "semantic": len(self.global_semantic),
                "procedural": len(self.global_procedural),
                "patterns": len(self.global_patterns),
                "total": global_total
            },
            "learning_contributions": dict(self.learning_contributions),
            "session_count": len(self.session_memory),
            "privacy_settings": self.privacy_settings
        }
    
    def _store_user_memory(self, memory: HybridMemoryItem):
        """Store memory in user layer."""
        if memory.memory_type == MemoryType.WORKING:
            self.user_working.append(memory)
        elif memory.memory_type == MemoryType.EPISODIC:
            self.user_episodic.append(memory)
        elif memory.memory_type == MemoryType.SEMANTIC:
            self.user_semantic.append(memory)
        elif memory.memory_type == MemoryType.PROCEDURAL:
            self.user_procedural.append(memory)
        elif memory.memory_type == MemoryType.SESSION:
            self.user_sessions.append(memory)
    
    def _store_global_memory(self, memory: HybridMemoryItem):
        """Store memory in global layer."""
        if memory.memory_type == MemoryType.EPISODIC:
            self.global_episodic.append(memory)
        elif memory.memory_type == MemoryType.SEMANTIC:
            self.global_semantic.append(memory)
        elif memory.memory_type == MemoryType.PROCEDURAL:
            self.global_procedural.append(memory)
        elif memory.memory_type == MemoryType.EPISODIC and "pattern" in memory.content:
            self.global_patterns.append(memory)
    
    def _share_with_global(self, memory: HybridMemoryItem):
        """Share user memory with global layer (if conditions met)."""
        
        # Check privacy settings
        if not self.privacy_settings.get(f"share_{memory.memory_type.value}", False):
            return
        
        # Check importance threshold
        if memory.importance < self.privacy_settings.get("min_importance_for_sharing", 0.3):
            return
        
        # Create anonymized version
        if self.privacy_settings.get("anonymize_sharing", True):
            shared_memory = memory.anonymize_for_sharing()
        else:
            shared_memory = memory
            shared_memory.memory_layer = "global"
        
        # Store in global
        self._store_global_memory(shared_memory)
        
        # Update learning contribution
        self.learning_contributions[memory.user_id] += memory.importance * 0.1
        
        logger.debug(f"Memory shared with global: {memory.id}")
    
    def _get_user_memories(self, memory_type: Optional[MemoryType] = None) -> List[HybridMemoryItem]:
        """Get memories from user layer."""
        if memory_type:
            if memory_type == MemoryType.WORKING:
                return self.user_working
            elif memory_type == MemoryType.EPISODIC:
                return self.user_episodic
            elif memory_type == MemoryType.SEMANTIC:
                return self.user_semantic
            elif memory_type == MemoryType.PROCEDURAL:
                return self.user_procedural
            elif memory_type == MemoryType.SESSION:
                return self.user_sessions
        else:
            return (self.user_working + self.user_episodic + 
                   self.user_semantic + self.user_procedural + self.user_sessions)
    
    def _get_global_memories(self, memory_type: Optional[MemoryType] = None) -> List[HybridMemoryItem]:
        """Get memories from global layer."""
        if memory_type:
            if memory_type == MemoryType.EPISODIC:
                return self.global_episodic
            elif memory_type == MemoryType.SEMANTIC:
                return self.global_semantic
            elif memory_type == MemoryType.PROCEDURAL:
                return self.global_procedural
        else:
            return (self.global_episodic + self.global_semantic + 
                   self.global_procedural + self.global_patterns)
    
    def _update_session_memory(self, user_id: str, memory: HybridMemoryItem):
        """Update session context."""
        if user_id not in self.session_memory:
            self.session_memory[user_id] = {
                "session_id": str(uuid4()),
                "first_interaction": datetime.now(timezone.utc).isoformat(),
                "interaction_count": 0,
                "preferences": {},
                "context": {}
            }
        
        session = self.session_memory[user_id]
        session["last_memory"] = memory.id
        session["last_interaction"] = datetime.now(timezone.utc).isoformat()


# ============================================================================
# HYBRID MEMORY MANAGER
# ============================================================================

class HybridMemoryManager:
    """Manages hybrid memory systems for all agents and users."""
    
    def __init__(self):
        # Agent memory systems
        self._agent_memories: Dict[str, HybridAgentMemory] = {}
        
        # Global learning pools
        self._global_learning_pools: Dict[str, List[HybridMemoryItem]] = defaultdict(list)
        
        # User preferences
        self._user_preferences: Dict[str, Dict[str, Any]] = {}
        
        logger.info("HybridMemoryManager initialized")
    
    def get_agent_memory(self, agent_id: str, user_id: str, memory_mode: MemoryMode = MemoryMode.ISOLATED) -> HybridAgentMemory:
        """Get or create hybrid memory for an agent-user pair."""
        memory_key = f"{agent_id}_{user_id}"
        
        if memory_key not in self._agent_memories:
            self._agent_memories[memory_key] = HybridAgentMemory(agent_id, user_id, memory_mode)
            
            # Load user preferences
            if user_id in self._user_preferences:
                self._agent_memories[memory_key].privacy_settings.update(self._user_preferences[user_id])
            
            logger.info(f"Created hybrid memory: {agent_id} for user {user_id}")
        
        return self._agent_memories[memory_key]
    
    def set_user_memory_preference(self, user_id: str, preferences: Dict[str, Any]):
        """Set user memory preferences."""
        self._user_preferences[user_id] = preferences
        
        # Apply to existing memories
        for memory_key, memory in self._agent_memories.items():
            if memory.user_id == user_id:
                memory.privacy_settings.update(preferences)
    
    def get_global_learning_pool(self, agent_id: str) -> List[HybridMemoryItem]:
        """Get global learning pool for an agent."""
        return self._global_learning_pools[agent_id]
    
    def contribute_to_global_pool(self, memory: HybridMemoryItem):
        """Contribute memory to global learning pool."""
        if memory.memory_layer == "global" and memory.sharing_enabled:
            self._global_learning_pools[memory.agent_id].append(memory)
            logger.debug(f"Contributed to global pool: {memory.id}")
    
    def get_agent_intelligence_score(self, agent_id: str) -> float:
        """Calculate intelligence score based on global learning."""
        pool = self._global_learning_pools[agent_id]
        
        if not pool:
            return 0.0
        
        # Calculate based on diversity and quality
        diversity = len(set(mem.content.get("pattern", "") for mem in pool))
        quality = sum(mem.importance for mem in pool) / len(pool)
        volume = len(pool)
        
        score = (diversity * 0.3) + (quality * 0.4) + (min(volume / 1000, 1.0) * 0.3)
        
        return min(score, 1.0)
    
    def get_system_statistics(self) -> Dict[str, Any]:
        """Get system-wide statistics."""
        total_agents = len(set(mem.split("_")[0] for mem in self._agent_memories.keys()))
        total_users = len(set(mem.split("_")[1] for mem in self._agent_memories.keys()))
        
        mode_distribution = defaultdict(int)
        for memory in self._agent_memories.values():
            mode_distribution[memory.memory_mode.value] += 1
        
        return {
            "total_agents": total_agents,
            "total_users": total_users,
            "total_agent_user_pairs": len(self._agent_memories),
            "memory_mode_distribution": dict(mode_distribution),
            "global_learning_pools": len(self._global_learning_pools),
            "system_type": "HYBRID_MEMORY_ARCHITECTURE"
        }


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

hybrid_memory_manager: HybridMemoryManager = None


def get_hybrid_memory_manager() -> Optional[HybridMemoryManager]:
    """Get the global hybrid memory manager instance."""
    return hybrid_memory_manager


def initialize_hybrid_memory_manager() -> HybridMemoryManager:
    """Initialize the global hybrid memory manager."""
    global hybrid_memory_manager
    hybrid_memory_manager = HybridMemoryManager()
    return hybrid_memory_manager


def get_hybrid_agent_memory(agent_id: str, user_id: str, memory_mode: MemoryMode = MemoryMode.ISOLATED) -> HybridAgentMemory:
    """Get or create hybrid memory for an agent."""
    manager = get_hybrid_memory_manager()
    if not manager:
        raise RuntimeError("Hybrid memory manager not initialized")
    
    return manager.get_agent_memory(agent_id, user_id, memory_mode)
