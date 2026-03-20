"""
Service Level Objective (SLO) definitions and tracking.
Production-grade SLO monitoring with error budgets.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Callable
from enum import Enum
from collections import deque


class SLOType(Enum):
    AVAILABILITY = "availability"
    LATENCY = "latency"
    ERROR_RATE = "error_rate"
    THROUGHPUT = "throughput"


@dataclass
class SLODefinition:
    """
    Service Level Objective definition.
    """
    name: str
    slo_type: SLOType
    target: float  # Target value (e.g., 0.999 for 99.9% availability)
    window_seconds: int = 86400 * 30  # 30 days default
    description: str = ""
    
    # Latency-specific
    latency_percentile: float = 0.99  # p99 by default
    latency_threshold_ms: float = 500.0
    
    # Error rate specific
    error_threshold: float = 0.01  # 1% error rate
    
    def __post_init__(self):
        if self.slo_type == SLOType.AVAILABILITY:
            assert 0 < self.target <= 1, "Availability target must be between 0 and 1"
        elif self.slo_type == SLOType.LATENCY:
            assert 0 < self.latency_percentile <= 1, "Percentile must be between 0 and 1"


@dataclass
class SLOStatus:
    """Current SLO status and error budget."""
    slo_name: str
    current_value: float
    target: float
    is_meeting_slo: bool
    error_budget_remaining: float  # Percentage of error budget remaining
    error_budget_consumed: float
    window_start: datetime
    window_end: datetime
    total_requests: int
    failed_requests: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "slo_name": self.slo_name,
            "current_value": round(self.current_value, 6),
            "target": self.target,
            "is_meeting_slo": self.is_meeting_slo,
            "error_budget_remaining_pct": round(self.error_budget_remaining * 100, 2),
            "error_budget_consumed_pct": round(self.error_budget_consumed * 100, 2),
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "total_requests": self.total_requests,
            "failed_requests": self.failed_requests,
        }


class SLOTracker:
    """
    Tracks SLO metrics with sliding window.
    """
    
    def __init__(self, definition: SLODefinition):
        self.definition = definition
        self._events: deque = deque()
        self._latencies: deque = deque()
        
    def record_request(
        self,
        success: bool,
        latency_ms: Optional[float] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        """Record a request for SLO tracking."""
        ts = timestamp or time.time()
        
        self._events.append({
            "timestamp": ts,
            "success": success,
            "latency_ms": latency_ms,
        })
        
        if latency_ms is not None:
            self._latencies.append((ts, latency_ms))
        
        # Cleanup old events
        self._cleanup()
    
    def _cleanup(self) -> None:
        """Remove events outside the window."""
        cutoff = time.time() - self.definition.window_seconds
        
        while self._events and self._events[0]["timestamp"] < cutoff:
            self._events.popleft()
        
        while self._latencies and self._latencies[0][0] < cutoff:
            self._latencies.popleft()
    
    def get_status(self) -> SLOStatus:
        """Calculate current SLO status."""
        self._cleanup()
        
        now = time.time()
        window_start = datetime.fromtimestamp(now - self.definition.window_seconds)
        window_end = datetime.fromtimestamp(now)
        
        total = len(self._events)
        if total == 0:
            return SLOStatus(
                slo_name=self.definition.name,
                current_value=1.0,
                target=self.definition.target,
                is_meeting_slo=True,
                error_budget_remaining=1.0,
                error_budget_consumed=0.0,
                window_start=window_start,
                window_end=window_end,
                total_requests=0,
                failed_requests=0,
            )
        
        if self.definition.slo_type == SLOType.AVAILABILITY:
            return self._calculate_availability_status(total, window_start, window_end)
        elif self.definition.slo_type == SLOType.LATENCY:
            return self._calculate_latency_status(total, window_start, window_end)
        elif self.definition.slo_type == SLOType.ERROR_RATE:
            return self._calculate_error_rate_status(total, window_start, window_end)
        else:
            return self._calculate_availability_status(total, window_start, window_end)
    
    def _calculate_availability_status(
        self,
        total: int,
        window_start: datetime,
        window_end: datetime,
    ) -> SLOStatus:
        """Calculate availability SLO status."""
        successful = sum(1 for e in self._events if e["success"])
        failed = total - successful
        
        availability = successful / total if total > 0 else 1.0
        
        # Error budget calculation
        # If target is 99.9%, error budget is 0.1%
        error_budget_total = 1.0 - self.definition.target
        error_rate = failed / total if total > 0 else 0.0
        
        if error_budget_total > 0:
            error_budget_consumed = error_rate / error_budget_total
            error_budget_remaining = max(0, 1.0 - error_budget_consumed)
        else:
            error_budget_consumed = 1.0 if error_rate > 0 else 0.0
            error_budget_remaining = 0.0 if error_rate > 0 else 1.0
        
        return SLOStatus(
            slo_name=self.definition.name,
            current_value=availability,
            target=self.definition.target,
            is_meeting_slo=availability >= self.definition.target,
            error_budget_remaining=error_budget_remaining,
            error_budget_consumed=min(1.0, error_budget_consumed),
            window_start=window_start,
            window_end=window_end,
            total_requests=total,
            failed_requests=failed,
        )
    
    def _calculate_latency_status(
        self,
        total: int,
        window_start: datetime,
        window_end: datetime,
    ) -> SLOStatus:
        """Calculate latency SLO status."""
        if not self._latencies:
            return SLOStatus(
                slo_name=self.definition.name,
                current_value=0.0,
                target=self.definition.target,
                is_meeting_slo=True,
                error_budget_remaining=1.0,
                error_budget_consumed=0.0,
                window_start=window_start,
                window_end=window_end,
                total_requests=total,
                failed_requests=0,
            )
        
        latencies = sorted([l[1] for l in self._latencies])
        percentile_idx = int(len(latencies) * self.definition.latency_percentile)
        percentile_latency = latencies[min(percentile_idx, len(latencies) - 1)]
        
        # Count requests exceeding threshold
        exceeding = sum(1 for l in latencies if l > self.definition.latency_threshold_ms)
        
        # Current value is the percentage meeting the latency target
        meeting_target = (len(latencies) - exceeding) / len(latencies)
        
        error_budget_total = 1.0 - self.definition.target
        error_rate = exceeding / len(latencies)
        
        if error_budget_total > 0:
            error_budget_consumed = error_rate / error_budget_total
            error_budget_remaining = max(0, 1.0 - error_budget_consumed)
        else:
            error_budget_consumed = 1.0 if error_rate > 0 else 0.0
            error_budget_remaining = 0.0 if error_rate > 0 else 1.0
        
        return SLOStatus(
            slo_name=self.definition.name,
            current_value=meeting_target,
            target=self.definition.target,
            is_meeting_slo=meeting_target >= self.definition.target,
            error_budget_remaining=error_budget_remaining,
            error_budget_consumed=min(1.0, error_budget_consumed),
            window_start=window_start,
            window_end=window_end,
            total_requests=len(latencies),
            failed_requests=exceeding,
        )
    
    def _calculate_error_rate_status(
        self,
        total: int,
        window_start: datetime,
        window_end: datetime,
    ) -> SLOStatus:
        """Calculate error rate SLO status."""
        failed = sum(1 for e in self._events if not e["success"])
        error_rate = failed / total if total > 0 else 0.0
        
        # For error rate, lower is better
        is_meeting = error_rate <= self.definition.error_threshold
        
        if self.definition.error_threshold > 0:
            error_budget_consumed = error_rate / self.definition.error_threshold
            error_budget_remaining = max(0, 1.0 - error_budget_consumed)
        else:
            error_budget_consumed = 1.0 if error_rate > 0 else 0.0
            error_budget_remaining = 0.0 if error_rate > 0 else 1.0
        
        return SLOStatus(
            slo_name=self.definition.name,
            current_value=1.0 - error_rate,  # Convert to success rate
            target=1.0 - self.definition.error_threshold,
            is_meeting_slo=is_meeting,
            error_budget_remaining=error_budget_remaining,
            error_budget_consumed=min(1.0, error_budget_consumed),
            window_start=window_start,
            window_end=window_end,
            total_requests=total,
            failed_requests=failed,
        )


class SLORegistry:
    """Registry for managing multiple SLOs."""
    
    def __init__(self):
        self._trackers: Dict[str, SLOTracker] = {}
    
    def register(self, definition: SLODefinition) -> SLOTracker:
        """Register a new SLO."""
        tracker = SLOTracker(definition)
        self._trackers[definition.name] = tracker
        return tracker
    
    def get(self, name: str) -> Optional[SLOTracker]:
        """Get an SLO tracker by name."""
        return self._trackers.get(name)
    
    def record(
        self,
        slo_name: str,
        success: bool,
        latency_ms: Optional[float] = None,
    ) -> None:
        """Record a request for an SLO."""
        tracker = self._trackers.get(slo_name)
        if tracker:
            tracker.record_request(success, latency_ms)
    
    def get_all_status(self) -> Dict[str, SLOStatus]:
        """Get status for all SLOs."""
        return {name: tracker.get_status() for name, tracker in self._trackers.items()}
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all SLOs."""
        statuses = self.get_all_status()
        
        meeting_slo = sum(1 for s in statuses.values() if s.is_meeting_slo)
        total_slos = len(statuses)
        
        return {
            "total_slos": total_slos,
            "meeting_slo": meeting_slo,
            "not_meeting_slo": total_slos - meeting_slo,
            "slo_compliance_rate": meeting_slo / total_slos if total_slos > 0 else 1.0,
            "slos": {name: status.to_dict() for name, status in statuses.items()},
        }


