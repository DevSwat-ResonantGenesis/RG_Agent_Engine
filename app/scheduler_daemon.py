"""
Scheduler Daemon
================

Asyncio background task that polls the DB for due schedules and triggers,
then fires agent sessions. Runs inside the FastAPI process — no Celery Beat needed.

Started on app startup via main.py when AGENT_ENGINE_ENABLE_SCHEDULER=true.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import select, text, and_

from .db import async_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
POLL_INTERVAL = int(os.getenv("SCHEDULER_POLL_INTERVAL", "60"))  # seconds
MAX_CONCURRENT_SCHEDULED = int(os.getenv("SCHEDULER_MAX_CONCURRENT", "2"))

_running = False
_task: Optional[asyncio.Task] = None
_semaphore: Optional[asyncio.Semaphore] = None


# ---------------------------------------------------------------------------
# Cron helper
# ---------------------------------------------------------------------------
def _next_run_from_cron(cron_expr: str, after: datetime) -> Optional[datetime]:
    """Calculate next run from a cron expression. Returns None if croniter unavailable."""
    try:
        from croniter import croniter
        return croniter(cron_expr, after).get_next(datetime)
    except ImportError:
        logger.warning("croniter not installed — falling back to 1-hour interval")
        return after + timedelta(hours=1)
    except Exception as e:
        logger.error(f"Bad cron expression '{cron_expr}': {e}")
        return None


def _next_run_from_interval(interval_seconds: int, after: datetime) -> datetime:
    return after + timedelta(seconds=interval_seconds)


# ---------------------------------------------------------------------------
# Core poll loop
# ---------------------------------------------------------------------------
async def _poll_once() -> int:
    """Check for due schedules + triggers and fire sessions. Returns count fired."""
    fired = 0
    now = datetime.now(timezone.utc)

    try:
        async with async_session() as db:
            # ---- 1. AgentSchedule (models_schedule.py) ----
            try:
                rows = await db.execute(
                    text("""
                        SELECT id, agent_id, user_id, goal, context,
                               cron_expression, interval_seconds
                        FROM agent_schedules
                        WHERE enabled = true
                          AND (next_run_at IS NULL OR next_run_at <= :now)
                    """),
                    {"now": now},
                )
                for row in rows.mappings():
                    sid = str(uuid4())
                    agent_id = str(row["agent_id"])
                    user_id = row.get("user_id")
                    goal = row.get("goal") or "Scheduled execution"
                    ctx = row.get("context") or {}

                    logger.info(
                        f"[SCHEDULER] Firing AgentSchedule {row['id']} → "
                        f"agent={agent_id} session={sid}"
                    )

                    # Calculate next_run_at
                    nxt = None
                    if row.get("cron_expression"):
                        nxt = _next_run_from_cron(row["cron_expression"], now)
                    elif row.get("interval_seconds"):
                        nxt = _next_run_from_interval(row["interval_seconds"], now)
                    else:
                        nxt = now + timedelta(hours=1)

                    await db.execute(
                        text("""
                            UPDATE agent_schedules
                            SET last_run_at = :now,
                                next_run_at = :nxt,
                                run_count = run_count + 1
                            WHERE id = :sid
                        """),
                        {"now": now, "nxt": nxt, "sid": row["id"]},
                    )

                    # Enqueue task via PostgreSQL task queue
                    from .task_queue_daemon import enqueue_task
                    await enqueue_task(
                        agent_id=agent_id,
                        goal=goal,
                        context=ctx,
                        user_id=user_id,
                        source="schedule",
                        source_id=str(row["id"]),
                        priority=5,  # Schedules get medium priority
                    )
                    fired += 1

                await db.commit()
            except Exception as e:
                if "agent_schedules" in str(e).lower() and "does not exist" in str(e).lower():
                    pass  # table not created yet — skip silently
                else:
                    logger.warning(f"[SCHEDULER] AgentSchedule poll error: {e}")
                await db.rollback()

            # ---- 2. WorkflowTrigger (models.py) — cron-type only ----
            try:
                rows = await db.execute(
                    text("""
                        SELECT id, agent_id, name, trigger_type, config,
                               cron_expression, next_run_at, input_template
                        FROM workflow_triggers
                        WHERE is_active = true
                          AND trigger_type = 'schedule'
                          AND (next_run_at IS NULL OR next_run_at <= :now)
                    """),
                    {"now": now},
                )
                for row in rows.mappings():
                    sid = str(uuid4())
                    agent_id = str(row["agent_id"])
                    cfg = row.get("config") or {}
                    goal = cfg.get("goal") or f"Scheduled workflow: {row.get('name', '')}"

                    logger.info(
                        f"[SCHEDULER] Firing WorkflowTrigger {row['id']} → "
                        f"agent={agent_id} session={sid}"
                    )

                    nxt = None
                    if row.get("cron_expression"):
                        nxt = _next_run_from_cron(row["cron_expression"], now)
                    else:
                        interval = cfg.get("interval_seconds", 3600)
                        nxt = _next_run_from_interval(interval, now)

                    await db.execute(
                        text("""
                            UPDATE workflow_triggers
                            SET last_triggered_at = :now,
                                next_run_at = :nxt,
                                trigger_count = trigger_count + 1
                            WHERE id = :tid
                        """),
                        {"now": now, "nxt": nxt, "tid": row["id"]},
                    )

                    # Enqueue task via PostgreSQL task queue
                    from .task_queue_daemon import enqueue_task
                    await enqueue_task(
                        agent_id=agent_id,
                        goal=goal,
                        context=cfg,
                        user_id=None,
                        source="workflow_trigger",
                        source_id=str(row["id"]),
                        priority=5,
                    )
                    fired += 1

                await db.commit()
            except Exception as e:
                if "workflow_triggers" in str(e).lower() and "does not exist" in str(e).lower():
                    pass
                else:
                    logger.warning(f"[SCHEDULER] WorkflowTrigger poll error: {e}")
                await db.rollback()

    except Exception as e:
        logger.error(f"[SCHEDULER] Outer poll error: {e}")

    return fired


async def _fire_session(*, agent_id: str, goal: str, context: dict,
                        user_id: Optional[str], source: str) -> None:
    """Create a session row and run the agent loop."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT_SCHEDULED)

    if _semaphore.locked():
        logger.warning(f"[SCHEDULER] Skipping {source}: max concurrent ({MAX_CONCURRENT_SCHEDULED}) reached")
        return

    async with _semaphore:
        await _fire_session_inner(agent_id=agent_id, goal=goal, context=context, user_id=user_id, source=source)


