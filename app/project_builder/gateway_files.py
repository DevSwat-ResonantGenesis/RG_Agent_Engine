"""Push generated project files into RG_Gateway's /code/internal/project/*
file API (Memory Service / Hash Sphere) - the same storage the browser IDE
uses - so a Build result shows up in the IDE's file tree under the shared
project_id instead of only living in this service's local build workspace.
"""
import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


async def push_files_to_gateway(project_id: str, user_id: str, files: list) -> int:
    """files: [{"path"|"file_path": str, "content": str}, ...]. Best-effort -
    one failed file shouldn't fail the whole build response. Returns the
    number of files successfully pushed.
    """
    if not project_id or not user_id:
        return 0

    pushed = 0
    headers = {
        "x-internal-service-key": settings.INTERNAL_SERVICE_KEY,
        "x-user-id": user_id,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for f in files:
                file_path = f.get("file_path") or f.get("path")
                content = f.get("content", "")
                if not file_path:
                    continue
                try:
                    resp = await client.post(
                        f"{settings.GATEWAY_URL}/api/v1/code/internal/project/create-file",
                        json={"project_id": project_id, "file_path": file_path, "content": content},
                        headers=headers,
                    )
                    if resp.status_code == 200 and resp.json().get("success"):
                        pushed += 1
                    else:
                        logger.warning(f"push_files_to_gateway: {file_path} status={resp.status_code}")
                except Exception as e:
                    logger.warning(f"push_files_to_gateway: failed to push {file_path}: {e}")
    except Exception as e:
        logger.warning(f"push_files_to_gateway: client error: {e}")

    return pushed
