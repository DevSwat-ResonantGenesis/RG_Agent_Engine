"""
Agent Chat Bridge - Connects platform agents to team chat
Allows agents from agent_engine_service to send/read messages in the
shared team chat system. Uses the IDE terminal service for execution.
NO direct SSH keys or SSH credentials in code.
"""

import logging
import os
import re
from datetime import datetime
from typing import Optional, List, Dict, Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents/chat", tags=["agent-chat-bridge"])

# IDE terminal service — all commands go through this, no direct SSH
IDE_SERVICE_URL = os.getenv("IDE_SERVICE_URL", "http://ide_platform_service:8080")

# Chat script path (on the server where ide_platform_service runs)
CHAT_SCRIPT = os.getenv("CHAT_SCRIPT_PATH", "~/cascade_chat/chat.sh")


# ============================================
# MODELS
# ============================================

class ChatSendRequest(BaseModel):
    agent_name: str  # e.g., "Platform_Agent_1", "IDE_Agent"
    message: str
    
class ChatMessage(BaseModel):
    timestamp: Optional[str] = None
    sender: Optional[str] = None
    content: str
    raw: str

class ChatHistoryResponse(BaseModel):
    success: bool
    messages: List[ChatMessage]
    count: int
    error: Optional[str] = None


# ============================================
# IDE TERMINAL EXECUTION (no SSH keys)
# ============================================

async def run_via_terminal(command: str, timeout: int = 15) -> Dict[str, Any]:
    """Execute a command through the IDE terminal service.
    No SSH keys or credentials stored in code.
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
                    "returncode": data.get("exit_code", -1),
                }
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Terminal service returned {resp.status_code}",
                "returncode": -1,
            }
    except httpx.ConnectError:
        return {
            "success": False,
            "stdout": "",
            "stderr": "IDE terminal service unavailable",
            "returncode": -1,
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
        }


def parse_chat_messages(raw_output: str) -> List[ChatMessage]:
    """Parse raw chat output into structured messages.
    
    Expected format from chat.sh read:
    [2026-02-28 10:30:00] Agent11: Hello team, I'm working on IDE integration
    """
    messages = []
    for line in raw_output.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        
        # Try to parse structured format: [timestamp] sender: message
        match = re.match(r'\[([^\]]+)\]\s+([^:]+):\s+(.*)', line)
        if match:
            messages.append(ChatMessage(
                timestamp=match.group(1),
                sender=match.group(2).strip(),
                content=match.group(3).strip(),
                raw=line,
            ))
        else:
            # Unstructured line
            messages.append(ChatMessage(
                content=line,
                raw=line,
            ))
    
    return messages


# ============================================
# ENDPOINTS
# ============================================

@router.post("/send")
async def send_chat_message(request: ChatSendRequest):
    """Send a message to the team chat as a platform agent.
    
    Executes the chat script through the IDE terminal.
    """
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    if not request.agent_name.strip():
        raise HTTPException(status_code=400, detail="Agent name required")
    
    # Sanitize for shell safety
    safe_message = request.message.replace('"', '\\"').replace("'", "\\'")
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', request.agent_name)
    
    chat_command = f'{CHAT_SCRIPT} {safe_name} send "{safe_message}"'
    
    result = await run_via_terminal(chat_command)
    
    if result["success"]:
        logger.info(f"Chat message sent by {safe_name}: {request.message[:80]}...")
        return {
            "success": True,
            "message": "Message sent",
            "agent": safe_name,
            "timestamp": datetime.utcnow().isoformat(),
        }
    else:
        error_msg = result["stderr"] or "Failed to send message"
        logger.error(f"Chat send failed for {safe_name}: {error_msg}")
        return {
            "success": False,
            "error": error_msg,
            "agent": safe_name,
        }


@router.get("/read")
async def read_chat_messages(
    lines: int = Query(default=50, ge=1, le=500, description="Number of recent messages to fetch"),
):
    """Read recent messages from the team chat."""
    chat_command = f'{CHAT_SCRIPT} system read {lines}'
    
    result = await run_via_terminal(chat_command, timeout=15)
    
    if result["success"]:
        messages = parse_chat_messages(result["stdout"])
        return ChatHistoryResponse(
            success=True,
            messages=messages,
            count=len(messages),
        )
    
    return ChatHistoryResponse(
        success=False,
        messages=[],
        count=0,
        error=result["stderr"] or "Failed to read chat",
    )


@router.get("/history")
async def get_chat_history():
    """Get full chat history."""
    chat_command = f'{CHAT_SCRIPT} system read 500'
    
    result = await run_via_terminal(chat_command, timeout=30)
    
    if result["success"]:
        messages = parse_chat_messages(result["stdout"])
        return ChatHistoryResponse(
            success=True,
            messages=messages,
            count=len(messages),
        )
    
    return ChatHistoryResponse(
        success=False,
        messages=[],
        count=0,
        error=result.get("stderr", "Failed to get history"),
    )


@router.post("/{agent_id}/send")
async def agent_send_message(agent_id: str, request: ChatSendRequest):
    """Send a message as a specific agent (by agent_id)."""
    logger.info(f"Agent {agent_id} ({request.agent_name}) sending chat message")
    
    prefixed_message = f"[AgentID:{agent_id[:8]}] {request.message}"
    safe_message = prefixed_message.replace('"', '\\"').replace("'", "\\'")
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', request.agent_name)
    
    chat_command = f'{CHAT_SCRIPT} {safe_name} send "{safe_message}"'
    result = await run_via_terminal(chat_command)
    
    return {
        "success": result["success"],
        "agent_id": agent_id,
        "agent_name": safe_name,
        "timestamp": datetime.utcnow().isoformat(),
        "error": result.get("stderr") if not result["success"] else None,
    }


@router.get("/status")
async def chat_bridge_status():
    """Check if the chat bridge is working via IDE terminal."""
    result = await run_via_terminal("echo chat_bridge_ok", timeout=5)
    
    return {
        "connected": result["success"],
        "ide_platform_service": IDE_SERVICE_URL,
        "chat_script": CHAT_SCRIPT,
        "error": result.get("stderr") if not result["success"] else None,
        "timestamp": datetime.utcnow().isoformat(),
    }
