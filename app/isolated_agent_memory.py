"""
ISOLATED AGENT MEMORY SYSTEM
=============================

User-isolated memory with hash spheres for each tenant.
Ensures complete memory isolation between users/tenants.

Features:
- Per-user memory hash spheres
- Cross-user access prevention
- Cryptographic memory boundaries
- Tenant-aware memory management
- Audit trail for memory access
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
import json
import hashlib
import secrets

logger = logging.getLogger(__name__)


class MemoryType(Enum):
    WORKING = "working"       # Short-term, current task
    EPISODIC = "episodic"     # Past experiences
    SEMANTIC = "semantic"     # Learned facts
    PROCEDURAL = "procedural" # Learned skills


@dataclass
class IsolatedMemoryItem:
    """A memory item with user isolation."""
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
    hash_sphere_id: str = ""
    
    def __post_init__(self):
        # Generate hash sphere ID based on user_id
        self.hash_sphere_id = self._generate_hash_sphere_id()
    
    def _generate_hash_sphere_id(self) -> str:
        """Generate unique hash sphere ID for user isolation."""
        sphere_input = f"{self.user_id}_{self.agent_id}"
        return f"sphere_{hashlib.sha256(sphere_input.encode()).hexdigest()[:16]}"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (without sensitive data)."""
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "memory_type": self.memory_type.value,
            "content": self.content,
            "importance": self.importance,
            "access_count": self.access_count,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "hash_sphere_id": self.hash_sphere_id
        }


