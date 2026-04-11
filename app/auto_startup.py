"""
AUTO-STARTUP FOR FULL AUTONOMY
==============================

Automatically starts the full autonomy system when the service boots.
No manual intervention required - agents start working immediately.

Features:
- Auto-start on FastAPI startup
- Watchdog for system health
- Auto-recovery from failures
- Graceful shutdown
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class AutoStartupManager:
    """
    Manages automatic startup of full autonomy.
    """
    
    def __init__(self):
        self._started = False
        self._watchdog_task: Optional[asyncio.Task] = None
        self._system = None
        self._startup_time: Optional[str] = None
        self._restart_count = 0
    
    async def on_startup(self):
        """Called when FastAPI starts - initiates full autonomy."""
        logger.info("=" * 60)
        logger.info("RESONANT GENESIS AUTO-STARTUP")
        logger.info("=" * 60)
        
        try:
            # Start full autonomy system with default genesis agent
            from .full_autonomy import start_full_autonomy, get_full_autonomy_system
            
            # Initialize the system
            self._system = get_full_autonomy_system()
            
            # Start full autonomy for the genesis agent
            await start_full_autonomy(
                agent_id="genesis-agent-001",
                goal="Coordinate the Resonant Genesis autonomous system and help users achieve their goals"
            )
            
            self._started = True
            self._startup_time = datetime.now(timezone.utc).isoformat()
            
            # Start watchdog
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())
            
            # Create initial autonomous agent
            await self._create_genesis_agent()
            
            logger.info("=" * 60)
            logger.info("RESONANT GENESIS FULLY OPERATIONAL")
            logger.info("Agents are running autonomously")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"Auto-startup failed: {e}")
    
    async def on_shutdown(self):
        """Called when FastAPI shuts down."""
        logger.info("Shutting down Resonant Genesis...")
        
        if self._watchdog_task:
            self._watchdog_task.cancel()
        
        if self._system:
            await self._system.stop()
        
        self._started = False
        logger.info("Resonant Genesis shutdown complete")
    
    async def _create_genesis_agent(self):
        """Create the initial genesis agent."""
        if not self._system:
            return
        
        try:
            agent_id = await self._system.create_autonomous_agent(
                name="GenesisAgent",
                goal="Coordinate the Resonant Genesis autonomous system and help users achieve their goals",
                capabilities=["coordinate", "execute", "learn", "spawn", "communicate"],
            )
            logger.info(f"Genesis Agent created: {agent_id}")
        except Exception as e:
            logger.error(f"Failed to create genesis agent: {e}")
    
    async def _watchdog_loop(self):
        """Watchdog that monitors system health and auto-recovers."""
        while self._started:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                if not self._system:
                    continue
                
                status = self._system.get_status()
                
                # Check if system is healthy
                healthy = status.get("healthy_subsystems", 0)
                total = status.get("total_subsystems", 1)
                
                if healthy < total * 0.5:  # Less than 50% healthy
                    logger.warning("System health degraded, attempting recovery")
                    await self._attempt_recovery()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Watchdog error: {e}")
    
    async def _attempt_recovery(self):
        """Attempt to recover the system."""
        self._restart_count += 1
        
        if self._restart_count > 5:
            logger.error("Too many restart attempts, giving up")
            return
        
        try:
            # Restart full autonomy
            from .full_autonomy import start_full_autonomy, get_full_autonomy_system
            self._system = get_full_autonomy_system()
            await start_full_autonomy(
                agent_id="genesis-agent-001",
                goal="Coordinate the Resonant Genesis autonomous system and help users achieve their goals"
            )
            
            logger.info("System recovered successfully")
            
        except Exception as e:
            logger.error(f"Recovery failed: {e}")
    
    def get_status(self):
        """Get auto-startup status."""
        return {
            "started": self._started,
            "startup_time": self._startup_time,
            "restart_count": self._restart_count,
            "watchdog_active": self._watchdog_task is not None and not self._watchdog_task.done(),
        }


# Global instance
_manager = AutoStartupManager()


async def auto_startup():
    """Auto-startup entry point."""
    await _manager.on_startup()


async def auto_shutdown():
    """Auto-shutdown entry point."""
    await _manager.on_shutdown()


def get_startup_manager() -> AutoStartupManager:
    """Get the startup manager."""
    return _manager
