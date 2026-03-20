"""
Learning Loop - Outcome Feedback & Pattern Extraction (Loop 3)
==============================================================

This is the critical third loop that enables true autonomous learning:
- Loop 1: Execution (plan → act → verify → rollback) ✅ EXISTS
- Loop 2: Decision (policy-based choices) ✅ EXISTS  
- Loop 3: Learning (outcome → classify → extract patterns → adapt) ← THIS FILE

The learning loop enables agents to:
1. Track execution outcomes (success/failure/partial)
2. Extract patterns from successful executions
3. Learn from failures to avoid repeating mistakes
4. Self-optimize behavior over time
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
from collections import defaultdict
import json
import hashlib

logger = logging.getLogger(__name__)


class OutcomeType(str, Enum):
    """Classification of execution outcomes."""
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class PatternType(str, Enum):
    """Types of patterns that can be extracted."""
    ACTION_SEQUENCE = "action_sequence"  # Successful action sequences
    TOOL_COMBINATION = "tool_combination"  # Effective tool combinations
    ERROR_PATTERN = "error_pattern"  # Common error patterns
    OPTIMIZATION = "optimization"  # Performance optimizations
    CONTEXT_TRIGGER = "context_trigger"  # Context-based triggers


@dataclass
class ExecutionOutcome:
    """Record of a single execution outcome."""
    session_id: str
    agent_id: str
    goal: str
    outcome: OutcomeType
    steps_taken: int
    tokens_used: int
    duration_seconds: float
    final_output: Optional[Any] = None
    error_message: Optional[str] = None
    step_history: List[Dict[str, Any]] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "goal": self.goal,
            "outcome": self.outcome.value,
            "steps_taken": self.steps_taken,
            "tokens_used": self.tokens_used,
            "duration_seconds": self.duration_seconds,
            "final_output": self.final_output,
            "error_message": self.error_message,
            "step_count": len(self.step_history),
            "timestamp": self.timestamp,
        }


@dataclass
class LearnedPattern:
    """A pattern extracted from execution history."""
    id: str
    pattern_type: PatternType
    description: str
    pattern_data: Dict[str, Any]
    confidence: float  # 0-1, based on observation frequency
    success_rate: float  # Success rate when this pattern is applied
    observation_count: int
    last_observed: str
    applicable_goals: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.pattern_type.value,
            "description": self.description,
            "confidence": self.confidence,
            "success_rate": self.success_rate,
            "observation_count": self.observation_count,
            "last_observed": self.last_observed,
        }


class OutcomeClassifier:
    """Classifies execution outcomes based on results."""
    
    def classify(
        self,
        goal_achieved: bool,
        error_message: Optional[str],
        steps_taken: int,
        max_steps: int,
        duration_seconds: float,
        timeout_seconds: float,
    ) -> OutcomeType:
        """Classify the outcome of an execution."""
        if error_message:
            if "blocked" in error_message.lower() or "safety" in error_message.lower():
                return OutcomeType.BLOCKED
            if "cancel" in error_message.lower():
                return OutcomeType.CANCELLED
            return OutcomeType.FAILURE
        
        if duration_seconds >= timeout_seconds:
            return OutcomeType.TIMEOUT
        
        if goal_achieved:
            return OutcomeType.SUCCESS
        
        # Partial success if made progress but didn't complete
        if steps_taken > 0 and steps_taken < max_steps:
            return OutcomeType.PARTIAL_SUCCESS
        
        return OutcomeType.FAILURE


class PatternExtractor:
    """Extracts patterns from execution history."""
    
    def __init__(self, min_observations: int = 3, min_confidence: float = 0.6):
        self.min_observations = min_observations
        self.min_confidence = min_confidence
    
    def extract_action_sequences(
        self,
        successful_outcomes: List[ExecutionOutcome]
    ) -> List[LearnedPattern]:
        """Extract successful action sequences."""
        patterns = []
        sequence_counts: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "successes": 0, "goals": set()}
        )
        
        for outcome in successful_outcomes:
            if outcome.outcome != OutcomeType.SUCCESS:
                continue
            
            # Extract action sequence
            actions = [step.get("action", "unknown") for step in outcome.step_history]
            if len(actions) < 2:
                continue
            
            # Create sequence key (2-3 action windows)
            for window_size in [2, 3]:
                for i in range(len(actions) - window_size + 1):
                    seq = tuple(actions[i:i + window_size])
                    seq_key = "->".join(seq)
                    sequence_counts[seq_key]["count"] += 1
                    sequence_counts[seq_key]["successes"] += 1
                    sequence_counts[seq_key]["goals"].add(outcome.goal[:50])
        
        # Create patterns from frequent sequences
        for seq_key, data in sequence_counts.items():
            if data["count"] >= self.min_observations:
                success_rate = data["successes"] / data["count"]
                if success_rate >= self.min_confidence:
                    pattern_id = hashlib.md5(seq_key.encode()).hexdigest()[:12]
                    patterns.append(LearnedPattern(
                        id=f"seq-{pattern_id}",
                        pattern_type=PatternType.ACTION_SEQUENCE,
                        description=f"Successful action sequence: {seq_key}",
                        pattern_data={"sequence": seq_key.split("->")},
                        confidence=min(1.0, data["count"] / 10),
                        success_rate=success_rate,
                        observation_count=data["count"],
                        last_observed=datetime.now(timezone.utc).isoformat(),
                        applicable_goals=list(data["goals"])[:5],
                    ))
        
        return patterns
    
    def extract_error_patterns(
        self,
        failed_outcomes: List[ExecutionOutcome]
    ) -> List[LearnedPattern]:
        """Extract common error patterns to avoid."""
        patterns = []
        error_counts: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "last_actions": [], "contexts": []}
        )
        
        for outcome in failed_outcomes:
            if outcome.outcome not in [OutcomeType.FAILURE, OutcomeType.BLOCKED]:
                continue
            
            error_key = outcome.error_message[:100] if outcome.error_message else "unknown"
            error_counts[error_key]["count"] += 1
            
            # Track what led to the error
            if outcome.step_history:
                last_action = outcome.step_history[-1].get("action", "unknown")
                error_counts[error_key]["last_actions"].append(last_action)
        
        # Create patterns from frequent errors
        for error_key, data in error_counts.items():
            if data["count"] >= self.min_observations:
                pattern_id = hashlib.md5(error_key.encode()).hexdigest()[:12]
                
                # Find most common action leading to error
                action_counts = defaultdict(int)
                for action in data["last_actions"]:
                    action_counts[action] += 1
                common_action = max(action_counts, key=action_counts.get) if action_counts else "unknown"
                
                patterns.append(LearnedPattern(
                    id=f"err-{pattern_id}",
                    pattern_type=PatternType.ERROR_PATTERN,
                    description=f"Common error: {error_key[:50]}...",
                    pattern_data={
                        "error": error_key,
                        "common_preceding_action": common_action,
                        "occurrence_count": data["count"],
                    },
                    confidence=min(1.0, data["count"] / 5),
                    success_rate=0.0,
                    observation_count=data["count"],
                    last_observed=datetime.now(timezone.utc).isoformat(),
                ))
        
        return patterns
    
    def extract_tool_combinations(
        self,
        successful_outcomes: List[ExecutionOutcome]
    ) -> List[LearnedPattern]:
        """Extract effective tool combinations."""
        patterns = []
        combo_counts: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "avg_steps": 0, "goals": set()}
        )
        
        for outcome in successful_outcomes:
            if outcome.outcome != OutcomeType.SUCCESS:
                continue
            
            # Extract unique tools used
            tools = set()
            for step in outcome.step_history:
                if step.get("action") == "tool_call":
                    tool_name = step.get("tool_name", "unknown")
                    tools.add(tool_name)
            
            if len(tools) >= 2:
                combo_key = ",".join(sorted(tools))
                combo_counts[combo_key]["count"] += 1
                combo_counts[combo_key]["avg_steps"] += outcome.steps_taken
                combo_counts[combo_key]["goals"].add(outcome.goal[:50])
        
        # Create patterns from frequent combinations
        for combo_key, data in combo_counts.items():
            if data["count"] >= self.min_observations:
                avg_steps = data["avg_steps"] / data["count"]
                pattern_id = hashlib.md5(combo_key.encode()).hexdigest()[:12]
                
                patterns.append(LearnedPattern(
                    id=f"combo-{pattern_id}",
                    pattern_type=PatternType.TOOL_COMBINATION,
                    description=f"Effective tool combo: {combo_key}",
                    pattern_data={
                        "tools": combo_key.split(","),
                        "avg_steps_to_completion": avg_steps,
                    },
                    confidence=min(1.0, data["count"] / 10),
                    success_rate=1.0,  # Only from successes
                    observation_count=data["count"],
                    last_observed=datetime.now(timezone.utc).isoformat(),
                    applicable_goals=list(data["goals"])[:5],
                ))
        
        return patterns


class LearningMemory:
    """Stores and retrieves learned patterns."""
    
    def __init__(self, max_patterns: int = 1000):
        self.max_patterns = max_patterns
        self._patterns: Dict[str, LearnedPattern] = {}
        self._outcomes: List[ExecutionOutcome] = []
        self._max_outcomes = 10000
    
    def record_outcome(self, outcome: ExecutionOutcome):
        """Record an execution outcome."""
        self._outcomes.append(outcome)
        
        # Trim if too many
        if len(self._outcomes) > self._max_outcomes:
            self._outcomes = self._outcomes[-self._max_outcomes:]
        
        logger.info(f"Recorded outcome: {outcome.outcome.value} for session {outcome.session_id}")
    
    def add_pattern(self, pattern: LearnedPattern):
        """Add or update a learned pattern."""
        existing = self._patterns.get(pattern.id)
        if existing:
            # Update existing pattern
            existing.observation_count += pattern.observation_count
            existing.confidence = min(1.0, (existing.confidence + pattern.confidence) / 2)
            existing.last_observed = pattern.last_observed
        else:
            self._patterns[pattern.id] = pattern
        
        # Trim if too many patterns (keep highest confidence)
        if len(self._patterns) > self.max_patterns:
            sorted_patterns = sorted(
                self._patterns.values(),
                key=lambda p: p.confidence,
                reverse=True
            )
            self._patterns = {p.id: p for p in sorted_patterns[:self.max_patterns]}
    
    def get_patterns(
        self,
        pattern_type: Optional[PatternType] = None,
        min_confidence: float = 0.0,
        goal_hint: Optional[str] = None,
    ) -> List[LearnedPattern]:
        """Get relevant patterns."""
        patterns = list(self._patterns.values())
        
        if pattern_type:
            patterns = [p for p in patterns if p.pattern_type == pattern_type]
        
        if min_confidence > 0:
            patterns = [p for p in patterns if p.confidence >= min_confidence]
        
        if goal_hint:
            # Prioritize patterns applicable to similar goals
            def relevance(p: LearnedPattern) -> float:
                for g in p.applicable_goals:
                    if goal_hint.lower() in g.lower() or g.lower() in goal_hint.lower():
                        return p.confidence + 0.5
                return p.confidence
            patterns.sort(key=relevance, reverse=True)
        
        return patterns
    
    def get_outcomes(
        self,
        agent_id: Optional[str] = None,
        outcome_type: Optional[OutcomeType] = None,
        limit: int = 100,
    ) -> List[ExecutionOutcome]:
        """Get recorded outcomes."""
        outcomes = self._outcomes
        
        if agent_id:
            outcomes = [o for o in outcomes if o.agent_id == agent_id]
        
        if outcome_type:
            outcomes = [o for o in outcomes if o.outcome == outcome_type]
        
        return outcomes[-limit:]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get learning statistics."""
        total_outcomes = len(self._outcomes)
        if total_outcomes == 0:
            return {"total_outcomes": 0, "patterns_learned": 0}
        
        success_count = sum(1 for o in self._outcomes if o.outcome == OutcomeType.SUCCESS)
        
        return {
            "total_outcomes": total_outcomes,
            "success_rate": success_count / total_outcomes,
            "patterns_learned": len(self._patterns),
            "patterns_by_type": {
                pt.value: sum(1 for p in self._patterns.values() if p.pattern_type == pt)
                for pt in PatternType
            },
            "avg_confidence": sum(p.confidence for p in self._patterns.values()) / len(self._patterns) if self._patterns else 0,
        }


