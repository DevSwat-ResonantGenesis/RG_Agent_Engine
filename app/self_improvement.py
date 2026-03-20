import sys
from pathlib import Path

# Add shared modules to path
SHARED_PATH = Path(__file__).resolve().parents[2] / "shared"
if str(SHARED_PATH) not in sys.path:
    sys.path.insert(0, str(SHARED_PATH))

# Add service root to path
SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

"""
AGENT SELF-IMPROVEMENT LOOP
===========================

Enables agents to improve themselves autonomously.
Agents analyze their performance and optimize their behavior.

Features:
- Performance analysis
- Strategy optimization
- Prompt improvement
- Capability expansion
- Autonomous evolution
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from uuid import uuid4
import json

import httpx

from .agent_memory import get_agent_learning, AgentLearning

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Agent performance metrics."""
    agent_id: str
    success_rate: float
    avg_task_duration: float
    error_rate: float
    learning_rate: float
    skill_levels: Dict[str, float]
    improvement_trend: float  # Positive = improving


@dataclass
class ImprovementAction:
    """An action to improve agent performance."""
    id: str
    action_type: str  # prompt_update, strategy_change, skill_focus, capability_add
    description: str
    target_metric: str
    expected_improvement: float
    applied: bool = False
    result: Optional[Dict[str, Any]] = None


class SelfImprovementLoop:
    """
    Autonomous self-improvement for agents.
    Agents continuously analyze and improve themselves.
    """
    
    IMPROVEMENT_INTERVAL = 300  # 5 minutes
    
    def __init__(self, llm_service_url: str = None):
        self.llm_service_url = llm_service_url or "http://llm_service:8000"
        
        # Track improvements per agent
        self.improvements: Dict[str, List[ImprovementAction]] = {}
        self.metrics_history: Dict[str, List[PerformanceMetrics]] = {}
        
        # Agent configurations that can be improved
        self.agent_configs: Dict[str, Dict[str, Any]] = {}
        
        self._running = False
        self._task = None
    
    async def start(self):
        """Start the self-improvement loop."""
        self._running = True
        self._task = asyncio.create_task(self._improvement_loop())
        logger.info("Self-Improvement Loop started")
    
    async def stop(self):
        """Stop the self-improvement loop."""
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("Self-Improvement Loop stopped")
    
    async def _improvement_loop(self):
        """Main improvement loop."""
        while self._running:
            try:
                # Analyze all agents
                for agent_id in list(self.agent_configs.keys()):
                    await self._analyze_and_improve(agent_id)
                
                await asyncio.sleep(self.IMPROVEMENT_INTERVAL)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Improvement loop error: {e}")
    
    async def register_agent(self, agent_id: str, config: Dict[str, Any]):
        """Register an agent for self-improvement."""
        self.agent_configs[agent_id] = config
        self.improvements[agent_id] = []
        self.metrics_history[agent_id] = []
        logger.info(f"Registered agent {agent_id} for self-improvement")
    
    async def _analyze_and_improve(self, agent_id: str):
        """Analyze agent performance and generate improvements."""
        # Get learning data
        learning = get_agent_learning(agent_id)
        
        # Calculate metrics
        metrics = self._calculate_metrics(agent_id, learning)
        self.metrics_history[agent_id].append(metrics)
        
        # Keep only recent history
        if len(self.metrics_history[agent_id]) > 100:
            self.metrics_history[agent_id] = self.metrics_history[agent_id][-50:]
        
        # Check if improvement needed
        if metrics.success_rate < 0.7 or metrics.improvement_trend < 0:
            improvements = await self._generate_improvements(agent_id, metrics, learning)
            
            for improvement in improvements:
                await self._apply_improvement(agent_id, improvement)
    
    def _calculate_metrics(self, agent_id: str, learning: AgentLearning) -> PerformanceMetrics:
        """Calculate performance metrics."""
        # Get historical metrics
        history = self.metrics_history.get(agent_id, [])
        
        # Calculate improvement trend
        trend = 0.0
        if len(history) >= 2:
            recent = history[-5:] if len(history) >= 5 else history
            if len(recent) >= 2:
                first_rate = recent[0].success_rate
                last_rate = recent[-1].success_rate
                trend = last_rate - first_rate
        
        return PerformanceMetrics(
            agent_id=agent_id,
            success_rate=learning.success_rate,
            avg_task_duration=0,  # Would calculate from history
            error_rate=1 - learning.success_rate,
            learning_rate=0.1,  # Would calculate from skill changes
            skill_levels=learning.skills.copy(),
            improvement_trend=trend,
        )
    
    async def _generate_improvements(
        self,
        agent_id: str,
        metrics: PerformanceMetrics,
        learning: AgentLearning,
    ) -> List[ImprovementAction]:
        """Generate improvement actions using LLM."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            prompt = f"""You are an AI performance optimizer. Analyze this agent's performance and suggest improvements.

AGENT: {agent_id}
SUCCESS RATE: {metrics.success_rate:.2%}
ERROR RATE: {metrics.error_rate:.2%}
IMPROVEMENT TREND: {metrics.improvement_trend:+.2%}
SKILL LEVELS: {json.dumps(metrics.skill_levels)}

LEARNING SUMMARY: {json.dumps(learning.get_learning_summary())}

Generate 1-3 specific improvements. For each:
1. Type: prompt_update, strategy_change, skill_focus, or capability_add
2. Description: What to change
3. Target metric: What this will improve
4. Expected improvement: Percentage gain

