"""
AGENT EXECUTION, REASONING & COLLABORATION API
===============================================

API endpoints for task execution, reasoning, and agent collaboration.
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from uuid import UUID as PyUUID
import httpx
import os
import logging

logger = logging.getLogger(__name__)

BILLING_SERVICE_URL = os.getenv("BILLING_SERVICE_URL", "http://billing_service:8000")
CREDIT_COST_AGENT_EXECUTION = 100

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import AgentDefinition, AgentSession, AgentStep

router = APIRouter(prefix="/execution", tags=["agent-execution"])


# === REQUEST MODELS ===

class ExecuteTaskRequest(BaseModel):
    task: str
    context: Optional[Dict[str, Any]] = None
    available_tools: Optional[List[str]] = None


# === TASK EXECUTION ===

@router.post("/agents/{agent_id}/execute")
async def execute_task(
    agent_id: str,
    request: ExecuteTaskRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Execute a task for an agent with tool access."""
    user_id = http_request.headers.get("x-user-id")
    org_id = http_request.headers.get("x-org-id")
    user_role = (http_request.headers.get("x-user-role") or "user").strip().lower()
    is_superuser = (http_request.headers.get("x-is-superuser") or "").strip().lower() in {"1", "true", "yes", "on"}

    # Credit pre-check: block zero-credit users
    is_privileged = is_superuser or user_role in ("platform_owner", "admin")
    if not is_privileged and user_id and user_id != "anonymous":
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                bal_resp = await client.get(
                    f"{BILLING_SERVICE_URL}/billing/credits/balance/{user_id}",
                    timeout=5.0,
                )
                if bal_resp.status_code == 200:
                    bal_data = bal_resp.json()
                    balance = bal_data.get("balance", 0)
                    if balance <= 0 and not bal_data.get("unlimited", False):
                        logger.warning(f"[Credits] User {user_id[:8]}... blocked from agent execution: 0 credits")
                        return JSONResponse(
                            status_code=402,
                            content={
                                "error": "insufficient_credits",
                                "detail": "Credits exhausted. Please upgrade your plan or purchase credits to run agents.",
                                "message": "Credits exhausted. Please upgrade your plan or purchase credits to run agents.",
                                "action_url": "/pricing",
                                "required": CREDIT_COST_AGENT_EXECUTION,
                                "available": balance,
                            },
                        )
        except Exception as e:
            logger.warning(f"[Credits] Balance check failed for agent execution: {e}")

    merged_context: Dict[str, Any] = dict(request.context or {})
    if user_id and "user_id" not in merged_context:
        merged_context["user_id"] = user_id
    if org_id and "org_id" not in merged_context:
        merged_context["org_id"] = org_id

    # Derive agent_hash for memory scoping from the agent definition when possible.
    agent = None
    effective_available_tools = request.available_tools

    try:
        agent_uuid = PyUUID(agent_id)
        result = await session.execute(select(AgentDefinition).where(AgentDefinition.id == agent_uuid))
        agent = result.scalar_one_or_none()
        if agent:
            derived_agent_hash = getattr(agent, "agent_public_hash", None) or (agent.safety_config or {}).get("agent_hash")
            if derived_agent_hash and "agent_hash" not in merged_context:
                merged_context["agent_hash"] = derived_agent_hash
            if "agent_id" not in merged_context:
                merged_context["agent_id"] = str(agent.id)
            if "agent_name" not in merged_context and getattr(agent, "name", None):
                merged_context["agent_name"] = agent.name
            if "agent_model" not in merged_context and getattr(agent, "model", None):
                merged_context["agent_model"] = agent.model
            if not effective_available_tools and isinstance(getattr(agent, "tools", None), list):
                effective_available_tools = [t for t in agent.tools if isinstance(t, str)]
    except Exception:
        pass

    # Route through real executor session
    from .executor import agent_executor as real_executor
    try:
        session_result = await real_executor.run_session(
            agent, request.task, merged_context,
        )
        return {
            "task_id": str(session_result) if session_result else agent_id,
            "success": True,
            "output": str(session_result)[:1000] if session_result else "",
            "tools_used": [],
            "duration_ms": 0,
            "error": None,
        }
    except Exception as e:
        return {
            "task_id": agent_id,
            "success": False,
            "output": None,
            "tools_used": [],
            "duration_ms": 0,
            "error": str(e),
        }


