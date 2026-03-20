"""
Idempotency key management for billing and critical operations.
Prevents double-spend and duplicate processing.
"""

import hashlib
import json
import time
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import redis.asyncio as redis


class IdempotencyStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class IdempotencyRecord:
    """Idempotency record for a request."""
    key: str
    status: IdempotencyStatus
    created_at: float
    completed_at: Optional[float]
    request_hash: str
    response: Optional[Dict]
    error: Optional[str]


class IdempotencyManager:
    """
    Production idempotency management with:
    - Redis-backed distributed locking
    - Request fingerprinting
    - Response caching
    - Automatic cleanup
    """
    
    IDEMPOTENCY_PREFIX = "idempotency:"
    LOCK_PREFIX = "idempotency_lock:"
    DEFAULT_TTL_SECONDS = 86400  # 24 hours
    LOCK_TTL_SECONDS = 30
    
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis_url = redis_url
        self._redis: Optional[redis.Redis] = None
    
    async def connect(self) -> None:
        self._redis = redis.from_url(self.redis_url, decode_responses=True)
    
    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
    
    def _hash_request(self, request_data: Dict) -> str:
        """Create deterministic hash of request data."""
        normalized = json.dumps(request_data, sort_keys=True)
        return hashlib.sha256(normalized.encode()).hexdigest()[:32]
    
    async def check_and_start(
        self,
        idempotency_key: str,
        request_data: Dict,
        user_id: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> Tuple[bool, Optional[IdempotencyRecord]]:
        """
        Check idempotency key and start processing if new.
        
        Returns:
            (should_process, existing_record)
            - (True, None) = new request, proceed with processing
            - (False, record) = duplicate, return cached response
        """
        if not self._redis:
            raise RuntimeError("IdempotencyManager not connected")
        
        key = f"{self.IDEMPOTENCY_PREFIX}{user_id}:{idempotency_key}"
        lock_key = f"{self.LOCK_PREFIX}{user_id}:{idempotency_key}"
        request_hash = self._hash_request(request_data)
        now = time.time()
        
        # Try to acquire lock
        lock_acquired = await self._redis.set(
            lock_key,
            "1",
            nx=True,
            ex=self.LOCK_TTL_SECONDS,
        )
        
        if not lock_acquired:
            # Another request is processing - wait and check
            await self._wait_for_completion(key)
        
        # Check existing record
        existing = await self._redis.hgetall(key)
        
        if existing:
            # Verify request hash matches
            if existing.get("request_hash") != request_hash:
                # Same idempotency key, different request = error
                await self._redis.delete(lock_key)
                raise ValueError(
                    "Idempotency key reused with different request parameters"
                )
            
            status = IdempotencyStatus(existing.get("status", "pending"))
            
            if status == IdempotencyStatus.COMPLETED:
                # Return cached response
                await self._redis.delete(lock_key)
                return False, IdempotencyRecord(
                    key=idempotency_key,
                    status=status,
                    created_at=float(existing["created_at"]),
                    completed_at=float(existing.get("completed_at", 0)) or None,
                    request_hash=existing["request_hash"],
                    response=json.loads(existing.get("response", "null")),
                    error=existing.get("error"),
                )
            
            if status == IdempotencyStatus.FAILED:
                # Allow retry of failed requests
                pass
            
            if status == IdempotencyStatus.PROCESSING:
                # Still processing - wait
                await self._wait_for_completion(key)
                return await self.check_and_start(
                    idempotency_key, request_data, user_id, ttl_seconds
                )
        
        # Create new record
        await self._redis.hset(key, mapping={
            "status": IdempotencyStatus.PROCESSING.value,
            "created_at": str(now),
            "request_hash": request_hash,
            "user_id": user_id,
        })
        await self._redis.expire(key, ttl_seconds)
        
        return True, None
    
    async def complete(
        self,
        idempotency_key: str,
        user_id: str,
        response: Dict,
    ) -> None:
        """Mark request as completed with response."""
        if not self._redis:
            return
        
        key = f"{self.IDEMPOTENCY_PREFIX}{user_id}:{idempotency_key}"
        lock_key = f"{self.LOCK_PREFIX}{user_id}:{idempotency_key}"
        
        await self._redis.hset(key, mapping={
            "status": IdempotencyStatus.COMPLETED.value,
            "completed_at": str(time.time()),
            "response": json.dumps(response),
        })
        
        await self._redis.delete(lock_key)
    
    async def fail(
        self,
        idempotency_key: str,
        user_id: str,
        error: str,
    ) -> None:
        """Mark request as failed."""
        if not self._redis:
            return
        
        key = f"{self.IDEMPOTENCY_PREFIX}{user_id}:{idempotency_key}"
        lock_key = f"{self.LOCK_PREFIX}{user_id}:{idempotency_key}"
        
        await self._redis.hset(key, mapping={
            "status": IdempotencyStatus.FAILED.value,
            "completed_at": str(time.time()),
            "error": error,
        })
        
        await self._redis.delete(lock_key)
    
    async def _wait_for_completion(
        self,
        key: str,
        max_wait_seconds: int = 30,
        poll_interval: float = 0.5,
    ) -> None:
        """Wait for a request to complete."""
        import asyncio
        
        start = time.time()
        while time.time() - start < max_wait_seconds:
            data = await self._redis.hgetall(key)
            if not data:
                return
            
            status = IdempotencyStatus(data.get("status", "pending"))
            if status in (IdempotencyStatus.COMPLETED, IdempotencyStatus.FAILED):
                return
            
            await asyncio.sleep(poll_interval)
    
    async def get_record(
        self,
        idempotency_key: str,
        user_id: str,
    ) -> Optional[IdempotencyRecord]:
        """Get idempotency record."""
        if not self._redis:
            return None
        
        key = f"{self.IDEMPOTENCY_PREFIX}{user_id}:{idempotency_key}"
        data = await self._redis.hgetall(key)
        
        if not data:
            return None
        
        return IdempotencyRecord(
            key=idempotency_key,
            status=IdempotencyStatus(data.get("status", "pending")),
            created_at=float(data["created_at"]),
            completed_at=float(data.get("completed_at", 0)) or None,
            request_hash=data["request_hash"],
            response=json.loads(data.get("response", "null")),
            error=data.get("error"),
        )


class AtomicCreditManager:
    """
    Atomic credit operations with double-spend protection.
    Uses Redis transactions for consistency.
    """
    
    CREDIT_PREFIX = "credits:"
    TRANSACTION_PREFIX = "credit_tx:"
    
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis_url = redis_url
        self._redis: Optional[redis.Redis] = None
    
    async def connect(self) -> None:
        self._redis = redis.from_url(self.redis_url, decode_responses=True)
    
    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
    
    async def get_balance(self, user_id: str) -> float:
        """Get current credit balance."""
        if not self._redis:
            return 0.0
        
        balance = await self._redis.get(f"{self.CREDIT_PREFIX}{user_id}")
        return float(balance) if balance else 0.0
    
    async def add_credits(
        self,
        user_id: str,
        amount: float,
        transaction_id: str,
        reason: str = "purchase",
    ) -> Tuple[bool, float]:
        """
        Add credits atomically.
        
        Returns:
            (success, new_balance)
        """
        if not self._redis:
            return False, 0.0
        
        if amount <= 0:
            return False, await self.get_balance(user_id)
        
        # Check for duplicate transaction
        tx_key = f"{self.TRANSACTION_PREFIX}{transaction_id}"
        if await self._redis.exists(tx_key):
            return False, await self.get_balance(user_id)
        
        # Atomic increment
        key = f"{self.CREDIT_PREFIX}{user_id}"
        new_balance = await self._redis.incrbyfloat(key, amount)
        
        # Record transaction
        await self._redis.hset(tx_key, mapping={
            "user_id": user_id,
            "amount": str(amount),
            "type": "credit",
            "reason": reason,
            "timestamp": str(time.time()),
            "balance_after": str(new_balance),
        })
        await self._redis.expire(tx_key, 86400 * 30)  # 30 days
        
        return True, new_balance
    
    async def deduct_credits(
        self,
        user_id: str,
        amount: float,
        transaction_id: str,
        reason: str = "usage",
    ) -> Tuple[bool, float, Optional[str]]:
        """
        Deduct credits atomically with double-spend protection.
        
        Returns:
            (success, new_balance, error_message)
        """
        if not self._redis:
            return False, 0.0, "Not connected"
        
        if amount <= 0:
            return False, await self.get_balance(user_id), "Invalid amount"
        
        # Check for duplicate transaction
        tx_key = f"{self.TRANSACTION_PREFIX}{transaction_id}"
        if await self._redis.exists(tx_key):
            return False, await self.get_balance(user_id), "Duplicate transaction"
        
        key = f"{self.CREDIT_PREFIX}{user_id}"
        
        # Use Lua script for atomic check-and-deduct
        lua_script = """
        local balance = tonumber(redis.call('GET', KEYS[1]) or '0')
        local amount = tonumber(ARGV[1])
        
        if balance < amount then
            return {0, balance}
        end
        
        local new_balance = redis.call('INCRBYFLOAT', KEYS[1], -amount)
        return {1, new_balance}
        """
        
        result = await self._redis.eval(lua_script, 1, key, str(amount))
        success = bool(result[0])
        new_balance = float(result[1])
        
        if not success:
            return False, new_balance, "Insufficient credits"
        
        # Record transaction
        await self._redis.hset(tx_key, mapping={
            "user_id": user_id,
            "amount": str(-amount),
            "type": "debit",
            "reason": reason,
            "timestamp": str(time.time()),
            "balance_after": str(new_balance),
        })
        await self._redis.expire(tx_key, 86400 * 30)
        
        return True, new_balance, None
    
    async def reserve_credits(
        self,
        user_id: str,
        amount: float,
        reservation_id: str,
        ttl_seconds: int = 300,
    ) -> Tuple[bool, Optional[str]]:
        """
        Reserve credits for a pending operation.
        
        Returns:
            (success, error_message)
        """
        if not self._redis:
            return False, "Not connected"
        
        key = f"{self.CREDIT_PREFIX}{user_id}"
        reserve_key = f"credit_reserve:{reservation_id}"
        
        # Check if reservation exists
        if await self._redis.exists(reserve_key):
            return False, "Reservation already exists"
        
        # Atomic reserve
        lua_script = """
        local balance = tonumber(redis.call('GET', KEYS[1]) or '0')
        local amount = tonumber(ARGV[1])
        
        if balance < amount then
            return 0
        end
        
        redis.call('INCRBYFLOAT', KEYS[1], -amount)
        return 1
        """
        
        success = await self._redis.eval(lua_script, 1, key, str(amount))
        
        if not success:
            return False, "Insufficient credits"
        
        # Store reservation
        await self._redis.hset(reserve_key, mapping={
            "user_id": user_id,
            "amount": str(amount),
            "created_at": str(time.time()),
        })
        await self._redis.expire(reserve_key, ttl_seconds)
        
        return True, None
    
    async def confirm_reservation(
        self,
        reservation_id: str,
        transaction_id: str,
    ) -> bool:
        """Confirm a reservation (credits already deducted)."""
        if not self._redis:
            return False
        
        reserve_key = f"credit_reserve:{reservation_id}"
        data = await self._redis.hgetall(reserve_key)
        
        if not data:
            return False
        
        # Record as completed transaction
        tx_key = f"{self.TRANSACTION_PREFIX}{transaction_id}"
        await self._redis.hset(tx_key, mapping={
            "user_id": data["user_id"],
            "amount": str(-float(data["amount"])),
            "type": "debit",
            "reason": "reservation_confirmed",
            "reservation_id": reservation_id,
            "timestamp": str(time.time()),
        })
        await self._redis.expire(tx_key, 86400 * 30)
        
        # Delete reservation
        await self._redis.delete(reserve_key)
        
        return True
    
    async def cancel_reservation(self, reservation_id: str) -> bool:
        """Cancel a reservation and refund credits."""
        if not self._redis:
            return False
        
        reserve_key = f"credit_reserve:{reservation_id}"
        data = await self._redis.hgetall(reserve_key)
        
        if not data:
            return False
        
        # Refund credits
        user_id = data["user_id"]
        amount = float(data["amount"])
        key = f"{self.CREDIT_PREFIX}{user_id}"
        
        await self._redis.incrbyfloat(key, amount)
        await self._redis.delete(reserve_key)
        
        return True
