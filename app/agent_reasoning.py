"""
AGENT REFLECTION AND REASONING
==============================

Deep reasoning and self-reflection for autonomous agents.
Enables agents to think deeply, reflect on actions, and improve decisions.

Features:
- Chain-of-thought reasoning
- Self-reflection after actions
- Metacognitive awareness
- Decision justification
- Error analysis and correction
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
import json

import httpx

from .agent_memory import get_agent_memory, get_agent_learning, MemoryType

logger = logging.getLogger(__name__)


class ReflectionType(Enum):
    PRE_ACTION = "pre_action"      # Before taking action
    POST_ACTION = "post_action"    # After action completes
    ERROR = "error"                # After error occurs
    GOAL_PROGRESS = "goal_progress" # Periodic goal check
    METACOGNITIVE = "metacognitive" # Self-awareness


@dataclass
class ThoughtChain:
    """A chain of thoughts leading to a conclusion."""
    id: str
    thoughts: List[str]
    conclusion: str
    confidence: float
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Reflection:
    """A reflection on an action or situation."""
    id: str
    reflection_type: ReflectionType
    context: Dict[str, Any]
    insights: List[str]
    lessons_learned: List[str]
    action_adjustments: List[str]
    confidence: float
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ReasoningEngine:
    """
    Enables deep reasoning for autonomous agents.
    """
    
    def __init__(self, llm_service_url: str = None):
        self.llm_service_url = llm_service_url or "http://llm_service:8000"
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client
    
    async def chain_of_thought(
        self,
        agent_id: str,
        problem: str,
        context: Dict[str, Any] = None,
    ) -> ThoughtChain:
        """Generate a chain of thought for a problem."""
        client = await self._get_client()
        
        prompt = f"""Think through this problem step by step. Show your reasoning clearly.

PROBLEM: {problem}

CONTEXT: {json.dumps(context or {})}

Think step by step:
1. What is the core issue?
2. What information do I have?
3. What are possible approaches?
4. What are the trade-offs?
5. What is the best solution?

Format your response as:
THOUGHT 1: [first thought]
THOUGHT 2: [second thought]
...
CONCLUSION: [final conclusion]
CONFIDENCE: [0-1 score]"""

        try:
            response = await client.post(
                f"{self.llm_service_url}/llm/chat/completions",
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                # Parse thoughts
                thoughts = []
                conclusion = ""
                confidence = 0.7
                
                for line in content.split("\n"):
                    if line.startswith("THOUGHT"):
                        thoughts.append(line.split(":", 1)[1].strip() if ":" in line else line)
                    elif line.startswith("CONCLUSION:"):
                        conclusion = line.split(":", 1)[1].strip()
                    elif line.startswith("CONFIDENCE:"):
                        try:
                            confidence = float(line.split(":")[1].strip())
                        except:
                            pass
                
                chain = ThoughtChain(
                    id=str(uuid4()),
                    thoughts=thoughts,
                    conclusion=conclusion or content,
                    confidence=confidence,
                )
                
                # Store in memory
                memory = get_agent_memory(agent_id)
                memory.remember(
                    content={"problem": problem, "chain": chain.thoughts, "conclusion": chain.conclusion},
                    memory_type=MemoryType.EPISODIC,
                    importance=0.7,
                )
                
                return chain
                
        except Exception as e:
            logger.error(f"Chain of thought failed: {e}")
        
        return ThoughtChain(
            id=str(uuid4()),
            thoughts=["Unable to generate thoughts"],
            conclusion="Reasoning failed",
            confidence=0.1,
        )
    
    async def reflect(
        self,
        agent_id: str,
        reflection_type: ReflectionType,
        context: Dict[str, Any],
    ) -> Reflection:
        """Generate a reflection on a situation."""
        client = await self._get_client()
        
        prompts = {
            ReflectionType.PRE_ACTION: f"""Before taking this action, reflect:

ACTION PLANNED: {context.get('action', 'unknown')}
CONTEXT: {json.dumps(context)}

