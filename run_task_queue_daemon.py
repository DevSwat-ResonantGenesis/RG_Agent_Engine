#!/usr/bin/env python3
"""
Standalone task queue daemon runner.
Bypasses FastAPI startup events by running directly.
"""
import asyncio
import os
import sys
import logging

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Add app to path
sys.path.insert(0, os.path.dirname(__file__))

from app.task_queue_daemon import start_task_queue, stop_task_queue

async def main():
    logging.info("[TASK_QUEUE] Starting daemon as standalone process...")
    await start_task_queue()

    # Run forever until interrupted
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        logging.info("[TASK_QUEUE] Interrupted, stopping...")
        await stop_task_queue()
        logging.info("[TASK_QUEUE] Stopped")

if __name__ == "__main__":
    asyncio.run(main())
