import sys
from pathlib import Path

# Add service root to path
SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

"""
Celery Tasks for Agent Engine
=============================

Background tasks for autonomous agent execution.
Enables agents to self-trigger and run without blocking.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from uuid import UUID

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from .celery_app import celery_app
from .db import async_session_maker
from .models import AgentDefinition, AgentSession, AgentSchedule
from .executor import agent_executor

logger = logging.getLogger(__name__)


def run_async(coro):
    """Helper to run async code in sync context."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    bind=True,
    name="app.tasks.execute_agent_session",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def execute_agent_session(
    self,
    session_id: str,
    agent_id: str,
    goal: str,
    context: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute an agent session in the background.
    
    This is the main entry point for background agent execution.
    """
    logger.info(f"Starting background agent session: {session_id}")
    
    async def _execute():
        async with async_session_maker() as db_session:
            # Load agent and session
            agent = await db_session.get(AgentDefinition, UUID(agent_id))
            if not agent:
                return {"status": "error", "error": "Agent not found"}
            
            session = await db_session.get(AgentSession, UUID(session_id))
            if not session:
                # Create new session
                session = AgentSession(
                    id=UUID(session_id),
                    agent_id=UUID(agent_id),
                    user_id=user_id,
                    status="initializing",
                    current_goal=goal,
                    context=context or {},
                    started_at=datetime.now(timezone.utc),
                )
                db_session.add(session)
                await db_session.commit()
                await db_session.refresh(session)
            
            # Update task ID for tracking
            session.celery_task_id = self.request.id
            await db_session.commit()
            
            try:
                # Run the agent loop
                result = await agent_executor.run_loop(
                    session=session,
                    agent=agent,
                    db_session=db_session,
                )
                
                return result
                
            except SoftTimeLimitExceeded:
                # Handle timeout gracefully
                session.status = "timeout"
                session.error_message = "Task exceeded time limit"
                session.completed_at = datetime.now(timezone.utc)
                await db_session.commit()
                
                # Create checkpoint for resumption
                await _create_checkpoint(session, db_session)
                
                return {"status": "timeout", "session_id": session_id}
    
    return run_async(_execute())


@celery_app.task(
    bind=True,
    name="app.tasks.execute_agent_step",
    max_retries=2,
)
def execute_agent_step(
    self,
    session_id: str,
    step_number: int,
) -> Dict[str, Any]:
    """
    Execute a single agent step.
    
    Used for fine-grained control over execution.
    """
    logger.info(f"Executing step {step_number} for session: {session_id}")
    
    async def _execute_step():
        async with async_session_maker() as db_session:
            session = await db_session.get(AgentSession, UUID(session_id))
            if not session:
                return {"status": "error", "error": "Session not found"}
            
            agent = await db_session.get(AgentDefinition, session.agent_id)
            if not agent:
                return {"status": "error", "error": "Agent not found"}
            
            # Execute single step
            result = await agent_executor._execute_step(
                session=session,
                agent=agent,
                tools=[],  # Will be loaded in executor
                history=[],  # Will be loaded from session
                db_session=db_session,
            )
            
            return result
    
    return run_async(_execute_step())


@celery_app.task(name="app.tasks.scheduled_agent_trigger")
def scheduled_agent_trigger(schedule_id: str) -> Dict[str, Any]:
    """
    Trigger an agent based on a schedule.
    
    Called by Celery Beat for periodic agent execution.
    """
    logger.info(f"Processing scheduled trigger: {schedule_id}")
    
    async def _trigger():
        async with async_session_maker() as db_session:
            schedule = await db_session.get(AgentSchedule, UUID(schedule_id))
            if not schedule:
                return {"status": "error", "error": "Schedule not found"}
            
            if not schedule.enabled:
                return {"status": "skipped", "reason": "Schedule disabled"}
            
            # Create new session
            from uuid import uuid4
            session_id = str(uuid4())
            
            # Queue the agent execution
            execute_agent_session.delay(
                session_id=session_id,
                agent_id=str(schedule.agent_id),
                goal=schedule.goal,
                context=schedule.context,
                user_id=schedule.user_id,
            )
            
            # Update schedule
            schedule.last_run_at = datetime.now(timezone.utc)
            schedule.run_count += 1
            await db_session.commit()
            
            return {
                "status": "triggered",
                "session_id": session_id,
                "schedule_id": schedule_id,
            }
    
    return run_async(_trigger())


@celery_app.task(name="app.tasks.process_scheduled_triggers")
def process_scheduled_triggers() -> Dict[str, Any]:
    """
    Process all due scheduled triggers.
    
    Called periodically by Celery Beat.
    """
    logger.info("Processing scheduled triggers")
    
    async def _process():
        from sqlalchemy import select
        
        async with async_session_maker() as db_session:
            now = datetime.now(timezone.utc)
            
            # Find due schedules
            result = await db_session.execute(
                select(AgentSchedule).where(
                    AgentSchedule.enabled == True,
                    AgentSchedule.next_run_at <= now,
                )
            )
            schedules = result.scalars().all()
            
            triggered = 0
            for schedule in schedules:
                # Trigger the agent
                scheduled_agent_trigger.delay(str(schedule.id))
                
                # Calculate next run time
                schedule.next_run_at = _calculate_next_run(
                    schedule.cron_expression,
                    schedule.interval_seconds,
                )
                triggered += 1
            
            await db_session.commit()
            
            return {"triggered": triggered}
    
    return run_async(_process())


@celery_app.task(name="app.tasks.check_agent_health")
def check_agent_health() -> Dict[str, Any]:
    """
    Check health of running agent sessions.
    
    Detects and handles stale/stuck sessions.
    """
    logger.info("Checking agent health")
    
    async def _check():
        from sqlalchemy import select
        
        async with async_session_maker() as db_session:
            now = datetime.now(timezone.utc)
            stale_threshold = 300  # 5 minutes
            
            # Find running sessions that haven't updated
            result = await db_session.execute(
                select(AgentSession).where(
                    AgentSession.status == "running",
                )
            )
            sessions = result.scalars().all()
            
            stale_count = 0
            for session in sessions:
                last_activity = session.last_activity_at or session.started_at
                if last_activity:
                    elapsed = (now - last_activity).total_seconds()
                    if elapsed > stale_threshold:
                        session.status = "stale"
                        stale_count += 1

            # Also expire WAITING_APPROVAL sessions stuck > 30 minutes
            approval_timeout = 1800  # 30 minutes
            result2 = await db_session.execute(
                select(AgentSession).where(
                    AgentSession.status == "waiting_approval",
                )
            )
            approval_sessions = result2.scalars().all()
            for session in approval_sessions:
                last_activity = session.last_activity_at or session.started_at
                if last_activity:
                    elapsed = (now - last_activity).total_seconds()
                    if elapsed > approval_timeout:
                        session.status = "failed"
                        session.error_message = "Approval timeout — no response within 30 minutes"
                        session.completed_at = now
                        stale_count += 1
            
            await db_session.commit()
            
            return {
                "checked": len(sessions),
                "stale": stale_count,
            }
    
    return run_async(_check())


@celery_app.task(name="app.tasks.cleanup_stale_sessions")
def cleanup_stale_sessions() -> Dict[str, Any]:
    """
    Clean up stale agent sessions.
    
    Marks old stale sessions as failed and frees resources.
    """
    logger.info("Cleaning up stale sessions")
    
    async def _cleanup():
        from sqlalchemy import select
        
        async with async_session_maker() as db_session:
            now = datetime.now(timezone.utc)
            
            # Find stale sessions older than 1 hour + lingering waiting_approval
            result = await db_session.execute(
                select(AgentSession).where(
                    AgentSession.status.in_(["stale", "waiting_approval"]),
                )
            )
            sessions = result.scalars().all()
            
            cleaned = 0
            for session in sessions:
                if session.last_activity_at:
                    elapsed = (now - session.last_activity_at).total_seconds()
                    if elapsed > 3600:  # 1 hour
                        session.status = "failed"
                        session.error_message = "Session timed out (stale)"
                        session.completed_at = now
                        cleaned += 1
            
            await db_session.commit()
            
            return {"cleaned": cleaned}
    
    return run_async(_cleanup())


@celery_app.task(
    bind=True,
    name="app.tasks.resume_agent_session",
)
def resume_agent_session(self, session_id: str) -> Dict[str, Any]:
    """
    Resume a paused or checkpointed agent session.
    """
    logger.info(f"Resuming agent session: {session_id}")
    
    async def _resume():
        async with async_session_maker() as db_session:
            session = await db_session.get(AgentSession, UUID(session_id))
            if not session:
                return {"status": "error", "error": "Session not found"}
            
            if session.status not in ("paused", "timeout", "stale"):
                return {"status": "error", "error": f"Cannot resume session in status: {session.status}"}
            
            agent = await db_session.get(AgentDefinition, session.agent_id)
            if not agent:
                return {"status": "error", "error": "Agent not found"}
            
            # Restore from checkpoint if available
            checkpoint = session.checkpoint_data
            if checkpoint:
                session.context = checkpoint.get("context", session.context)
            
            session.status = "running"
            session.celery_task_id = self.request.id
            await db_session.commit()
            
            # Continue execution
            result = await agent_executor.run_loop(
                session=session,
                agent=agent,
                db_session=db_session,
            )
            
            return result
    
    return run_async(_resume())


async def _create_checkpoint(session: AgentSession, db_session) -> None:
    """Create a checkpoint for session resumption."""
    session.checkpoint_data = {
        "context": session.context,
        "loop_count": session.loop_count,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_session.commit()


def _calculate_next_run(cron_expression: Optional[str], interval_seconds: Optional[int]) -> datetime:
    """Calculate next run time based on schedule."""
    now = datetime.now(timezone.utc)
    
    if interval_seconds:
        from datetime import timedelta
        return now + timedelta(seconds=interval_seconds)
    
    if cron_expression:
        try:
            from croniter import croniter
            cron = croniter(cron_expression, now)
            return cron.get_next(datetime)
        except ImportError:
            logger.warning("croniter not installed, using default interval")
            from datetime import timedelta
            return now + timedelta(hours=1)
    
    # Default: 1 hour
    from datetime import timedelta
    return now + timedelta(hours=1)


# Convenience functions for API
def queue_agent_execution(
    agent_id: str,
    goal: str,
    context: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    priority: str = "normal",
) -> str:
    """
    Queue an agent for background execution.
    
    Returns the session ID for tracking.
    """
    from uuid import uuid4
    session_id = str(uuid4())
    
    # Choose queue based on priority
    queue = "agents"
    if priority == "high":
        queue = "agents.high"
    elif priority == "low":
        queue = "agents.low"
    
    execute_agent_session.apply_async(
        kwargs={
            "session_id": session_id,
            "agent_id": agent_id,
            "goal": goal,
            "context": context,
            "user_id": user_id,
        },
        queue=queue,
    )
    
    return session_id


def get_task_status(task_id: str) -> Dict[str, Any]:
    """Get the status of a Celery task."""
    result = celery_app.AsyncResult(task_id)
    
    return {
        "task_id": task_id,
        "status": result.status,
        "ready": result.ready(),
        "successful": result.successful() if result.ready() else None,
        "result": result.result if result.ready() else None,
    }