# === EXECUTION HISTORY ===

@router.get("/agents/{agent_id}/executions")
async def get_agent_executions(
    agent_id: str,
    request: Request,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """Get execution history for an agent."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    try:
        agent_uuid = PyUUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent_id")

    try:
        user_uuid = PyUUID(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user id")

    agent_result = await session.execute(
        select(AgentDefinition).where(AgentDefinition.id == agent_uuid).where(AgentDefinition.user_id == user_uuid)
    )
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    query = select(AgentSession).where(AgentSession.agent_id == agent_uuid).where(AgentSession.user_id == user_uuid)
    if status:
        query = query.where(AgentSession.status == status)
    query = query.order_by(desc(AgentSession.created_at)).limit(limit).offset(offset)

    result = await session.execute(query)
    sessions = result.scalars().all()

    executions = []
    for s in sessions:
        executions.append(
            {
                "id": str(s.id),
                "agent_id": str(s.agent_id),
                "status": s.status,
                "goal": s.current_goal,
                "loop_count": s.loop_count,
                "tokens_used": s.total_tokens_used,
                "tool_calls": s.total_tool_calls,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                "duration_ms": int((s.completed_at - s.started_at).total_seconds() * 1000)
                if s.started_at and s.completed_at
                else None,
                "error": s.error_message,
            }
        )

    return {
        "executions": executions,
        "total": len(executions),
        "limit": limit,
        "offset": offset,
    }


@router.get("/executions/{execution_id}")
async def get_execution_details(
    execution_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Get detailed execution information including all steps."""
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    try:
        exec_uuid = PyUUID(execution_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid execution_id")

    try:
        user_uuid = PyUUID(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user id")

    result = await session.execute(
        select(AgentSession).where(AgentSession.id == exec_uuid).where(AgentSession.user_id == user_uuid)
    )
    exec_session = result.scalar_one_or_none()
    if not exec_session:
        raise HTTPException(status_code=404, detail="Execution not found")

    steps_result = await session.execute(
        select(AgentStep).where(AgentStep.session_id == exec_uuid).order_by(AgentStep.step_number)
    )
    steps = steps_result.scalars().all()

    cost = (exec_session.total_tokens_used / 1000) * 0.01 if exec_session.total_tokens_used else 0

    return {
        "id": str(exec_session.id),
        "agent_id": str(exec_session.agent_id),
        "status": exec_session.status,
        "goal": exec_session.current_goal,
        "context": exec_session.context,
        "loop_count": exec_session.loop_count,
        "tokens_used": exec_session.total_tokens_used,
        "tool_calls": exec_session.total_tool_calls,
        "cost": round(cost, 4),
        "started_at": exec_session.started_at.isoformat() if exec_session.started_at else None,
        "completed_at": exec_session.completed_at.isoformat() if exec_session.completed_at else None,
        "duration_ms": int((exec_session.completed_at - exec_session.started_at).total_seconds() * 1000)
        if exec_session.started_at and exec_session.completed_at
        else None,
        "error": exec_session.error_message,
        "output": exec_session.final_output,
        "steps": [
            {
                "id": str(step.id),
                "step_number": step.step_number,
                "type": step.step_type,
                "reasoning": step.reasoning,
                "tool_name": step.tool_name,
                "tool_input": step.tool_input,
                "tool_output": step.tool_output,
                "input": step.input_data,
                "output": step.output_data,
                "created_at": step.created_at.isoformat() if hasattr(step, "created_at") and step.created_at else None,
            }
            for step in steps
        ],
    }
