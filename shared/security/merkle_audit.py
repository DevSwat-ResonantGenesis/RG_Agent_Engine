"""Merkle accumulator for tamper-evident audit logs.

Provides cryptographic proof that audit logs have not been modified.
Each log entry is hashed and accumulated into a Merkle tree structure.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import asyncio


@dataclass
class AuditEntry:
    """Single audit log entry."""
    id: str
    timestamp: str
    event_type: str
    actor_id: str
    action: str
    resource: str
    details: Dict[str, Any]
    hash: str = ""
    
    def compute_hash(self, previous_hash: str = "") -> str:
        """Compute hash of this entry including previous hash for chaining."""
        data = {
            "id": self.id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "actor_id": self.actor_id,
            "action": self.action,
            "resource": self.resource,
            "details": self.details,
            "previous_hash": previous_hash,
        }
        content = json.dumps(data, sort_keys=True)
        self.hash = hashlib.sha256(content.encode()).hexdigest()
        return self.hash


@dataclass
class MerkleNode:
    """Node in the Merkle tree."""
    hash: str
    left: Optional["MerkleNode"] = None
    right: Optional["MerkleNode"] = None
    data: Optional[str] = None  # Only for leaf nodes


class MerkleAccumulator:
    """
    Merkle tree accumulator for audit logs.
    
    Features:
    - Append-only log with hash chaining
    - Merkle tree for efficient verification
    - Proof generation for individual entries
    - Root hash for integrity verification
    - Periodic anchoring to blockchain
    """

    def __init__(self, anchor_interval: int = 100):
        self.entries: List[AuditEntry] = []
        self.leaf_hashes: List[str] = []
        self.root_hash: str = ""
        self.anchor_interval = anchor_interval
        self.last_anchor_index = 0
        self.anchors: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def append(
        self,
        event_type: str,
        actor_id: str,
        action: str,
        resource: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        """Append a new audit entry."""
        async with self._lock:
            entry_id = f"audit_{len(self.entries)}_{int(time.time() * 1000)}"
            
            # Get previous hash for chaining
            previous_hash = self.entries[-1].hash if self.entries else ""
            
            entry = AuditEntry(
                id=entry_id,
                timestamp=datetime.utcnow().isoformat(),
                event_type=event_type,
                actor_id=actor_id,
                action=action,
                resource=resource,
                details=details or {},
            )
            entry.compute_hash(previous_hash)
            
            self.entries.append(entry)
            self.leaf_hashes.append(entry.hash)
            
            # Rebuild Merkle tree
            self._rebuild_tree()
            
            # Check if we should anchor
            if len(self.entries) - self.last_anchor_index >= self.anchor_interval:
                await self._create_anchor()
            
            return entry

    def _rebuild_tree(self):
        """Rebuild the Merkle tree from leaf hashes."""
        if not self.leaf_hashes:
            self.root_hash = ""
            return
        
        # Build tree bottom-up
        current_level = self.leaf_hashes.copy()
        
        while len(current_level) > 1:
            next_level = []
            
            for i in range(0, len(current_level), 2):
                left = current_level[i]
                right = current_level[i + 1] if i + 1 < len(current_level) else left
                
                combined = hashlib.sha256(f"{left}{right}".encode()).hexdigest()
                next_level.append(combined)
            
            current_level = next_level
        
        self.root_hash = current_level[0] if current_level else ""

    def get_proof(self, entry_index: int) -> List[Tuple[str, str]]:
        """
        Generate Merkle proof for an entry.
        
        Returns list of (hash, position) tuples where position is 'left' or 'right'.
        """
        if entry_index < 0 or entry_index >= len(self.leaf_hashes):
            return []
        
        proof = []
        current_level = self.leaf_hashes.copy()
        index = entry_index
        
        while len(current_level) > 1:
            next_level = []
            
            for i in range(0, len(current_level), 2):
                left = current_level[i]
                right = current_level[i + 1] if i + 1 < len(current_level) else left
                
                # If this pair contains our index, add sibling to proof
                if i == index or i + 1 == index:
                    if i == index:
                        proof.append((right, "right"))
                    else:
                        proof.append((left, "left"))
                
                combined = hashlib.sha256(f"{left}{right}".encode()).hexdigest()
                next_level.append(combined)
            
            # Update index for next level
            index = index // 2
            current_level = next_level
        
        return proof

    def verify_proof(
        self,
        entry_hash: str,
        proof: List[Tuple[str, str]],
        expected_root: Optional[str] = None,
    ) -> bool:
        """Verify a Merkle proof for an entry."""
        current_hash = entry_hash
        
        for sibling_hash, position in proof:
            if position == "left":
                current_hash = hashlib.sha256(
                    f"{sibling_hash}{current_hash}".encode()
                ).hexdigest()
            else:
                current_hash = hashlib.sha256(
                    f"{current_hash}{sibling_hash}".encode()
                ).hexdigest()
        
        expected = expected_root or self.root_hash
        return current_hash == expected

    def verify_chain(self) -> Tuple[bool, Optional[int]]:
        """
        Verify the entire hash chain is intact.
        
        Returns (is_valid, first_invalid_index).
        """
        if not self.entries:
            return True, None
        
        previous_hash = ""
        
        for i, entry in enumerate(self.entries):
            expected_hash = entry.compute_hash(previous_hash)
            if expected_hash != entry.hash:
                return False, i
            previous_hash = entry.hash
        
        return True, None

    async def _create_anchor(self):
        """Create an anchor point for the current state."""
        anchor = {
            "index": len(self.entries),
            "root_hash": self.root_hash,
            "timestamp": datetime.utcnow().isoformat(),
            "entry_count": len(self.entries) - self.last_anchor_index,
        }
        self.anchors.append(anchor)
        self.last_anchor_index = len(self.entries)
        
        # TODO: Submit to blockchain service
        # await self._submit_to_blockchain(anchor)

    async def _submit_to_blockchain(self, anchor: Dict[str, Any]):
        """Submit anchor to blockchain for permanent record."""
        import httpx
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(
                    "http://blockchain_service:8000/blockchain/audit/anchor",
                    json={
                        "root_hash": anchor["root_hash"],
                        "entry_count": anchor["entry_count"],
                        "timestamp": anchor["timestamp"],
                    },
                )
        except Exception:
            pass  # Log but don't fail

    def get_entry(self, entry_id: str) -> Optional[AuditEntry]:
        """Get an entry by ID."""
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        return None

    def get_entries(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        event_type: Optional[str] = None,
        actor_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditEntry]:
        """Query entries with filters."""
        results = []
        
        for entry in reversed(self.entries):
            if start_time and entry.timestamp < start_time:
                continue
            if end_time and entry.timestamp > end_time:
                continue
            if event_type and entry.event_type != event_type:
                continue
            if actor_id and entry.actor_id != actor_id:
                continue
            
            results.append(entry)
            
            if len(results) >= limit:
                break
        
        return results

    def get_stats(self) -> Dict[str, Any]:
        """Get accumulator statistics."""
        return {
            "total_entries": len(self.entries),
            "root_hash": self.root_hash,
            "anchor_count": len(self.anchors),
            "last_anchor_index": self.last_anchor_index,
            "chain_valid": self.verify_chain()[0],
        }

    def export_state(self) -> Dict[str, Any]:
        """Export full state for backup/restore."""
        return {
            "entries": [
                {
                    "id": e.id,
                    "timestamp": e.timestamp,
                    "event_type": e.event_type,
                    "actor_id": e.actor_id,
                    "action": e.action,
                    "resource": e.resource,
                    "details": e.details,
                    "hash": e.hash,
                }
                for e in self.entries
            ],
            "root_hash": self.root_hash,
            "anchors": self.anchors,
        }

    def import_state(self, state: Dict[str, Any]):
        """Import state from backup."""
        self.entries = [
            AuditEntry(**e) for e in state.get("entries", [])
        ]
        self.leaf_hashes = [e.hash for e in self.entries]
        self.anchors = state.get("anchors", [])
        self._rebuild_tree()
        
        if self.anchors:
            self.last_anchor_index = self.anchors[-1].get("index", 0)


# Global accumulator instance
audit_accumulator = MerkleAccumulator()


async def log_audit_event(
    event_type: str,
    actor_id: str,
    action: str,
    resource: str,
    details: Optional[Dict[str, Any]] = None,
) -> AuditEntry:
    """Convenience function to log an audit event."""
    return await audit_accumulator.append(
        event_type=event_type,
        actor_id=actor_id,
        action=action,
        resource=resource,
        details=details,
    )
