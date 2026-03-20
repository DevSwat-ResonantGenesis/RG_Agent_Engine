"""
Rate Limiting for Agent Execution
Per-user rate limits based on subscription tier
"""

import time
from typing import Dict, Optional
from datetime import datetime, timedelta
from collections import defaultdict
import asyncio

class RateLimiter:
    """
    In-memory rate limiter for agent operations.
    
    Tracks requests per user per time window.
    Different limits based on subscription tier.
    """
    
    def __init__(self):
        # Store: user_id -> [(timestamp, operation)]
        self._requests: Dict[str, list] = defaultdict(list)
        self._lock = asyncio.Lock()
    
    # Rate limits by tier (requests per hour)
    TIER_LIMITS = {
        "free": 100,
        "developer": 100,
        "plus": 1000,
        "professional": 1000,
        "enterprise": 10000,
        "unlimited": 999999,
    }
    
    # Agent creation limits (per day)
    AGENT_CREATION_LIMITS = {
        "free": 3,
        "developer": 3,
        "plus": 20,
        "professional": 20,
        "enterprise": 100,
        "unlimited": 999999,
    }
    
    # Execution limits (per hour)
    EXECUTION_LIMITS = {
        "free": 100,
        "developer": 100,
        "plus": 1000,
        "professional": 1000,
        "enterprise": 10000,
        "unlimited": 999999,
    }
    
    async def check_rate_limit(
        self, 
        user_id: str, 
        tier: str, 
        operation: str = "default",
        window_seconds: int = 3600
    ) -> tuple[bool, Optional[str]]:
        """
        Check if user is within rate limits.
        
        Args:
            user_id: User identifier
            tier: Subscription tier
            operation: Type of operation (default, execution, creation)
            window_seconds: Time window in seconds (default 1 hour)
        
        Returns:
            (allowed, error_message)
        """
        async with self._lock:
            now = time.time()
            window_start = now - window_seconds
            
            # Clean old requests
            if user_id in self._requests:
                self._requests[user_id] = [
                    (ts, op) for ts, op in self._requests[user_id]
                    if ts > window_start
                ]
            
            # Get limit for tier and operation
            if operation == "execution":
                limit = self.EXECUTION_LIMITS.get(tier, 100)
            elif operation == "creation":
                limit = self.AGENT_CREATION_LIMITS.get(tier, 3)
                window_seconds = 86400  # 24 hours for creation
                window_start = now - window_seconds
            else:
                limit = self.TIER_LIMITS.get(tier, 100)
            
            # Count requests in window
            count = len([
                1 for ts, op in self._requests[user_id]
                if ts > window_start and (operation == "default" or op == operation)
            ])
            
            if count >= limit:
                wait_time = int(window_seconds / 60)  # minutes
                return False, f"Rate limit exceeded ({count}/{limit}). Try again in {wait_time} minutes. Upgrade to increase limits."
            
            # Record this request
            self._requests[user_id].append((now, operation))
            
            return True, None
    
    async def get_usage(
        self, 
        user_id: str, 
        tier: str,
        operation: str = "default",
        window_seconds: int = 3600
    ) -> dict:
        """
        Get current usage stats for a user.
        
        Returns:
            {
                "used": int,
                "limit": int,
                "remaining": int,
                "reset_in_seconds": int
            }
        """
        async with self._lock:
            now = time.time()
            window_start = now - window_seconds
            
            # Get limit
            if operation == "execution":
                limit = self.EXECUTION_LIMITS.get(tier, 100)
            elif operation == "creation":
                limit = self.AGENT_CREATION_LIMITS.get(tier, 3)
                window_seconds = 86400
                window_start = now - window_seconds
            else:
                limit = self.TIER_LIMITS.get(tier, 100)
            
            # Count usage
            requests = [
                (ts, op) for ts, op in self._requests.get(user_id, [])
                if ts > window_start and (operation == "default" or op == operation)
            ]
            used = len(requests)
            
            # Calculate reset time
            oldest_ts = min([ts for ts, _ in requests], default=now)
            reset_in = int(window_seconds - (now - oldest_ts))
            
            return {
                "used": used,
                "limit": limit,
                "remaining": max(0, limit - used),
                "reset_in_seconds": max(0, reset_in),
                "tier": tier,
            }
    
    async def cleanup(self):
        """Remove old requests to prevent memory bloat."""
        async with self._lock:
            now = time.time()
            cutoff = now - 86400  # Keep last 24 hours
            
            for user_id in list(self._requests.keys()):
                self._requests[user_id] = [
                    (ts, op) for ts, op in self._requests[user_id]
                    if ts > cutoff
                ]
                
                # Remove empty entries
                if not self._requests[user_id]:
                    del self._requests[user_id]


# Global rate limiter instance
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get or create the global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


async def check_user_rate_limit(
    user_id: str,
    tier: str,
    operation: str = "default"
) -> tuple[bool, Optional[str]]:
    """
    Convenience function to check rate limits.
    
    Usage:
        allowed, error = await check_user_rate_limit(user_id, tier, "execution")
        if not allowed:
            raise HTTPException(status_code=429, detail=error)
    """
    limiter = get_rate_limiter()
    return await limiter.check_rate_limit(user_id, tier, operation)
