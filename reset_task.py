#!/usr/bin/env python3
import asyncio
from app.db import async_session
from sqlalchemy import text

async def reset():
    async with async_session() as db:
        await db.execute(text("UPDATE agent_task_queue SET status = 'pending' WHERE status = 'running'"))
        await db.commit()
        print('Reset running tasks to pending')

if __name__ == "__main__":
    asyncio.run(reset())
