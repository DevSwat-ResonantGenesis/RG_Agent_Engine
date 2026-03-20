"""
Request signing for sensitive endpoints.
HMAC-based request authentication for critical operations.
"""

import hashlib
import hmac
import time
import json
from typing import Optional, Dict, Tuple
from dataclasses import dataclass


@dataclass
class SignedRequest:
    """Signed request metadata."""
    timestamp: int
    signature: str
    key_id: str
    body_hash: str


class RequestSigner:
    """
    Production request signing with:
    - HMAC-SHA256 signatures
    - Timestamp-based replay protection
    - Body integrity verification
    - Key rotation support
    """
    
    SIGNATURE_HEADER = "X-Signature"
    TIMESTAMP_HEADER = "X-Timestamp"
    KEY_ID_HEADER = "X-Key-Id"
    MAX_CLOCK_SKEW_SECONDS = 300  # 5 minutes
    
    def __init__(self, signing_keys: Dict[str, str]):
        """
        Initialize with signing keys.
        
        Args:
            signing_keys: Dict of key_id -> secret
        """
        self._keys = {k: v.encode() for k, v in signing_keys.items()}
        self._default_key_id = list(signing_keys.keys())[0] if signing_keys else None
    
    def sign_request(
        self,
        method: str,
        path: str,
        body: Optional[bytes] = None,
        key_id: Optional[str] = None,
        timestamp: Optional[int] = None,
    ) -> Dict[str, str]:
        """
        Sign a request.
        
        Returns:
            Dict of headers to add to request
        """
        key_id = key_id or self._default_key_id
        if not key_id or key_id not in self._keys:
            raise ValueError(f"Unknown key_id: {key_id}")
        
        timestamp = timestamp or int(time.time())
        body_hash = hashlib.sha256(body or b"").hexdigest()
        
        # Create signature payload
        payload = f"{method.upper()}:{path}:{timestamp}:{body_hash}"
        
        signature = hmac.new(
            self._keys[key_id],
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        
        return {
            self.SIGNATURE_HEADER: signature,
            self.TIMESTAMP_HEADER: str(timestamp),
            self.KEY_ID_HEADER: key_id,
        }
    
    def verify_request(
        self,
        method: str,
        path: str,
        body: Optional[bytes],
        signature: str,
        timestamp: str,
        key_id: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify a signed request.
        
        Returns:
            (is_valid, error_message)
        """
        # Check key exists
        if key_id not in self._keys:
            return False, f"Unknown key_id: {key_id}"
        
        # Check timestamp
        try:
            ts = int(timestamp)
        except ValueError:
            return False, "Invalid timestamp"
        
        now = int(time.time())
        if abs(now - ts) > self.MAX_CLOCK_SKEW_SECONDS:
            return False, "Request timestamp too old or too far in future"
        
        # Compute expected signature
        body_hash = hashlib.sha256(body or b"").hexdigest()
        payload = f"{method.upper()}:{path}:{ts}:{body_hash}"
        
        expected_sig = hmac.new(
            self._keys[key_id],
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        
        if not hmac.compare_digest(signature, expected_sig):
            return False, "Invalid signature"
        
        return True, None
    
    def add_key(self, key_id: str, secret: str) -> None:
        """Add a new signing key."""
        self._keys[key_id] = secret.encode()
    
    def remove_key(self, key_id: str) -> bool:
        """Remove a signing key."""
        if key_id in self._keys:
            del self._keys[key_id]
            return True
        return False


class RequestSigningMiddleware:
    """
    FastAPI middleware for request signature verification.
    """
    
    def __init__(
        self,
        signer: RequestSigner,
        protected_paths: Optional[list] = None,
    ):
        self.signer = signer
        self.protected_paths = protected_paths or [
            "/billing/",
            "/admin/",
            "/api/auth/auth/delete",
            "/api/auth/auth/change-password",
        ]
    
    def _is_protected(self, path: str) -> bool:
        """Check if path requires signature."""
        return any(path.startswith(p) for p in self.protected_paths)
    
    async def __call__(self, request, call_next):
        from fastapi.responses import JSONResponse
        
        if not self._is_protected(request.url.path):
            return await call_next(request)
        
        # Get signature headers
        signature = request.headers.get(RequestSigner.SIGNATURE_HEADER)
        timestamp = request.headers.get(RequestSigner.TIMESTAMP_HEADER)
        key_id = request.headers.get(RequestSigner.KEY_ID_HEADER)
        
        if not all([signature, timestamp, key_id]):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing request signature"},
            )
        
        # Get body
        body = await request.body()
        
        # Verify
        is_valid, error = self.signer.verify_request(
            method=request.method,
            path=request.url.path,
            body=body,
            signature=signature,
            timestamp=timestamp,
            key_id=key_id,
        )
        
        if not is_valid:
            return JSONResponse(
                status_code=401,
                content={"detail": f"Invalid request signature: {error}"},
            )
        
        return await call_next(request)
