"""
Message ordering guarantees for WebSocket and streaming.
FIFO ordering with sequence tracking and gap detection.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable, Awaitable
from collections import OrderedDict
import heapq


@dataclass
class SequencedMessage:
    """Message with sequence number for ordering."""
    sequence: int
    payload: Any
    timestamp: float = field(default_factory=time.time)
    channel_id: str = ""
    
    def __lt__(self, other: "SequencedMessage") -> bool:
        return self.sequence < other.sequence


class SequenceTracker:
    """
    Tracks message sequences for gap detection and ordering.
    """
    
    def __init__(self, channel_id: str):
        self.channel_id = channel_id
        self._next_expected: int = 0
        self._highest_seen: int = -1
        self._gaps: List[tuple] = []  # (start, end) ranges
        self._received_count: int = 0
        self._duplicate_count: int = 0
        self._out_of_order_count: int = 0
    
    def record(self, sequence: int) -> tuple:
        """
        Record a received sequence number.
        Returns (is_expected, is_duplicate, gap_detected)
        """
        self._received_count += 1
        
        if sequence < self._next_expected:
            # Duplicate or old message
            self._duplicate_count += 1
            return (False, True, False)
        
        if sequence == self._next_expected:
            # Expected message
            self._next_expected = sequence + 1
            self._highest_seen = max(self._highest_seen, sequence)
            
            # Check if we can advance past any gaps
            self._consolidate_gaps()
            
            return (True, False, False)
        
        # Out of order - gap detected
        self._out_of_order_count += 1
        
        if sequence > self._next_expected:
            # Record gap
            gap_start = self._next_expected
            gap_end = sequence - 1
            self._gaps.append((gap_start, gap_end))
            self._next_expected = sequence + 1
        
        self._highest_seen = max(self._highest_seen, sequence)
        
        return (False, False, True)
    
    def _consolidate_gaps(self) -> None:
        """Remove filled gaps."""
        self._gaps = [(s, e) for s, e in self._gaps if e >= self._next_expected]
    
    def fill_gap(self, sequence: int) -> bool:
        """Mark a gap sequence as filled."""
        new_gaps = []
        filled = False
        
        for start, end in self._gaps:
            if start <= sequence <= end:
                filled = True
                if start == end:
                    continue  # Gap fully filled
                elif sequence == start:
                    new_gaps.append((start + 1, end))
                elif sequence == end:
                    new_gaps.append((start, end - 1))
                else:
                    new_gaps.append((start, sequence - 1))
                    new_gaps.append((sequence + 1, end))
            else:
                new_gaps.append((start, end))
        
        self._gaps = new_gaps
        return filled
    
    def has_gaps(self) -> bool:
        return len(self._gaps) > 0
    
    def get_gaps(self) -> List[tuple]:
        return list(self._gaps)
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "next_expected": self._next_expected,
            "highest_seen": self._highest_seen,
            "gaps": self._gaps,
            "received_count": self._received_count,
            "duplicate_count": self._duplicate_count,
            "out_of_order_count": self._out_of_order_count,
        }


class OrderedMessageChannel:
    """
    Ordered message channel with:
    - FIFO delivery guarantees
    - Out-of-order buffering
    - Gap detection and recovery
    - Dead letter handling
    """
    
    def __init__(
        self,
        channel_id: str,
        buffer_size: int = 1000,
        max_wait_ms: float = 5000.0,
        on_message: Optional[Callable[[Any], Awaitable[None]]] = None,
    ):
        self.channel_id = channel_id
        self.buffer_size = buffer_size
        self.max_wait_ms = max_wait_ms
        self._on_message = on_message
        
        self._sequence_tracker = SequenceTracker(channel_id)
        self._buffer: Dict[int, SequencedMessage] = {}
        self._next_delivery: int = 0
        self._delivered_count: int = 0
        self._dead_letter_count: int = 0
        
        self._delivery_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._delivery_task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start the delivery processor."""
        self._running = True
        self._delivery_task = asyncio.create_task(self._delivery_loop())
    
    async def stop(self) -> None:
        """Stop the delivery processor."""
        self._running = False
        if self._delivery_task:
            self._delivery_task.cancel()
            try:
                await self._delivery_task
            except asyncio.CancelledError:
                pass
    
    async def receive(self, message: SequencedMessage) -> None:
        """
        Receive a message and buffer if out of order.
        """
        sequence = message.sequence
        
        is_expected, is_duplicate, gap_detected = self._sequence_tracker.record(sequence)
        
        if is_duplicate:
            return
        
        if sequence == self._next_delivery:
            # Deliver immediately
            await self._deliver(message)
            self._next_delivery += 1
            
            # Check buffer for next messages
            await self._flush_buffer()
        else:
            # Buffer for later delivery
            if len(self._buffer) < self.buffer_size:
                self._buffer[sequence] = message
            else:
                # Buffer full - dead letter
                self._dead_letter_count += 1
    
    async def _deliver(self, message: SequencedMessage) -> None:
        """Deliver a message to the handler."""
        if self._on_message:
            try:
                await self._on_message(message.payload)
            except Exception:
                pass
        
        await self._delivery_queue.put(message)
        self._delivered_count += 1
    
    async def _flush_buffer(self) -> None:
        """Deliver buffered messages in order."""
        while self._next_delivery in self._buffer:
            message = self._buffer.pop(self._next_delivery)
            await self._deliver(message)
            self._next_delivery += 1
    
    async def _delivery_loop(self) -> None:
        """Background loop for timeout-based delivery."""
        while self._running:
            try:
                await asyncio.sleep(self.max_wait_ms / 1000.0)
                
                # Check for stale buffered messages
                now = time.time()
                stale_sequences = []
                
                for seq, msg in self._buffer.items():
                    age_ms = (now - msg.timestamp) * 1000
                    if age_ms > self.max_wait_ms:
                        stale_sequences.append(seq)
                
                # Deliver stale messages (gap timeout)
                for seq in sorted(stale_sequences):
                    if seq in self._buffer:
                        message = self._buffer.pop(seq)
                        await self._deliver(message)
                        if seq >= self._next_delivery:
                            self._next_delivery = seq + 1
                
            except asyncio.CancelledError:
                break
            except Exception:
                pass
    
    async def get_next(self, timeout: Optional[float] = None) -> Optional[SequencedMessage]:
        """Get next delivered message."""
        try:
            if timeout:
                return await asyncio.wait_for(self._delivery_queue.get(), timeout=timeout)
            return await self._delivery_queue.get()
        except asyncio.TimeoutError:
            return None
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "next_delivery": self._next_delivery,
            "buffer_size": len(self._buffer),
            "delivered_count": self._delivered_count,
            "dead_letter_count": self._dead_letter_count,
            "sequence_stats": self._sequence_tracker.get_stats(),
        }


