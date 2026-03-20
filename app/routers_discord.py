"""
Discord Integration API
========================

CRUD endpoints for managing Discord ↔ Agent connections.
Users configure which agent handles messages in which Discord guild/channel.

Endpoints:
  POST   /discord/connections          — Create a connection (guild → agent)
  GET    /discord/connections          — List user's connections
  GET    /discord/connections/{id}     — Get one connection
  PATCH  /discord/connections/{id}     — Update a connection
  DELETE /discord/connections/{id}     — Delete a connection
  GET    /discord/invite-url           — Get the bot invite URL
  GET    /discord/lookup/{guild_id}    — Internal: bot looks up agent for a guild (no auth)
"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional, List
from uuid import uuid4

from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/discord", tags=["discord"])

DISCORD_BOT_CLIENT_ID = os.getenv("DISCORD_BOT_CLIENT_ID", "")
PLATFORM_DOMAIN = os.getenv("PLATFORM_DOMAIN", "resonantgenesis.xyz")


# ============================================
# Pydantic Models
# ============================================

class CreateConnectionRequest(BaseModel):
    agent_id: str
    guild_id: str
    guild_name: Optional[str] = None
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    respond_to_mentions: bool = True
    respond_to_dms: bool = True
    respond_to_all: bool = False
    custom_system_prompt: Optional[str] = None


class UpdateConnectionRequest(BaseModel):
    agent_id: Optional[str] = None
    enabled: Optional[bool] = None
    respond_to_mentions: Optional[bool] = None
    respond_to_dms: Optional[bool] = None
    respond_to_all: Optional[bool] = None
    custom_system_prompt: Optional[str] = None
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None


class ConnectionResponse(BaseModel):
    id: str
    user_id: str
    agent_id: str
    agent_name: Optional[str] = None
    guild_id: str
    guild_name: Optional[str] = None
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    enabled: bool
    respond_to_mentions: bool
    respond_to_dms: bool
    respond_to_all: bool
    custom_system_prompt: Optional[str] = None
    message_count: int = 0
    last_message_at: Optional[str] = None
    created_at: Optional[str] = None


class GuildLookupResponse(BaseModel):
    """Returned to the bot for routing decisions."""
    agent_id: str
    user_id: str
    connection_id: str
    respond_to_mentions: bool
    respond_to_dms: bool
    respond_to_all: bool
    custom_system_prompt: Optional[str] = None


# ============================================
# Authenticated Endpoints (via gateway)
# ============================================

def _get_user_id(request: Request) -> str:
    user_id = request.headers.get("x-user-id", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing x-user-id header")
    return user_id


@router.post("/connections", response_model=ConnectionResponse)
async def create_connection(
    body: CreateConnectionRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Create a Discord guild → agent connection."""
    user_id = _get_user_id(request)

    # Verify agent exists and belongs to user
    agent_row = await db.execute(
        text("SELECT id, name, user_id FROM agent_definitions WHERE id = :aid"),
        {"aid": body.agent_id},
    )
    agent = agent_row.mappings().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Check user owns the agent (or is superuser)
    is_superuser = request.headers.get("x-is-superuser", "").lower() == "true"
    if str(agent["user_id"]) != user_id and not is_superuser:
        raise HTTPException(status_code=403, detail="You don't own this agent")

    # Check for existing active connection for this guild+channel
    channel_check = body.channel_id or "__all__"
    existing = await db.execute(
        text("""
            SELECT id FROM discord_connections
            WHERE guild_id = :gid AND COALESCE(channel_id, '__all__') = :cid AND enabled = true
        """),
        {"gid": body.guild_id, "cid": channel_check},
    )
    if existing.first():
        raise HTTPException(
            status_code=409,
            detail="An active connection already exists for this guild/channel. Disable or delete it first.",
        )

    conn_id = str(uuid4())
    await db.execute(
        text("""
            INSERT INTO discord_connections
                (id, user_id, agent_id, guild_id, guild_name, channel_id, channel_name,
                 respond_to_mentions, respond_to_dms, respond_to_all, custom_system_prompt)
            VALUES
                (:id, :uid, :aid, :gid, :gname, :cid, :cname,
                 :mentions, :dms, :all_msg, :prompt)
        """),
        {
            "id": conn_id,
            "uid": user_id,
            "aid": body.agent_id,
            "gid": body.guild_id,
            "gname": body.guild_name,
            "cid": body.channel_id,
            "cname": body.channel_name,
            "mentions": body.respond_to_mentions,
            "dms": body.respond_to_dms,
            "all_msg": body.respond_to_all,
            "prompt": body.custom_system_prompt,
        },
    )
    await db.commit()

    logger.info(f"Created Discord connection {conn_id}: guild {body.guild_id} → agent {body.agent_id} (user {user_id})")

    return ConnectionResponse(
        id=conn_id,
        user_id=user_id,
        agent_id=body.agent_id,
        agent_name=agent.get("name"),
        guild_id=body.guild_id,
        guild_name=body.guild_name,
        channel_id=body.channel_id,
        channel_name=body.channel_name,
        enabled=True,
        respond_to_mentions=body.respond_to_mentions,
        respond_to_dms=body.respond_to_dms,
        respond_to_all=body.respond_to_all,
        custom_system_prompt=body.custom_system_prompt,
        message_count=0,
    )


