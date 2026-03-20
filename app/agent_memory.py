"""
AGENT MEMORY AND LEARNING SYSTEM
================================

Persistent memory and learning for autonomous agents.
Enables agents to remember, learn, and improve over time.

Features:
- Short-term working memory
- Long-term persistent memory
- Episodic memory (past experiences)
- Semantic memory (learned knowledge)
- Procedural memory (learned skills)
- Memory consolidation and forgetting
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

logger = logging.getLogger(__name__)


class MemoryType(Enum):
    WORKING = "working"       # Short-term, current task
    EPISODIC = "episodic"     # Past experiences
    SEMANTIC = "semantic"     # Learned facts
    PROCEDURAL = "procedural" # Learned skills


@dataclass
class MemoryItem:
    """A single memory item."""
    id: str
    memory_type: MemoryType
    content: Dict[str, Any]
    importance: float = 0.5  # 0-1
    access_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_accessed: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    decay_rate: float = 0.01  # How fast it fades
    associations: List[str] = field(default_factory=list)  # Related memory IDs
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.memory_type.value,
            "content": self.content,
            "importance": self.importance,
            "access_count": self.access_count,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
        }


@dataclass
class LearnedPattern:
    """A pattern learned from experience."""
    id: str
    pattern_type: str  # success, failure, optimization
    trigger: Dict[str, Any]
    outcome: Dict[str, Any]
    confidence: float
    occurrences: int = 1
    last_seen: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AgentMemory:
    """
    Memory system for an autonomous agent.
    """
    
    MAX_WORKING_MEMORY = 10
    MAX_EPISODIC_MEMORY = 1000
    MAX_SEMANTIC_MEMORY = 5000
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        
        # Memory stores
        self.working: List[MemoryItem] = []
        self.episodic: List[MemoryItem] = []
        self.semantic: List[MemoryItem] = []
        self.procedural: Dict[str, LearnedPattern] = {}
        
        # Indexes for fast retrieval
        self._content_index: Dict[str, str] = {}  # content_hash -> memory_id
        self._keyword_index: Dict[str, List[str]] = {}  # keyword -> memory_ids
    
    def remember(
        self,
        content: Dict[str, Any],
        memory_type: MemoryType = MemoryType.WORKING,
        importance: float = 0.5,
        associations: List[str] = None,
    ) -> str:
        """Store a memory."""
        memory_id = str(uuid4())
        
        memory = MemoryItem(
            id=memory_id,
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
        
        return memory_id
    
    def recall(
        self,
        query: str = None,
        memory_type: MemoryType = None,
        limit: int = 10,
    ) -> List[MemoryItem]:
        """Recall memories matching a query."""
        candidates = []
        
        # Get candidates from appropriate stores
        if memory_type:
            if memory_type == MemoryType.WORKING:
                candidates = self.working
            elif memory_type == MemoryType.EPISODIC:
                candidates = self.episodic
            elif memory_type == MemoryType.SEMANTIC:
                candidates = self.semantic
        else:
            candidates = self.working + self.episodic + self.semantic
        
        if query:
            # Filter by keyword match
            keywords = query.lower().split()
            matching_ids = set()
            for kw in keywords:
                if kw in self._keyword_index:
                    matching_ids.update(self._keyword_index[kw])
            
            candidates = [m for m in candidates if m.id in matching_ids]
        
        # Sort by importance and recency
        candidates.sort(key=lambda m: (m.importance, m.access_count), reverse=True)
        
        # Update access count
        for m in candidates[:limit]:
            m.access_count += 1
            m.last_accessed = datetime.now(timezone.utc).isoformat()
        
        return candidates[:limit]
    
    def learn_pattern(
        self,
        pattern_type: str,
        trigger: Dict[str, Any],
        outcome: Dict[str, Any],
        confidence: float = 0.5,
    ) -> str:
        """Learn a pattern from experience."""
        pattern_hash = hashlib.md5(
            json.dumps({"trigger": trigger, "outcome": outcome}, sort_keys=True).encode()
        ).hexdigest()[:16]
        
        if pattern_hash in self.procedural:
            # Reinforce existing pattern
            pattern = self.procedural[pattern_hash]
            pattern.occurrences += 1
            pattern.confidence = min(1.0, pattern.confidence + 0.1)
            pattern.last_seen = datetime.now(timezone.utc).isoformat()
        else:
            # New pattern
            self.procedural[pattern_hash] = LearnedPattern(
                id=pattern_hash,
                pattern_type=pattern_type,
                trigger=trigger,
                outcome=outcome,
                confidence=confidence,
            )
        
        return pattern_hash
    
    def apply_learned_patterns(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Apply learned patterns to current context."""
        applicable = []
        
        for pattern in self.procedural.values():
            # Check if trigger matches context
            if self._pattern_matches(pattern.trigger, context):
                applicable.append({
                    "pattern_id": pattern.id,
                    "type": pattern.pattern_type,
                    "expected_outcome": pattern.outcome,
                    "confidence": pattern.confidence,
                    "occurrences": pattern.occurrences,
                })
        
        # Sort by confidence
        applicable.sort(key=lambda p: p["confidence"], reverse=True)
        
        return applicable
    
    def _pattern_matches(self, trigger: Dict, context: Dict) -> bool:
        """Check if a pattern trigger matches the context."""
        for key, value in trigger.items():
            if key not in context:
                return False
            if isinstance(value, str) and value != context[key]:
                return False
        return True
    
    def _index_memory(self, memory: MemoryItem):
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
    
    def _consolidate_working_memory(self):
        """Consolidate working memory to long-term."""
        # Move important items to episodic
        important = [m for m in self.working if m.importance > 0.7]
        for m in important:
            m.memory_type = MemoryType.EPISODIC
            self.episodic.append(m)
        
        # Keep only recent working memory
        self.working = self.working[-self.MAX_WORKING_MEMORY:]
    
    def _forget_old_episodic(self):
        """Forget old, low-importance episodic memories."""
        now = datetime.now(timezone.utc)
        
        def should_forget(m: MemoryItem) -> bool:
            age = (now - datetime.fromisoformat(m.created_at.replace('Z', '+00:00'))).days
            decay = m.decay_rate * age
            effective_importance = m.importance - decay
            return effective_importance < 0.2 and m.access_count < 3
        
        self.episodic = [m for m in self.episodic if not should_forget(m)]
    
    def _forget_low_importance_semantic(self):
        """Forget low-importance semantic memories."""
        self.semantic.sort(key=lambda m: (m.importance, m.access_count), reverse=True)
        self.semantic = self.semantic[:self.MAX_SEMANTIC_MEMORY]
    
    def get_summary(self) -> Dict[str, Any]:
        """Get memory summary."""
        return {
            "agent_id": self.agent_id,
            "working_memory": len(self.working),
            "episodic_memory": len(self.episodic),
            "semantic_memory": len(self.semantic),
            "learned_patterns": len(self.procedural),
        }
    
    def export(self) -> Dict[str, Any]:
        """Export all memories for persistence."""
        return {
            "agent_id": self.agent_id,
            "working": [m.to_dict() for m in self.working],
            "episodic": [m.to_dict() for m in self.episodic],
            "semantic": [m.to_dict() for m in self.semantic],
            "procedural": {k: {
                "id": v.id,
                "type": v.pattern_type,
                "trigger": v.trigger,
                "outcome": v.outcome,
                "confidence": v.confidence,
                "occurrences": v.occurrences,
            } for k, v in self.procedural.items()},
        }


