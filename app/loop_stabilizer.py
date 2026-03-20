"""Loop stabilization policies for autonomous agent execution.

Prevents:
- Stagnation (no progress)
- Hallucination loops
- Infinite planning
- Oscillating states
- Error-retry spirals

Provides:
- Step confidence thresholds
- Rollback to checkpoints
- Automatic escalation
- Progress tracking
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class StabilityStatus(str, Enum):
    STABLE = "stable"
    WARNING = "warning"
    UNSTABLE = "unstable"
    CRITICAL = "critical"


class StabilityAction(str, Enum):
    CONTINUE = "continue"
    SLOW_DOWN = "slow_down"
    REPLAN = "replan"
    ROLLBACK = "rollback"
    ESCALATE = "escalate"
    ABORT = "abort"


@dataclass
class StabilityConfig:
    """Configuration for loop stabilization."""
    max_iterations: int = 50
    max_consecutive_errors: int = 3
    max_plan_revisions: int = 5
    stagnation_window: int = 5  # Steps to check for stagnation
    min_progress_per_window: float = 0.1  # Minimum progress expected
    confidence_threshold: float = 0.6
    oscillation_detection_window: int = 6
    max_similar_steps: int = 3
    cooldown_seconds: float = 0.5
    escalation_delay_seconds: float = 30.0


@dataclass
class LoopState:
    """Current state of the execution loop."""
    iteration: int = 0
    consecutive_errors: int = 0
    plan_revisions: int = 0
    last_progress: float = 0.0
    progress_history: List[float] = field(default_factory=list)
    step_hashes: List[str] = field(default_factory=list)
    action_sequence: List[str] = field(default_factory=list)
    last_checkpoint_id: Optional[str] = None
    last_checkpoint_iteration: int = 0
    started_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)


@dataclass
class StabilityReport:
    """Report from stability check."""
    status: StabilityStatus
    action: StabilityAction
    reason: str
    confidence: float
    metrics: Dict[str, Any] = field(default_factory=dict)
    suggestions: List[str] = field(default_factory=list)


class LoopStabilizer:
    """
    Monitors and stabilizes autonomous execution loops.
    
    Detects:
    - Stagnation patterns
    - Oscillating behavior
    - Infinite loops
    - Error spirals
    - Low confidence trends
    
    Actions:
    - Slow down execution
    - Force replanning
    - Rollback to checkpoint
    - Escalate to supervisor
    - Abort execution
    """

    def __init__(self, config: Optional[StabilityConfig] = None):
        self.config = config or StabilityConfig()
        self.state = LoopState()
        self.checkpoints: Dict[str, Dict[str, Any]] = {}

    def reset(self):
        """Reset stabilizer for new session."""
        self.state = LoopState()
        self.checkpoints = {}

    def record_step(
        self,
        step_type: str,
        step_input: Dict[str, Any],
        step_output: Dict[str, Any],
        success: bool,
        confidence: float = 1.0,
        progress: float = 0.0,
    ) -> StabilityReport:
        """Record a step and check stability."""
        self.state.iteration += 1
        self.state.last_activity_at = time.time()

        # Track errors
        if not success:
            self.state.consecutive_errors += 1
        else:
            self.state.consecutive_errors = 0

        # Track progress
        self.state.progress_history.append(progress)
        self.state.last_progress = progress

        # Track step hash for loop detection
        step_hash = self._hash_step(step_type, step_input)
        self.state.step_hashes.append(step_hash)

        # Track action sequence for oscillation detection
        self.state.action_sequence.append(step_type)

        # Run stability checks
        return self._check_stability(confidence)

    def record_plan_revision(self, reason: str):
        """Record a plan revision."""
        self.state.plan_revisions += 1

    def create_checkpoint(self, checkpoint_data: Dict[str, Any]) -> str:
        """Create a checkpoint for potential rollback."""
        checkpoint_id = f"cp_{self.state.iteration}_{int(time.time())}"
        self.checkpoints[checkpoint_id] = {
            "id": checkpoint_id,
            "iteration": self.state.iteration,
            "data": checkpoint_data,
            "created_at": datetime.utcnow().isoformat(),
        }
        self.state.last_checkpoint_id = checkpoint_id
        self.state.last_checkpoint_iteration = self.state.iteration
        return checkpoint_id

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Dict[str, Any]]:
        """Get checkpoint data for rollback."""
        return self.checkpoints.get(checkpoint_id)

    def get_latest_checkpoint(self) -> Optional[Dict[str, Any]]:
        """Get the most recent checkpoint."""
        if self.state.last_checkpoint_id:
            return self.checkpoints.get(self.state.last_checkpoint_id)
        return None

    def _check_stability(self, confidence: float) -> StabilityReport:
        """Run all stability checks and return report."""
        checks = [
            self._check_max_iterations(),
            self._check_consecutive_errors(),
            self._check_plan_revisions(),
            self._check_stagnation(),
            self._check_oscillation(),
            self._check_loops(),
            self._check_confidence(confidence),
            self._check_timeout(),
        ]

        # Find the most severe issue
        worst_status = StabilityStatus.STABLE
        worst_action = StabilityAction.CONTINUE
        reasons = []
        suggestions = []

        status_severity = {
            StabilityStatus.STABLE: 0,
            StabilityStatus.WARNING: 1,
            StabilityStatus.UNSTABLE: 2,
            StabilityStatus.CRITICAL: 3,
        }

        for check in checks:
            if check and status_severity[check.status] > status_severity[worst_status]:
                worst_status = check.status
                worst_action = check.action
            if check and check.reason:
                reasons.append(check.reason)
            if check and check.suggestions:
                suggestions.extend(check.suggestions)

        return StabilityReport(
            status=worst_status,
            action=worst_action,
            reason="; ".join(reasons) if reasons else "Loop is stable",
            confidence=confidence,
            metrics=self._get_metrics(),
            suggestions=suggestions,
        )

    def _check_max_iterations(self) -> Optional[StabilityReport]:
        """Check if max iterations exceeded."""
        if self.state.iteration >= self.config.max_iterations:
            return StabilityReport(
                status=StabilityStatus.CRITICAL,
                action=StabilityAction.ABORT,
                reason=f"Max iterations ({self.config.max_iterations}) reached",
                confidence=1.0,
                suggestions=["Task may be too complex", "Consider breaking into subtasks"],
            )
        
        # Warning at 80%
        if self.state.iteration >= self.config.max_iterations * 0.8:
            return StabilityReport(
                status=StabilityStatus.WARNING,
                action=StabilityAction.SLOW_DOWN,
                reason=f"Approaching max iterations ({self.state.iteration}/{self.config.max_iterations})",
                confidence=0.8,
            )
        
        return None

    def _check_consecutive_errors(self) -> Optional[StabilityReport]:
        """Check for error spirals."""
        if self.state.consecutive_errors >= self.config.max_consecutive_errors:
            return StabilityReport(
                status=StabilityStatus.CRITICAL,
                action=StabilityAction.ROLLBACK,
                reason=f"Too many consecutive errors ({self.state.consecutive_errors})",
                confidence=0.95,
                suggestions=["Rollback to last checkpoint", "Try different approach"],
            )
        
        if self.state.consecutive_errors >= 2:
            return StabilityReport(
                status=StabilityStatus.WARNING,
                action=StabilityAction.REPLAN,
                reason=f"Multiple consecutive errors ({self.state.consecutive_errors})",
                confidence=0.7,
            )
        
        return None

    def _check_plan_revisions(self) -> Optional[StabilityReport]:
        """Check for infinite replanning."""
        if self.state.plan_revisions >= self.config.max_plan_revisions:
            return StabilityReport(
                status=StabilityStatus.CRITICAL,
                action=StabilityAction.ESCALATE,
                reason=f"Too many plan revisions ({self.state.plan_revisions})",
                confidence=0.9,
                suggestions=["Goal may be unclear", "Escalate to supervisor"],
            )
        
        return None

    def _check_stagnation(self) -> Optional[StabilityReport]:
        """Check for lack of progress."""
        window = self.config.stagnation_window
        if len(self.state.progress_history) < window:
            return None

        recent_progress = self.state.progress_history[-window:]
        total_progress = sum(recent_progress)

        if total_progress < self.config.min_progress_per_window:
            return StabilityReport(
                status=StabilityStatus.UNSTABLE,
                action=StabilityAction.REPLAN,
                reason=f"Stagnation detected: {total_progress:.2f} progress in {window} steps",
                confidence=0.85,
                suggestions=["Force new approach", "Check if goal is achievable"],
            )
        
        return None

    def _check_oscillation(self) -> Optional[StabilityReport]:
        """Check for oscillating behavior (A->B->A->B pattern)."""
        window = self.config.oscillation_detection_window
        if len(self.state.action_sequence) < window:
            return None

        recent = self.state.action_sequence[-window:]
        
        # Check for ABAB pattern
        if window >= 4:
            half = window // 2
            first_half = recent[:half]
            second_half = recent[half:half*2]
            
            if first_half == second_half:
                return StabilityReport(
                    status=StabilityStatus.UNSTABLE,
                    action=StabilityAction.REPLAN,
                    reason=f"Oscillation detected: {first_half} repeating",
                    confidence=0.9,
                    suggestions=["Break the cycle with different action"],
                )
        
        return None

    def _check_loops(self) -> Optional[StabilityReport]:
        """Check for exact step repetition."""
        if len(self.state.step_hashes) < self.config.max_similar_steps:
            return None

        recent = self.state.step_hashes[-10:]
        
        # Count occurrences of each hash
        from collections import Counter
        counts = Counter(recent)
        
        for hash_val, count in counts.items():
            if count >= self.config.max_similar_steps:
                return StabilityReport(
                    status=StabilityStatus.CRITICAL,
                    action=StabilityAction.ROLLBACK,
                    reason=f"Infinite loop detected: same step repeated {count} times",
                    confidence=0.95,
                    suggestions=["Rollback and try different approach"],
                )
        
        return None

    def _check_confidence(self, confidence: float) -> Optional[StabilityReport]:
        """Check for low confidence trend."""
        if confidence < self.config.confidence_threshold:
            return StabilityReport(
                status=StabilityStatus.WARNING,
                action=StabilityAction.SLOW_DOWN,
                reason=f"Low confidence: {confidence:.2f}",
                confidence=confidence,
                suggestions=["Verify step output", "Consider alternative approach"],
            )
        
        return None

    def _check_timeout(self) -> Optional[StabilityReport]:
        """Check for execution timeout."""
        elapsed = time.time() - self.state.started_at
        max_time = 3600  # 1 hour default

        if elapsed > max_time:
            return StabilityReport(
                status=StabilityStatus.CRITICAL,
                action=StabilityAction.ABORT,
                reason=f"Execution timeout: {elapsed:.0f}s > {max_time}s",
                confidence=1.0,
            )
        
        if elapsed > max_time * 0.8:
            return StabilityReport(
                status=StabilityStatus.WARNING,
                action=StabilityAction.SLOW_DOWN,
                reason=f"Approaching timeout: {elapsed:.0f}s",
                confidence=0.8,
            )
        
        return None

    def _hash_step(self, step_type: str, step_input: Dict[str, Any]) -> str:
        """Create a hash of a step for comparison."""
        data = json.dumps({"type": step_type, "input": step_input}, sort_keys=True)
        return hashlib.md5(data.encode()).hexdigest()[:12]

    def _get_metrics(self) -> Dict[str, Any]:
        """Get current stability metrics."""
        return {
            "iteration": self.state.iteration,
            "consecutive_errors": self.state.consecutive_errors,
            "plan_revisions": self.state.plan_revisions,
            "last_progress": self.state.last_progress,
            "elapsed_seconds": time.time() - self.state.started_at,
            "checkpoints": len(self.checkpoints),
            "unique_steps": len(set(self.state.step_hashes)),
        }

    def should_create_checkpoint(self) -> bool:
        """Determine if a checkpoint should be created."""
        # Checkpoint every 5 successful iterations
        if self.state.iteration - self.state.last_checkpoint_iteration >= 5:
            return True
        
        # Checkpoint after significant progress
        if self.state.last_progress > 0.2:
            return True
        
        return False

    def get_cooldown(self) -> float:
        """Get recommended cooldown between steps."""
        # Increase cooldown if unstable
        if self.state.consecutive_errors > 0:
            return self.config.cooldown_seconds * (1 + self.state.consecutive_errors)
        
        return self.config.cooldown_seconds


# Global stabilizer instance
loop_stabilizer = LoopStabilizer()
