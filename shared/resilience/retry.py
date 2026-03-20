"""
Retry policies with exponential backoff for resilient service calls.
"""

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Optional, Callable, Any, Tuple, Type, Union
from functools import wraps


@dataclass
class RetryConfig:
    """Retry policy configuration."""
    max_retries: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    jitter_factor: float = 0.1
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,)
    retryable_status_codes: Tuple[int, ...] = (429, 500, 502, 503, 504)


class ExponentialBackoff:
    """
    Exponential backoff calculator with jitter.
    """
    
    def __init__(self, config: RetryConfig):
        self.config = config
    
    def get_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number (0-indexed)."""
        delay = self.config.base_delay_seconds * (self.config.exponential_base ** attempt)
        delay = min(delay, self.config.max_delay_seconds)
        
        if self.config.jitter:
            jitter_range = delay * self.config.jitter_factor
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0, delay)


class RetryPolicy:
    """
    Production retry policy with:
    - Exponential backoff
    - Configurable jitter
    - Exception filtering
    - Status code filtering
    - Metrics collection
    """
    
    def __init__(self, config: Optional[RetryConfig] = None):
        self.config = config or RetryConfig()
        self.backoff = ExponentialBackoff(self.config)
        
        # Metrics
        self._total_attempts = 0
        self._total_retries = 0
        self._total_successes = 0
        self._total_failures = 0
    
    def _is_retryable_exception(self, exc: Exception) -> bool:
        """Check if exception is retryable."""
        return isinstance(exc, self.config.retryable_exceptions)
    
    def _is_retryable_status(self, status_code: int) -> bool:
        """Check if status code is retryable."""
        return status_code in self.config.retryable_status_codes
    
    async def execute(
        self,
        func: Callable[..., Any],
        *args,
        **kwargs,
    ) -> Any:
        """
        Execute function with retry policy.
        
        Raises:
            Last exception if all retries exhausted
        """
        last_exception: Optional[Exception] = None
        
        for attempt in range(self.config.max_retries + 1):
            self._total_attempts += 1
            
            try:
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                
                self._total_successes += 1
                return result
                
            except Exception as e:
                last_exception = e
                
                if not self._is_retryable_exception(e):
                    self._total_failures += 1
                    raise
                
                if attempt < self.config.max_retries:
                    self._total_retries += 1
                    delay = self.backoff.get_delay(attempt)
                    await asyncio.sleep(delay)
                else:
                    self._total_failures += 1
                    raise
        
        # Should not reach here, but just in case
        if last_exception:
            raise last_exception
    
    async def execute_with_status_check(
        self,
        func: Callable[..., Any],
        get_status: Callable[[Any], int],
        *args,
        **kwargs,
    ) -> Any:
        """
        Execute function with retry based on response status code.
        
        Args:
            func: Function to execute
            get_status: Function to extract status code from result
        """
        last_result = None
        last_exception: Optional[Exception] = None
        
        for attempt in range(self.config.max_retries + 1):
            self._total_attempts += 1
            
            try:
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                
                status = get_status(result)
                
                if not self._is_retryable_status(status):
                    self._total_successes += 1
                    return result
                
                last_result = result
                
                if attempt < self.config.max_retries:
                    self._total_retries += 1
                    delay = self.backoff.get_delay(attempt)
                    await asyncio.sleep(delay)
                else:
                    self._total_failures += 1
                    return result
                    
            except Exception as e:
                last_exception = e
                
                if not self._is_retryable_exception(e):
                    self._total_failures += 1
                    raise
                
                if attempt < self.config.max_retries:
                    self._total_retries += 1
                    delay = self.backoff.get_delay(attempt)
                    await asyncio.sleep(delay)
                else:
                    self._total_failures += 1
                    raise
        
        if last_result is not None:
            return last_result
        if last_exception:
            raise last_exception
    
    def get_stats(self) -> dict:
        """Get retry policy statistics."""
        return {
            "total_attempts": self._total_attempts,
            "total_retries": self._total_retries,
            "total_successes": self._total_successes,
            "total_failures": self._total_failures,
            "retry_rate": self._total_retries / max(1, self._total_attempts),
        }


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
):
    """Decorator to add retry logic to a function."""
    config = RetryConfig(
        max_retries=max_retries,
        base_delay_seconds=base_delay,
        max_delay_seconds=max_delay,
        retryable_exceptions=retryable_exceptions,
    )
    policy = RetryPolicy(config)
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await policy.execute(func, *args, **kwargs)
        
        wrapper.retry_policy = policy
        return wrapper
    
    return decorator
