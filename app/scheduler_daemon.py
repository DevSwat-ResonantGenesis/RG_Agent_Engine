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
POLL_INTERVAL = int(os.getenv("SCHEDULER_POLL_INTERVAL", "30"))  # seconds
MAX_CONCURRENT_SCHEDULED = int(os.getenv("SCHEDULER_MAX_CONCURRENT", "3"))

_running = False
_task: Optional[asyncio.Task] = None


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

                    # Fire the session in the background
                    asyncio.create_task(
                        _fire_session(agent_id=agent_id, goal=goal,
                                      context=ctx, user_id=user_id,
                                      source=f"schedule:{row['id']}")
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

                    asyncio.create_task(
                        _fire_session(agent_id=agent_id, goal=goal,
                                      context=cfg, user_id=None,
                                      source=f"workflow_trigger:{row['id']}")
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
    from uuid import UUID as PyUUID

    try:
        async with async_session() as db:
            from .models import AgentDefinition, AgentSession

            agent = await db.get(AgentDefinition, PyUUID(agent_id))
            if not agent:
                logger.error(f"[SCHEDULER] Agent {agent_id} not found for {source}")
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
