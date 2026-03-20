"""
Background workflow executor for agent teams.
Executes team workflows asynchronously.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, List
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
            
            try:
                if workflow_type == "sequential":
                    result = await execute_sequential(session, agent_ids, input_data)
                elif workflow_type == "parallel":
                    result = await execute_parallel(session, agent_ids, input_data)
                elif workflow_type == "branching":
                    result = await execute_branching(session, agent_ids, input_data, team.config)
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
    input_data: Dict[str, Any]
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
        
        # Execute agent (mock for now - TODO: integrate with actual agent execution)
        output = await execute_agent_task(agent, current_input)
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
    input_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Execute all agents in parallel with same input."""
    tasks = []
    
    for agent_id in agent_ids:
        # Get agent
        result = await session.execute(
            select(AgentDefinition).where(AgentDefinition.id == agent_id)
        )
        agent = result.scalar_one_or_none()
        
        if not agent:
            logger.warning(f"Agent {agent_id} not found, skipping")
            continue
        
        # Create task for parallel execution
        task = execute_agent_task(agent, input_data)
        tasks.append((agent_id, agent.name, task))
    
    # Execute all in parallel
    results = []
    for agent_id, agent_name, task in tasks:
        try:
            output = await task
            results.append({
                "agent_id": str(agent_id),
                "agent_name": agent_name,
                "output": output,
            })
        except Exception as e:
            logger.error(f"Agent {agent_id} failed: {e}")
            results.append({
                "agent_id": str(agent_id),
                "agent_name": agent_name,
                "error": str(e),
            })
    
    return {
        "type": "parallel",
        "results": results,
        "combined_output": "\n\n".join([r.get("output", "") for r in results if "output" in r]),
    }


async def execute_branching(
    session: AsyncSession,
    agent_ids: List[UUID],
    input_data: Dict[str, Any],
    config: Dict[str, Any]
) -> Dict[str, Any]:
    """Execute agents based on branching conditions."""
    # TODO: Implement branching logic based on config
    # For now, fall back to sequential
    return await execute_sequential(session, agent_ids, input_data)


async def execute_agent_task(
    agent: AgentDefinition,
    input_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Execute a single agent task.
    
    TODO: Integrate with actual agent execution system.
    For now, returns mock response.
    """
    # Simulate agent execution
    await asyncio.sleep(0.1)
    
    return {
        "agent": agent.name,
        "status": "completed",
        "output": f"Agent {agent.name} processed the task successfully",
        "input_summary": str(input_data)[:100],
    }