class AgentLearning:
    """
    Learning system that improves agent performance over time.
    """
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.memory = AgentMemory(agent_id)
        
        # Performance tracking
        self.success_rate: float = 0.5
        self.total_tasks: int = 0
        self.successful_tasks: int = 0
        
        # Skill levels
        self.skills: Dict[str, float] = {}  # skill -> proficiency (0-1)
    
    def record_experience(
        self,
        task_type: str,
        context: Dict[str, Any],
        action: Dict[str, Any],
        result: Dict[str, Any],
        success: bool,
    ):
        """Record an experience for learning."""
        # Store as episodic memory
        self.memory.remember(
            content={
                "task_type": task_type,
                "context": context,
                "action": action,
                "result": result,
                "success": success,
            },
            memory_type=MemoryType.EPISODIC,
            importance=0.8 if success else 0.6,
        )
        
        # Learn pattern
        pattern_type = "success" if success else "failure"
        self.memory.learn_pattern(
            pattern_type=pattern_type,
            trigger={"task_type": task_type, **context},
            outcome={"action": action, "success": success},
        )
        
        # Update statistics
        self.total_tasks += 1
        if success:
            self.successful_tasks += 1
        self.success_rate = self.successful_tasks / self.total_tasks
        
        # Update skills
        if task_type not in self.skills:
            self.skills[task_type] = 0.5
        
        if success:
            self.skills[task_type] = min(1.0, self.skills[task_type] + 0.05)
        else:
            self.skills[task_type] = max(0.1, self.skills[task_type] - 0.02)
    
    def get_recommendations(self, task_type: str, context: Dict[str, Any]) -> List[Dict]:
        """Get recommendations based on past experiences."""
        # Apply learned patterns
        patterns = self.memory.apply_learned_patterns({
            "task_type": task_type,
            **context,
        })
        
        # Filter to success patterns
        recommendations = [
            p for p in patterns
            if p["type"] == "success" and p["confidence"] > 0.5
        ]
        
        return recommendations
    
    def get_skill_level(self, task_type: str) -> float:
        """Get skill level for a task type."""
        return self.skills.get(task_type, 0.5)
    
    def get_learning_summary(self) -> Dict[str, Any]:
        """Get learning summary."""
        return {
            "agent_id": self.agent_id,
            "success_rate": self.success_rate,
            "total_tasks": self.total_tasks,
            "skills": self.skills,
            "memory": self.memory.get_summary(),
        }


# Global memory store
_agent_memories: Dict[str, AgentMemory] = {}
_agent_learning: Dict[str, AgentLearning] = {}


def get_agent_memory(agent_id: str) -> AgentMemory:
    """Get or create memory for an agent."""
    if agent_id not in _agent_memories:
        _agent_memories[agent_id] = AgentMemory(agent_id)
    return _agent_memories[agent_id]


def get_agent_learning(agent_id: str) -> AgentLearning:
    """Get or create learning system for an agent."""
    if agent_id not in _agent_learning:
        _agent_learning[agent_id] = AgentLearning(agent_id)
    return _agent_learning[agent_id]
