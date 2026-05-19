"""
Task Queue Daemon
=================

PostgreSQL-backed task queue for agent execution.
Tasks survive restarts, support retries, and have priority scheduling.

Started on app startup via main.py when AGENT_ENGINE_ENABLE_TASK_QUEUE=true.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID as PyUUID

from sqlalchemy import text

from .db import async_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
POLL_INTERVAL = int(os.getenv("TASK_QUEUE_POLL_INTERVAL", "5"))  # seconds
MAX_CONCURRENT_TASKS = int(os.getenv("TASK_QUEUE_MAX_CONCURRENT", "4"))
TASK_TIMEOUT = int(os.getenv("TASK_QUEUE_TIMEOUT", "600"))  # 10 minutes

_running = False
_task: Optional[asyncio.Task] = None
_semaphore: Optional[asyncio.Semaphore] = None


# ---------------------------------------------------------------------------
# Core queue operations
# ---------------------------------------------------------------------------
async def enqueue_task(
    agent_id: str,
    goal: str,
    context: dict,
    user_id: Optional[str],
    source: str,
    source_id: Optional[str] = None,
    priority: int = 0,
    max_retries: int = 3,
) -> str:
    """Enqueue a task for execution. Returns task_id."""
    import json
    async with async_session() as db:
        result = await db.execute(
            text("""
                INSERT INTO agent_task_queue (agent_id, user_id, goal, context, source, source_id, priority, max_retries)
                VALUES (:aid, :uid, :goal, :ctx, :src, :sid, :prio, :max_retries)
                RETURNING id
            """),
            {
                "aid": agent_id,
                "uid": user_id,
                "goal": goal,
                "ctx": json.dumps(context),
                "src": source,
                "sid": source_id,
                "prio": priority,
                "max_retries": max_retries,
            },
        )
        task_id = result.scalar()
        await db.commit()
        logger.info(f"[TASK_QUEUE] Enqueued task {task_id} for agent {agent_id} (source={source})")
        return str(task_id)


async def _mark_running(task_id: str) -> bool:
    """Mark task as running with FOR UPDATE SKIP LOCKED to prevent double-execution."""
    async with async_session() as db:
        result = await db.execute(
            text("""
                UPDATE agent_task_queue
                SET status = 'running', started_at = NOW()
                WHERE id = :tid AND status = 'pending'
                RETURNING id
            """),
            {"tid": task_id},
        )
        await db.commit()
        return result.scalar() is not None


async def _mark_completed(task_id: str, error: Optional[str] = None) -> None:
    """Mark task as completed or failed."""
    async with async_session() as db:
        if error:
            await db.execute(
                text("""
                    UPDATE agent_task_queue
                    SET status = 'failed', completed_at = NOW(), error_message = :err, retry_count = retry_count + 1
                    WHERE id = :tid
                """),
                {"tid": task_id, "err": error},
            )
        else:
            await db.execute(
                text("""
                    UPDATE agent_task_queue
                    SET status = 'completed', completed_at = NOW()
                    WHERE id = :tid
                """),
                {"tid": task_id},
            )
        await db.commit()


async def _check_retry(task_id: str, retry_count: int, max_retries: int) -> bool:
    """Check if task should be retried. Returns True if requeued."""
    if retry_count >= max_retries:
        return False

    async with async_session() as db:
        await db.execute(
            text("""
                UPDATE agent_task_queue
                SET status = 'pending', error_message = NULL, started_at = NULL
                WHERE id = :tid
            """),
            {"tid": task_id},
        )
        await db.commit()
        logger.info(f"[TASK_QUEUE] Requeued task {task_id} (retry {retry_count + 1}/{max_retries})")
        return True


# ---------------------------------------------------------------------------
# Task execution
# ---------------------------------------------------------------------------
async def _execute_task(task_id: str, agent_id: str, goal: str, context: dict, user_id: Optional[str]) -> None:
    """Execute a single task."""
    try:
        async with asyncio.timeout(TASK_TIMEOUT):
            from .scheduler_daemon import _fire_session_inner

            await _fire_session_inner(
                agent_id=agent_id,
                goal=goal,
                context=context,
                user_id=user_id,
                source=f"task_queue:{task_id}",
            )
            await _mark_completed(task_id)
            logger.info(f"[TASK_QUEUE] Task {task_id} completed")
    except asyncio.TimeoutError:
        await _mark_completed(task_id, "Task timeout")
        logger.error(f"[TASK_QUEUE] Task {task_id} timed out")
    except Exception as e:
        # Check retry count
        async with async_session() as db:
            result = await db.execute(
                text("SELECT retry_count, max_retries FROM agent_task_queue WHERE id = :tid"),
                {"tid": task_id},
            )
            row = result.fetchone()
            if row:
                retry_count, max_retries = row
                if await _check_retry(task_id, retry_count, max_retries):
                    return

        await _mark_completed(task_id, str(e))
        logger.error(f"[TASK_QUEUE] Task {task_id} failed: {e}")


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------
async def _poll_once() -> int:
    """Poll for pending tasks and execute them. Returns count executed."""
    executed = 0

    async with async_session() as db:
        # Claim pending tasks (FOR UPDATE SKIP LOCKED prevents double-execution)
        result = await db.execute(
            text("""
                UPDATE agent_task_queue
                SET status = 'running', started_at = NOW()
                WHERE id IN (
                    SELECT id FROM agent_task_queue
                    WHERE status = 'pending'
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 10
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, agent_id, user_id, goal, context, retry_count, max_retries
            """),
        )
        tasks = result.fetchall()
        await db.commit()

        if not tasks:
            return 0

        logger.info(f"[TASK_QUEUE] Claimed {len(tasks)} task(s)")

        # Execute tasks concurrently with semaphore
        global _semaphore
        if _semaphore is None:
            _semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

        async def run_one(task):
            task_id, agent_id, user_id, goal, context, retry_count, max_retries = task
            async with _semaphore:
                await _execute_task(str(task_id), str(agent_id), goal, context, user_id)

        await asyncio.gather(*[run_one(t) for t in tasks])
        executed = len(tasks)

    return executed


# ---------------------------------------------------------------------------
# Daemon lifecycle
# ---------------------------------------------------------------------------
async def _daemon_loop() -> None:
    global _running
    logger.info(f"[TASK_QUEUE] Daemon started (poll every {POLL_INTERVAL}s, max_concurrent={MAX_CONCURRENT_TASKS})")

    while _running:
        try:
            executed = await _poll_once()
            if executed:
                logger.info(f"[TASK_QUEUE] Executed {executed} task(s)")
        except Exception as e:
            logger.error(f"[TASK_QUEUE] Loop error: {e}")

        await asyncio.sleep(POLL_INTERVAL)

    logger.info("[TASK_QUEUE] Daemon stopped")


async def start_task_queue() -> None:
    global _running, _task
    if _running:
        return
    _running = True
    _task = asyncio.create_task(_daemon_loop())
    logger.info("[TASK_QUEUE] Task queue daemon starting")


async def stop_task_queue() -> None:
    global _running, _task
    _running = False
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    logger.info("[TASK_QUEUE] Task queue daemon stopped")


# ---------------------------------------------------------------------------
# Cleanup old completed tasks (call periodically)
# ---------------------------------------------------------------------------
async def cleanup_old_tasks(days: int = 7) -> int:
    """Delete completed/failed tasks older than N days."""
    async with async_session() as db:
        result = await db.execute(
            text("""
                DELETE FROM agent_task_queue
                WHERE status IN ('completed', 'failed')
                  AND completed_at < NOW() - INTERVAL ':days days'
                RETURNING id
            """),
            {"days": days},
        )
        count = len(result.fetchall())
        await db.commit()
        logger.info(f"[TASK_QUEUE] Cleaned up {count} old task(s)")
        return count
