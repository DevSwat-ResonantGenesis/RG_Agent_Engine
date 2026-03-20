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

"""Tool planning and goal decomposition for agents."""

import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from .config import settings
from .models import AgentPlan
from .tool_spec import ToolSpec

logger = logging.getLogger(__name__)


class ToolPlanner:
    """Plans tool usage and decomposes goals into actionable steps."""

    PLANNING_PROMPT = """You are a planning assistant. Given a goal, decompose it into concrete steps.

Available tools:
{tools}

Goal: {goal}

Context: {context}

Create a plan with specific, actionable steps. Each step should specify:
1. What action to take
2. Which tool to use (if any)
3. Expected outcome

Respond in JSON format:
{{
    "analysis": "Brief analysis of the goal",
    "steps": [
        {{
            "step_number": 1,
            "description": "What to do",
            "tool": "tool_name or null",
            "tool_input": {{}},
            "expected_outcome": "What we expect",
            "dependencies": []
        }}
    ],
    "success_criteria": "How to know the goal is achieved"
}}"""

    REPLANNING_PROMPT = """The current plan needs revision based on new information.

Original goal: {goal}
Current plan: {current_plan}
Completed steps: {completed_steps}
Issue encountered: {issue}

Create a revised plan that addresses the issue while still achieving the goal.

Respond in the same JSON format as before."""

    async def _call_llm_json(self, prompt: str, max_tokens: int = 2048) -> Optional[str]:
        """Call LLM with JSON output via UnifiedLLMClient."""
        from .executor import _llm_client
        from rg_llm import LLMRequest

        try:
            response = await _llm_client.complete(LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            ))
            return response.content if response.content else None
        except Exception as e:
            logger.warning(f"[PLANNER] LLM call failed: {e}")
            return None

    async def create_plan(
        self,
        goal: str,
        available_tools: List[ToolSpec],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create an execution plan for a goal."""
        tools_desc = self._format_tools(available_tools)
        context_str = json.dumps(context or {}, indent=2)

        prompt = self.PLANNING_PROMPT.format(
            tools=tools_desc,
            goal=goal,
            context=context_str,
        )

        try:
            content = await self._call_llm_json(prompt)
            if content:
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    m = re.search(r"\{.*\}", content, flags=re.DOTALL)
                    if m:
                        return json.loads(m.group(0))
        except Exception as e:
            return {
                "analysis": f"Planning failed: {str(e)}",
                "steps": [{"step_number": 1, "description": goal, "tool": None}],
                "success_criteria": "Goal completion",
            }

        return {"steps": [], "error": "Failed to create plan"}

    async def revise_plan(
        self,
        goal: str,
        current_plan: Dict[str, Any],
        completed_steps: List[Dict[str, Any]],
        issue: str,
        available_tools: List[ToolSpec],
    ) -> Dict[str, Any]:
        """Revise a plan based on execution feedback."""
        prompt = self.REPLANNING_PROMPT.format(
            goal=goal,
            current_plan=json.dumps(current_plan, indent=2),
            completed_steps=json.dumps(completed_steps, indent=2),
            issue=issue,
        )

        try:
            content = await self._call_llm_json(prompt)
            if content:
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    m = re.search(r"\{.*\}", content, flags=re.DOTALL)
                    if m:
                        return json.loads(m.group(0))
        except Exception as e:
            return {"error": str(e)}

        return {"error": "Failed to revise plan"}

    async def select_next_action(
        self,
        plan: Dict[str, Any],
        current_step_index: int,
        execution_context: Dict[str, Any],
        available_tools: List[ToolSpec],
    ) -> Optional[Dict[str, Any]]:
        """Select the next action to execute based on plan and context."""
        steps = plan.get("steps", [])
        
        if current_step_index >= len(steps):
            return None  # Plan completed

        current_step = steps[current_step_index]
        
        # Check if dependencies are met
        dependencies = current_step.get("dependencies", [])
        for dep in dependencies:
            if dep not in execution_context.get("completed_steps", []):
                return {"wait": True, "reason": f"Waiting for dependency: {dep}"}

        # Prepare action
        tool_name = current_step.get("tool")
        if tool_name:
            tool = next((t for t in available_tools if t.name == tool_name), None)
            if not tool:
                return {"error": f"Tool not found: {tool_name}"}
            
            return {
                "action": "tool_call",
                "tool": tool_name,
                "input": current_step.get("tool_input", {}),
                "description": current_step.get("description"),
            }
        
        return {
            "action": "think",
            "description": current_step.get("description"),
        }

    def _format_tools(self, tools: List[ToolSpec]) -> str:
        """Format tools for prompt inclusion."""
        if not tools:
            return "No tools available."
        
        lines = []
        for tool in tools:
            params = tool.parameters_schema or {}
            lines.append(f"- {tool.name}: {tool.description}")
            if params:
                lines.append(f"  Parameters: {json.dumps(params)}")
        
        return "\n".join(lines)


class GoalDecomposer:
    """Decomposes complex goals into sub-goals."""

    DECOMPOSITION_PROMPT = """Analyze this goal and break it into smaller, manageable sub-goals.

Goal: {goal}

Consider:
1. What are the main components of this goal?
2. What needs to happen first?
3. Are there parallel tasks?

Respond in JSON:
{{
    "main_goal": "The original goal",
    "sub_goals": [
        {{
            "id": "sg1",
            "description": "Sub-goal description",
            "priority": 1,
            "dependencies": [],
            "estimated_complexity": "low|medium|high"
        }}
    ],
    "execution_order": ["sg1", "sg2"]
}}"""

    async def decompose(self, goal: str) -> Dict[str, Any]:
        """Decompose a complex goal into sub-goals."""
        prompt = self.DECOMPOSITION_PROMPT.format(goal=goal)

        try:
            content = await ToolPlanner()._call_llm_json(prompt, max_tokens=1024)
            if content:
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    m = re.search(r"\{.*\}", content, flags=re.DOTALL)
                    if m:
                        return json.loads(m.group(0))
        except Exception as e:
            return {
                "main_goal": goal,
                "sub_goals": [{"id": "sg1", "description": goal, "priority": 1}],
                "execution_order": ["sg1"],
            }

        return {"error": "Decomposition failed"}


tool_planner = ToolPlanner()
goal_decomposer = GoalDecomposer()
