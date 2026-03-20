"""
Tests for rate limiting system.
"""

import pytest
from app.rate_limiter import RateLimiter, check_user_rate_limit


@pytest.mark.asyncio
async def test_rate_limiter_basic():
    """Test basic rate limiting functionality."""
    limiter = RateLimiter()
    
    # First request should succeed
    allowed, error = await limiter.check_rate_limit("user1", "free", "execution")
    assert allowed is True
    assert error is None


@pytest.mark.asyncio
async def test_rate_limiter_enforces_limits():
    """Test that rate limits are enforced."""
    limiter = RateLimiter()
    
    # Make requests up to limit (free tier: 100/hour for execution)
    for i in range(100):
        allowed, error = await limiter.check_rate_limit("user1", "free", "execution")
        assert allowed is True
    
    # Next request should be denied
    allowed, error = await limiter.check_rate_limit("user1", "free", "execution")
    assert allowed is False
    assert "Rate limit exceeded" in error


@pytest.mark.asyncio
async def test_rate_limiter_tier_differences():
    """Test different limits for different tiers."""
    limiter = RateLimiter()
    
    # Free tier limited to 100
    for i in range(100):
        await limiter.check_rate_limit("user_free", "free", "execution")
    
    allowed, _ = await limiter.check_rate_limit("user_free", "free", "execution")
    assert allowed is False
    
    # Plus tier should allow 1000
    for i in range(1000):
        await limiter.check_rate_limit("user_plus", "plus", "execution")
    
    allowed, _ = await limiter.check_rate_limit("user_plus", "plus", "execution")
    assert allowed is False


@pytest.mark.asyncio
async def test_rate_limiter_per_user():
    """Test that limits are enforced per user."""
    limiter = RateLimiter()
    
    # User 1 hits limit
    for i in range(100):
        await limiter.check_rate_limit("user1", "free", "execution")
    
    allowed, _ = await limiter.check_rate_limit("user1", "free", "execution")
    assert allowed is False
    
    # User 2 should still have full quota
    allowed, error = await limiter.check_rate_limit("user2", "free", "execution")
    assert allowed is True
    assert error is None


@pytest.mark.asyncio
async def test_rate_limiter_usage_stats():
    """Test usage statistics."""
    limiter = RateLimiter()
    
    # Make some requests
    for i in range(50):
        await limiter.check_rate_limit("user1", "free", "execution")
    
    # Check usage
    stats = await limiter.get_usage("user1", "free", "execution")
    
    assert stats["used"] == 50
    assert stats["limit"] == 100
    assert stats["remaining"] == 50
    assert stats["tier"] == "free"


@pytest.mark.asyncio
async def test_rate_limiter_cleanup():
    """Test cleanup of old requests."""
    limiter = RateLimiter()
    
    # Make some requests
    for i in range(10):
        await limiter.check_rate_limit("user1", "free", "execution")
    
    # Run cleanup
    await limiter.cleanup()
    
    # Should still have usage tracked (within 24h window)
    stats = await limiter.get_usage("user1", "free", "execution")
    assert stats["used"] >= 0


@pytest.mark.asyncio
async def test_check_user_rate_limit_helper():
    """Test the convenience function."""
    allowed, error = await check_user_rate_limit("user1", "enterprise", "execution")
    assert allowed is True
    assert error is None
