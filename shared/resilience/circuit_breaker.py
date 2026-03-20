"""
Circuit breaker pattern for service resilience.
Prevents cascade failures across microservices.
"""

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Callable, Any, TypeVar, Generic
from functools import wraps


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""
    failure_threshold: int = 5
    success_threshold: int = 3
    timeout_seconds: float = 30.0
    half_open_max_calls: int = 3
    excluded_exceptions: tuple = ()


@dataclass
class CircuitStats:
    """Circuit breaker statistics."""
    state: CircuitState
    failure_count: int
    success_count: int
    last_failure_time: Optional[float]
    last_success_time: Optional[float]
    total_calls: int
    total_failures: int
    total_successes: int


class CircuitOpenError(Exception):
    """Raised when circuit is open and request is rejected."""
    def __init__(self, circuit_name: str, retry_after: float):
        self.circuit_name = circuit_name
        self.retry_after = retry_after
        super().__init__(f"Circuit '{circuit_name}' is open. Retry after {retry_after:.1f}s")


T = TypeVar('T')


class CircuitBreaker:
    """
    Production circuit breaker with:
    - Configurable thresholds
    - Half-open state for recovery testing
    - Metrics collection
    - Thread-safe state management
    """
    
    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        
        # State
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._last_state_change: float = time.time()
        self._half_open_calls = 0
        
        # Metrics
        self._total_calls = 0
        self._total_failures = 0
        self._total_successes = 0
        
        # Lock for thread safety
        self._lock = asyncio.Lock()
    
    @property
    def state(self) -> CircuitState:
        return self._state
    
    @property
    def is_closed(self) -> bool:
        return self._state == CircuitState.CLOSED
    
    @property
    def is_open(self) -> bool:
        return self._state == CircuitState.OPEN
    
    @property
    def is_half_open(self) -> bool:
        return self._state == CircuitState.HALF_OPEN
    
    async def _check_state_transition(self) -> None:
        """Check if state should transition based on timeout."""
        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._last_state_change
            if elapsed >= self.config.timeout_seconds:
                await self._transition_to(CircuitState.HALF_OPEN)
    
    async def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state."""
        old_state = self._state
        self._state = new_state
        self._last_state_change = time.time()
        
        if new_state == CircuitState.CLOSED:
            self._failure_count = 0
            self._success_count = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
            self._success_count = 0
    
    async def _record_success(self) -> None:
        """Record a successful call."""
        self._total_successes += 1
        self._success_count += 1
        
        if self._state == CircuitState.HALF_OPEN:
            if self._success_count >= self.config.success_threshold:
                await self._transition_to(CircuitState.CLOSED)
    
    async def _record_failure(self, error: Exception) -> None:
        """Record a failed call."""
        self._total_failures += 1
        self._failure_count += 1
        self._last_failure_time = time.time()
        
        if self._state == CircuitState.CLOSED:
            if self._failure_count >= self.config.failure_threshold:
                await self._transition_to(CircuitState.OPEN)
        elif self._state == CircuitState.HALF_OPEN:
            await self._transition_to(CircuitState.OPEN)
    
    async def call(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """
        Execute a function through the circuit breaker.
        
        Raises:
            CircuitOpenError: If circuit is open
        """
        async with self._lock:
            await self._check_state_transition()
            
            if self._state == CircuitState.OPEN:
                retry_after = self.config.timeout_seconds - (time.time() - self._last_state_change)
                raise CircuitOpenError(self.name, max(0, retry_after))
            
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    raise CircuitOpenError(self.name, 1.0)
                self._half_open_calls += 1
            
            self._total_calls += 1
        
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            async with self._lock:
                await self._record_success()
            
            return result
            
        except Exception as e:
            if not isinstance(e, self.config.excluded_exceptions):
                async with self._lock:
                    await self._record_failure(e)
            raise
    
    def get_stats(self) -> CircuitStats:
        """Get current circuit breaker statistics."""
        return CircuitStats(
            state=self._state,
            failure_count=self._failure_count,
            success_count=self._success_count,
            last_failure_time=self._last_failure_time,
            last_success_time=None,
            total_calls=self._total_calls,
            total_failures=self._total_failures,
            total_successes=self._total_successes,
        )
    
    async def reset(self) -> None:
        """Manually reset the circuit breaker."""
        async with self._lock:
            await self._transition_to(CircuitState.CLOSED)
            self._failure_count = 0
            self._success_count = 0


class CircuitBreakerRegistry:
    """Registry for managing multiple circuit breakers."""
    
    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()
    
    async def get_or_create(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ) -> CircuitBreaker:
        """Get existing or create new circuit breaker."""
        async with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name, config)
            return self._breakers[name]
    
    def get(self, name: str) -> Optional[CircuitBreaker]:
        """Get circuit breaker by name."""
        return self._breakers.get(name)
    
    def get_all_stats(self) -> Dict[str, CircuitStats]:
        """Get stats for all circuit breakers."""
        return {name: cb.get_stats() for name, cb in self._breakers.items()}
    
    async def reset_all(self) -> None:
        """Reset all circuit breakers."""
        for cb in self._breakers.values():
            await cb.reset()


# Global registry
circuit_registry = CircuitBreakerRegistry()


def circuit_breaker(
    name: str,
    config: Optional[CircuitBreakerConfig] = None,
):
    """Decorator to wrap a function with circuit breaker."""
    def decorator(func: Callable) -> Callable:
        cb = CircuitBreaker(name, config)
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await cb.call(func, *args, **kwargs)
        
        wrapper.circuit_breaker = cb
        return wrapper
    
    return decorator