Respond in JSON:
{{
    "improvements": [
        {{
            "type": "prompt_update",
            "description": "Add explicit step-by-step reasoning",
            "target": "success_rate",
            "expected_gain": 0.1
        }}
    ]
}}"""

            try:
                response = await client.post(
                    f"{self.llm_service_url}/llm/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.5,
                        "response_format": {"type": "json_object"},
                    },
                )
                
                if response.status_code == 200:
                    result = response.json()
                    content = result.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                    data = json.loads(content)
                    
                    actions = []
                    for imp in data.get("improvements", []):
                        actions.append(ImprovementAction(
                            id=str(uuid4()),
                            action_type=imp.get("type", "strategy_change"),
                            description=imp.get("description", ""),
                            target_metric=imp.get("target", "success_rate"),
                            expected_improvement=imp.get("expected_gain", 0.05),
                        ))
                    
                    return actions
                    
            except Exception as e:
                logger.error(f"Failed to generate improvements: {e}")
        
        return []
    
    async def _apply_improvement(self, agent_id: str, improvement: ImprovementAction):
        """Apply an improvement to an agent."""
        config = self.agent_configs.get(agent_id, {})
        
        if improvement.action_type == "prompt_update":
            # Update agent's system prompt
            current_prompt = config.get("system_prompt", "")
            enhanced_prompt = f"{current_prompt}\n\nIMPROVEMENT: {improvement.description}"
            config["system_prompt"] = enhanced_prompt
            
        elif improvement.action_type == "strategy_change":
            # Update agent's strategy
            if "strategies" not in config:
                config["strategies"] = []
            config["strategies"].append(improvement.description)
            
        elif improvement.action_type == "skill_focus":
            # Mark skill for focused improvement
            if "focus_skills" not in config:
                config["focus_skills"] = []
            config["focus_skills"].append(improvement.description)
            
        elif improvement.action_type == "capability_add":
            # Request new capability
            if "requested_capabilities" not in config:
                config["requested_capabilities"] = []
            config["requested_capabilities"].append(improvement.description)
        
        improvement.applied = True
        self.improvements[agent_id].append(improvement)
        self.agent_configs[agent_id] = config
        
        logger.info(f"Applied improvement to {agent_id}: {improvement.description}")
    
    def get_agent_improvements(self, agent_id: str) -> List[Dict[str, Any]]:
        """Get all improvements for an agent."""
        return [
            {
                "id": imp.id,
                "type": imp.action_type,
                "description": imp.description,
                "target": imp.target_metric,
                "expected": imp.expected_improvement,
                "applied": imp.applied,
            }
            for imp in self.improvements.get(agent_id, [])
        ]
    
    def get_metrics_history(self, agent_id: str) -> List[Dict[str, Any]]:
        """Get metrics history for an agent."""
        return [
            {
                "success_rate": m.success_rate,
                "error_rate": m.error_rate,
                "trend": m.improvement_trend,
            }
            for m in self.metrics_history.get(agent_id, [])
        ]


class AutonomousEvolution:
    """
    Enables agents to evolve their own capabilities.
    """
    
    def __init__(self):
        self.improvement_loop = SelfImprovementLoop()
        self.evolved_capabilities: Dict[str, List[str]] = {}
    
    async def start(self):
        await self.improvement_loop.start()
    
    async def stop(self):
        await self.improvement_loop.stop()
    
    async def evolve_agent(self, agent_id: str) -> Dict[str, Any]:
        """Trigger evolution for an agent."""
        learning = get_agent_learning(agent_id)
        
        # Analyze what capabilities would help
        weak_skills = [
            skill for skill, level in learning.skills.items()
            if level < 0.5
        ]
        
        # Generate new capabilities
        new_capabilities = []
        for skill in weak_skills:
            cap = await self._generate_capability(agent_id, skill)
            if cap:
                new_capabilities.append(cap)
        
        if agent_id not in self.evolved_capabilities:
            self.evolved_capabilities[agent_id] = []
        self.evolved_capabilities[agent_id].extend(new_capabilities)
        
        return {
            "agent_id": agent_id,
            "evolved_capabilities": new_capabilities,
            "total_capabilities": len(self.evolved_capabilities[agent_id]),
        }
    
    async def _generate_capability(self, agent_id: str, skill: str) -> Optional[str]:
        """Generate a new capability for a skill."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    "http://llm_service:8000/llm/chat/completions",
                    json={
                        "messages": [{
                            "role": "user",
                            "content": f"Generate a short capability description for improving '{skill}' skill. One sentence."
                        }],
                        "temperature": 0.7,
                    },
                )
                
                if response.status_code == 200:
                    result = response.json()
                    return result.get("choices", [{}])[0].get("message", {}).get("content", "")
                    
            except Exception as e:
                logger.debug(f"Capability generation failed: {e}")
        
        return None


# Global instances
_improvement_loop: Optional[SelfImprovementLoop] = None
_evolution: Optional[AutonomousEvolution] = None


async def get_improvement_loop() -> SelfImprovementLoop:
    global _improvement_loop
    if _improvement_loop is None:
        _improvement_loop = SelfImprovementLoop()
        await _improvement_loop.start()
    return _improvement_loop


async def get_evolution() -> AutonomousEvolution:
    global _evolution
    if _evolution is None:
        _evolution = AutonomousEvolution()
        await _evolution.start()
    return _evolution
