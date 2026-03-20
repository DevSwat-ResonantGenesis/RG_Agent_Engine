"""
Crypto Identity Extraction - Shared Utility

All services should use this to extract crypto identity headers
propagated by the gateway.
"""

from fastapi import Request
from typing import Optional


class CryptoIdentity:
    """
    Crypto identity extracted from gateway headers.
    
    Gateway propagates these headers after JWT validation:
    - X-User-ID: User UUID
    - X-Org-ID: Organization UUID
    - X-User-Role: User role
    - X-User-Plan: Subscription tier
    - X-Crypto-Hash: Cryptographic identity hash
    - X-User-Hash: Hash Sphere semantic identity
    - X-Universe-ID: User's semantic universe ID
    """
    
    def __init__(
        self,
        user_id: str,
        org_id: str,
        role: str = "user",
        tier: str = "developer",
        crypto_hash: Optional[str] = None,
        user_hash: Optional[str] = None,
        universe_id: Optional[str] = None,
    ):
        self.user_id = user_id
        self.org_id = org_id
        self.role = role
        self.tier = tier
        self.crypto_hash = crypto_hash
        self.user_hash = user_hash
        self.universe_id = universe_id
    
    @classmethod
    def from_request(cls, request: Request) -> "CryptoIdentity":
        """
        Extract crypto identity from request headers.
        
        Usage:
            identity = CryptoIdentity.from_request(request)
            if not identity.user_id:
                raise HTTPException(401, "Unauthorized")
            
            # Use crypto identity
            logger.info(f"Request from user_hash: {identity.user_hash}")
        """
        return cls(
            user_id=request.headers.get("x-user-id", ""),
            org_id=request.headers.get("x-org-id", ""),
            role=request.headers.get("x-user-role", "user"),
            tier=request.headers.get("x-user-plan", "developer"),
            crypto_hash=request.headers.get("x-crypto-hash"),
            user_hash=request.headers.get("x-user-hash"),
            universe_id=request.headers.get("x-universe-id"),
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "user_id": self.user_id,
            "org_id": self.org_id,
            "role": self.role,
            "tier": self.tier,
            "crypto_hash": self.crypto_hash,
            "user_hash": self.user_hash,
            "universe_id": self.universe_id,
        }
    
    def has_crypto_identity(self) -> bool:
        """Check if crypto identity is present."""
        return bool(self.crypto_hash and self.user_hash and self.universe_id)
    
    def __repr__(self) -> str:
        return f"CryptoIdentity(user_id={self.user_id[:8]}..., user_hash={self.user_hash[:8] if self.user_hash else 'None'}...)"


# Convenience function
def get_crypto_identity(request: Request) -> CryptoIdentity:
    """
    Extract crypto identity from request headers.
    
    Usage:
        from shared.crypto_identity import get_crypto_identity
        
        @app.post("/endpoint")
        async def endpoint(request: Request):
            identity = get_crypto_identity(request)
            
            # Use crypto identity
            logger.info(f"User: {identity.user_hash}")
            
            # Store with universe_id
            memory = Memory(
                user_id=identity.user_id,
                universe_id=identity.universe_id,
                content=content
            )
    """
    return CryptoIdentity.from_request(request)
