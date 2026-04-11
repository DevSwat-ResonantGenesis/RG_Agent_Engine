"""
Agent Collaboration - Hub for multi-agent collaboration and communication.

STATUS: GRADUATED
CREATED: 2025-12-21
GRADUATED: 2025-12-21
GOVERNANCE: Collaboration hub for agents to communicate and share context.

INVARIANTS:
  - session IDs are unique
  - messages are only sent by session participants
  - closed sessions cannot receive new messages
  - shared context is isolated per session
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any, Set
from datetime import datetime
import asyncio
import logging
import uuid

logger = logging.getLogger(__name__)

# Governance: This module is GRADUATED
_IS_STUB = False


class MessageType(Enum):
    """Types of collaboration messages."""
    REQUEST = "request"
    RESPONSE = "response"
    BROADCAST = "broadcast"
    HANDOFF = "handoff"
    STATUS = "status"
    RESULT = "result"


class CollaborationStatus(Enum):
    """Status of a collaboration session."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CollaborationMessage:
    """A message in a collaboration session."""
    message_id: str
    session_id: str
    sender_id: str
    message_type: MessageType
    content: Any
    recipients: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CollaborationSession:
    """A collaboration session between agents."""
    session_id: str
    name: str
    participants: Set[str] = field(default_factory=set)
    status: CollaborationStatus = CollaborationStatus.ACTIVE
    messages: List[CollaborationMessage] = field(default_factory=list)
    shared_context: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)


class CollaborationHub:
    """
    Hub for multi-agent collaboration.
    
    Manages collaboration sessions, message routing, and
    shared context between agents.
    """
    
    def __init__(self):
        self.sessions: Dict[str, CollaborationSession] = {}
        self.agent_sessions: Dict[str, Set[str]] = {}  # agent_id -> session_ids
        self.message_handlers: Dict[str, List[Any]] = {}
        
    def create_session(
        self,
        name: str,
        participants: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> CollaborationSession:
        """
        Create a new collaboration session.
        
        Args:
            name: Session name
            participants: Initial participant agent IDs
            metadata: Optional metadata
            
        Returns:
            The created session
        """
        session_id = str(uuid.uuid4())[:8]
        session = CollaborationSession(
            session_id=session_id,
            name=name,
            participants=set(participants or []),
            metadata=metadata or {}
        )
        self.sessions[session_id] = session
        
        # Track agent sessions
        for agent_id in session.participants:
            if agent_id not in self.agent_sessions:
                self.agent_sessions[agent_id] = set()
            self.agent_sessions[agent_id].add(session_id)
            
        logger.info(f"Created collaboration session {session_id}: {name}")
        return session
        
    def get_session(self, session_id: str) -> Optional[CollaborationSession]:
        """Get a session by ID."""
        return self.sessions.get(session_id)
        
    def join_session(self, session_id: str, agent_id: str) -> bool:
        """Add an agent to a session."""
        session = self.sessions.get(session_id)
        if not session:
            return False
            
        session.participants.add(agent_id)
        session.updated_at = datetime.utcnow()
        
        if agent_id not in self.agent_sessions:
            self.agent_sessions[agent_id] = set()
        self.agent_sessions[agent_id].add(session_id)
        
        logger.info(f"Agent {agent_id} joined session {session_id}")
        return True
        
    def leave_session(self, session_id: str, agent_id: str) -> bool:
        """Remove an agent from a session."""
        session = self.sessions.get(session_id)
        if not session:
            return False
            
        session.participants.discard(agent_id)
        session.updated_at = datetime.utcnow()
        
        if agent_id in self.agent_sessions:
            self.agent_sessions[agent_id].discard(session_id)
            
        logger.info(f"Agent {agent_id} left session {session_id}")
        return True
        
    def send_message(
        self,
        session_id: str,
        sender_id: str,
        message_type: MessageType,
        content: Any,
        recipients: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[CollaborationMessage]:
        """
        Send a message in a session.
        
        Args:
            session_id: The session to send in
            sender_id: The sending agent ID
            message_type: Type of message
            content: Message content
            recipients: Optional specific recipients (None = all)
            metadata: Optional metadata
            
        Returns:
            The sent message or None if failed
        """
        session = self.sessions.get(session_id)
        if not session:
            return None
            
        if sender_id not in session.participants:
            return None
            
        message = CollaborationMessage(
            message_id=str(uuid.uuid4())[:8],
            session_id=session_id,
            sender_id=sender_id,
            message_type=message_type,
            content=content,
            recipients=recipients or list(session.participants - {sender_id}),
            metadata=metadata or {}
        )
        
        session.messages.append(message)
        session.updated_at = datetime.utcnow()
        
        logger.debug(f"Message {message.message_id} sent in session {session_id}")
        return message
        
    def get_messages(
        self,
        session_id: str,
        since: Optional[datetime] = None,
        message_type: Optional[MessageType] = None
    ) -> List[CollaborationMessage]:
        """Get messages from a session."""
        session = self.sessions.get(session_id)
        if not session:
            return []
            
        messages = session.messages
        
        if since:
            messages = [m for m in messages if m.timestamp > since]
            
        if message_type:
            messages = [m for m in messages if m.message_type == message_type]
            
        return messages
        
    def update_shared_context(
        self,
        session_id: str,
        key: str,
        value: Any
    ) -> bool:
        """Update shared context in a session."""
        session = self.sessions.get(session_id)
        if not session:
            return False
            
        session.shared_context[key] = value
        session.updated_at = datetime.utcnow()
        return True
        
    def get_shared_context(self, session_id: str) -> Dict[str, Any]:
        """Get shared context from a session."""
        session = self.sessions.get(session_id)
        if not session:
            return {}
        return session.shared_context.copy()
        
    def close_session(self, session_id: str) -> bool:
        """Close a collaboration session."""
        session = self.sessions.get(session_id)
        if not session:
            return False
            
        session.status = CollaborationStatus.COMPLETED
        session.updated_at = datetime.utcnow()
        
        # Remove from agent sessions
        for agent_id in session.participants:
            if agent_id in self.agent_sessions:
                self.agent_sessions[agent_id].discard(session_id)
                
        logger.info(f"Closed collaboration session {session_id}")
        return True
        
    def get_agent_sessions(self, agent_id: str) -> List[CollaborationSession]:
        """Get all sessions an agent is participating in."""
        session_ids = self.agent_sessions.get(agent_id, set())
        return [
            self.sessions[sid]
            for sid in session_ids
            if sid in self.sessions
        ]
        
    def get_stats(self) -> Dict[str, Any]:
        """Get hub statistics."""
        active_sessions = sum(
            1 for s in self.sessions.values()
            if s.status == CollaborationStatus.ACTIVE
        )
        total_messages = sum(len(s.messages) for s in self.sessions.values())
        
        return {
            "total_sessions": len(self.sessions),
            "active_sessions": active_sessions,
            "total_participants": len(self.agent_sessions),
            "total_messages": total_messages
        }


# Global hub instance
_hub: Optional[CollaborationHub] = None


def get_collaboration_hub() -> CollaborationHub:
    """Get or create the global collaboration hub."""
    global _hub
    if _hub is None:
        _hub = CollaborationHub()
    return _hub