@router.get("/connections", response_model=List[ConnectionResponse])
async def list_connections(
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """List all Discord connections for the current user."""
    user_id = _get_user_id(request)

    result = await db.execute(
        text("""
            SELECT dc.*, a.name as agent_name
            FROM discord_connections dc
            LEFT JOIN agent_definitions a ON a.id = dc.agent_id
            WHERE dc.user_id = :uid
            ORDER BY dc.created_at DESC
        """),
        {"uid": user_id},
    )
    rows = result.mappings().all()

    return [
        ConnectionResponse(
            id=str(row["id"]),
            user_id=row["user_id"],
            agent_id=str(row["agent_id"]),
            agent_name=row.get("agent_name"),
            guild_id=row["guild_id"],
            guild_name=row.get("guild_name"),
            channel_id=row.get("channel_id"),
            channel_name=row.get("channel_name"),
            enabled=row["enabled"],
            respond_to_mentions=row["respond_to_mentions"],
            respond_to_dms=row["respond_to_dms"],
            respond_to_all=row["respond_to_all"],
            custom_system_prompt=row.get("custom_system_prompt"),
            message_count=row.get("message_count", 0),
            last_message_at=row["last_message_at"].isoformat() if row.get("last_message_at") else None,
            created_at=row["created_at"].isoformat() if row.get("created_at") else None,
        )
        for row in rows
    ]


@router.get("/connections/{connection_id}", response_model=ConnectionResponse)
async def get_connection(
    connection_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Get a single Discord connection."""
    user_id = _get_user_id(request)

    result = await db.execute(
        text("""
            SELECT dc.*, a.name as agent_name
            FROM discord_connections dc
            LEFT JOIN agent_definitions a ON a.id = dc.agent_id
            WHERE dc.id = :cid AND dc.user_id = :uid
        """),
        {"cid": connection_id, "uid": user_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Connection not found")

    return ConnectionResponse(
        id=str(row["id"]),
        user_id=row["user_id"],
        agent_id=str(row["agent_id"]),
        agent_name=row.get("agent_name"),
        guild_id=row["guild_id"],
        guild_name=row.get("guild_name"),
        channel_id=row.get("channel_id"),
        channel_name=row.get("channel_name"),
        enabled=row["enabled"],
        respond_to_mentions=row["respond_to_mentions"],
        respond_to_dms=row["respond_to_dms"],
        respond_to_all=row["respond_to_all"],
        custom_system_prompt=row.get("custom_system_prompt"),
        message_count=row.get("message_count", 0),
        last_message_at=row["last_message_at"].isoformat() if row.get("last_message_at") else None,
        created_at=row["created_at"].isoformat() if row.get("created_at") else None,
    )


@router.patch("/connections/{connection_id}", response_model=ConnectionResponse)
async def update_connection(
    connection_id: str,
    body: UpdateConnectionRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Update a Discord connection."""
    user_id = _get_user_id(request)

    # Build dynamic SET clause
    updates = {}
    set_parts = []
    if body.agent_id is not None:
        updates["agent_id"] = body.agent_id
        set_parts.append("agent_id = :agent_id")
    if body.enabled is not None:
        updates["enabled"] = body.enabled
        set_parts.append("enabled = :enabled")
    if body.respond_to_mentions is not None:
        updates["respond_to_mentions"] = body.respond_to_mentions
        set_parts.append("respond_to_mentions = :respond_to_mentions")
    if body.respond_to_dms is not None:
        updates["respond_to_dms"] = body.respond_to_dms
        set_parts.append("respond_to_dms = :respond_to_dms")
    if body.respond_to_all is not None:
        updates["respond_to_all"] = body.respond_to_all
        set_parts.append("respond_to_all = :respond_to_all")
    if body.custom_system_prompt is not None:
        updates["custom_system_prompt"] = body.custom_system_prompt
        set_parts.append("custom_system_prompt = :custom_system_prompt")
    if body.channel_id is not None:
        updates["channel_id"] = body.channel_id
        set_parts.append("channel_id = :channel_id")
    if body.channel_name is not None:
        updates["channel_name"] = body.channel_name
        set_parts.append("channel_name = :channel_name")

    if not set_parts:
        raise HTTPException(status_code=400, detail="No fields to update")

    set_parts.append("updated_at = NOW()")
    updates["cid"] = connection_id
    updates["uid"] = user_id

    sql = f"UPDATE discord_connections SET {', '.join(set_parts)} WHERE id = :cid AND user_id = :uid RETURNING id"
    result = await db.execute(text(sql), updates)
    if not result.first():
        raise HTTPException(status_code=404, detail="Connection not found")
    await db.commit()

    logger.info(f"Updated Discord connection {connection_id}")

    # Return updated record
    return await get_connection(connection_id, request, db)


@router.delete("/connections/{connection_id}")
async def delete_connection(
    connection_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Delete a Discord connection."""
    user_id = _get_user_id(request)

    result = await db.execute(
        text("DELETE FROM discord_connections WHERE id = :cid AND user_id = :uid RETURNING id"),
        {"cid": connection_id, "uid": user_id},
    )
    if not result.first():
        raise HTTPException(status_code=404, detail="Connection not found")
    await db.commit()

    logger.info(f"Deleted Discord connection {connection_id}")
    return {"status": "deleted", "connection_id": connection_id}


@router.get("/invite-url")
async def get_invite_url():
    """
    Get the bot invite URL that users can use to add the platform bot
    to their Discord server.
    """
    if not DISCORD_BOT_CLIENT_ID:
        raise HTTPException(
            status_code=503,
            detail="Discord bot client ID not configured. Contact platform admin.",
        )

    permissions = 274877975552  # Send Messages, Read History, Read Messages, Embed Links, Add Reactions, Use Slash Commands
    scopes = "bot+applications.commands"
    invite_url = f"https://discord.com/oauth2/authorize?client_id={DISCORD_BOT_CLIENT_ID}&permissions={permissions}&scope={scopes}"

    return {
        "invite_url": invite_url,
        "client_id": DISCORD_BOT_CLIENT_ID,
        "instructions": [
            "1. Click the invite URL to add the bot to your Discord server",
            "2. Select the server and authorize the bot",
            "3. In your Discord server, run: /connect <your-agent-id>",
            "4. The bot will start responding to @mentions!",
        ],
    }


# ============================================
# Internal Endpoints (called by discord_bridge bot, no auth)
# ============================================

@router.get("/lookup/{guild_id}")
async def lookup_guild_connection(
    guild_id: str,
    channel_id: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
):
    """
    Internal endpoint: bot calls this to find which agent handles a guild/channel.
    Tries channel-specific first, then falls back to guild-wide.
    No auth required — only accessible on Docker internal network.
    """
    # Try channel-specific connection first
    if channel_id:
        result = await db.execute(
            text("""
                SELECT dc.id, dc.user_id, dc.agent_id, dc.respond_to_mentions,
                       dc.respond_to_dms, dc.respond_to_all, dc.custom_system_prompt
                FROM discord_connections dc
                WHERE dc.guild_id = :gid AND dc.channel_id = :cid AND dc.enabled = true
                LIMIT 1
            """),
            {"gid": guild_id, "cid": channel_id},
        )
        row = result.mappings().first()
        if row:
            return GuildLookupResponse(
                agent_id=str(row["agent_id"]),
                user_id=row["user_id"],
                connection_id=str(row["id"]),
                respond_to_mentions=row["respond_to_mentions"],
                respond_to_dms=row["respond_to_dms"],
                respond_to_all=row["respond_to_all"],
                custom_system_prompt=row.get("custom_system_prompt"),
            )

    # Fall back to guild-wide connection (channel_id IS NULL)
    result = await db.execute(
        text("""
            SELECT dc.id, dc.user_id, dc.agent_id, dc.respond_to_mentions,
                   dc.respond_to_dms, dc.respond_to_all, dc.custom_system_prompt
            FROM discord_connections dc
            WHERE dc.guild_id = :gid AND dc.channel_id IS NULL AND dc.enabled = true
            LIMIT 1
        """),
        {"gid": guild_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="No active connection for this guild")

    return GuildLookupResponse(
        agent_id=str(row["agent_id"]),
        user_id=row["user_id"],
        connection_id=str(row["id"]),
        respond_to_mentions=row["respond_to_mentions"],
        respond_to_dms=row["respond_to_dms"],
        respond_to_all=row["respond_to_all"],
        custom_system_prompt=row.get("custom_system_prompt"),
    )


@router.post("/lookup/{connection_id}/message-sent")
async def record_message_sent(
    connection_id: str,
    db: AsyncSession = Depends(get_session),
):
    """Internal: bot calls this after successfully processing a message to update stats."""
    await db.execute(
        text("""
            UPDATE discord_connections
            SET message_count = message_count + 1, last_message_at = :now, updated_at = :now
            WHERE id = :cid
        """),
        {"cid": connection_id, "now": datetime.now(timezone.utc)},
    )
    await db.commit()
    return {"status": "ok"}


# ============================================
# Internal Bot-Initiated Endpoints
# ============================================
# These are called by the Discord bot's slash commands (/connect, /disconnect)
# so users can set up connections directly from Discord without API access.

class BotConnectRequest(BaseModel):
    """Body sent by the bot when a user runs /connect in Discord."""
    agent_id: str
    guild_id: str
    guild_name: Optional[str] = None
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    discord_user_id: str
    discord_user_name: Optional[str] = None


@router.post("/internal/connect")
async def bot_create_connection(
    body: BotConnectRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """
    Internal: bot calls this when a user runs /connect <agent_id> in Discord.
    Looks up the agent owner automatically — no platform auth needed.
    Only accessible on Docker internal network (x-internal-service header).
    """
    if request.headers.get("x-internal-service") != "discord_bridge":
        raise HTTPException(status_code=403, detail="Internal endpoint")

    # Look up the agent and its owner
    agent_row = await db.execute(
        text("SELECT id, name, user_id FROM agent_definitions WHERE id = :aid"),
        {"aid": body.agent_id},
    )
    agent = agent_row.mappings().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found. Check your agent ID on the platform dashboard.")

    owner_user_id = str(agent["user_id"])

    # Check for existing active connection for this guild
    channel_check = body.channel_id or "__all__"
    existing = await db.execute(
        text("""
            SELECT id, agent_id FROM discord_connections
            WHERE guild_id = :gid AND COALESCE(channel_id, '__all__') = :cid AND enabled = true
        """),
        {"gid": body.guild_id, "cid": channel_check},
    )
    existing_row = existing.mappings().first()
    if existing_row:
        raise HTTPException(
            status_code=409,
            detail=f"This server already has an active connection (agent: {existing_row['agent_id']}). "
                   f"Use /disconnect first, then /connect again.",
        )

    conn_id = str(uuid4())
    await db.execute(
        text("""
            INSERT INTO discord_connections
                (id, user_id, agent_id, guild_id, guild_name, channel_id, channel_name,
                 respond_to_mentions, respond_to_dms, respond_to_all)
            VALUES
                (:id, :uid, :aid, :gid, :gname, :cid, :cname,
                 true, true, false)
        """),
        {
            "id": conn_id,
            "uid": owner_user_id,
            "aid": body.agent_id,
            "gid": body.guild_id,
            "gname": body.guild_name,
            "cid": body.channel_id,
            "cname": body.channel_name,
        },
    )
    await db.commit()

    logger.info(
        f"Bot /connect: guild {body.guild_id} ({body.guild_name}) → agent {body.agent_id} "
        f"by Discord user {body.discord_user_name} ({body.discord_user_id})"
    )

    return {
        "status": "connected",
        "connection_id": conn_id,
        "agent_id": body.agent_id,
        "agent_name": agent.get("name"),
        "guild_id": body.guild_id,
        "owner_user_id": owner_user_id,
    }


@router.post("/internal/disconnect")
async def bot_remove_connection(
    request: Request,
    guild_id: str = None,
    db: AsyncSession = Depends(get_session),
):
    """
    Internal: bot calls this when a user runs /disconnect in Discord.
    Removes all active connections for the guild.
    """
    if request.headers.get("x-internal-service") != "discord_bridge":
        raise HTTPException(status_code=403, detail="Internal endpoint")

    body = await request.json()
    gid = body.get("guild_id")
    if not gid:
        raise HTTPException(status_code=400, detail="guild_id required")

    result = await db.execute(
        text("""
            DELETE FROM discord_connections WHERE guild_id = :gid AND enabled = true
            RETURNING id, agent_id
        """),
        {"gid": gid},
    )
    deleted = result.mappings().all()
    await db.commit()

    if not deleted:
        raise HTTPException(status_code=404, detail="No active connection found for this server.")

    logger.info(f"Bot /disconnect: removed {len(deleted)} connection(s) for guild {gid}")

    return {
        "status": "disconnected",
        "guild_id": gid,
        "removed_count": len(deleted),
        "removed_agents": [str(d["agent_id"]) for d in deleted],
    }


@router.get("/internal/agents-for-user/{agent_id}")
async def bot_get_agent_info(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """
    Internal: bot calls this to verify an agent exists and get its name.
    Used by the /connect command for confirmation messaging.
    """
    if request.headers.get("x-internal-service") != "discord_bridge":
        raise HTTPException(status_code=403, detail="Internal endpoint")

    result = await db.execute(
        text("SELECT id, name, user_id, status FROM agent_definitions WHERE id = :aid"),
        {"aid": agent_id},
    )
    agent = result.mappings().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return {
        "agent_id": str(agent["id"]),
        "name": agent.get("name"),
        "status": agent.get("status"),
    }