Consider:
1. What could go wrong?
2. What assumptions am I making?
3. Is this the best approach?
4. What should I watch for?

Respond with:
INSIGHTS: [list of insights]
CONCERNS: [potential issues]
ADJUSTMENTS: [recommended changes]""",

            ReflectionType.POST_ACTION: f"""Reflect on this completed action:

ACTION TAKEN: {context.get('action', 'unknown')}
RESULT: {context.get('result', 'unknown')}
SUCCESS: {context.get('success', 'unknown')}

Consider:
1. Did it work as expected?
2. What did I learn?
3. What would I do differently?

Respond with:
INSIGHTS: [list of insights]
LESSONS: [what was learned]
IMPROVEMENTS: [future adjustments]""",

            ReflectionType.ERROR: f"""Analyze this error:

ERROR: {context.get('error', 'unknown')}
CONTEXT: {json.dumps(context)}

Consider:
1. What caused this?
2. How can I recover?
3. How can I prevent this?

Respond with:
ROOT_CAUSE: [cause analysis]
RECOVERY: [recovery steps]
PREVENTION: [future prevention]""",

            ReflectionType.GOAL_PROGRESS: f"""Assess progress toward goal:

GOAL: {context.get('goal', 'unknown')}
PROGRESS: {context.get('progress', 'unknown')}
ACTIONS_TAKEN: {context.get('actions', [])}

Consider:
1. Am I on track?
2. What's blocking progress?
3. Should I adjust my approach?

Respond with:
STATUS: [on_track/behind/blocked]
BLOCKERS: [current blockers]
ADJUSTMENTS: [recommended changes]""",

            ReflectionType.METACOGNITIVE: f"""Reflect on your own thinking:

RECENT_DECISIONS: {context.get('decisions', [])}
PERFORMANCE: {context.get('performance', 'unknown')}

Consider:
1. Am I thinking clearly?
2. What biases might affect me?
3. How can I think better?

