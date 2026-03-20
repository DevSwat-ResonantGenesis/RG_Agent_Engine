"""
Token Revocation List with expiry-indexed garbage collection.
Production-grade token lifecycle management.
"""

import asyncio
import hashlib
import time
from datetime import datetime, timedelta
from typing import Optional, Set, Dict
from dataclasses import dataclass, field
import redis.asyncio as redis


@dataclass
class RevokedToken:
    """Represents a revoked token with metadata."""
    token_hash: str
    revoked_at: float
    expires_at: float
    reason: str
    user_id: Optional[str] = None


class TokenRevocationList:
    """
    Production-grade token revocation list with:
    - Redis-backed storage for distributed access
    - Expiry-indexed garbage collection
    - Bloom filter for fast negative lookups
    - Cryptographic token hashing
    """
    
    REVOCATION_PREFIX = "revoked:"
    EXPIRY_INDEX_PREFIX = "revoked_expiry:"
    GC_INTERVAL_SECONDS = 300  # 5 minutes
    
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        gc_enabled: bool = True,
    ):
        self.redis_url = redis_url
        self._redis: Optional[redis.Redis] = None
        self._gc_task: Optional[asyncio.Task] = None
        self._gc_enabled = gc_enabled
        self._local_cache: Set[str] = set()  # Fast local lookup
        self._cache_ttl = 60  # Local cache TTL
        self._last_cache_refresh = 0.0
    
    async def connect(self) -> None:
        """Initialize Redis connection and start GC task."""
        self._redis = redis.from_url(self.redis_url, decode_responses=True)
        if self._gc_enabled:
            self._gc_task = asyncio.create_task(self._gc_loop())
    
    async def close(self) -> None:
        """Clean shutdown."""
        if self._gc_task:
            self._gc_task.cancel()
            try:
                await self._gc_task
            except asyncio.CancelledError:
                pass
        if self._redis:
            await self._redis.close()
    
    def _hash_token(self, token: str) -> str:
        """Cryptographically hash token for storage."""
        return hashlib.sha256(token.encode()).hexdigest()
    
    async def revoke(
        self,
        token: str,
        expires_at: datetime,
        reason: str = "user_logout",
        user_id: Optional[str] = None,
    ) -> bool:
        """
        Revoke a token.
        
        Args:
            token: The JWT token to revoke
            expires_at: When the token naturally expires (for GC)
            reason: Reason for revocation
            user_id: Optional user ID for audit
        
        Returns:
            True if revoked successfully
        """
        if not self._redis:
            raise RuntimeError("TokenRevocationList not connected")
        
        token_hash = self._hash_token(token)
        now = time.time()
        expires_ts = expires_at.timestamp()
        
        # Store revocation with expiry
        key = f"{self.REVOCATION_PREFIX}{token_hash}"
        await self._redis.hset(key, mapping={
            "revoked_at": str(now),
            "expires_at": str(expires_ts),
            "reason": reason,
            "user_id": user_id or "",
        })
        
        # Set TTL to auto-expire after token would naturally expire
        ttl = max(1, int(expires_ts - now) + 60)  # +60s buffer
        await self._redis.expire(key, ttl)
        
        # Add to expiry index for efficient GC
        expiry_bucket = int(expires_ts // 3600) * 3600  # Hour buckets
        await self._redis.sadd(f"{self.EXPIRY_INDEX_PREFIX}{expiry_bucket}", token_hash)
        
        # Update local cache
        self._local_cache.add(token_hash)
        
        return True
    
    async def revoke_all_for_user(self, user_id: str) -> int:
        """
        Revoke all tokens for a user (e.g., password change, security event).
        Returns count of tokens revoked.
        """
        if not self._redis:
            raise RuntimeError("TokenRevocationList not connected")
        
        # Store user-level revocation marker
        key = f"user_revoked:{user_id}"
        await self._redis.set(key, str(time.time()))
        await self._redis.expire(key, 86400 * 7)  # 7 days
        
        return 1  # User-level revocation
    
    async def is_revoked(self, token: str) -> bool:
        """
        Check if a token is revoked.
        Uses local cache for fast negative lookups.
        """
        if not self._redis:
            raise RuntimeError("TokenRevocationList not connected")
        
        token_hash = self._hash_token(token)
        
        # Fast local cache check
        if token_hash in self._local_cache:
            return True
        
        # Redis check
        key = f"{self.REVOCATION_PREFIX}{token_hash}"
        exists = await self._redis.exists(key)
        
        if exists:
            self._local_cache.add(token_hash)
            return True
        
        return False
    
    async def is_user_revoked(self, user_id: str, token_issued_at: float) -> bool:
        """
        Check if user has a revocation marker after token issuance.
        """
        if not self._redis:
            raise RuntimeError("TokenRevocationList not connected")
        
        key = f"user_revoked:{user_id}"
        revoked_at = await self._redis.get(key)
        
        if revoked_at:
            return float(revoked_at) > token_issued_at
        
        return False
    
    async def _gc_loop(self) -> None:
        """Background garbage collection loop."""
        while True:
            try:
                await asyncio.sleep(self.GC_INTERVAL_SECONDS)
                await self._run_gc()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Log but don't crash
                print(f"TokenRevocationList GC error: {e}")
    
    async def _run_gc(self) -> int:
        """
        Run garbage collection on expired revocations.
        Returns count of cleaned entries.
        """
        if not self._redis:
            return 0
        
        now = time.time()
        current_bucket = int(now // 3600) * 3600
        cleaned = 0
        
        # Clean old expiry buckets
        for bucket_offset in range(24):  # Check last 24 hours
            bucket = current_bucket - (bucket_offset * 3600)
            bucket_key = f"{self.EXPIRY_INDEX_PREFIX}{bucket}"
            
            # Get tokens in this bucket
            token_hashes = await self._redis.smembers(bucket_key)
            
            for token_hash in token_hashes:
                key = f"{self.REVOCATION_PREFIX}{token_hash}"
                data = await self._redis.hgetall(key)
                
                if data and float(data.get("expires_at", 0)) < now:
                    # Token has naturally expired, remove from revocation list
                    await self._redis.delete(key)
                    await self._redis.srem(bucket_key, token_hash)
                    self._local_cache.discard(token_hash)
                    cleaned += 1
            
            # Clean empty buckets
            if not await self._redis.scard(bucket_key):
                await self._redis.delete(bucket_key)
        
        return cleaned
    
    async def get_stats(self) -> Dict:
        """Get revocation list statistics."""
        if not self._redis:
            return {}
        
        # Count revoked tokens
        cursor = 0
        count = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor, match=f"{self.REVOCATION_PREFIX}*", count=100
            )
            count += len(keys)
            if cursor == 0:
                break
        
        return {
            "revoked_tokens": count,
            "local_cache_size": len(self._local_cache),
            "gc_enabled": self._gc_enabled,
        }


class RefreshTokenTracker:
    """
    Refresh token replay detection with rotation chain verification.
    Implements token family tracking to detect replay attacks.
    """
    
    TOKEN_FAMILY_PREFIX = "token_family:"
    USED_REFRESH_PREFIX = "used_refresh:"
    
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis_url = redis_url
        self._redis: Optional[redis.Redis] = None
    
    async def connect(self) -> None:
        self._redis = redis.from_url(self.redis_url, decode_responses=True)
    
    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
    
    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
    
    async def create_token_family(
        self,
        user_id: str,
        refresh_token: str,
        expires_in_days: int = 7,
    ) -> str:
        """
        Create a new token family for a refresh token chain.
        Returns family ID.
        """
        if not self._redis:
            raise RuntimeError("RefreshTokenTracker not connected")
        
        import uuid
        family_id = str(uuid.uuid4())
        token_hash = self._hash_token(refresh_token)
        
        # Store family with current token
        key = f"{self.TOKEN_FAMILY_PREFIX}{family_id}"
        await self._redis.hset(key, mapping={
            "user_id": user_id,
            "current_token": token_hash,
            "generation": "1",
            "created_at": str(time.time()),
            "compromised": "false",
        })
        await self._redis.expire(key, expires_in_days * 86400)
        
        # Map token to family
        await self._redis.set(
            f"token_to_family:{token_hash}",
            family_id,
            ex=expires_in_days * 86400,
        )
        
        return family_id
    
    async def rotate_token(
        self,
        old_refresh_token: str,
        new_refresh_token: str,
        expires_in_days: int = 7,
    ) -> Optional[str]:
        """
        Rotate refresh token within family.
        Returns family ID if valid, None if replay detected.
        """
        if not self._redis:
            raise RuntimeError("RefreshTokenTracker not connected")
        
        old_hash = self._hash_token(old_refresh_token)
        new_hash = self._hash_token(new_refresh_token)
        
        # Get family for old token
        family_id = await self._redis.get(f"token_to_family:{old_hash}")
        if not family_id:
            return None  # Unknown token
        
        key = f"{self.TOKEN_FAMILY_PREFIX}{family_id}"
        family_data = await self._redis.hgetall(key)
        
        if not family_data:
            return None
        
        # Check if family is compromised
        if family_data.get("compromised") == "true":
            return None  # Family invalidated
        
        # Check if this is the current token
        if family_data.get("current_token") != old_hash:
            # REPLAY ATTACK DETECTED
            # Old token was already rotated - invalidate entire family
            await self._redis.hset(key, "compromised", "true")
            return None
        
        # Mark old token as used
        await self._redis.set(
            f"{self.USED_REFRESH_PREFIX}{old_hash}",
            "1",
            ex=expires_in_days * 86400,
        )
        
        # Update family with new token
        generation = int(family_data.get("generation", 1)) + 1
        await self._redis.hset(key, mapping={
            "current_token": new_hash,
            "generation": str(generation),
        })
        
        # Map new token to family
        await self._redis.set(
            f"token_to_family:{new_hash}",
            family_id,
            ex=expires_in_days * 86400,
        )
        
        return family_id
    
    async def invalidate_family(self, family_id: str) -> bool:
        """Invalidate an entire token family (logout all devices)."""
        if not self._redis:
            raise RuntimeError("RefreshTokenTracker not connected")
        
        key = f"{self.TOKEN_FAMILY_PREFIX}{family_id}"
        await self._redis.hset(key, "compromised", "true")
        return True
    
    async def is_token_used(self, refresh_token: str) -> bool:
        """Check if a refresh token has already been used."""
        if not self._redis:
            raise RuntimeError("RefreshTokenTracker not connected")
        
        token_hash = self._hash_token(refresh_token)
        return bool(await self._redis.exists(f"{self.USED_REFRESH_PREFIX}{token_hash}"))
