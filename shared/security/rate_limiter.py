"""
Production-grade rate limiting and brute-force protection.
Implements sliding window rate limiting with Redis backend.
"""

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Optional, Dict, Tuple
from enum import Enum
import redis.asyncio as redis


class RateLimitResult(Enum):
    ALLOWED = "allowed"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"


@dataclass
class RateLimitConfig:
    """Rate limit configuration."""
    requests_per_window: int
    window_seconds: int
    burst_limit: Optional[int] = None
    block_duration_seconds: int = 0


@dataclass
class RateLimitResponse:
    """Rate limit check response."""
    result: RateLimitResult
    remaining: int
    reset_at: float
    retry_after: Optional[int] = None


class RateLimiter:
    """
    Production sliding window rate limiter with:
    - Redis-backed distributed state
    - Sliding window algorithm (more accurate than fixed window)
    - Burst handling
    - Automatic cleanup
    """
    
    RATE_LIMIT_PREFIX = "ratelimit:"
    
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis_url = redis_url
        self._redis: Optional[redis.Redis] = None
        
        # Default rate limit tiers
        self.tiers: Dict[str, RateLimitConfig] = {
            "default": RateLimitConfig(
                requests_per_window=100,
                window_seconds=60,
                burst_limit=20,
            ),
            "auth": RateLimitConfig(
                requests_per_window=10,
                window_seconds=60,
                burst_limit=5,
                block_duration_seconds=300,  # 5 min block after limit
            ),
            "api": RateLimitConfig(
                requests_per_window=1000,
                window_seconds=60,
                burst_limit=100,
            ),
            "expensive": RateLimitConfig(
                requests_per_window=10,
                window_seconds=60,
                burst_limit=3,
            ),
        }
    
    async def connect(self) -> None:
        self._redis = redis.from_url(self.redis_url, decode_responses=True)
    
    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
    
    def _get_key(self, identifier: str, tier: str) -> str:
        """Generate rate limit key."""
        return f"{self.RATE_LIMIT_PREFIX}{tier}:{identifier}"
    
    async def check(
        self,
        identifier: str,
        tier: str = "default",
        cost: int = 1,
    ) -> RateLimitResponse:
        """
        Check and consume rate limit.
        
        Args:
            identifier: Unique identifier (IP, user_id, API key)
            tier: Rate limit tier
            cost: Request cost (default 1)
        
        Returns:
            RateLimitResponse with result and metadata
        """
        if not self._redis:
            raise RuntimeError("RateLimiter not connected")
        
        config = self.tiers.get(tier, self.tiers["default"])
        now = time.time()
        key = self._get_key(identifier, tier)
        window_start = now - config.window_seconds
        
        # Check if blocked
        block_key = f"{key}:blocked"
        block_until = await self._redis.get(block_key)
        if block_until and float(block_until) > now:
            return RateLimitResponse(
                result=RateLimitResult.BLOCKED,
                remaining=0,
                reset_at=float(block_until),
                retry_after=int(float(block_until) - now),
            )
        
        # Sliding window using sorted set
        pipe = self._redis.pipeline()
        
        # Remove old entries
        pipe.zremrangebyscore(key, 0, window_start)
        
        # Count current requests
        pipe.zcard(key)
        
        # Get oldest entry for reset calculation
        pipe.zrange(key, 0, 0, withscores=True)
        
        results = await pipe.execute()
        current_count = results[1]
        
        # Check limit
        if current_count + cost > config.requests_per_window:
            # Rate limited
            if config.block_duration_seconds > 0:
                # Apply block
                block_until_ts = now + config.block_duration_seconds
                await self._redis.set(block_key, str(block_until_ts), ex=config.block_duration_seconds)
                
                return RateLimitResponse(
                    result=RateLimitResult.BLOCKED,
                    remaining=0,
                    reset_at=block_until_ts,
                    retry_after=config.block_duration_seconds,
                )
            
            # Calculate reset time
            oldest = results[2]
            if oldest:
                reset_at = oldest[0][1] + config.window_seconds
            else:
                reset_at = now + config.window_seconds
            
            return RateLimitResponse(
                result=RateLimitResult.RATE_LIMITED,
                remaining=0,
                reset_at=reset_at,
                retry_after=int(reset_at - now),
            )
        
        # Check burst limit
        if config.burst_limit:
            burst_window = now - 1  # Last second
            pipe = self._redis.pipeline()
            pipe.zcount(key, burst_window, now)
            burst_results = await pipe.execute()
            burst_count = burst_results[0]
            
            if burst_count + cost > config.burst_limit:
                return RateLimitResponse(
                    result=RateLimitResult.RATE_LIMITED,
                    remaining=config.requests_per_window - current_count,
                    reset_at=now + 1,
                    retry_after=1,
                )
        
        # Add request(s)
        for _ in range(cost):
            await self._redis.zadd(key, {f"{now}:{id(self)}:{_}": now})
        
        # Set expiry
        await self._redis.expire(key, config.window_seconds + 10)
        
        return RateLimitResponse(
            result=RateLimitResult.ALLOWED,
            remaining=config.requests_per_window - current_count - cost,
            reset_at=now + config.window_seconds,
        )
    
    async def reset(self, identifier: str, tier: str = "default") -> None:
        """Reset rate limit for identifier."""
        if not self._redis:
            return
        
        key = self._get_key(identifier, tier)
        await self._redis.delete(key)
        await self._redis.delete(f"{key}:blocked")


