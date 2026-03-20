"""
Backpressure control for WebSocket and streaming connections.
Prevents overwhelming slow consumers.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, Awaitable
from enum import Enum


class FlowState(Enum):
    FLOWING = "flowing"
    PAUSED = "paused"
    BLOCKED = "blocked"


@dataclass
class FlowMetrics:
    """Metrics for flow control."""
    messages_sent: int = 0
    messages_dropped: int = 0
    bytes_sent: int = 0
    bytes_dropped: int = 0
    pause_count: int = 0
    resume_count: int = 0
    current_queue_size: int = 0
    max_queue_size: int = 0


class FlowControl:
    """
    Flow control for individual connections.
    """
    
    def __init__(
        self,
        high_water_mark: int = 100,
        low_water_mark: int = 25,
    ):
        self.high_water_mark = high_water_mark
        self.low_water_mark = low_water_mark
        self._state = FlowState.FLOWING
        self._pending_count = 0
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Start flowing
    
    @property
    def state(self) -> FlowState:
        return self._state
    
    @property
    def is_flowing(self) -> bool:
        return self._state == FlowState.FLOWING
    
    def increment(self, count: int = 1) -> FlowState:
        """Increment pending count and check thresholds."""
        self._pending_count += count
        
        if self._pending_count >= self.high_water_mark:
            if self._state == FlowState.FLOWING:
                self._state = FlowState.PAUSED
                self._pause_event.clear()
        
        return self._state
    
    def decrement(self, count: int = 1) -> FlowState:
        """Decrement pending count and check thresholds."""
        self._pending_count = max(0, self._pending_count - count)
        
        if self._pending_count <= self.low_water_mark:
            if self._state == FlowState.PAUSED:
                self._state = FlowState.FLOWING
                self._pause_event.set()
        
        return self._state
    
    async def wait_for_drain(self, timeout: Optional[float] = None) -> bool:
        """Wait until flow resumes."""
        try:
            if timeout:
                await asyncio.wait_for(self._pause_event.wait(), timeout=timeout)
            else:
                await self._pause_event.wait()
            return True
        except asyncio.TimeoutError:
            return False
    
    def force_resume(self) -> None:
        """Force resume flow."""
        self._state = FlowState.FLOWING
        self._pause_event.set()
    
    def force_block(self) -> None:
        """Force block flow."""
        self._state = FlowState.BLOCKED
        self._pause_event.clear()


class BackpressureController:
    """
    Production backpressure controller with:
    - Per-connection flow control
    - Adaptive rate limiting
    - Drop policies
    - Metrics collection
    """
    
    def __init__(
        self,
        max_queue_size: int = 1000,
        high_water_mark: int = 100,
        low_water_mark: int = 25,
        drop_policy: str = "tail",  # "tail", "head", "random"
    ):
        self.max_queue_size = max_queue_size
        self.high_water_mark = high_water_mark
        self.low_water_mark = low_water_mark
        self.drop_policy = drop_policy
        
        self._connections: Dict[str, FlowControl] = {}
        self._queues: Dict[str, asyncio.Queue] = {}
        self._metrics: Dict[str, FlowMetrics] = {}
        
        self._global_flow = FlowControl(high_water_mark * 10, low_water_mark * 10)
    
    def register_connection(self, connection_id: str) -> FlowControl:
        """Register a new connection for backpressure control."""
        flow = FlowControl(self.high_water_mark, self.low_water_mark)
        self._connections[connection_id] = flow
        self._queues[connection_id] = asyncio.Queue(maxsize=self.max_queue_size)
        self._metrics[connection_id] = FlowMetrics()
        return flow
    
    def unregister_connection(self, connection_id: str) -> None:
        """Unregister a connection."""
        self._connections.pop(connection_id, None)
        self._queues.pop(connection_id, None)
        self._metrics.pop(connection_id, None)
    
    async def send(
        self,
        connection_id: str,
        message: Any,
        sender: Callable[[Any], Awaitable[bool]],
        timeout: float = 5.0,
    ) -> bool:
        """
        Send message with backpressure control.
        
        Args:
            connection_id: Target connection
            message: Message to send
            sender: Async function to actually send the message
            timeout: Max time to wait for flow to resume
        
        Returns:
            True if sent, False if dropped
        """
        flow = self._connections.get(connection_id)
        metrics = self._metrics.get(connection_id)
        
        if not flow or not metrics:
            return False
        
        # Check flow state
        if not flow.is_flowing:
            # Wait for drain
            if not await flow.wait_for_drain(timeout):
                # Timeout - drop message
                metrics.messages_dropped += 1
                return False
        
        # Try to send
        try:
            flow.increment()
            success = await sender(message)
            
            if success:
                metrics.messages_sent += 1
            else:
                metrics.messages_dropped += 1
            
            flow.decrement()
            return success
            
        except Exception:
            flow.decrement()
            metrics.messages_dropped += 1
            return False
    
    async def queue_message(
        self,
        connection_id: str,
        message: Any,
    ) -> bool:
        """Queue a message for later delivery."""
        queue = self._queues.get(connection_id)
        metrics = self._metrics.get(connection_id)
        
        if not queue or not metrics:
            return False
        
        if queue.full():
            # Apply drop policy
            if self.drop_policy == "tail":
                # Drop new message
                metrics.messages_dropped += 1
                return False
            elif self.drop_policy == "head":
                # Drop oldest message
                try:
                    queue.get_nowait()
                    metrics.messages_dropped += 1
                except asyncio.QueueEmpty:
                    pass
        
        try:
            queue.put_nowait(message)
            metrics.current_queue_size = queue.qsize()
            metrics.max_queue_size = max(metrics.max_queue_size, queue.qsize())
            return True
        except asyncio.QueueFull:
            metrics.messages_dropped += 1
            return False
    
    async def drain_queue(
        self,
        connection_id: str,
        sender: Callable[[Any], Awaitable[bool]],
        batch_size: int = 10,
    ) -> int:
        """Drain queued messages for a connection."""
        queue = self._queues.get(connection_id)
        flow = self._connections.get(connection_id)
        metrics = self._metrics.get(connection_id)
        
        if not queue or not flow or not metrics:
            return 0
        
        sent = 0
        
        for _ in range(batch_size):
            if queue.empty():
                break
            
            if not flow.is_flowing:
                break
            
            try:
                message = queue.get_nowait()
                
                flow.increment()
                success = await sender(message)
                flow.decrement()
                
                if success:
                    metrics.messages_sent += 1
                    sent += 1
                else:
                    # Re-queue on failure
                    await self.queue_message(connection_id, message)
                    break
                    
            except asyncio.QueueEmpty:
                break
            except Exception:
                flow.decrement()
                break
        
        metrics.current_queue_size = queue.qsize()
        return sent
    
    def get_connection_metrics(self, connection_id: str) -> Optional[FlowMetrics]:
        """Get metrics for a connection."""
        return self._metrics.get(connection_id)
    
    def get_all_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Get metrics for all connections."""
        result = {}
        
        for conn_id, metrics in self._metrics.items():
            flow = self._connections.get(conn_id)
            result[conn_id] = {
                "state": flow.state.value if flow else "unknown",
                "messages_sent": metrics.messages_sent,
                "messages_dropped": metrics.messages_dropped,
                "current_queue_size": metrics.current_queue_size,
                "max_queue_size": metrics.max_queue_size,
                "pause_count": metrics.pause_count,
            }
        
        return result
    
    def get_global_stats(self) -> Dict[str, Any]:
        """Get global backpressure stats."""
        total_sent = sum(m.messages_sent for m in self._metrics.values())
        total_dropped = sum(m.messages_dropped for m in self._metrics.values())
        total_queued = sum(m.current_queue_size for m in self._metrics.values())
        
        return {
            "connections": len(self._connections),
            "global_state": self._global_flow.state.value,
            "total_messages_sent": total_sent,
            "total_messages_dropped": total_dropped,
            "total_queued": total_queued,
            "drop_rate": total_dropped / max(1, total_sent + total_dropped),
        }
