#!/usr/bin/env python3
import asyncio
from app.db import async_session
from sqlalchemy import text

async def check():
    async with async_session() as db:
        r = await db.execute(text("SELECT COUNT(*) FROM agent_task_queue WHERE status = 'completed'"))
        print('Completed tasks:', r.scalar())
        r2 = await db.execute(text("SELECT COUNT(*) FROM agent_task_queue WHERE status = 'pending'"))
        print('Pending tasks:', r2.scalar())
        r3 = await db.execute(text("SELECT COUNT(*) FROM agent_task_queue WHERE status = 'running'"))
        print('Running tasks:', r3.scalar())

if __name__ == "__main__":
    asyncio.run(check())