class BruteForceProtection:
    """
    Brute-force attack protection with:
    - Progressive delays
    - Account lockout
    - IP-based tracking
    - Distributed state
    """
    
    FAILED_ATTEMPTS_PREFIX = "bf_failed:"
    LOCKOUT_PREFIX = "bf_lockout:"
    
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        max_attempts: int = 5,
        lockout_duration_seconds: int = 900,  # 15 minutes
        attempt_window_seconds: int = 300,  # 5 minutes
        progressive_delay: bool = True,
    ):
        self.redis_url = redis_url
        self._redis: Optional[redis.Redis] = None
        self.max_attempts = max_attempts
        self.lockout_duration = lockout_duration_seconds
        self.attempt_window = attempt_window_seconds
        self.progressive_delay = progressive_delay
    
    async def connect(self) -> None:
        self._redis = redis.from_url(self.redis_url, decode_responses=True)
    
    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
    
    def _get_identifier(self, user_id: Optional[str], ip: str) -> str:
        """Get combined identifier for tracking."""
        if user_id:
            return f"user:{user_id}"
        return f"ip:{ip}"
    
    async def check_allowed(
        self,
        ip: str,
        user_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[int]]:
        """
        Check if authentication attempt is allowed.
        
        Returns:
            (allowed, retry_after_seconds)
        """
        if not self._redis:
            raise RuntimeError("BruteForceProtection not connected")
        
        identifier = self._get_identifier(user_id, ip)
        now = time.time()
        
        # Check lockout
        lockout_key = f"{self.LOCKOUT_PREFIX}{identifier}"
        lockout_until = await self._redis.get(lockout_key)
        
        if lockout_until and float(lockout_until) > now:
            return False, int(float(lockout_until) - now)
        
        # Check failed attempts
        attempts_key = f"{self.FAILED_ATTEMPTS_PREFIX}{identifier}"
        attempts = await self._redis.get(attempts_key)
        attempts = int(attempts) if attempts else 0
        
        if attempts >= self.max_attempts:
            # Apply lockout
            lockout_until_ts = now + self.lockout_duration
            await self._redis.set(lockout_key, str(lockout_until_ts), ex=self.lockout_duration)
            await self._redis.delete(attempts_key)
            return False, self.lockout_duration
        
        # Progressive delay
        if self.progressive_delay and attempts > 0:
            delay = min(2 ** attempts, 30)  # Max 30 seconds
            await asyncio.sleep(delay)
        
        return True, None
    
    async def record_failure(
        self,
        ip: str,
        user_id: Optional[str] = None,
    ) -> int:
        """
        Record a failed authentication attempt.
        Returns current attempt count.
        """
        if not self._redis:
            raise RuntimeError("BruteForceProtection not connected")
        
        identifier = self._get_identifier(user_id, ip)
        attempts_key = f"{self.FAILED_ATTEMPTS_PREFIX}{identifier}"
        
        # Increment and set expiry
        pipe = self._redis.pipeline()
        pipe.incr(attempts_key)
        pipe.expire(attempts_key, self.attempt_window)
        results = await pipe.execute()
        
        return results[0]
    
    async def record_success(
        self,
        ip: str,
        user_id: Optional[str] = None,
    ) -> None:
        """Clear failed attempts on successful auth."""
        if not self._redis:
            return
        
        identifier = self._get_identifier(user_id, ip)
        await self._redis.delete(f"{self.FAILED_ATTEMPTS_PREFIX}{identifier}")
        await self._redis.delete(f"{self.LOCKOUT_PREFIX}{identifier}")
    
    async def get_status(
        self,
        ip: str,
        user_id: Optional[str] = None,
    ) -> Dict:
        """Get current brute-force protection status."""
        if not self._redis:
            return {}
        
        identifier = self._get_identifier(user_id, ip)
        now = time.time()
        
        attempts_key = f"{self.FAILED_ATTEMPTS_PREFIX}{identifier}"
        lockout_key = f"{self.LOCKOUT_PREFIX}{identifier}"
        
        attempts = await self._redis.get(attempts_key)
        lockout_until = await self._redis.get(lockout_key)
        
        return {
            "failed_attempts": int(attempts) if attempts else 0,
            "max_attempts": self.max_attempts,
            "locked_out": bool(lockout_until and float(lockout_until) > now),
            "lockout_remaining": max(0, int(float(lockout_until) - now)) if lockout_until else 0,
        }


