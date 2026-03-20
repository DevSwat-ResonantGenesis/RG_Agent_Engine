import hashlib
import json
from typing import Any, Dict, List, Optional


def _canonical_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_manifest_hash(
    *,
    name: str,
    description: Optional[str],
    system_prompt: Optional[str],
    model: str,
    temperature: float,
    max_tokens: int,
    tools: Optional[List[str]],
    allowed_actions: Optional[List[str]],
    blocked_actions: Optional[List[str]],
) -> str:
    """Deterministic bytes32-like hash used for on-chain agent registry.

    Returns a 0x-prefixed sha256 hex digest (64 hex chars).
    """

    manifest = {
        "name": name,
        "description": description or "",
        "system_prompt": system_prompt or "",
        "model": model,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "tools": sorted(tools or []),
        "allowed_actions": sorted(allowed_actions or []),
        "blocked_actions": sorted(blocked_actions or []),
    }

    digest = hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()
    return f"0x{digest}"