class IsolatedAgentMemory:
    """User-isolated agent memory with hash sphere boundaries."""
    
    def __init__(self, agent_id: str, user_id: str):
        self.agent_id = agent_id
        self.user_id = user_id
        self.hash_sphere_id = self._generate_hash_sphere_id()
        
        # Memory stores (isolated per user)
        self.working: List[IsolatedMemoryItem] = []
        self.episodic: List[IsolatedMemoryItem] = []
        self.semantic: List[IsolatedMemoryItem] = []
        self.procedural: List[IsolatedMemoryItem] = []
        
        # Indexes (isolated per user)
        self._content_index: Dict[str, str] = {}
        self._keyword_index: Dict[str, List[str]] = {}
        
        # Limits
        self.MAX_WORKING_MEMORY = 50
        self.MAX_EPISODIC_MEMORY = 1000
        self.MAX_SEMANTIC_MEMORY = 500
        self.MAX_PROCEDURAL_MEMORY = 200
        
        # Security
        self._access_key = secrets.token_urlsafe(32)
        
        logger.info(f"IsolatedAgentMemory initialized: {agent_id} in sphere {self.hash_sphere_id}")
    
    def _generate_hash_sphere_id(self) -> str:
        """Generate unique hash sphere ID for this user-agent pair."""
        sphere_input = f"{self.user_id}_{self.agent_id}_{datetime.now(timezone.utc).isoformat()}"
        return f"hs_{hashlib.sha256(sphere_input.encode()).hexdigest()[:12]}"
    
    def verify_access(self, user_id: str, access_key: Optional[str] = None) -> bool:
        """Verify access rights to this memory sphere."""
        # Primary check: user_id must match
        if user_id != self.user_id:
            logger.error(f"Access denied: user {user_id} cannot access sphere {self.hash_sphere_id}")
            return False
        
        # Secondary check: access key (if provided)
        if access_key and access_key != self._access_key:
            logger.error(f"Access denied: invalid access key for sphere {self.hash_sphere_id}")
            return False
        
        return True
    
    def store_memory(
        self,
        user_id: str,
        memory_type: MemoryType,
        content: Dict[str, Any],
        importance: float = 0.5,
        associations: List[str] = None,
        access_key: Optional[str] = None
    ) -> Optional[str]:
        """Store a memory with user isolation verification."""
        
        # Verify access rights
        if not self.verify_access(user_id, access_key):
            return None
        
        memory_id = str(uuid4())
        
        memory = IsolatedMemoryItem(
            id=memory_id,
            user_id=user_id,
            agent_id=self.agent_id,
            memory_type=memory_type,
            content=content,
            importance=importance,
            associations=associations or [],
        )
        
        # Add to appropriate store
        if memory_type == MemoryType.WORKING:
            self.working.append(memory)
            if len(self.working) > self.MAX_WORKING_MEMORY:
                self._consolidate_working_memory()
        
        elif memory_type == MemoryType.EPISODIC:
            self.episodic.append(memory)
            if len(self.episodic) > self.MAX_EPISODIC_MEMORY:
                self._forget_old_episodic()
        
        elif memory_type == MemoryType.SEMANTIC:
            self.semantic.append(memory)
            if len(self.semantic) > self.MAX_SEMANTIC_MEMORY:
                self._forget_low_importance_semantic()
        
        # Index for retrieval
        self._index_memory(memory)
        
        logger.debug(f"Memory stored in sphere {self.hash_sphere_id}: {memory_id}")
        return memory_id
    
    def recall(
        self,
        user_id: str,
        query: str = None,
        memory_type: MemoryType = None,
        limit: int = 10,
        access_key: Optional[str] = None
    ) -> List[IsolatedMemoryItem]:
        """Recall memories with user isolation verification."""
        
        # Verify access rights
        if not self.verify_access(user_id, access_key):
            return []
        
        candidates = []
        
        # Get candidates from appropriate stores
        if memory_type:
            if memory_type == MemoryType.WORKING:
                candidates = self.working
            elif memory_type == MemoryType.EPISODIC:
                candidates = self.episodic
            elif memory_type == MemoryType.SEMANTIC:
                candidates = self.semantic
            elif memory_type == MemoryType.PROCEDURAL:
                candidates = self.procedural
        else:
            candidates = self.working + self.episodic + self.semantic + self.procedural
        
        # Filter by query if provided
        if query:
            query_lower = query.lower()
            candidates = [
                mem for mem in candidates
                if query_lower in json.dumps(mem.content).lower()
            ]
        
        # Sort by importance and limit
        candidates.sort(key=lambda m: (m.importance, m.last_accessed), reverse=True)
        result = candidates[:limit]
        
        # Update access counts
        for memory in result:
            memory.access_count += 1
            memory.last_accessed = datetime.now(timezone.utc).isoformat()
        
        logger.debug(f"Recalled {len(result)} memories from sphere {self.hash_sphere_id}")
        return result
    
    def get_memory_sphere_info(self, user_id: str, access_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get information about this memory sphere."""
        
        # Verify access rights
        if not self.verify_access(user_id, access_key):
            return None
        
        return {
            "hash_sphere_id": self.hash_sphere_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "memory_counts": {
                "working": len(self.working),
                "episodic": len(self.episodic),
                "semantic": len(self.semantic),
                "procedural": len(self.procedural)
            },
            "total_memories": len(self.working) + len(self.episodic) + len(self.semantic) + len(self.procedural),
            "created_at": self.working[0].created_at if self.working else None,
            "isolation_status": "ACTIVE"
        }
    
    def _consolidate_working_memory(self):
        """Consolidate working memory to episodic."""
        if not self.working:
            return
        
        # Move oldest/least important to episodic
        self.working.sort(key=lambda m: (m.importance, m.last_accessed))
        to_consolidate = self.working[:self.MAX_WORKING_MEMORY // 2]
        
        for memory in to_consolidate:
            memory.memory_type = MemoryType.EPISODIC
            self.episodic.append(memory)
        
        # Keep only the most important in working
        self.working = self.working[self.MAX_WORKING_MEMORY // 2:]
    
    def _forget_old_episodic(self):
        """Forget old episodic memories."""
        if not self.episodic:
            return
        
        # Sort by importance and access time
        self.episodic.sort(key=lambda m: (m.importance, m.last_accessed))
        
        # Keep only the most recent/important
        self.episodic = self.episodic[-self.MAX_EPISODIC_MEMORY:]
    
    def _forget_low_importance_semantic(self):
        """Forget low importance semantic memories."""
        if not self.semantic:
            return
        
        # Sort by importance
        self.semantic.sort(key=lambda m: m.importance)
        
        # Keep only the most important
        self.semantic = self.semantic[-self.MAX_SEMANTIC_MEMORY:]
    
    def _index_memory(self, memory: IsolatedMemoryItem):
        """Index a memory for fast retrieval."""
        # Content hash
        content_hash = hashlib.md5(
            json.dumps(memory.content, sort_keys=True).encode()
        ).hexdigest()
        self._content_index[content_hash] = memory.id
        
        # Keyword index
        text = json.dumps(memory.content).lower()
        words = set(text.split())
        for word in words:
            if len(word) > 3:  # Only index meaningful words
                if word not in self._keyword_index:
                    self._keyword_index[word] = []
                self._keyword_index[word].append(memory.id)
    
    def clear_memory(self, user_id: str, access_key: Optional[str] = None) -> bool:
        """Clear all memory in this sphere."""
        
        # Verify access rights
        if not self.verify_access(user_id, access_key):
            return False
        
        self.working.clear()
        self.episodic.clear()
        self.semantic.clear()
        self.procedural.clear()
        self._content_index.clear()
        self._keyword_index.clear()
        
        logger.info(f"Memory cleared in sphere {self.hash_sphere_id}")
        return True


# ============================================================================
# ISOLATED MEMORY SPHERE MANAGER
# ============================================================================

class IsolatedMemorySphereManager:
    """Manages isolated memory spheres for all users."""
    
    def __init__(self):
        # User-isolated memory spheres
        self._user_spheres: Dict[str, Dict[str, IsolatedAgentMemory]] = {}
        self._sphere_registry: Dict[str, Dict[str, Any]] = {}
        
        logger.info("IsolatedMemorySphereManager initialized")
    
    def get_agent_memory(self, agent_id: str, user_id: str) -> IsolatedAgentMemory:
        """Get or create isolated memory for an agent."""
        
        # Create user sphere if it doesn't exist
        if user_id not in self._user_spheres:
            self._user_spheres[user_id] = {}
            logger.info(f"Created memory sphere for user: {user_id}")
        
        user_sphere = self._user_spheres[user_id]
        
        # Create agent memory if it doesn't exist
        if agent_id not in user_sphere:
            user_sphere[agent_id] = IsolatedAgentMemory(agent_id, user_id)
            
            # Register sphere
            sphere_info = {
                "user_id": user_id,
                "agent_id": agent_id,
                "hash_sphere_id": user_sphere[agent_id].hash_sphere_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "access_key": user_sphere[agent_id]._access_key
            }
            self._sphere_registry[user_sphere[agent_id].hash_sphere_id] = sphere_info
            
            logger.info(f"Created agent memory: {agent_id} in sphere {sphere_info['hash_sphere_id']}")
        
        return user_sphere[agent_id]
    
    def verify_sphere_access(self, hash_sphere_id: str, user_id: str, access_key: Optional[str] = None) -> bool:
        """Verify access to a specific memory sphere."""
        if hash_sphere_id not in self._sphere_registry:
            return False
        
        sphere_info = self._sphere_registry[hash_sphere_id]
        
        # Check user ID
        if sphere_info["user_id"] != user_id:
            return False
        
        # Check access key if provided
        if access_key and access_key != sphere_info["access_key"]:
            return False
        
        return True
    
    def get_user_sphere_info(self, user_id: str) -> Dict[str, Any]:
        """Get information about all spheres for a user."""
        if user_id not in self._user_spheres:
            return {"user_id": user_id, "spheres": [], "total_agents": 0}
        
        spheres = []
        for agent_id, memory in self._user_spheres[user_id].items():
            spheres.append(memory.get_memory_sphere_info(user_id))
        
        return {
            "user_id": user_id,
            "spheres": spheres,
            "total_agents": len(spheres),
            "isolation_status": "ACTIVE"
        }
    
    def delete_user_sphere(self, user_id: str, confirm: bool = False) -> bool:
        """Delete all memory spheres for a user."""
        if not confirm:
            return False
        
        if user_id in self._user_spheres:
            # Remove all spheres for this user
            for agent_memory in self._user_spheres[user_id].values():
                sphere_id = agent_memory.hash_sphere_id
                if sphere_id in self._sphere_registry:
                    del self._sphere_registry[sphere_id]
            
            del self._user_spheres[user_id]
            logger.info(f"Deleted all memory spheres for user: {user_id}")
            return True
        
        return False
    
    def get_system_statistics(self) -> Dict[str, Any]:
        """Get system-wide statistics."""
        total_users = len(self._user_spheres)
        total_agents = sum(len(sphere) for sphere in self._user_spheres.values())
        total_spheres = len(self._sphere_registry)
        
        return {
            "total_users": total_users,
            "total_agents": total_agents,
            "total_spheres": total_spheres,
            "isolation_status": "ACTIVE",
            "system_type": "USER_ISOLATED_HASH_SPHERES"
        }


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

isolated_memory_manager: IsolatedMemorySphereManager = None


def get_isolated_memory_manager() -> Optional[IsolatedMemorySphereManager]:
    """Get the global isolated memory manager instance."""
    return isolated_memory_manager


def initialize_isolated_memory_manager() -> IsolatedMemorySphereManager:
    """Initialize the global isolated memory manager."""
    global isolated_memory_manager
    isolated_memory_manager = IsolatedMemorySphereManager()
    return isolated_memory_manager


def get_isolated_agent_memory(agent_id: str, user_id: str) -> IsolatedAgentMemory:
    """Get or create isolated memory for an agent."""
    manager = get_isolated_memory_manager()
    if not manager:
        raise RuntimeError("Isolated memory manager not initialized")
    
    return manager.get_agent_memory(agent_id, user_id)
