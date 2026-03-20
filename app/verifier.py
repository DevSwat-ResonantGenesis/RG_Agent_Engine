"""Dedicated Verifier Agent for autonomous pipeline quality control.

The Verifier Agent is a critical reliability multiplier that:
1. Re-evaluates each step output for correctness
2. Validates success criteria are met
3. Catches silent failures and hallucinations
4. Blocks bad tool calls before execution
5. Requests modifications when quality is insufficient
6. Detects stagnation and infinite loops
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import settings


class VerificationResult(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_MODIFICATION = "needs_modification"
    STAGNATION_DETECTED = "stagnation_detected"
    HALLUCINATION_DETECTED = "hallucination_detected"
    LOOP_DETECTED = "loop_detected"


@dataclass
class VerificationReport:
    """Report from verification check."""
    result: VerificationResult
    confidence: float  # 0.0 to 1.0
    reasoning: str
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    should_rollback: bool = False
    checkpoint_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class VerifierAgent:
    """
    Dedicated verification agent that validates autonomous execution quality.
    
    This agent runs independently and can:
    - Verify step outputs match expected outcomes
    - Detect hallucinations in LLM responses
    - Identify stagnation (no progress)
    - Catch infinite loops
    - Validate tool call safety
    - Assess overall task progress
    """

    VERIFICATION_PROMPT = """You are a Verification Agent. Your job is to validate the quality and correctness of autonomous agent actions.

TASK GOAL: {goal}

CURRENT STEP: {step_number}
STEP TYPE: {step_type}
STEP INPUT: {step_input}
STEP OUTPUT: {step_output}

PREVIOUS STEPS SUMMARY:
{history_summary}

EXPECTED OUTCOME: {expected_outcome}

Analyze this step and determine:
1. Did the step achieve its intended purpose?
2. Is the output correct and complete?
3. Are there any signs of hallucination (made-up data, false claims)?
4. Is there evidence of stagnation (same actions repeated)?
5. Does this step move toward the goal?

Respond in JSON:
{{
    "result": "approved|rejected|needs_modification|stagnation_detected|hallucination_detected|loop_detected",
    "confidence": 0.0-1.0,
    "reasoning": "Your analysis",
    "issues": ["list of issues found"],
    "suggestions": ["list of suggestions for improvement"],
    "should_rollback": true/false,
    "progress_toward_goal": 0.0-1.0
}}"""

    HALLUCINATION_CHECK_PROMPT = """Analyze this LLM response for potential hallucinations:

RESPONSE: {response}

CONTEXT: {context}

Check for:
1. Made-up file paths that don't exist
2. Invented function names or APIs
3. False claims about code behavior
4. Non-existent libraries or packages
5. Fabricated error messages
6. Incorrect syntax for the language

Respond in JSON:
{{
    "has_hallucination": true/false,
    "confidence": 0.0-1.0,
    "hallucination_type": "none|file_path|api|code|library|error|syntax",
    "details": "explanation"
}}"""

    STAGNATION_CHECK_PROMPT = """Analyze these recent steps for stagnation patterns:

RECENT STEPS:
{recent_steps}

GOAL: {goal}

Check for:
1. Repeated identical actions
2. Oscillating between two states
3. No meaningful progress
4. Circular reasoning
5. Stuck in error-retry loop