class LearningLoop:
    """
    The main learning loop that enables autonomous improvement.
    
    This is Loop 3 - the learning/adaptation loop that completes
    the trifecta needed for true autonomy.
    """
    
    def __init__(
        self,
        memory: Optional[LearningMemory] = None,
        classifier: Optional[OutcomeClassifier] = None,
        extractor: Optional[PatternExtractor] = None,
    ):
        self.memory = memory or LearningMemory()
        self.classifier = classifier or OutcomeClassifier()
        self.extractor = extractor or PatternExtractor()
        
        self._learning_enabled = True
        self._extraction_interval = 50  # Extract patterns every N outcomes
        self._outcomes_since_extraction = 0
    
    def record_execution(
        self,
        session_id: str,
        agent_id: str,
        goal: str,
        goal_achieved: bool,
        steps_taken: int,
        tokens_used: int,
        duration_seconds: float,
        step_history: List[Dict[str, Any]],
        error_message: Optional[str] = None,
        final_output: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
        max_steps: int = 50,
        timeout_seconds: float = 3600,
    ) -> ExecutionOutcome:
        """
        Record an execution and trigger learning.
        
        This is the main entry point for the learning loop.
        """
        # Classify outcome
        outcome_type = self.classifier.classify(
            goal_achieved=goal_achieved,
            error_message=error_message,
            steps_taken=steps_taken,
            max_steps=max_steps,
            duration_seconds=duration_seconds,
            timeout_seconds=timeout_seconds,
        )
        
        # Create outcome record
        outcome = ExecutionOutcome(
            session_id=session_id,
            agent_id=agent_id,
            goal=goal,
            outcome=outcome_type,
            steps_taken=steps_taken,
            tokens_used=tokens_used,
            duration_seconds=duration_seconds,
            final_output=final_output,
            error_message=error_message,
            step_history=step_history,
            context=context or {},
        )
        
        # Record in memory
        self.memory.record_outcome(outcome)
        
        # Trigger pattern extraction periodically
        self._outcomes_since_extraction += 1
        if self._outcomes_since_extraction >= self._extraction_interval:
            self._extract_patterns()
            self._outcomes_since_extraction = 0
        
        return outcome
    
    def _extract_patterns(self):
        """Extract patterns from recent outcomes."""
        if not self._learning_enabled:
            return
        
        outcomes = self.memory.get_outcomes(limit=500)
        successful = [o for o in outcomes if o.outcome == OutcomeType.SUCCESS]
        failed = [o for o in outcomes if o.outcome in [OutcomeType.FAILURE, OutcomeType.BLOCKED]]
        
        # Extract different pattern types
        for pattern in self.extractor.extract_action_sequences(successful):
            self.memory.add_pattern(pattern)
        
        for pattern in self.extractor.extract_error_patterns(failed):
            self.memory.add_pattern(pattern)
        
        for pattern in self.extractor.extract_tool_combinations(successful):
            self.memory.add_pattern(pattern)
        
        logger.info(f"Extracted patterns. Total: {len(self.memory._patterns)}")
    
    def get_recommendations(
        self,
        goal: str,
        available_tools: List[str],
    ) -> Dict[str, Any]:
        """
        Get recommendations based on learned patterns.
        
        This is how learning improves future executions.
        """
        recommendations = {
            "suggested_sequences": [],
            "effective_tool_combos": [],
            "patterns_to_avoid": [],
            "confidence": 0.0,
        }
        
        # Get relevant patterns
        sequences = self.memory.get_patterns(
            pattern_type=PatternType.ACTION_SEQUENCE,
            min_confidence=0.5,
            goal_hint=goal,
        )[:5]
        
        combos = self.memory.get_patterns(
            pattern_type=PatternType.TOOL_COMBINATION,
            min_confidence=0.5,
            goal_hint=goal,
        )[:3]
        
        errors = self.memory.get_patterns(
            pattern_type=PatternType.ERROR_PATTERN,
            min_confidence=0.5,
        )[:5]
        
        # Build recommendations
        recommendations["suggested_sequences"] = [
            {
                "sequence": p.pattern_data.get("sequence", []),
                "confidence": p.confidence,
                "success_rate": p.success_rate,
            }
            for p in sequences
        ]
        
        recommendations["effective_tool_combos"] = [
            {
                "tools": p.pattern_data.get("tools", []),
                "avg_steps": p.pattern_data.get("avg_steps_to_completion", 0),
                "confidence": p.confidence,
            }
            for p in combos
            if any(t in available_tools for t in p.pattern_data.get("tools", []))
        ]
        
        recommendations["patterns_to_avoid"] = [
            {
                "error": p.pattern_data.get("error", ""),
                "common_cause": p.pattern_data.get("common_preceding_action", ""),
                "occurrences": p.observation_count,
            }
            for p in errors
        ]
        
        # Calculate overall confidence
        all_patterns = sequences + combos
        if all_patterns:
            recommendations["confidence"] = sum(p.confidence for p in all_patterns) / len(all_patterns)
        
        return recommendations
    
    def get_stats(self) -> Dict[str, Any]:
        """Get learning loop statistics."""
        stats = self.memory.get_stats()
        stats["learning_enabled"] = self._learning_enabled
        stats["outcomes_since_extraction"] = self._outcomes_since_extraction
        return stats
    
    def enable_learning(self):
        """Enable the learning loop."""
        self._learning_enabled = True
        logger.info("Learning loop enabled")
    
    def disable_learning(self):
        """Disable the learning loop."""
        self._learning_enabled = False
        logger.info("Learning loop disabled")


# Singleton instance
_learning_loop: Optional[LearningLoop] = None


def get_learning_loop() -> LearningLoop:
    """Get the singleton learning loop instance."""
    global _learning_loop
    if _learning_loop is None:
        _learning_loop = LearningLoop()
    return _learning_loop


def init_learning_loop(
    max_patterns: int = 1000,
    min_observations: int = 3,
    min_confidence: float = 0.6,
) -> LearningLoop:
    """Initialize the learning loop with custom settings."""
    global _learning_loop
    _learning_loop = LearningLoop(
        memory=LearningMemory(max_patterns=max_patterns),
        extractor=PatternExtractor(
            min_observations=min_observations,
            min_confidence=min_confidence,
        ),
    )
    return _learning_loop
