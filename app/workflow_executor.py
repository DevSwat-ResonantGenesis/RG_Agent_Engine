"""
Background workflow executor for agent teams.
Executes team workflows asynchronously.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import async_session
from .models import AgentTeam, AgentTeamWorkflow, AgentDefinition

logger = logging.getLogger(__name__)


async def execute_workflow_background(
    workflow_id: str,
    team: AgentTeam,
    input_data: Dict[str, Any]
):
    """
    Execute a team workflow in the background.
    
    This function runs the workflow based on the team's configuration:
    - Sequential: Execute agents one after another
    - Parallel: Execute all agents simultaneously
    - Branching: Execute based on conditions
    """
    try:
        async with async_session() as session:
            # Get workflow
            result = await session.execute(
                select(AgentTeamWorkflow).where(AgentTeamWorkflow.id == workflow_id)
            )
            workflow = result.scalar_one_or_none()
            
            if not workflow:
                logger.error(f"Workflow {workflow_id} not found")
                return
            
            # Update status to running
            workflow.status = "running"
            workflow.started_at = datetime.utcnow()
            await session.commit()
            
            # Get team agents
            agent_ids = team.member_agent_ids or []
            if not agent_ids:
                workflow.status = "failed"
                workflow.error_message = "No agents in team"
                workflow.completed_at = datetime.utcnow()
                await session.commit()
                return
            
            # Execute based on workflow type
            workflow_type = team.config.get("type", "sequential") if team.config else "sequential"
            team_prompt = (team.config or {}).get("team_prompt")

            # A team_prompt is the user's own instructions for how members
            # should collaborate (tone, role split, constraints) — fold it
            # into the goal every member actually receives instead of
            # silently ignoring it.
            effective_input = dict(input_data) if isinstance(input_data, dict) else {"goal": str(input_data)}
            if team_prompt:
                original_goal = effective_input.get("goal", "")
                effective_input["goal"] = (
                    f"TEAM INSTRUCTIONS: {team_prompt}\n\nTASK: {original_goal}" if original_goal else team_prompt
                )

            team_user_id = str(team.user_id) if team.user_id else None

            try:
                if workflow_type == "sequential":
                    result = await execute_sequential(session, agent_ids, effective_input, team_user_id)
                elif workflow_type == "parallel":
                    result = await execute_parallel(session, agent_ids, effective_input, team_user_id)
                elif workflow_type == "branching":
                    result = await execute_branching(session, agent_ids, effective_input, team.config, team_user_id)
                else:
                    raise ValueError(f"Unknown workflow type: {workflow_type}")
                
                # Update workflow with results
                workflow.status = "completed"
                workflow.final_output = result
                workflow.completed_at = datetime.utcnow()
                
            except Exception as e:
                logger.error(f"Workflow {workflow_id} failed: {e}", exc_info=True)
                workflow.status = "failed"
                workflow.error_message = str(e)
                workflow.completed_at = datetime.utcnow()
            
            await session.commit()
            
    except Exception as e:
        logger.error(f"Failed to execute workflow {workflow_id}: {e}", exc_info=True)


async def execute_sequential(
    session: AsyncSession,
    agent_ids: List[UUID],
    input_data: Dict[str, Any],
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute agents sequentially, passing output to next agent."""
    current_input = input_data
    results = []

    for agent_id in agent_ids:
        # Get agent
        result = await session.execute(
            select(AgentDefinition).where(AgentDefinition.id == agent_id)
        )
        agent = result.scalar_one_or_none()

        if not agent:
            logger.warning(f"Agent {agent_id} not found, skipping")
            continue

        output = await execute_agent_task(agent, current_input, user_id)
        results.append({
            "agent_id": str(agent_id),
            "agent_name": agent.name,
            "input": current_input,
            "output": output,
        })
        
        # Output becomes input for next agent
        current_input = output
    
    return {
        "type": "sequential",
        "steps": results,
        "final_output": current_input,
    }


async def execute_parallel(
    session: AsyncSession,
    agent_ids: List[UUID],
    input_data: Dict[str, Any],
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute all agents concurrently (asyncio.gather) with the same input."""
    agents = []
    for agent_id in agent_ids:
        result = await session.execute(
            select(AgentDefinition).where(AgentDefinition.id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            logger.warning(f"Agent {agent_id} not found, skipping")
            continue
        agents.append(agent)

    outputs = await asyncio.gather(
        *(execute_agent_task(agent, input_data, user_id) for agent in agents),
        return_exceptions=True,
    )

    results = []
    for agent, output in zip(agents, outputs):
        if isinstance(output, Exception):
            logger.error(f"Agent {agent.id} failed: {output}")
            results.append({"agent_id": str(agent.id), "agent_name": agent.name, "error": str(output)})
        else:
            results.append({"agent_id": str(agent.id), "agent_name": agent.name, "output": output})

    return {
        "type": "parallel",
        "results": results,
        "combined_output": "\n\n".join(
            str(r["output"].get("output", "") if isinstance(r.get("output"), dict) else r.get("output", ""))
            for r in results if "output" in r
        ),
    }


async def execute_branching(
    session: AsyncSession,
    agent_ids: List[UUID],
    input_data: Dict[str, Any],
    config: Dict[str, Any],
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute agents based on branching conditions."""
    # TODO: Implement branching logic based on config
    # For now, fall back to sequential
    return await execute_sequential(session, agent_ids, input_data, user_id)


async def execute_agent_task(
    agent: AgentDefinition,
    input_data: Dict[str, Any],
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a real agent session (start_session + run_loop) and return its actual output."""
    from .executor import agent_executor

    if isinstance(input_data, dict):
        goal = input_data.get("goal") or input_data.get("output") or json.dumps(input_data)
    else:
        goal = str(input_data)

    async with async_session() as db:
        child_session = await agent_executor.start_session(
            agent=agent,
            goal=goal,
            initial_context={},
            user_id=user_id,
            db_session=db,
        )
        result = await agent_executor.run_loop(child_session, agent, db)

    return {
        "agent": agent.name,
        "status": result.get("status"),
        "output": result.get("output"),
        "error": result.get("error"),
        "session_id": str(child_session.id),
    }
