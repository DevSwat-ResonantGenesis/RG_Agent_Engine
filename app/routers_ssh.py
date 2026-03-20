"""
Agent Terminal Execution Module
Allows platform agents to execute commands through the IDE terminal service.
NO direct SSH — agents use the same IDE terminal that users do.
Includes safety controls and audit logging.
"""

import logging
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["agent-terminal"])

# IDE service URL — agents execute commands through the IDE terminal
IDE_SERVICE_URL = os.getenv("IDE_SERVICE_URL", "http://ide_platform_service:8080")

# Safety: commands that are never allowed
BLOCKED_COMMANDS = [
    "rm -rf /",
    "mkfs",
    "dd if=",
    ":(){:|:&};:",
    "chmod -R 777 /",
    "shutdown",
    "reboot",
    "init 0",
    "init 6",
]

# Safety: allowed command prefixes for restricted mode
ALLOWED_PREFIXES = [
    "ls", "cat", "head", "tail", "grep", "find", "wc",
    "echo", "pwd", "whoami", "date", "uptime",
    "docker ps", "docker logs", "docker inspect",
    "git status", "git log", "git branch", "git diff",
    "pip list", "pip show", "python --version",
    "node --version", "npm list",
    "curl", "wget",
    "df", "du", "free", "top -bn1", "ps aux",
    "systemctl status", "journalctl",
]

# In-memory audit log (production: use DB)
audit_log: List[Dict[str, Any]] = []
MAX_AUDIT_LOG = 1000


# ============================================
# MODELS
# ============================================

class AgentTerminalRequest(BaseModel):
    command: str
    timeout: int = 30
    restricted: bool = True  # If True, only allow safe commands
    cwd: Optional[str] = None  # Working directory

class AgentTerminalResponse(BaseModel):
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    command: str
    agent_id: str
    timestamp: str


# ============================================
# SAFETY CHECK
# ============================================

def is_command_safe(command: str, restricted: bool = True) -> tuple[bool, str]:
    """Check if a command is safe to execute.
    
    Returns (is_safe, reason).
    """
    cmd_lower = command.strip().lower()
    
    # Always block dangerous commands
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd_lower:
            return False, f"Blocked: dangerous command pattern '{blocked}'"
    
    # Block command chaining with destructive potential
    if "&&" in command and any(d in cmd_lower for d in ["rm ", "kill ", "pkill "]):
        return False, "Blocked: destructive chained command"
    
    # In restricted mode, only allow whitelisted prefixes
    if restricted:
        allowed = False
        for prefix in ALLOWED_PREFIXES:
            if cmd_lower.startswith(prefix):
                allowed = True
                break
        
        if not allowed:
            return False, f"Restricted mode: command not in allowed list. Set restricted=false for full access."
    
    return True, "ok"


# ============================================
# IDE TERMINAL EXECUTION (no SSH, no keys)
# ============================================

async def execute_via_ide_terminal(
    command: str,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Execute a command through the IDE terminal service.
    
    This routes through ide_platform_service's /terminal/execute endpoint,
    which runs commands as a local subprocess. No SSH involved.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout + 5) as client:
            resp = await client.post(
                f"{IDE_SERVICE_URL}/terminal/execute",
                json={"command": command, "timeout": timeout},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "success": data.get("success", False),
                    "stdout": data.get("stdout", ""),
                    "stderr": data.get("stderr", ""),
                    "exit_code": data.get("exit_code", -1),
                }
            else:
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": f"IDE terminal returned {resp.status_code}",
                    "exit_code": -1,
                }
    except httpx.ConnectError:
        return {
            "success": False,
            "stdout": "",
            "stderr": "IDE terminal service unavailable",
            "exit_code": -1,
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1,
        }


def log_audit(agent_id: str, command: str, result: Dict, blocked: bool = False):
    """Log command execution for audit trail."""
    entry = {
        "agent_id": agent_id,
        "command": command,
        "success": result.get("success", False),
        "exit_code": result.get("exit_code", -1),
        "blocked": blocked,
        "timestamp": datetime.utcnow().isoformat(),
    }
    audit_log.append(entry)
    
    # Trim log
    if len(audit_log) > MAX_AUDIT_LOG:
        audit_log.pop(0)
    
    if blocked:
        logger.warning(f"BLOCKED command from agent {agent_id}: {command[:80]}")
    else:
        logger.info(f"Terminal command from agent {agent_id}: {command[:80]} -> exit={result.get('exit_code', '?')}")


# ============================================
# ENDPOINTS
# ============================================

@router.post("/{agent_id}/terminal/execute")
async def agent_terminal_execute(agent_id: str, request: AgentTerminalRequest):
    """Execute a command through the IDE terminal as a specific agent.
    
    Safety:
    - By default, restricted=True allows only whitelisted read-only commands.
    - Set restricted=False for full command access (owner-only in production).
    - Dangerous commands are always blocked regardless of restricted setting.
    - All commands are audit-logged.
    - No SSH keys or credentials used — routes through IDE terminal service.
    """
    # Safety check
    is_safe, reason = is_command_safe(request.command, request.restricted)
    if not is_safe:
        result = {"success": False, "stdout": "", "stderr": reason, "exit_code": -1}
        log_audit(agent_id, request.command, result, blocked=True)
        raise HTTPException(status_code=403, detail=reason)
    
    # Execute through IDE terminal
    result = await execute_via_ide_terminal(request.command, request.timeout)
    
    # Audit log
    log_audit(agent_id, request.command, result)
    
    return AgentTerminalResponse(
        success=result["success"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
        command=request.command,
        agent_id=agent_id,
        timestamp=datetime.utcnow().isoformat(),
    )


@router.get("/{agent_id}/terminal/audit")
async def agent_terminal_audit(
    agent_id: str,
    limit: int = Query(default=50, ge=1, le=500),
):
    """Get command audit trail for an agent."""
    agent_entries = [e for e in audit_log if e["agent_id"] == agent_id]
    return {
        "agent_id": agent_id,
        "entries": agent_entries[-limit:],
        "total": len(agent_entries),
    }


@router.get("/terminal/audit")
async def all_terminal_audit(limit: int = Query(default=100, ge=1, le=1000)):
    """Get full terminal command audit trail across all agents."""
    return {
        "entries": audit_log[-limit:],
        "total": len(audit_log),
    }


@router.get("/{agent_id}/terminal/status")
async def agent_terminal_status(agent_id: str):
    """Check if the IDE terminal service is reachable."""
    result = await execute_via_ide_terminal("echo terminal_ok", timeout=5)
    
    return {
        "agent_id": agent_id,
        "connected": result["success"],
        "ide_platform_service": IDE_SERVICE_URL,
        "error": result["stderr"] if not result["success"] else None,
        "timestamp": datetime.utcnow().isoformat(),
    }
