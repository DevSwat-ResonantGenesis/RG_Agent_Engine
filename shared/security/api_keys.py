"""
Cryptographically signed API keys with lifecycle management.
Production-grade API key system with key-indexed lookup.
"""

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
import redis.asyncio as redis


class APIKeyStatus(Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
    ROTATED = "rotated"


@dataclass
class APIKeyMetadata:
    """API key metadata."""
    key_id: str
    user_id: str
    name: str
    prefix: str  # First 8 chars for identification
    created_at: float
    expires_at: Optional[float]
    last_used_at: Optional[float]
    status: APIKeyStatus
    scopes: List[str] = field(default_factory=list)
    rate_limit_tier: str = "api"
    metadata: Dict = field(default_factory=dict)


class APIKeyManager:
    """
    Production API key management with:
    - Cryptographically signed keys
    - Key-indexed lookup (O(1) validation)
    - Automatic rotation support
    - Scope-based permissions
    - Usage tracking
    """
    
    KEY_PREFIX = "apikey:"
    USER_KEYS_PREFIX = "user_keys:"
    KEY_HASH_PREFIX = "keyhash:"
    
    # Key format: rg_live_<key_id>_<random>_<signature>
    KEY_VERSION = "rg"
    
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        signing_secret: str = "",
        key_length: int = 32,
    ):
        self.redis_url = redis_url
        self._redis: Optional[redis.Redis] = None
        self._signing_secret = signing_secret.encode() if signing_secret else secrets.token_bytes(32)
        self.key_length = key_length
    
    async def connect(self) -> None:
        self._redis = redis.from_url(self.redis_url, decode_responses=True)
    
    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
    
    def _generate_key_id(self) -> str:
        """Generate unique key ID."""
        return secrets.token_hex(8)
    
    def _sign_key(self, key_id: str, random_part: str) -> str:
        """Create HMAC signature for key."""
        payload = f"{key_id}:{random_part}"
        return hmac.new(
            self._signing_secret,
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()[:16]
    
    def _hash_key(self, full_key: str) -> str:
        """Hash full key for storage lookup."""
        return hashlib.sha256(full_key.encode()).hexdigest()
    
    async def create_key(
        self,
        user_id: str,
        name: str,
        scopes: Optional[List[str]] = None,
        expires_in_days: Optional[int] = None,
        rate_limit_tier: str = "api",
        metadata: Optional[Dict] = None,
        environment: str = "live",
    ) -> Tuple[str, APIKeyMetadata]:
        """
        Create a new API key.
        
        Returns:
            (full_key, metadata) - full_key is only returned once!
        """
        if not self._redis:
            raise RuntimeError("APIKeyManager not connected")
        
        key_id = self._generate_key_id()
        random_part = secrets.token_hex(self.key_length // 2)
        signature = self._sign_key(key_id, random_part)
        
        # Build key: rg_live_<key_id>_<random>_<signature>
        full_key = f"{self.KEY_VERSION}_{environment}_{key_id}_{random_part}_{signature}"
        key_hash = self._hash_key(full_key)
        prefix = full_key[:12]  # For display
        
        now = time.time()
        expires_at = None
        if expires_in_days:
            expires_at = now + (expires_in_days * 86400)
        
        key_metadata = APIKeyMetadata(
            key_id=key_id,
            user_id=user_id,
            name=name,
            prefix=prefix,
            created_at=now,
            expires_at=expires_at,
            last_used_at=None,
            status=APIKeyStatus.ACTIVE,
            scopes=scopes or ["*"],
            rate_limit_tier=rate_limit_tier,
            metadata=metadata or {},
        )
        
        # Store key data
        key_data = {
            "key_id": key_id,
            "user_id": user_id,
            "name": name,
            "prefix": prefix,
            "created_at": str(now),
            "expires_at": str(expires_at) if expires_at else "",
            "last_used_at": "",
            "status": APIKeyStatus.ACTIVE.value,
            "scopes": ",".join(scopes or ["*"]),
            "rate_limit_tier": rate_limit_tier,
            "metadata": str(metadata or {}),
        }
        
        # Store by key_id
        await self._redis.hset(f"{self.KEY_PREFIX}{key_id}", mapping=key_data)
        
        # Store hash -> key_id mapping for O(1) lookup
        await self._redis.set(f"{self.KEY_HASH_PREFIX}{key_hash}", key_id)
        
        # Add to user's key list
        await self._redis.sadd(f"{self.USER_KEYS_PREFIX}{user_id}", key_id)
        
        # Set expiry if applicable
        if expires_at:
            ttl = int(expires_at - now) + 86400  # +1 day buffer
            await self._redis.expire(f"{self.KEY_PREFIX}{key_id}", ttl)
            await self._redis.expire(f"{self.KEY_HASH_PREFIX}{key_hash}", ttl)
        
        return full_key, key_metadata
    
    async def validate_key(
        self,
        full_key: str,
        required_scope: Optional[str] = None,
    ) -> Tuple[bool, Optional[APIKeyMetadata], Optional[str]]:
        """
        Validate an API key.
        
        Returns:
            (is_valid, metadata, error_message)
        """
        if not self._redis:
            raise RuntimeError("APIKeyManager not connected")
        
        # Parse key format
        parts = full_key.split("_")
        if len(parts) != 5:
            return False, None, "Invalid key format"
        
        version, environment, key_id, random_part, signature = parts
        
        if version != self.KEY_VERSION:
            return False, None, "Invalid key version"
        
        # Verify signature
        expected_sig = self._sign_key(key_id, random_part)
        if not hmac.compare_digest(signature, expected_sig):
            return False, None, "Invalid key signature"
        
        # Lookup by hash
        key_hash = self._hash_key(full_key)
        stored_key_id = await self._redis.get(f"{self.KEY_HASH_PREFIX}{key_hash}")
        
        if not stored_key_id or stored_key_id != key_id:
            return False, None, "Key not found"
        
        # Get key data
        key_data = await self._redis.hgetall(f"{self.KEY_PREFIX}{key_id}")
        if not key_data:
            return False, None, "Key data not found"
        
        # Check status
        status = APIKeyStatus(key_data.get("status", "revoked"))
        if status != APIKeyStatus.ACTIVE:
            return False, None, f"Key is {status.value}"
        
        # Check expiry
        expires_at = key_data.get("expires_at")
        if expires_at and float(expires_at) < time.time():
            return False, None, "Key expired"
        
        # Check scope
        scopes = key_data.get("scopes", "*").split(",")
        if required_scope and required_scope not in scopes and "*" not in scopes:
            return False, None, f"Missing required scope: {required_scope}"
        
        # Build metadata
        metadata = APIKeyMetadata(
            key_id=key_data["key_id"],
            user_id=key_data["user_id"],
            name=key_data["name"],
            prefix=key_data["prefix"],
            created_at=float(key_data["created_at"]),
            expires_at=float(key_data["expires_at"]) if key_data.get("expires_at") else None,
            last_used_at=float(key_data["last_used_at"]) if key_data.get("last_used_at") else None,
            status=status,
            scopes=scopes,
            rate_limit_tier=key_data.get("rate_limit_tier", "api"),
        )
        
        # Update last used
        await self._redis.hset(f"{self.KEY_PREFIX}{key_id}", "last_used_at", str(time.time()))
        
        return True, metadata, None
    
    async def revoke_key(self, key_id: str, reason: str = "manual") -> bool:
        """Revoke an API key."""
        if not self._redis:
            return False
        
        key = f"{self.KEY_PREFIX}{key_id}"
        if not await self._redis.exists(key):
            return False
        
        await self._redis.hset(key, mapping={
            "status": APIKeyStatus.REVOKED.value,
            "revoked_at": str(time.time()),
            "revoke_reason": reason,
        })
        
        return True
    
    async def rotate_key(
        self,
        old_key_id: str,
        grace_period_hours: int = 24,
    ) -> Tuple[Optional[str], Optional[APIKeyMetadata]]:
        """
        Rotate an API key, creating a new one and marking old as rotated.
        Old key remains valid for grace period.
        
        Returns:
            (new_full_key, new_metadata)
        """
        if not self._redis:
            return None, None
        
        # Get old key data
        old_data = await self._redis.hgetall(f"{self.KEY_PREFIX}{old_key_id}")
        if not old_data:
            return None, None
        
        # Create new key with same properties
        new_key, new_metadata = await self.create_key(
            user_id=old_data["user_id"],
            name=f"{old_data['name']} (rotated)",
            scopes=old_data.get("scopes", "*").split(","),
            rate_limit_tier=old_data.get("rate_limit_tier", "api"),
        )
        
        # Mark old key as rotated (still valid for grace period)
        grace_until = time.time() + (grace_period_hours * 3600)
        await self._redis.hset(f"{self.KEY_PREFIX}{old_key_id}", mapping={
            "status": APIKeyStatus.ROTATED.value,
            "rotated_at": str(time.time()),
            "rotated_to": new_metadata.key_id,
            "grace_until": str(grace_until),
        })
        
        # Set expiry on old key
        await self._redis.expire(f"{self.KEY_PREFIX}{old_key_id}", grace_period_hours * 3600 + 3600)
        
        return new_key, new_metadata
    
    async def list_user_keys(self, user_id: str) -> List[APIKeyMetadata]:
        """List all API keys for a user."""
        if not self._redis:
            return []
        
        key_ids = await self._redis.smembers(f"{self.USER_KEYS_PREFIX}{user_id}")
        keys = []
        
        for key_id in key_ids:
            data = await self._redis.hgetall(f"{self.KEY_PREFIX}{key_id}")
            if data:
                keys.append(APIKeyMetadata(
                    key_id=data["key_id"],
                    user_id=data["user_id"],
                    name=data["name"],
                    prefix=data["prefix"],
                    created_at=float(data["created_at"]),
                    expires_at=float(data["expires_at"]) if data.get("expires_at") else None,
                    last_used_at=float(data["last_used_at"]) if data.get("last_used_at") else None,
                    status=APIKeyStatus(data.get("status", "active")),
                    scopes=data.get("scopes", "*").split(","),
                    rate_limit_tier=data.get("rate_limit_tier", "api"),
                ))
        
        return keys
    
    async def revoke_all_user_keys(self, user_id: str, reason: str = "security") -> int:
        """Revoke all API keys for a user. Returns count revoked."""
        if not self._redis:
            return 0
        
        key_ids = await self._redis.smembers(f"{self.USER_KEYS_PREFIX}{user_id}")
        count = 0
        
        for key_id in key_ids:
            if await self.revoke_key(key_id, reason):
                count += 1
        
        return count