# Default SLO definitions for ResonantGenesis services
DEFAULT_SLOS = [
    SLODefinition(
        name="gateway_availability",
        slo_type=SLOType.AVAILABILITY,
        target=0.999,  # 99.9%
        description="Gateway service availability",
    ),
    SLODefinition(
        name="gateway_latency_p99",
        slo_type=SLOType.LATENCY,
        target=0.99,  # 99% of requests
        latency_threshold_ms=500.0,
        description="Gateway p99 latency under 500ms",
    ),
    SLODefinition(
        name="auth_availability",
        slo_type=SLOType.AVAILABILITY,
        target=0.9999,  # 99.99%
        description="Auth service availability",
    ),
    SLODefinition(
        name="chat_availability",
        slo_type=SLOType.AVAILABILITY,
        target=0.999,
        description="Chat service availability",
    ),
    SLODefinition(
        name="rag_latency_p95",
        slo_type=SLOType.LATENCY,
        target=0.95,
        latency_percentile=0.95,
        latency_threshold_ms=2000.0,
        description="RAG query p95 latency under 2s",
    ),
    SLODefinition(
        name="billing_error_rate",
        slo_type=SLOType.ERROR_RATE,
        target=0.9999,
        error_threshold=0.0001,  # 0.01% error rate
        description="Billing service error rate",
    ),
]


def create_default_registry() -> SLORegistry:
    """Create registry with default SLOs."""
    registry = SLORegistry()
    for slo in DEFAULT_SLOS:
        registry.register(slo)
    return registry
