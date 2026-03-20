"""
Production WebSocket manager with connection pooling and message queuing.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable, Set
from enum import Enum
import uuid


class ConnectionState(Enum):
    CONNECTING = "connecting"
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass
class WebSocketConnection:
    """Represents a WebSocket connection with metadata."""
    connection_id: str
    user_id: Optional[str]
    websocket: Any  # WebSocket instance
    state: ConnectionState = ConnectionState.CONNECTING
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    subscriptions: Set[str] = field(default_factory=set)
    
    def update_activity(self) -> None:
        self.last_activity = time.time()


class MessageQueue:
    """
    Async message queue with backpressure support.
    """
    
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._dropped_count = 0
    
    async def put(self, message: Any, timeout: float = 1.0) -> bool:
        """Put message in queue with timeout."""
        try:
            await asyncio.wait_for(self._queue.put(message), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            self._dropped_count += 1
            return False
    
    def put_nowait(self, message: Any) -> bool:
        """Put message without waiting."""
        try:
            self._queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            self._dropped_count += 1
            return False
    
    async def get(self, timeout: Optional[float] = None) -> Optional[Any]:
        """Get message from queue."""
        try:
            if timeout:
                return await asyncio.wait_for(self._queue.get(), timeout=timeout)
            return await self._queue.get()
        except asyncio.TimeoutError:
            return None
    
    def qsize(self) -> int:
        return self._queue.qsize()
    
    def is_full(self) -> bool:
        return self._queue.full()
    
    @property
    def dropped_count(self) -> int:
        return self._dropped_count


class WebSocketManager:
    """
    Production WebSocket connection manager with:
    - Connection pooling
    - Message broadcasting
    - Subscription management
    - Heartbeat/keepalive
    - Graceful shutdown
    """
    
    def __init__(
        self,
        heartbeat_interval: float = 30.0,
        connection_timeout: float = 300.0,
        max_connections: int = 10000,
    ):
        self.heartbeat_interval = heartbeat_interval
        self.connection_timeout = connection_timeout
        self.max_connections = max_connections
        
        self._connections: Dict[str, WebSocketConnection] = {}
        self._user_connections: Dict[str, Set[str]] = {}
        self._channel_subscribers: Dict[str, Set[str]] = {}
        self._message_queues: Dict[str, MessageQueue] = {}
        
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Metrics
        self._total_connections = 0
        self._total_messages_sent = 0
        self._total_messages_received = 0
    
    async def start(self) -> None:
        """Start background tasks."""
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def stop(self) -> None:
        """Stop and cleanup."""
        self._running = False
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()
        
        # Close all connections
        for conn in list(self._connections.values()):
            await self.disconnect(conn.connection_id)
    
    async def connect(
        self,
        websocket: Any,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> WebSocketConnection:
        """Register a new WebSocket connection."""
        if len(self._connections) >= self.max_connections:
            raise ConnectionError("Maximum connections reached")
        
        connection_id = str(uuid.uuid4())
        
        conn = WebSocketConnection(
            connection_id=connection_id,
            user_id=user_id,
            websocket=websocket,
            state=ConnectionState.OPEN,
            metadata=metadata or {},
        )
        
        self._connections[connection_id] = conn
        self._message_queues[connection_id] = MessageQueue()
        
        if user_id:
            if user_id not in self._user_connections:
                self._user_connections[user_id] = set()
            self._user_connections[user_id].add(connection_id)
        
        self._total_connections += 1
        
        return conn
    
    async def disconnect(self, connection_id: str) -> None:
        """Disconnect and cleanup a connection."""
        conn = self._connections.get(connection_id)
        if not conn:
            return
        
        conn.state = ConnectionState.CLOSING
        
        # Remove from user connections
        if conn.user_id and conn.user_id in self._user_connections:
            self._user_connections[conn.user_id].discard(connection_id)
            if not self._user_connections[conn.user_id]:
                del self._user_connections[conn.user_id]
        
        # Remove from channel subscriptions
        for channel in list(conn.subscriptions):
            await self.unsubscribe(connection_id, channel)
        
        # Close websocket
        try:
            await conn.websocket.close()
        except Exception:
            pass
        
        conn.state = ConnectionState.CLOSED
        
        # Cleanup
        del self._connections[connection_id]
        if connection_id in self._message_queues:
            del self._message_queues[connection_id]
    
    async def subscribe(self, connection_id: str, channel: str) -> bool:
        """Subscribe connection to a channel."""
        conn = self._connections.get(connection_id)
        if not conn or conn.state != ConnectionState.OPEN:
            return False
        
        conn.subscriptions.add(channel)
        
        if channel not in self._channel_subscribers:
            self._channel_subscribers[channel] = set()
        self._channel_subscribers[channel].add(connection_id)
        
        return True
    
    async def unsubscribe(self, connection_id: str, channel: str) -> bool:
        """Unsubscribe connection from a channel."""
        conn = self._connections.get(connection_id)
        if not conn:
            return False
        
        conn.subscriptions.discard(channel)
        
        if channel in self._channel_subscribers:
            self._channel_subscribers[channel].discard(connection_id)
            if not self._channel_subscribers[channel]:
                del self._channel_subscribers[channel]
        
        return True
    
    async def send(
        self,
        connection_id: str,
        message: Dict[str, Any],
        timeout: float = 5.0,
    ) -> bool:
        """Send message to a specific connection."""
        conn = self._connections.get(connection_id)
        if not conn or conn.state != ConnectionState.OPEN:
            return False
        
        try:
            data = json.dumps(message)
            await asyncio.wait_for(conn.websocket.send_text(data), timeout=timeout)
            conn.update_activity()
            self._total_messages_sent += 1
            return True
        except Exception:
            return False
    
    async def send_to_user(
        self,
        user_id: str,
        message: Dict[str, Any],
    ) -> int:
        """Send message to all connections for a user."""
        connection_ids = self._user_connections.get(user_id, set())
        sent = 0
        
        for conn_id in list(connection_ids):
            if await self.send(conn_id, message):
                sent += 1
        
        return sent
    
    async def broadcast(
        self,
        channel: str,
        message: Dict[str, Any],
        exclude: Optional[Set[str]] = None,
    ) -> int:
        """Broadcast message to all subscribers of a channel."""
        subscriber_ids = self._channel_subscribers.get(channel, set())
        exclude = exclude or set()
        sent = 0
        
        for conn_id in list(subscriber_ids):
            if conn_id not in exclude:
                if await self.send(conn_id, message):
                    sent += 1
        
        return sent
    
    async def broadcast_all(
        self,
        message: Dict[str, Any],
        exclude: Optional[Set[str]] = None,
    ) -> int:
        """Broadcast message to all connections."""
        exclude = exclude or set()
        sent = 0
        
        for conn_id in list(self._connections.keys()):
            if conn_id not in exclude:
                if await self.send(conn_id, message):
                    sent += 1
        
        return sent
    
    def get_connection(self, connection_id: str) -> Optional[WebSocketConnection]:
        """Get connection by ID."""
        return self._connections.get(connection_id)
    
    def get_user_connections(self, user_id: str) -> List[WebSocketConnection]:
        """Get all connections for a user."""
        conn_ids = self._user_connections.get(user_id, set())
        return [self._connections[cid] for cid in conn_ids if cid in self._connections]
    
    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats."""
        while self._running:
            try:
                await asyncio.sleep(self.heartbeat_interval)
                
                ping_message = {"type": "ping", "timestamp": time.time()}
                
                for conn_id in list(self._connections.keys()):
                    await self.send(conn_id, ping_message)
                    
            except asyncio.CancelledError:
                break
            except Exception:
                pass
    
    async def _cleanup_loop(self) -> None:
        """Cleanup stale connections."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                now = time.time()
                stale_connections = []
                
                for conn_id, conn in self._connections.items():
                    if now - conn.last_activity > self.connection_timeout:
                        stale_connections.append(conn_id)
                
                for conn_id in stale_connections:
                    await self.disconnect(conn_id)
                    
            except asyncio.CancelledError:
                break
            except Exception:
                pass
    
    def get_stats(self) -> Dict[str, Any]:
        """Get manager statistics."""
        return {
            "active_connections": len(self._connections),
            "total_connections": self._total_connections,
            "total_messages_sent": self._total_messages_sent,
            "total_messages_received": self._total_messages_received,
            "channels": len(self._channel_subscribers),
            "users_connected": len(self._user_connections),
        }
