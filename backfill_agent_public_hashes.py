import asyncio
import hashlib
from typing import Optional

from sqlalchemy import select

from app.db import async_session
from app.models import AgentDefinition, AgentVersion
from app.routers import compute_manifest_hash


def _compute_agent_public_hash(*, agent_id: str, owner_user_id: str) -> str:
    digest = hashlib.sha256(f"agent_public:{agent_id}:{owner_user_id}".encode("utf-8")).hexdigest()
    return f"0x{digest}"


async def backfill(limit: Optional[int] = None) -> None:
    async with async_session() as session:
        stmt = select(AgentDefinition).order_by(AgentDefinition.created_at.asc())
        if limit:
            stmt = stmt.limit(int(limit))

        result = await session.execute(stmt)
        agents = result.scalars().all()

        mutated = 0
        created_versions = 0

        for agent in agents:
            changed = False
            safety_config = agent.safety_config or {}

            if not agent.agent_public_hash:
                owner = str(agent.user_id) if agent.user_id else ""
                agent.agent_public_hash = _compute_agent_public_hash(agent_id=str(agent.id), owner_user_id=owner)
                changed = True

            manifest_hash = safety_config.get("manifest_hash")
            if not manifest_hash:
                manifest_hash = compute_manifest_hash(
                    name=agent.name,
                    description=agent.description,
                    system_prompt=agent.system_prompt,
                    model=agent.model,
                    temperature=agent.temperature,
                    max_tokens=agent.max_tokens,
                    tools=agent.tools,
                    allowed_actions=agent.allowed_actions,
                    blocked_actions=agent.blocked_actions,
                )
                safety_config["manifest_hash"] = manifest_hash
                changed = True

            if not agent.agent_version_hash:
                agent.agent_version_hash = str(manifest_hash)
                changed = True

            if safety_config.get("agent_hash") != str(agent.agent_public_hash or ""):
                safety_config["agent_hash"] = str(agent.agent_public_hash or "")
                changed = True

            if changed:
                agent.safety_config = safety_config
                mutated += 1

            existing_version = await session.execute(
                select(AgentVersion).where(
                    AgentVersion.agent_id == agent.id,
                    AgentVersion.version_number == int(agent.version or 1),
                    AgentVersion.agent_version_hash == str(agent.agent_version_hash or ""),
                )
            )
            if existing_version.scalar_one_or_none() is None:
                session.add(
                    AgentVersion(
                        agent_id=agent.id,
                        agent_public_hash=str(agent.agent_public_hash or ""),
                        version_number=int(agent.version or 1),
                        agent_version_hash=str(agent.agent_version_hash or ""),
                        changelog=None,
                        config_snapshot={
                            "name": agent.name,
                            "description": agent.description,
                            "system_prompt": agent.system_prompt,
                            "model": agent.model,
                            "temperature": agent.temperature,
                            "max_tokens": agent.max_tokens,
                            "tools": agent.tools or [],
                            "tool_config": agent.tool_config or {},
                            "allowed_actions": agent.allowed_actions or [],
                            "blocked_actions": agent.blocked_actions or [],
                            "safety_config": agent.safety_config or {},
                            "agent_public_hash": agent.agent_public_hash,
                            "agent_version_hash": agent.agent_version_hash,
                            "version": agent.version,
                        },
                    )
                )
                created_versions += 1

        await session.commit()

    print(
        {
            "agents_seen": len(agents),
            "agents_mutated": mutated,
            "agent_versions_created": created_versions,
        }
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    asyncio.run(backfill(limit=args.limit))


if __name__ == "__main__":
    main()
