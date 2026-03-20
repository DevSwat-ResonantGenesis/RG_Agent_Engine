"""
CSRF Protection for cookie-based authentication.
Production-grade double-submit cookie pattern with signed tokens.
"""

import hashlib
import hmac
import secrets
import time
from typing import Optional, Tuple
from dataclasses import dataclass


@dataclass
class CSRFConfig:
    """CSRF protection configuration."""
    secret_key: str
    token_length: int = 32
    token_ttl_seconds: int = 3600  # 1 hour
    cookie_name: str = "csrf_token"
    header_name: str = "X-CSRF-Token"
    safe_methods: Tuple[str, ...] = ("GET", "HEAD", "OPTIONS", "TRACE")


class CSRFProtection:
    """
    Production CSRF protection with:
    - Signed tokens with HMAC
    - Timestamp-based expiry
    - Double-submit cookie pattern
    - Per-session binding
    """
    
    def __init__(self, config: CSRFConfig):
        self.config = config
        self._secret = config.secret_key.encode()
    
    def generate_token(self, session_id: Optional[str] = None) -> str:
        """
        Generate a signed CSRF token.
        
        Args:
            session_id: Optional session ID to bind token to
        
        Returns:
            Signed CSRF token
        """
        timestamp = int(time.time())
        random_bytes = secrets.token_hex(self.config.token_length // 2)
        
        # Create payload
        payload = f"{timestamp}:{random_bytes}"
        if session_id:
            payload = f"{payload}:{session_id}"
        
        # Sign with HMAC
        signature = hmac.new(
            self._secret,
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()[:16]
        
        return f"{payload}:{signature}"
    
    def validate_token(
        self,
        token: str,
        session_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate a CSRF token.
        
        Returns:
            (is_valid, error_message)
        """
        if not token:
            return False, "Missing CSRF token"
        
        parts = token.split(":")
        if len(parts) < 3:
            return False, "Invalid token format"
        
        try:
            timestamp = int(parts[0])
            random_bytes = parts[1]
            
            # Check if session-bound
            if len(parts) == 4:
                token_session_id = parts[2]
                signature = parts[3]
                payload = f"{timestamp}:{random_bytes}:{token_session_id}"
                
                if session_id and token_session_id != session_id:
                    return False, "Token session mismatch"
            else:
                signature = parts[2]
                payload = f"{timestamp}:{random_bytes}"
            
            # Verify signature
            expected_sig = hmac.new(
                self._secret,
                payload.encode(),
                hashlib.sha256,
            ).hexdigest()[:16]
            
            if not hmac.compare_digest(signature, expected_sig):
                return False, "Invalid token signature"
            
            # Check expiry
            now = int(time.time())
            if now - timestamp > self.config.token_ttl_seconds:
                return False, "Token expired"
            
            return True, None
            
        except (ValueError, IndexError) as e:
            return False, f"Token validation error: {e}"
    
    def is_safe_method(self, method: str) -> bool:
        """Check if HTTP method is safe (doesn't require CSRF)."""
        return method.upper() in self.config.safe_methods
    
    def get_cookie_settings(self) -> dict:
        """Get recommended cookie settings for CSRF token."""
        return {
            "key": self.config.cookie_name,
            "httponly": False,  # Must be readable by JS
            "secure": True,
            "samesite": "strict",
            "max_age": self.config.token_ttl_seconds,
        }


class CSRFMiddleware:
    """
    FastAPI middleware for CSRF protection.
    """
    
    def __init__(self, csrf: CSRFProtection):
        self.csrf = csrf
    
    async def __call__(self, request, call_next):
        from fastapi import Request
        from fastapi.responses import JSONResponse
        
        # Skip safe methods
        if self.csrf.is_safe_method(request.method):
            return await call_next(request)
        
        # Skip if no cookies (API key auth)
        if not request.cookies:
            return await call_next(request)
        
        # Get token from header
        header_token = request.headers.get(self.csrf.config.header_name)
        
        # Get token from cookie
        cookie_token = request.cookies.get(self.csrf.config.cookie_name)
        
        # Validate double-submit
        if not header_token or not cookie_token:
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing"},
            )
        
        if header_token != cookie_token:
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token mismatch"},
            )
        
        # Validate token signature
        session_id = request.cookies.get("session_id")
        is_valid, error = self.csrf.validate_token(header_token, session_id)
        
        if not is_valid:
            return JSONResponse(
                status_code=403,
                content={"detail": f"CSRF validation failed: {error}"},
            )
        
        return await call_next(request)