Respond in JSON:
{{
    "is_stagnating": true/false,
    "stagnation_type": "none|repetition|oscillation|no_progress|circular|error_loop",
    "confidence": 0.0-1.0,
    "recommendation": "continue|replan|escalate|abort"
}}"""

    def __init__(self):
        self.verification_history: List[VerificationReport] = []
        self.step_hashes: List[str] = []  # For loop detection
        self.confidence_threshold = 0.7
        self.max_consecutive_failures = 3
        self.consecutive_failures = 0

    async def verify_step(
        self,
        goal: str,
        step_number: int,
        step_type: str,
        step_input: Dict[str, Any],
        step_output: Dict[str, Any],
        history: List[Dict[str, Any]],
        expected_outcome: Optional[str] = None,
    ) -> VerificationReport:
        """Verify a single step's output."""
        start_time = time.time()

        # Quick checks first
        loop_check = self._check_for_loops(step_input, step_output)
        if loop_check:
            return loop_check

        # Build history summary
        history_summary = self._summarize_history(history[-5:])  # Last 5 steps

        prompt = self.VERIFICATION_PROMPT.format(
            goal=goal,
            step_number=step_number,
            step_type=step_type,
            step_input=json.dumps(step_input, indent=2)[:2000],
            step_output=json.dumps(step_output, indent=2)[:2000],
            history_summary=history_summary,
            expected_outcome=expected_outcome or "Step completion",
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{settings.LLM_SERVICE_URL}/llm/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": prompt}],
                        "model": settings.DEFAULT_MODEL,
                        "max_tokens": 1024,
                        "response_format": {"type": "json_object"},
                    },
                )

                if response.status_code == 200:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    result_data = json.loads(content)

                    report = VerificationReport(
                        result=VerificationResult(result_data.get("result", "approved")),
                        confidence=result_data.get("confidence", 0.5),
                        reasoning=result_data.get("reasoning", ""),
                        issues=result_data.get("issues", []),
                        suggestions=result_data.get("suggestions", []),
                        should_rollback=result_data.get("should_rollback", False),
                    )

                    # Track consecutive failures
                    if report.result != VerificationResult.APPROVED:
                        self.consecutive_failures += 1
                    else:
                        self.consecutive_failures = 0

                    self.verification_history.append(report)
                    return report

        except Exception as e:
            # On verification failure, be conservative
            return VerificationReport(
                result=VerificationResult.NEEDS_MODIFICATION,
                confidence=0.3,
                reasoning=f"Verification failed: {str(e)}",
                issues=["Verification system error"],
                suggestions=["Retry step with caution"],
            )

        # Default to needs modification if we can't verify
        return VerificationReport(
            result=VerificationResult.NEEDS_MODIFICATION,
            confidence=0.5,
            reasoning="Could not complete verification",
            issues=["Verification incomplete"],
        )

    async def check_hallucination(
        self,
        response: str,
        context: Dict[str, Any],
    ) -> Tuple[bool, float, str]:
        """Check if an LLM response contains hallucinations."""
        prompt = self.HALLUCINATION_CHECK_PROMPT.format(
            response=response[:3000],
            context=json.dumps(context, indent=2)[:1000],
        )

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{settings.LLM_SERVICE_URL}/llm/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": prompt}],
                        "model": settings.DEFAULT_MODEL,
                        "max_tokens": 512,
                        "response_format": {"type": "json_object"},
                    },
                )

                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    result = json.loads(content)

                    return (
                        result.get("has_hallucination", False),
                        result.get("confidence", 0.5),
                        result.get("details", ""),
                    )

        except Exception:
            pass

        return False, 0.5, "Could not check for hallucinations"

    async def check_stagnation(
        self,
        recent_steps: List[Dict[str, Any]],
        goal: str,
    ) -> Tuple[bool, str, str]:
        """Check if the agent is stagnating."""
        if len(recent_steps) < 3:
            return False, "none", "Not enough steps to detect stagnation"

        prompt = self.STAGNATION_CHECK_PROMPT.format(
            recent_steps=json.dumps(recent_steps[-10:], indent=2)[:3000],
            goal=goal,
        )

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{settings.LLM_SERVICE_URL}/llm/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": prompt}],
                        "model": settings.DEFAULT_MODEL,
                        "max_tokens": 512,
                        "response_format": {"type": "json_object"},
                    },
                )

                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    result = json.loads(content)

                    return (
                        result.get("is_stagnating", False),
                        result.get("stagnation_type", "none"),
                        result.get("recommendation", "continue"),
                    )

        except Exception:
            pass

        return False, "none", "continue"

    def _check_for_loops(
        self,
        step_input: Dict[str, Any],
        step_output: Dict[str, Any],
    ) -> Optional[VerificationReport]:
        """Quick check for obvious loops using hashing."""
        import hashlib

        # Create a hash of the step
        step_str = json.dumps({"input": step_input, "output": step_output}, sort_keys=True)
        step_hash = hashlib.md5(step_str.encode()).hexdigest()

        # Check if we've seen this exact step before
        if step_hash in self.step_hashes[-10:]:
            count = self.step_hashes[-10:].count(step_hash)
            if count >= 2:
                return VerificationReport(
                    result=VerificationResult.LOOP_DETECTED,
                    confidence=0.95,
                    reasoning=f"Detected repeated step pattern ({count} occurrences)",
                    issues=["Infinite loop detected"],
                    suggestions=["Force replan with different approach"],
                    should_rollback=True,
                )

        self.step_hashes.append(step_hash)
        
        # Keep only last 50 hashes
        if len(self.step_hashes) > 50:
            self.step_hashes = self.step_hashes[-50:]

        return None

    def _summarize_history(self, history: List[Dict[str, Any]]) -> str:
        """Create a brief summary of recent history."""
        if not history:
            return "No previous steps"

        summaries = []
        for i, step in enumerate(history):
            step_type = step.get("step_type", "unknown")
            success = "✓" if step.get("success", False) else "✗"
            summary = f"Step {i+1} ({step_type}): {success}"
            if step.get("error"):
                summary += f" - Error: {step.get('error')[:50]}"
            summaries.append(summary)

        return "\n".join(summaries)

    async def validate_tool_call(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """Validate a tool call before execution."""
        # Quick safety checks
        dangerous_patterns = [
            ("rm -rf", "Dangerous recursive delete"),
            ("DROP TABLE", "SQL table deletion"),
            ("DELETE FROM", "SQL data deletion"),
            ("format", "Disk formatting"),
            ("shutdown", "System shutdown"),
            ("reboot", "System reboot"),
        ]

        input_str = json.dumps(tool_input).lower()
        for pattern, reason in dangerous_patterns:
            if pattern.lower() in input_str:
                return False, f"Blocked: {reason}"

        # Check for suspicious file paths
        if "path" in tool_input:
            path = tool_input["path"]
            if path.startswith("/etc") or path.startswith("/sys") or path.startswith("/proc"):
                return False, "Blocked: Access to system directories"
            if ".." in path:
                return False, "Blocked: Path traversal attempt"

        return True, "Tool call approved"

    def get_verification_stats(self) -> Dict[str, Any]:
        """Get statistics about verification history."""
        if not self.verification_history:
            return {"total": 0}

        results = [r.result.value for r in self.verification_history]
        confidences = [r.confidence for r in self.verification_history]

        return {
            "total": len(self.verification_history),
            "approved": results.count("approved"),
            "rejected": results.count("rejected"),
            "needs_modification": results.count("needs_modification"),
            "loops_detected": results.count("loop_detected"),
            "hallucinations_detected": results.count("hallucination_detected"),
            "stagnations_detected": results.count("stagnation_detected"),
            "average_confidence": sum(confidences) / len(confidences),
            "consecutive_failures": self.consecutive_failures,
        }

    def should_abort(self) -> Tuple[bool, str]:
        """Determine if the autonomous loop should abort."""
        if self.consecutive_failures >= self.max_consecutive_failures:
            return True, f"Too many consecutive failures ({self.consecutive_failures})"

        # Check for repeated loops
        loop_count = sum(
            1 for r in self.verification_history[-10:]
            if r.result == VerificationResult.LOOP_DETECTED
        )
        if loop_count >= 2:
            return True, "Multiple loop detections"

        # Check for low confidence trend
        recent_confidences = [r.confidence for r in self.verification_history[-5:]]
        if recent_confidences and sum(recent_confidences) / len(recent_confidences) < 0.3:
            return True, "Consistently low verification confidence"

        return False, ""

    def reset(self):
        """Reset verifier state for new session."""
        self.verification_history = []
        self.step_hashes = []
        self.consecutive_failures = 0


# Global verifier instance
verifier_agent = VerifierAgent()
