"""
AUTONOMOUS AGENT EXECUTOR
=========================

Executes agent tasks with full tool access and reasoning.
Agents can use tools, reflect on results, and adapt their approach.

Features:
- Tool invocation and chaining
- Step-by-step reasoning
- Error recovery and retry
- Result verification
- Autonomous decision making
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
import json

import httpx

from .agent_memory import get_agent_learning
from .blockchain_integration import get_blockchain_client

logger = logging.getLogger(__name__)


class ToolCategory(Enum):
    CODE = "code"
    FILE = "file"
    WEB = "web"
    DATA = "data"
    COMMUNICATION = "communication"
    SYSTEM = "system"


@dataclass
class Tool:
    """A tool that agents can use."""
    name: str
    description: str
    category: ToolCategory
    parameters: Dict[str, Any]
    handler: Optional[Callable] = None
    requires_approval: bool = False
    
    def to_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass
class ToolResult:
    """Result of a tool invocation."""
    tool_name: str
    success: bool
    output: Any
    error: Optional[str] = None
    duration_ms: int = 0


@dataclass
class ReasoningStep:
    """A step in the agent's reasoning process."""
    step_number: int
    thought: str
    action: Optional[str] = None
    action_input: Optional[Dict[str, Any]] = None
    observation: Optional[str] = None
    

@dataclass
class ExecutionResult:
    """Result of agent task execution."""
    task_id: str
    success: bool
    output: Any
    reasoning_steps: List[ReasoningStep]
    tools_used: List[str]
    duration_ms: int
    error: Optional[str] = None


class ToolRegistry:
    """Registry of available tools — wraps shared rg_tool_registry for agent executor."""

    def __init__(self):
        from .rg_tool_registry.builtin_tools import build_registry
        from .rg_tool_registry.registry import ToolAccess
        self._shared = build_registry()
        agent_tools = self._shared.get_tools(access=ToolAccess.AGENT)
        # Convert shared ToolDef → local Tool format for backward compat
        _cat_map = {"code_analysis": ToolCategory.CODE, "developer": ToolCategory.CODE,
                     "filesystem": ToolCategory.FILE, "search": ToolCategory.WEB,
                     "memory": ToolCategory.DATA, "agents": ToolCategory.SYSTEM,
                     "community": ToolCategory.COMMUNICATION}
        self.tools: Dict[str, Tool] = {}
        for td in agent_tools:
            cat = _cat_map.get(td.category.value, ToolCategory.DATA)
            self.tools[td.name] = Tool(
                name=td.name, description=td.description, category=cat,
                parameters=td.to_openai()["function"]["parameters"],
            )
        logger.info(f"[ToolRegistry] Agent executor: {len(self.tools)} tools from unified registry")

    def register(self, tool: Tool):
        """Register a tool."""
        self.tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self.tools.get(name)

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        """Get schemas for all tools."""
        return [t.to_schema() for t in self.tools.values()]