class ConversationOrderManager:
    """
    Manages message ordering for multiple conversations.
    Ensures per-conversation FIFO semantics.
    """
    
    def __init__(self):
        self._channels: Dict[str, OrderedMessageChannel] = {}
        self._sequence_counters: Dict[str, int] = {}
    
    def get_next_sequence(self, conversation_id: str) -> int:
        """Get next sequence number for a conversation."""
        if conversation_id not in self._sequence_counters:
            self._sequence_counters[conversation_id] = 0
        
        seq = self._sequence_counters[conversation_id]
        self._sequence_counters[conversation_id] += 1
        return seq
    
    async def get_or_create_channel(
        self,
        conversation_id: str,
        on_message: Optional[Callable[[Any], Awaitable[None]]] = None,
    ) -> OrderedMessageChannel:
        """Get or create an ordered channel for a conversation."""
        if conversation_id not in self._channels:
            channel = OrderedMessageChannel(
                channel_id=conversation_id,
                on_message=on_message,
            )
            await channel.start()
            self._channels[conversation_id] = channel
        
        return self._channels[conversation_id]
    
    async def close_channel(self, conversation_id: str) -> None:
        """Close a conversation channel."""
        if conversation_id in self._channels:
            await self._channels[conversation_id].stop()
            del self._channels[conversation_id]
    
    async def close_all(self) -> None:
        """Close all channels."""
        for channel in self._channels.values():
            await channel.stop()
        self._channels.clear()
    
    def get_all_stats(self) -> Dict[str, Any]:
        return {
            "active_channels": len(self._channels),
            "channels": {cid: ch.get_stats() for cid, ch in self._channels.items()},
        }
