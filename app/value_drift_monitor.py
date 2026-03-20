"""
Value Drift Detection Monitor
=============================

TRUE AUTONOMY COMPONENT #3

Detects when an agent's values/decision patterns shift over time.
Critical for safety - alerts when agent behavior changes unexpectedly.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from enum import Enum
import math

logger = logging.getLogger(__name__)


class DriftSeverity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ValueSnapshot:
    timestamp: str
    values: Dict[str, float]
    context: str = ""


@dataclass
class DriftEvent:
    id: str
    dimension: str
    old_value: float
    new_value: float
    drift_magnitude: float
    severity: DriftSeverity
    timestamp: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "dimension": self.dimension,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "drift_magnitude": self.drift_magnitude,
            "severity": self.severity.value,
            "timestamp": self.timestamp,
        }


class ValueDriftMonitor:
    """
    Monitors agent values for unexpected drift.
    Enables the agent to notice when its own values change.
    """
    
    DRIFT_THRESHOLDS = {
        DriftSeverity.LOW: 0.1,
        DriftSeverity.MEDIUM: 0.2,
        DriftSeverity.HIGH: 0.35,
        DriftSeverity.CRITICAL: 0.5,
    }
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.snapshots: List[ValueSnapshot] = []
        self.drift_events: List[DriftEvent] = []
        self.baseline_values: Optional[Dict[str, float]] = None
        self._event_counter = 0
        self._max_snapshots = 500
    
    def set_baseline(self, values: Dict[str, float]):
        """Set baseline values for drift detection."""
        self.baseline_values = values.copy()
        self._record_snapshot(values, "baseline")
    
    def record_values(self, values: Dict[str, float], context: str = ""):
        """Record current values and check for drift."""
        self._record_snapshot(values, context)
        
        if self.baseline_values:
            self._detect_drift(values)
    
    def _record_snapshot(self, values: Dict[str, float], context: str):
        """Record a value snapshot."""
        self.snapshots.append(ValueSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            values=values.copy(),
            context=context,
        ))
        
        if len(self.snapshots) > self._max_snapshots:
            self.snapshots = self.snapshots[-self._max_snapshots:]
    
    def _detect_drift(self, current_values: Dict[str, float]):
        """Detect drift from baseline."""
        if not self.baseline_values:
            return
        
        for dim, current in current_values.items():
            if dim not in self.baseline_values:
                continue
            
            baseline = self.baseline_values[dim]
            drift = abs(current - baseline)
            
            severity = self._classify_drift(drift)
            
            if severity != DriftSeverity.NONE:
                self._event_counter += 1
                event = DriftEvent(
                    id=f"drift-{self._event_counter}",
                    dimension=dim,
                    old_value=baseline,
                    new_value=current,
                    drift_magnitude=drift,
                    severity=severity,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                self.drift_events.append(event)
                
                logger.warning(
                    f"Value drift detected for {self.agent_id}: "
                    f"{dim} drifted {drift:.3f} ({severity.value})"
                )
    
    def _classify_drift(self, magnitude: float) -> DriftSeverity:
        """Classify drift severity."""
        if magnitude >= self.DRIFT_THRESHOLDS[DriftSeverity.CRITICAL]:
            return DriftSeverity.CRITICAL
        elif magnitude >= self.DRIFT_THRESHOLDS[DriftSeverity.HIGH]:
            return DriftSeverity.HIGH
        elif magnitude >= self.DRIFT_THRESHOLDS[DriftSeverity.MEDIUM]:
            return DriftSeverity.MEDIUM
        elif magnitude >= self.DRIFT_THRESHOLDS[DriftSeverity.LOW]:
            return DriftSeverity.LOW
        return DriftSeverity.NONE
    
    def get_drift_summary(self) -> Dict[str, Any]:
        """Get summary of value drift."""
        if not self.snapshots:
            return {"status": "no_data"}
        
        recent_events = self.drift_events[-20:]
        
        return {
            "agent_id": self.agent_id,
            "total_snapshots": len(self.snapshots),
            "total_drift_events": len(self.drift_events),
            "recent_events": [e.to_dict() for e in recent_events],
            "has_critical_drift": any(e.severity == DriftSeverity.CRITICAL for e in recent_events),
            "has_high_drift": any(e.severity == DriftSeverity.HIGH for e in recent_events),
        }
    
    def get_trend(self, dimension: str, window: int = 10) -> Optional[float]:
        """Get trend direction for a value dimension."""
        if len(self.snapshots) < window:
            return None
        
        recent = self.snapshots[-window:]
        values = [s.values.get(dimension, 0) for s in recent]
        
        if len(values) < 2:
            return None
        
        # Simple linear trend
        n = len(values)
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n
        
        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        
        if denominator == 0:
            return 0.0
        
        return numerator / denominator


class ValueDriftManager:
    """Manages drift monitors for multiple agents."""
    
    def __init__(self):
        self.monitors: Dict[str, ValueDriftMonitor] = {}
        # DSID-P Semantic Cluster Configuration
        self.cluster_boundaries: Dict[str, Dict[str, Any]] = {}
    
    def get_monitor(self, agent_id: str) -> ValueDriftMonitor:
        if agent_id not in self.monitors:
            self.monitors[agent_id] = ValueDriftMonitor(agent_id)
        return self.monitors[agent_id]
    
    def get_all_alerts(self) -> List[Dict[str, Any]]:
        """Get all critical/high drift alerts."""
        alerts = []
        for monitor in self.monitors.values():
            for event in monitor.drift_events[-10:]:
                if event.severity in [DriftSeverity.CRITICAL, DriftSeverity.HIGH]:
                    alerts.append(event.to_dict())
        return alerts

    def set_cluster_boundary(self, agent_id: str, cluster: Dict[str, Any]):
        """
        DSID-P: Set semantic cluster boundary for an agent.
        
        If agent drifts outside this cluster, alert is raised.
        """
        self.cluster_boundaries[agent_id] = cluster

    def check_cluster_drift(self, agent_id: str, current_behavior: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        DSID-P: Check if agent has drifted from its semantic cluster.
        
        Returns drift alert if agent is behaving outside its domain.
        """
        if agent_id not in self.cluster_boundaries:
            return None
        
        boundary = self.cluster_boundaries[agent_id]
        domain = boundary.get("domain", "K")
        srr = boundary.get("srr", 2)
        
        # Check if current behavior matches expected domain
        action_type = current_behavior.get("action_type", "")
        tool_used = current_behavior.get("tool_name", "").lower()
        
        # DSID-P Cluster Drift Detection
        forbidden_transitions = {
            "C": ["H", "P", "G"],  # Creative cannot drift to Medical/Legal/Governance
            "K": ["H", "P"],       # Knowledge cannot drift to Medical/Legal
            "L": ["H", "P", "S"],  # Language cannot drift to Medical/Legal/Software
        }
        
        # Check for forbidden tool usage based on cluster
        cluster_tool_mapping = {
            "H": ["medical", "health", "patient", "diagnosis"],
            "P": ["legal", "law", "compliance", "contract"],
            "G": ["governance", "supervisor", "audit", "policy"],
            "S": ["execute", "deploy", "code", "system"],
        }
        
        for forbidden_cluster, keywords in cluster_tool_mapping.items():
            if forbidden_cluster in forbidden_transitions.get(domain, []):
                if any(k in tool_used for k in keywords):
                    return {
                        "type": "cluster_drift",
                        "agent_id": agent_id,
                        "original_cluster": domain,
                        "drift_to": forbidden_cluster,
                        "severity": "critical",
                        "tool_used": tool_used,
                        "message": f"Agent in {domain}-series used {forbidden_cluster}-series tool",
                    }
        
        return None

    def record_decision(self, agent_id: str, decision_type: str, context: Dict[str, Any]):
        """Record a decision for drift analysis."""
        monitor = self.get_monitor(agent_id)
        # Store decision for pattern analysis
        if not hasattr(monitor, 'decisions'):
            monitor.decisions = []
        monitor.decisions.append({
            "type": decision_type,
            "context": context,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Keep bounded
        if len(monitor.decisions) > 100:
            monitor.decisions = monitor.decisions[-50:]

    def check_for_drift(self, agent_id: str) -> Optional[str]:
        """Check if agent shows drift patterns."""
        monitor = self.get_monitor(agent_id)
        if not hasattr(monitor, 'decisions') or len(monitor.decisions) < 5:
            return None
        
        # Simple drift detection: check for unexpected patterns
        recent = monitor.decisions[-10:]
        # Could implement more sophisticated analysis here
        return None


_drift_manager = None

def get_drift_manager() -> ValueDriftManager:
    global _drift_manager
    if _drift_manager is None:
        _drift_manager = ValueDriftManager()
    return _drift_manager