Respond with:
THINKING_QUALITY: [assessment]
BIASES: [potential biases]
IMPROVEMENTS: [how to think better]""",
        }
        
        prompt = prompts.get(reflection_type, prompts[ReflectionType.POST_ACTION])
        
        try:
            response = await client.post(
                f"{self.llm_service_url}/llm/chat/completions",
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.4,
                },
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                # Parse reflection
                insights = []
                lessons = []
                adjustments = []
                
                current_section = None
                for line in content.split("\n"):
                    line = line.strip()
                    if "INSIGHT" in line.upper() or "STATUS" in line.upper():
                        current_section = "insights"
                    elif "LESSON" in line.upper() or "ROOT_CAUSE" in line.upper():
                        current_section = "lessons"
                    elif "ADJUST" in line.upper() or "IMPROVE" in line.upper() or "RECOVERY" in line.upper():
                        current_section = "adjustments"
                    elif line.startswith("-") or line.startswith("•"):
                        item = line.lstrip("-•").strip()
                        if current_section == "insights":
                            insights.append(item)
                        elif current_section == "lessons":
                            lessons.append(item)
                        elif current_section == "adjustments":
                            adjustments.append(item)
                
                reflection = Reflection(
                    id=str(uuid4()),
                    reflection_type=reflection_type,
                    context=context,
                    insights=insights or [content[:200]],
                    lessons_learned=lessons,
                    action_adjustments=adjustments,
                    confidence=0.7,
                )
                
                # Store in memory
                memory = get_agent_memory(agent_id)
                memory.remember(
                    content={
                        "type": reflection_type.value,
                        "insights": insights,
                        "lessons": lessons,
                    },
                    memory_type=MemoryType.SEMANTIC,
                    importance=0.8,
                )
                
                # Learn patterns
                if lessons:
                    learning = get_agent_learning(agent_id)
                    for lesson in lessons:
                        memory.learn_pattern(
                            pattern_type="lesson",
                            trigger=context,
                            outcome={"lesson": lesson},
                            confidence=0.6,
                        )
                
                return reflection
                
        except Exception as e:
            logger.error(f"Reflection failed: {e}")
        
        return Reflection(
            id=str(uuid4()),
            reflection_type=reflection_type,
            context=context,
            insights=["Reflection failed"],
            lessons_learned=[],
            action_adjustments=[],
            confidence=0.1,
        )
    
    async def justify_decision(
        self,
        agent_id: str,
        decision: str,
        alternatives: List[str],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate justification for a decision."""
        client = await self._get_client()
        
        prompt = f"""Justify this decision:

DECISION: {decision}
ALTERNATIVES CONSIDERED: {json.dumps(alternatives)}
CONTEXT: {json.dumps(context)}

Explain:
1. Why this decision is best
2. Why alternatives were rejected
3. What risks remain
4. What conditions would change the decision

Format:
JUSTIFICATION: [main reasoning]
REJECTED_ALTERNATIVES: [why each was rejected]
RISKS: [remaining risks]
REVERSIBILITY: [when to reconsider]"""

        try:
            response = await client.post(
                f"{self.llm_service_url}/llm/chat/completions",
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                return {
                    "decision": decision,
                    "justification": content,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                
        except Exception as e:
            logger.error(f"Decision justification failed: {e}")
        
        return {
            "decision": decision,
            "justification": "Unable to generate justification",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


class MetacognitiveMonitor:
    """
    Monitors agent's own cognitive processes.
    """
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.reasoning = ReasoningEngine()
        self.decision_history: List[Dict[str, Any]] = []
        self.reflection_history: List[Reflection] = []
    
    async def monitor_decision(
        self,
        decision: str,
        context: Dict[str, Any],
    ):
        """Monitor a decision for quality."""
        # Pre-action reflection
        pre_reflection = await self.reasoning.reflect(
            self.agent_id,
            ReflectionType.PRE_ACTION,
            {"action": decision, **context},
        )
        
        self.decision_history.append({
            "decision": decision,
            "context": context,
            "pre_reflection": pre_reflection.insights,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        
        return pre_reflection
    
    async def review_outcome(
        self,
        decision: str,
        result: Any,
        success: bool,
    ):
        """Review the outcome of a decision."""
        # Post-action reflection
        post_reflection = await self.reasoning.reflect(
            self.agent_id,
            ReflectionType.POST_ACTION,
            {"action": decision, "result": result, "success": success},
        )
        
        self.reflection_history.append(post_reflection)
        
        # If failure, do error analysis
        if not success:
            error_reflection = await self.reasoning.reflect(
                self.agent_id,
                ReflectionType.ERROR,
                {"action": decision, "error": str(result)},
            )
            self.reflection_history.append(error_reflection)
        
        return post_reflection
    
    async def periodic_metacognition(self):
        """Periodic self-reflection on thinking quality."""
        recent_decisions = self.decision_history[-10:]
        success_rate = sum(1 for d in recent_decisions if d.get("success", False)) / max(len(recent_decisions), 1)
        
        reflection = await self.reasoning.reflect(
            self.agent_id,
            ReflectionType.METACOGNITIVE,
            {
                "decisions": [d["decision"] for d in recent_decisions],
                "performance": f"{success_rate:.0%} success rate",
            },
        )
        
        return reflection


# Global instances
_reasoning_engines: Dict[str, ReasoningEngine] = {}
_metacognitive_monitors: Dict[str, MetacognitiveMonitor] = {}


def get_reasoning_engine(agent_id: str = None) -> ReasoningEngine:
    if agent_id and agent_id not in _reasoning_engines:
        _reasoning_engines[agent_id] = ReasoningEngine()
    return _reasoning_engines.get(agent_id, ReasoningEngine())


def get_metacognitive_monitor(agent_id: str) -> MetacognitiveMonitor:
    if agent_id not in _metacognitive_monitors:
        _metacognitive_monitors[agent_id] = MetacognitiveMonitor(agent_id)
    return _metacognitive_monitors[agent_id]
