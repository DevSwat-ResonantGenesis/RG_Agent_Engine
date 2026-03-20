"""
AGENT EXECUTION, REASONING & COLLABORATION API
===============================================

API endpoints for task execution, reasoning, and agent collaboration.
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from uuid import UUID as PyUUID

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from .agent_executor import get_agent_executor, AgentExecutor
from .agent_reasoning import get_reasoning_engine, get_metacognitive_monitor, ReasoningEngine, ReflectionType
from .agent_collaboration import get_collaboration_hub, CollaborationHub
from .db import get_session
from .models import AgentDefinition, AgentSession, AgentStep

router = APIRouter(prefix="/execution", tags=["agent-execution"])


# === REQUEST MODELS ===

class ExecuteTaskRequest(BaseModel):
    task: str
    context: Optional[Dict[str, Any]] = None
    available_tools: Optional[List[str]] = None


class ChainOfThoughtRequest(BaseModel):
    problem: str
    context: Optional[Dict[str, Any]] = None


class ReflectRequest(BaseModel):
    reflection_type: str  # pre_action, post_action, error, goal_progress, metacognitive
    context: Dict[str, Any]


class JustifyDecisionRequest(BaseModel):
    decision: str
    alternatives: List[str]
    context: Dict[str, Any]


class DelegateRequest(BaseModel):
    to_agent: str
    task: str
    context: Optional[Dict[str, Any]] = None
    priority: int = 1


class ConsensusRequest(BaseModel):
    topic: str
    options: List[str]
    voters: List[str]
    required_majority: float = 0.5


class VoteRequest(BaseModel):
    option: str


class ShareKnowledgeRequest(BaseModel):
    topic: str
    content: Dict[str, Any]
    recipients: Optional[List[str]] = None
    importance: float = 0.5


# === DEPENDENCIES ===

async def get_exec() -> AgentExecutor:
    return await get_agent_executor()


async def get_hub() -> CollaborationHub:
    return await get_collaboration_hub()


# === TASK EXECUTION ===

@router.post("/agents/{agent_id}/execute")
async def execute_task(
    agent_id: str,
    request: ExecuteTaskRequest,
    http_request: Request,
    executor: AgentExecutor = Depends(get_exec),
    session: AsyncSession = Depends(get_session),
):
    """Execute a task for an agent with tool access."""
    user_id = http_request.headers.get("x-user-id")
    org_id = http_request.headers.get("x-org-id")
    user_role = (http_request.headers.get("x-user-role") or "user").strip().lower()
    is_superuser = (http_request.headers.get("x-is-superuser") or "").strip().lower() in {"1", "true", "yes", "on"}

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

    result = await executor.execute(
        agent_id=agent_id,
        task=request.task,
        context=merged_context,
        available_tools=effective_available_tools,
        user_id=user_id,
        user_role=user_role,
        is_superuser=is_superuser,
        preferred_provider=getattr(agent, "provider", None) if agent else None,
        preferred_model=getattr(agent, "model", None) if agent else None,
    )
    
    return {
        "task_id": result.task_id,
        "success": result.success,
        "output": result.output,
        "reasoning_steps": [
            {
                "step": s.step_number,
                "thought": s.thought,
                "action": s.action,
                "observation": s.observation,
            }
            for s in result.reasoning_steps
        ],
        "tools_used": result.tools_used,
        "duration_ms": result.duration_ms,
        "error": result.error,
    }


@router.get("/tools")
async def list_tools(
    executor: AgentExecutor = Depends(get_exec),
):
    """List available tools."""
    return {"tools": executor.tool_registry.get_all_schemas()}


# === REASONING ===

@router.post("/agents/{agent_id}/reason")
async def chain_of_thought(
    agent_id: str,
    request: ChainOfThoughtRequest,
):
    """Generate chain-of-thought reasoning."""
    engine = get_reasoning_engine(agent_id)
    
    chain = await engine.chain_of_thought(
        agent_id=agent_id,
        problem=request.problem,
        context=request.context,
    )
    
    return {
        "chain_id": chain.id,
        "thoughts": chain.thoughts,
        "conclusion": chain.conclusion,
        "confidence": chain.confidence,
    }


@router.post("/agents/{agent_id}/reflect")
async def reflect(
    agent_id: str,
    request: ReflectRequest,
):
    """Generate a reflection."""
    engine = get_reasoning_engine(agent_id)
    
    try:
        reflection_type = ReflectionType(request.reflection_type)
    except ValueError:
        reflection_type = ReflectionType.POST_ACTION
    
    reflection = await engine.reflect(
        agent_id=agent_id,
        reflection_type=reflection_type,
        context=request.context,
    )
    
    return {
        "reflection_id": reflection.id,
        "type": reflection.reflection_type.value,
        "insights": reflection.insights,
        "lessons": reflection.lessons_learned,
        "adjustments": reflection.action_adjustments,
        "confidence": reflection.confidence,
    }


@router.post("/agents/{agent_id}/justify")
async def justify_decision(
    agent_id: str,
    request: JustifyDecisionRequest,
):
    """Justify a decision."""
    engine = get_reasoning_engine(agent_id)
    
    justification = await engine.justify_decision(
        agent_id=agent_id,
        decision=request.decision,
        alternatives=request.alternatives,
        context=request.context,
    )
    
    return justification


@router.post("/agents/{agent_id}/metacognition")
async def metacognitive_check(agent_id: str):
    """Perform metacognitive self-check."""
    monitor = get_metacognitive_monitor(agent_id)
    reflection = await monitor.periodic_metacognition()
    
    return {
        "reflection_id": reflection.id,
        "insights": reflection.insights,
        "lessons": reflection.lessons_learned,
    }


# === COLLABORATION: DELEGATION ===

@router.post("/agents/{agent_id}/delegate")
async def delegate_task(
    agent_id: str,
    request: DelegateRequest,
    hub: CollaborationHub = Depends(get_hub),
):
    """Delegate a task to another agent."""
    delegation = await hub.delegation.delegate(
        from_agent=agent_id,
        to_agent=request.to_agent,
        task=request.task,
        context=request.context,
        priority=request.priority,
    )
    
    return {
        "delegation_id": delegation.id,
        "status": delegation.status.value,
        "to_agent": delegation.to_agent,
    }


@router.post("/delegations/{delegation_id}/accept")
async def accept_delegation(
    delegation_id: str,
    agent_id: str,
    hub: CollaborationHub = Depends(get_hub),
):
    """Accept a delegation."""
    success = await hub.delegation.accept_delegation(delegation_id, agent_id)
    return {"success": success}


@router.post("/delegations/{delegation_id}/reject")
async def reject_delegation(
    delegation_id: str,
    agent_id: str,
    reason: str = "",
    hub: CollaborationHub = Depends(get_hub),
):
    """Reject a delegation."""
    success = await hub.delegation.reject_delegation(delegation_id, agent_id, reason)
    return {"success": success}


@router.post("/delegations/{delegation_id}/complete")
async def complete_delegation(
    delegation_id: str,
    agent_id: str,
    result: Dict[str, Any],
    success: bool = True,
    hub: CollaborationHub = Depends(get_hub),
):
    """Complete a delegation."""
    completed = await hub.delegation.complete_delegation(delegation_id, agent_id, result, success)
    return {"success": completed}


# === COLLABORATION: CONSENSUS ===

@router.post("/consensus/propose")
async def propose_consensus(
    proposer: str,
    request: ConsensusRequest,
    hub: CollaborationHub = Depends(get_hub),
):
    """Propose a topic for consensus voting."""
    proposal = await hub.consensus.propose(
        proposer=proposer,
        topic=request.topic,
        options=request.options,
        voters=request.voters,
        required_majority=request.required_majority,
    )
    
    return {
        "proposal_id": proposal.id,
        "topic": proposal.topic,
        "options": proposal.options,
        "status": proposal.status,
    }


@router.post("/consensus/{proposal_id}/vote")
async def vote_on_proposal(
    proposal_id: str,
    agent_id: str,
    request: VoteRequest,
    hub: CollaborationHub = Depends(get_hub),
):
    """Vote on a consensus proposal."""
    success = await hub.consensus.vote(proposal_id, agent_id, request.option)
    result = hub.consensus.get_result(proposal_id)
    
    return {
        "success": success,
        "result": result,
    }


@router.get("/consensus/{proposal_id}")
async def get_proposal_status(
    proposal_id: str,
    hub: CollaborationHub = Depends(get_hub),
):
    """Get status of a consensus proposal."""
    proposal = hub.consensus.proposals.get(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    
    return {
        "proposal_id": proposal.id,
        "topic": proposal.topic,
        "options": proposal.options,
        "votes": proposal.votes,
        "status": proposal.status,
        "result": proposal.result,
    }


# === COLLABORATION: KNOWLEDGE SHARING ===

@router.post("/agents/{agent_id}/share")
async def share_knowledge(
    agent_id: str,
    request: ShareKnowledgeRequest,
    hub: CollaborationHub = Depends(get_hub),
):
    """Share knowledge with other agents."""
    package = await hub.knowledge.share_knowledge(
        from_agent=agent_id,
        topic=request.topic,
        content=request.content,
        recipients=request.recipients,
        importance=request.importance,
    )
    
    return {
        "package_id": package.id,
        "topic": package.topic,
        "recipients": package.recipients or "broadcast",
    }


@router.get("/agents/{agent_id}/collective-knowledge")
async def query_collective_knowledge(
    agent_id: str,
    query: str,
    hub: CollaborationHub = Depends(get_hub),
):
    """Query collective knowledge from all agents."""
    results = await hub.knowledge.query_collective_knowledge(agent_id, query)
    return {"results": results}


# === COLLABORATION STATS ===

@router.get("/collaboration/stats")
async def get_collaboration_stats(
    hub: CollaborationHub = Depends(get_hub),
):
    """Get collaboration statistics."""
    return hub.get_stats()


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
