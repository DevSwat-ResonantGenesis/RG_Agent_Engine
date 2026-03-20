from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._connections: Dict[str, Dict[int, WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        async with self._lock:
            bucket = self._connections.setdefault(session_id, {})
            bucket[id(websocket)] = websocket

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            ws_id = id(websocket)
            for session_id, bucket in list(self._connections.items()):
                if ws_id in bucket:
                    bucket.pop(ws_id, None)
                    if not bucket:
                        self._connections.pop(session_id, None)
                    break

    async def broadcast(self, session_id: str, message: str) -> None:
        async with self._lock:
            targets = list(self._connections.get(session_id, {}).values())

        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                await self.disconnect(ws)


class ExecutionStreamer:
    def __init__(self, manager: ConnectionManager) -> None:
        self._manager = manager

    async def emit(self, session_id: str, event: Dict[str, Any]) -> None:
        await self._manager.broadcast(session_id, str(event))


_connection_manager: Optional[ConnectionManager] = None
_execution_streamer: Optional[ExecutionStreamer] = None


def get_connection_manager() -> ConnectionManager:
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager()
    return _connection_manager


def get_execution_streamer() -> ExecutionStreamer:
    global _execution_streamer
    if _execution_streamer is None:
        _execution_streamer = ExecutionStreamer(get_connection_manager())
    return _execution_streamer