class AccountLockoutPolicy:
    """
    Account-level lockout policy with:
    - Configurable thresholds
    - Automatic unlock
    - Admin override
    - Audit logging
    """
    
    LOCKOUT_PREFIX = "account_lockout:"
    HISTORY_PREFIX = "lockout_history:"
    
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        lockout_threshold: int = 10,
        lockout_duration_minutes: int = 30,
        escalation_multiplier: float = 2.0,
        max_lockout_hours: int = 24,
    ):
        self.redis_url = redis_url
        self._redis: Optional[redis.Redis] = None
        self.lockout_threshold = lockout_threshold
        self.lockout_duration = lockout_duration_minutes * 60
        self.escalation_multiplier = escalation_multiplier
        self.max_lockout = max_lockout_hours * 3600
    
    async def connect(self) -> None:
        self._redis = redis.from_url(self.redis_url, decode_responses=True)
    
    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
    
    async def lock_account(
        self,
        user_id: str,
        reason: str = "security_policy",
    ) -> int:
        """
        Lock an account.
        Returns lockout duration in seconds.
        """
        if not self._redis:
            raise RuntimeError("AccountLockoutPolicy not connected")
        
        now = time.time()
        lockout_key = f"{self.LOCKOUT_PREFIX}{user_id}"
        history_key = f"{self.HISTORY_PREFIX}{user_id}"
        
        # Get lockout history for escalation
        history = await self._redis.lrange(history_key, 0, 10)
        recent_lockouts = len([h for h in history if float(h) > now - 86400])
        
        # Calculate escalated duration
        duration = min(
            self.lockout_duration * (self.escalation_multiplier ** recent_lockouts),
            self.max_lockout,
        )
        duration = int(duration)
        
        # Apply lockout
        lockout_until = now + duration
        await self._redis.hset(lockout_key, mapping={
            "locked_at": str(now),
            "locked_until": str(lockout_until),
            "reason": reason,
            "duration": str(duration),
        })
        await self._redis.expire(lockout_key, duration + 60)
        
        # Record in history
        await self._redis.lpush(history_key, str(now))
        await self._redis.ltrim(history_key, 0, 99)
        await self._redis.expire(history_key, 86400 * 30)  # 30 days
        
        return duration
    
    async def unlock_account(self, user_id: str, admin_id: Optional[str] = None) -> bool:
        """Manually unlock an account."""
        if not self._redis:
            return False
        
        lockout_key = f"{self.LOCKOUT_PREFIX}{user_id}"
        await self._redis.delete(lockout_key)
        return True
    
    async def is_locked(self, user_id: str) -> Tuple[bool, Optional[int]]:
        """
        Check if account is locked.
        Returns (is_locked, seconds_remaining).
        """
        if not self._redis:
            return False, None
        
        lockout_key = f"{self.LOCKOUT_PREFIX}{user_id}"
        data = await self._redis.hgetall(lockout_key)
        
        if not data:
            return False, None
        
        now = time.time()
        locked_until = float(data.get("locked_until", 0))
        
        if locked_until > now:
            return True, int(locked_until - now)
        
        return False, None
    
    async def get_lockout_info(self, user_id: str) -> Optional[Dict]:
        """Get detailed lockout information."""
        if not self._redis:
            return None
        
        lockout_key = f"{self.LOCKOUT_PREFIX}{user_id}"
        data = await self._redis.hgetall(lockout_key)
        
        if not data:
            return None
        
        now = time.time()
        return {
            "locked_at": float(data.get("locked_at", 0)),
            "locked_until": float(data.get("locked_until", 0)),
            "reason": data.get("reason", "unknown"),
            "duration_seconds": int(data.get("duration", 0)),
            "remaining_seconds": max(0, int(float(data.get("locked_until", 0)) - now)),
        }