async def _fire_session_inner(*, agent_id: str, goal: str, context: dict,
                               user_id: Optional[str], source: str) -> None:
    from uuid import UUID as PyUUID

    try:
        async with async_session() as db:
            from .models import AgentDefinition, AgentSession

            agent = await db.get(AgentDefinition, PyUUID(agent_id))
            if not agent:
                logger.error(f"[SCHEDULER] Agent {agent_id} not found for {source}")
                return

            # Skip archived/inactive agents — disable the schedule too
            if not agent.is_active or agent.archived_at is not None:
                logger.warning(
                    f"[SCHEDULER] Skipping {source}: agent '{agent.name}' is "
                    f"{'archived' if agent.archived_at else 'inactive'} — disabling schedule"
                )
                parts = source.split(":", 1)
                if len(parts) == 2 and parts[0] == "schedule":
                    await db.execute(
                        text("UPDATE agent_schedules SET enabled = false WHERE id = :sid"),
                        {"sid": parts[1]},
                    )
                    await db.commit()
                return

            session = AgentSession(
                id=uuid4(),
                agent_id=agent.id,
                user_id=user_id or str(agent.user_id or "system"),
                status="running",
                current_goal=goal,
                context=context,
                started_at=datetime.now(timezone.utc),
            )
            db.add(session)
            await db.commit()
            await db.refresh(session)

            logger.info(f"[SCHEDULER] Session {session.id} created for {source}")

            from .executor import agent_executor

            await agent_executor.run_loop(
                session=session,
                agent=agent,
                db_session=db,
            )

            logger.info(f"[SCHEDULER] Session {session.id} completed ({source})")

    except Exception as e:
        logger.error(f"[SCHEDULER] _fire_session failed for {source}: {e}")

    # Update success_count / failure_count on the schedule row
    await _update_schedule_counts(source)


async def _update_schedule_counts(source: str) -> None:
    """Increment success_count or failure_count on the schedule/trigger row.

    `source` is e.g. "schedule:<uuid>" or "workflow_trigger:<uuid>".
    We look at the LAST session fired for that agent to determine outcome.
    Since _fire_session_inner already ran run_loop, the session row has its
    final status by now (completed / failed).
    """
    try:
        parts = source.split(":", 1)
        if len(parts) != 2:
            return
        source_type, source_id = parts

        async with async_session() as db:
            if source_type == "schedule":
                # Find the most recent session for this schedule's agent
                row = await db.execute(
                    text("""
                        SELECT s.status
                        FROM agent_sessions s
                        JOIN agent_schedules sc ON sc.agent_id = s.agent_id
                        WHERE sc.id = :sid
                        ORDER BY s.created_at DESC
                        LIMIT 1
                    """),
                    {"sid": source_id},
                )
                status_row = row.fetchone()
                if status_row:
                    final_status = status_row[0]
                    if final_status == "completed":
                        await db.execute(
                            text("UPDATE agent_schedules SET success_count = success_count + 1 WHERE id = :sid"),
                            {"sid": source_id},
                        )
                    elif final_status == "failed":
                        await db.execute(
                            text("UPDATE agent_schedules SET failure_count = failure_count + 1 WHERE id = :sid"),
                            {"sid": source_id},
                        )
                    await db.commit()

            elif source_type == "workflow_trigger":
                # workflow_triggers don't have success/failure columns yet — skip
                pass

    except Exception as e:
        logger.warning(f"[SCHEDULER] Failed to update counts for {source}: {e}")


# ---------------------------------------------------------------------------
# Daemon lifecycle
# ---------------------------------------------------------------------------
async def _daemon_loop() -> None:
    global _running
    logger.info(f"[SCHEDULER] Daemon started (poll every {POLL_INTERVAL}s)")

    while _running:
        try:
            fired = await _poll_once()
            if fired:
                logger.info(f"[SCHEDULER] Poll fired {fired} session(s)")
        except Exception as e:
            logger.error(f"[SCHEDULER] Loop error: {e}")

        await asyncio.sleep(POLL_INTERVAL)

    logger.info("[SCHEDULER] Daemon stopped")


async def start_scheduler() -> None:
    global _running, _task
    if _running:
        return
    _running = True
    _task = asyncio.create_task(_daemon_loop())
    logger.info("[SCHEDULER] Scheduler daemon starting")


async def stop_scheduler() -> None:
    global _running, _task
    _running = False
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    logger.info("[SCHEDULER] Scheduler daemon stopped")