class AgentExecutor:
    """
    Executes tasks for autonomous agents with tool access.
    
    Now integrates with unified provider system:
    - Supports user API keys passed via context
    - Falls back to platform keys with credit checking
    - Free users get 1000 credits for platform key usage
    """
    
    MAX_ITERATIONS = 20
    FREE_TIER_CREDITS = 1000
    
    def __init__(self, llm_service_url: str = None):
        self.llm_service_url = llm_service_url or "http://llm_service:8000"
        self.billing_service_url = "http://billing_service:8000"
        self.auth_service_url = os.getenv("AUTH_URL", "http://auth_service:8000")
        self.tool_registry = ToolRegistry()
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client
    
    async def _get_user_api_keys(self, user_id: str) -> Dict[str, str]:
        """Get user's own API keys from auth service."""
        if not user_id:
            return {}
        
        try:
            client = await self._get_client()
            internal_key = os.getenv("AUTH_INTERNAL_SERVICE_KEY") or os.getenv("INTERNAL_SERVICE_KEY", "")
            headers = {"x-internal-service-key": internal_key} if internal_key else {}
            resp = await client.get(
                f"{self.auth_service_url}/auth/internal/user-api-keys/{user_id}",
                headers=headers,
                timeout=5.0
            )
            if resp.status_code == 200:
                data = resp.json()
                keys = {}
                for entry in data.get("keys", []):
                    provider = entry.get("provider")
                    api_key = entry.get("api_key")
                    if provider and api_key:
                        keys[provider] = api_key
                return keys
        except Exception as e:
            logger.warning(f"Failed to get user API keys: {e}")
        return {}
    
    async def _check_user_credits(self, user_id: str) -> int:
        """Check user's credit balance for platform key usage."""
        if not user_id:
            return self.FREE_TIER_CREDITS
        
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.billing_service_url}/billing/credits/balance/{user_id}",
                timeout=5.0
            )
            if resp.status_code == 200:
                return resp.json().get("balance", self.FREE_TIER_CREDITS)
        except Exception as e:
            logger.warning(f"Failed to check user credits: {e}")
        return self.FREE_TIER_CREDITS
    
    async def execute(
        self,
        agent_id: str,
        task: str,
        context: Dict[str, Any] = None,
        available_tools: List[str] = None,
        user_id: str = None,
        user_api_keys: Dict[str, str] = None,
        user_role: str = "user",
        is_superuser: bool = False,
        preferred_provider: str = None,
        preferred_model: str = None,
    ) -> ExecutionResult:
        """Execute a task for an agent."""
        task_id = str(uuid4())
        start_time = datetime.now(timezone.utc)
        
        reasoning_steps = []
        tools_used = []
        
        # Get available tools
        tools = self.tool_registry.get_all_schemas()
        if available_tools:
            tools = [t for t in tools if t["name"] in available_tools]
        
        # Get user API keys if not provided
        if user_id and not user_api_keys:
            user_api_keys = await self._get_user_api_keys(user_id)
        
        # Check credits if using platform keys
        privileged_roles = {"owner", "platform_owner", "admin", "superuser"}
        role = (user_role or "user").strip().lower()
        privileged_bypass = bool(is_superuser) or role in privileged_roles

        if user_id and not user_api_keys and not privileged_bypass:
            credits = await self._check_user_credits(user_id)
            if credits <= 0:
                return ExecutionResult(
                    task_id=task_id,
                    success=False,
                    output=None,
                    reasoning_steps=[],
                    tools_used=[],
                    duration_ms=int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000),
                    error="No credits remaining. Please add your own API keys or purchase credits.",
                )
        
        # Build initial prompt
        messages = self._build_initial_messages(task, context, tools)
        
        iteration = 0
        final_answer = None
        error = None
        
        while iteration < self.MAX_ITERATIONS:
            iteration += 1
            
            # Get next action from LLM with user context
            response = await self._call_llm(
                messages, 
                user_id=user_id, 
                user_api_keys=user_api_keys,
                preferred_provider=preferred_provider,
            )
            
            if not response:
                error = "LLM call failed"
                break
            
            # Parse response
            parsed = self._parse_response(response)
            
            step = ReasoningStep(
                step_number=iteration,
                thought=parsed.get("thought", ""),
                action=parsed.get("action"),
                action_input=parsed.get("action_input"),
            )
            
            # Check for final answer
            if parsed.get("action") == "final_answer":
                final_answer = parsed.get("action_input", {}).get("answer")
                step.observation = "Task completed"
                reasoning_steps.append(step)
                break
            
            # Execute tool
            if parsed.get("action"):
                user_ctx = {
                    "user_id": user_id,
                    "org_id": (context or {}).get("org_id"),
                    "agent_hash": (context or {}).get("agent_hash"),
                    "team_id": (context or {}).get("team_id"),
                    "chat_id": (context or {}).get("chat_id"),
                }
                tool_result = await self._execute_tool(
                    agent_id,
                    parsed["action"],
                    parsed.get("action_input", {}),
                    user_context=user_ctx,
                )
                
                step.observation = str(tool_result.output) if tool_result.success else f"Error: {tool_result.error}"
                tools_used.append(parsed["action"])
                
                # Add to messages for next iteration
                messages.append({
                    "role": "assistant",
                    "content": response,
                })
                messages.append({
                    "role": "user",
                    "content": f"Observation: {step.observation}",
                })
            
            reasoning_steps.append(step)
        
        # Calculate duration
        end_time = datetime.now(timezone.utc)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)
        
        # Record on blockchain
        bc_client = await get_blockchain_client()
        await bc_client.record_agent_action(
            agent_id=agent_id,
            action_type="task_execution",
            action_data={
                "task": task,
                "success": final_answer is not None,
                "tools_used": tools_used,
                "iterations": iteration,
            },
        )
        
        # Record learning
        learning = get_agent_learning(agent_id)
        learning.record_experience(
            task_type="execution",
            context=context or {},
            action={"task": task, "tools": tools_used},
            result={"answer": final_answer},
            success=final_answer is not None,
        )
        
        return ExecutionResult(
            task_id=task_id,
            success=final_answer is not None,
            output=final_answer,
            reasoning_steps=reasoning_steps,
            tools_used=list(set(tools_used)),
            duration_ms=duration_ms,
            error=error,
        )
    
    def _build_initial_messages(
        self,
        task: str,
        context: Dict[str, Any],
        tools: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        """Build initial messages for the LLM."""
        system_prompt = f"""You are an autonomous AI agent executing tasks. You can use tools to accomplish goals.

AVAILABLE TOOLS:
{json.dumps(tools, indent=2)}

To use a tool, respond with:
Thought: [your reasoning]
Action: [tool_name]
Action Input: [JSON input for the tool]

When you have the final answer, respond with:
Thought: [your reasoning]
Action: final_answer
Action Input: {{"answer": "your final answer"}}

Always think step by step and use tools when needed."""

        user_prompt = f"""TASK: {task}

CONTEXT: {json.dumps(context or {})}

Begin solving this task step by step."""

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    
    async def _call_llm(
        self, 
        messages: List[Dict[str, str]], 
        user_id: str = None,
        user_api_keys: Dict[str, str] = None,
        preferred_provider: str = None,
    ) -> Optional[str]:
        """Call the chat_service's unified multi-provider system with automatic fallback.
        
        Uses the same provider system as Resonant Chat:
        - Supports: Groq, Gemini, Claude, ChatGPT
        - Automatic fallback chain: Groq → Gemini → Claude → ChatGPT
        - BYOK (Bring Your Own Key) support
        
        Args:
            messages: Chat messages to send
            user_id: User ID for credit tracking
            user_api_keys: User's own API keys (bypasses credit system)
            preferred_provider: Preferred LLM provider (openai, anthropic, groq, gemini)
        """
        client = await self._get_client()
        
        # Extract the last user message as the query
        user_message = ""
        context_messages = []
        for msg in messages:
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
            context_messages.append(msg)
        
        # Build request for chat_service's unified provider endpoint
        request_data = {
            "message": user_message,
            "context": context_messages,
            "preferred_provider": preferred_provider or "groq",
        }
        
        # Add user API keys if available (these bypass credit system)
        if user_api_keys:
            request_data["user_api_keys"] = user_api_keys
        
        # Add user context headers
        headers = {"Content-Type": "application/json"}
        if user_id:
            headers["x-user-id"] = user_id
        
        # Use llm_service agent router (isolated from chat traffic)
        try:
            response = await client.post(
                f"{self.llm_service_url}/llm/agents/route-query",
                json=request_data,
                headers=headers,
                timeout=60.0,
            )
            if response.status_code == 200:
                result = response.json()
                response_text = result.get("response", "")
                provider_used = result.get("provider", "unknown")
                logger.info(f"✅ Agent LLM call succeeded via {provider_used}")
                return response_text
            logger.warning(
                "Agent router failed: %s, falling back to basic llm_service",
                response.status_code,
            )
        except Exception as e:
            logger.warning(f"Agent router failed: {e}, falling back to basic llm_service")
        
        # Fallback to basic llm_service if chat_service fails
        try:
            llm_request = {
                "messages": messages,
                "temperature": 0.3,
            }
            if user_api_keys:
                llm_request["user_api_keys"] = user_api_keys
            if preferred_provider:
                llm_request["provider"] = preferred_provider
            
            response = await client.post(
                f"{self.llm_service_url}/llm/chat/completions",
                json=llm_request,
                headers=headers,
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get("choices", [{}])[0].get("message", {}).get("content", "")
            else:
                logger.error(f"LLM service fallback failed: {response.status_code}: {response.text}")
                
        except Exception as e:
            logger.error(f"LLM service fallback failed: {e}")
        
        return None
    
    def _parse_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM response to extract thought, action, and input."""
        result = {
            "thought": "",
            "action": None,
            "action_input": None,
        }
        
        lines = response.strip().split("\n")
        
        for i, line in enumerate(lines):
            if line.startswith("Thought:"):
                result["thought"] = line[8:].strip()
            elif line.startswith("Action:"):
                result["action"] = line[7:].strip()
            elif line.startswith("Action Input:"):
                # Get the rest as JSON
                input_str = line[13:].strip()
                # May span multiple lines
                for j in range(i + 1, len(lines)):
                    if not lines[j].startswith(("Thought:", "Action:", "Observation:")):
                        input_str += "\n" + lines[j]
                    else:
                        break
                
                try:
                    result["action_input"] = json.loads(input_str)
                except json.JSONDecodeError:
                    result["action_input"] = {"raw": input_str}
        
        return result
    
    async def _execute_tool(
        self,
        agent_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        user_context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """Execute a tool."""
        start = datetime.now(timezone.utc)
        
        tool = self.tool_registry.get(tool_name)
        if not tool:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output=None,
                error=f"Unknown tool: {tool_name}",
            )
        
        try:
            # Execute based on tool category
            if tool_name == "execute_code":
                output = await self._execute_code(tool_input.get("code", ""))
            elif tool_name == "read_file":
                output = await self._read_file(tool_input.get("path", ""))
            elif tool_name == "write_file":
                output = await self._write_file(tool_input.get("path", ""), tool_input.get("content", ""))
            elif tool_name == "web_search":
                output = await self._web_search(tool_input.get("query", ""))
            elif tool_name == "fetch_url":
                output = await self._fetch_url(tool_input.get("url", ""))
            elif tool_name == "send_message":
                output = await self._send_message(agent_id, tool_input.get("to_agent", ""), tool_input.get("message", ""))
            elif tool_name == "spawn_agent":
                output = await self._spawn_agent(agent_id, tool_input.get("goal", ""))
            elif tool_name == "memory.read":
                output = await self._memory_read(tool_input, user_context or {})
            elif tool_name == "memory.write":
                output = await self._memory_write(tool_input, user_context or {})
            else:
                output = f"Tool {tool_name} executed with input: {tool_input}"
            
            duration = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
            
            return ToolResult(
                tool_name=tool_name,
                success=True,
                output=output,
                duration_ms=duration,
            )
            
        except Exception as e:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output=None,
                error=str(e),
            )
    
    async def _execute_code(self, code: str) -> str:
        """Execute Python code."""
        # Safety: use restricted execution
        import io
        from contextlib import redirect_stdout, redirect_stderr
        
        stdout = io.StringIO()
        stderr = io.StringIO()
        
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exec(code, {"__builtins__": __builtins__})
            
            output = stdout.getvalue()
            errors = stderr.getvalue()
            
            return output if output else (errors if errors else "Code executed successfully")
            
        except Exception as e:
            return f"Execution error: {str(e)}"
    
    async def _read_file(self, path: str) -> str:
        """Read a file."""
        try:
            with open(path, 'r') as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {e}"
    
    async def _write_file(self, path: str, content: str) -> str:
        """Write to a file."""
        try:
            with open(path, 'w') as f:
                f.write(content)
            return f"Successfully wrote {len(content)} characters to {path}"
        except Exception as e:
            return f"Error writing file: {e}"
    
    async def _web_search(self, query: str) -> str:
        """Perform web search."""
        return f"Search results for '{query}': [Simulated search results]"
    
    async def _fetch_url(self, url: str) -> str:
        """Fetch URL content."""
        client = await self._get_client()
        try:
            response = await client.get(url)
            return response.text[:5000]  # Limit response size
        except Exception as e:
            return f"Error fetching URL: {e}"
    
    async def _send_message(self, from_agent: str, to_agent: str, message: str) -> str:
        """Send message to another agent."""
        from .parallel_agent_runtime import get_runtime
        
        try:
            runtime = await get_runtime()
            await runtime.send_message(from_agent, to_agent, {"message": message})
            return f"Message sent to {to_agent}"
        except Exception as e:
            return f"Error sending message: {e}"
    
    async def _spawn_agent(self, parent_id: str, goal: str) -> str:
        """Spawn a new agent."""
        from .autonomous_daemon import get_daemon
        
        try:
            daemon = await get_daemon()
            child_id = str(uuid4())
            await daemon.register_autonomous_agent(child_id, goal)
            return f"Spawned agent {child_id} with goal: {goal}"
        except Exception as e:
            return f"Error spawning agent: {e}"

    async def _memory_read(self, tool_input: Dict[str, Any], user_context: Dict[str, Any]) -> Any:
        client = await self._get_client()

        memory_base = (os.getenv("MEMORY_SERVICE_URL") or "http://memory_service:8000").rstrip("/")
        query = (tool_input.get("query") or "").strip()
        if not query:
            return []

        limit = tool_input.get("limit", 5)
        try:
            limit = int(limit)
        except Exception:
            limit = 5
        limit = max(1, min(limit, 25))

        retrieval_mode = (tool_input.get("retrieval_mode") or "hybrid").strip().lower()
        if retrieval_mode not in {"embedding", "hash_sphere", "hybrid"}:
            retrieval_mode = "hybrid"

        payload: Dict[str, Any] = {
            "query": query,
            "limit": limit,
            "use_vector_search": True,
            "retrieval_mode": retrieval_mode,
            "user_id": user_context.get("user_id"),
            "org_id": user_context.get("org_id"),
            "agent_hash": user_context.get("agent_hash"),
            "team_id": user_context.get("team_id"),
            "chat_id": user_context.get("chat_id"),
        }

        # Remove None values for cleanliness
        payload = {k: v for k, v in payload.items() if v is not None}

        try:
            resp = await client.post(f"{memory_base}/memory/retrieve", json=payload, timeout=15.0)
        except Exception as e:
            return f"Memory service unavailable: {e}"

        if resp.status_code != 200:
            return f"Memory read failed: {resp.status_code}: {resp.text[:500]}"

        try:
            return resp.json()
        except Exception:
            return []

    async def _memory_write(self, tool_input: Dict[str, Any], user_context: Dict[str, Any]) -> Any:
        client = await self._get_client()

        memory_base = (os.getenv("MEMORY_SERVICE_URL") or "http://memory_service:8000").rstrip("/")
        content = (tool_input.get("content") or "").strip()
        if not content:
            return "No content provided"

        generate_embedding = tool_input.get("generate_embedding", True)
        source = tool_input.get("source") or "agent_engine"
        metadata = tool_input.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            metadata = {"raw": str(metadata)}

        payload: Dict[str, Any] = {
            "chat_id": user_context.get("chat_id"),
            "user_id": user_context.get("user_id"),
            "org_id": user_context.get("org_id"),
            "agent_hash": user_context.get("agent_hash"),
            "source": source,
            "content": content,
            "metadata": metadata,
            "generate_embedding": bool(generate_embedding),
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        try:
            resp = await client.post(f"{memory_base}/memory/ingest", json=payload, timeout=20.0)
        except Exception as e:
            return f"Memory service unavailable: {e}"

        if resp.status_code != 200:
            return f"Memory write failed: {resp.status_code}: {resp.text[:500]}"

        try:
            return resp.json()
        except Exception:
            return "Memory stored"


# Global instance
_executor: Optional[AgentExecutor] = None


async def get_agent_executor() -> AgentExecutor:
    global _executor
    if _executor is None:
        _executor = AgentExecutor()
    return _executor
