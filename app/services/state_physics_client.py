"""
Hash Sphere Client for Agent Engine Service
============================================

Provides Hash Sphere integration for agent operations.
Enables agents to:
- Hash their thoughts and goals into semantic space
- Find semantically similar memories
- Track semantic drift over time
"""

import os
import hashlib
import math
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

HASH_SPHERE_SERVICE_URL = os.getenv("HASH_SPHERE_SERVICE_URL") or os.getenv("STATE_PHYSICS_URL", "http://rg_users_invarients_sim:8091")


@dataclass
class SemanticHash:
    """A semantic hash with 3D coordinates."""
    hash_value: str
    x: float
    y: float
    z: float
    energy: float
    spin: float
    anchors: List[str]


class StatePhysicsClient:
    """
    Client for Hash Sphere service integration.
    
    Provides semantic hashing and retrieval for agent operations.
    """
    
    def __init__(self, service_url: str = None):
        self.service_url = service_url or HASH_SPHERE_SERVICE_URL
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client
    
    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
    
    def hash_text(self, text: str, context: Optional[str] = None) -> SemanticHash:
        """
        Generate a semantic hash for text.
        
        This is a local implementation that doesn't require the service.
        """
        normalized = text.lower().strip()
        if context:
            normalized = f"{context}:{normalized}"
        
        # Generate semantic hash
        semantic_hash = hashlib.sha256(normalized.encode()).hexdigest()
        
        # Calculate energy and spin
        energy = self._calculate_energy(text)
        spin = self._calculate_spin(text)
        
        # Convert to 3D coordinates
        x, y, z = self._hash_to_xyz(semantic_hash)
        
        # Extract anchors
        anchors = self._extract_anchors(text)
        
        return SemanticHash(
            hash_value=semantic_hash,
            x=x,
            y=y,
            z=z,
            energy=energy,
            spin=spin,
            anchors=anchors,
        )
    
    def _calculate_energy(self, text: str) -> float:
        """Calculate energy score (0-1) based on text intensity."""
        energy = 0.5
        energy += min(text.count('!') * 0.1, 0.3)
        energy += min(text.count('?') * 0.05, 0.15)
        
        emotional_words = ['love', 'hate', 'amazing', 'terrible', 'urgent', 'critical']
        for word in emotional_words:
            if word in text.lower():
                energy += 0.05
        
        return min(max(energy, 0.0), 1.0)
    
    def _calculate_spin(self, text: str) -> float:
        """Calculate spin score (0-1) based on sentiment."""
        positive_words = ['good', 'great', 'excellent', 'love', 'happy', 'success']
        negative_words = ['bad', 'terrible', 'hate', 'sad', 'fail', 'problem']
        
        text_lower = text.lower()
        positive_count = sum(1 for word in positive_words if word in text_lower)
        negative_count = sum(1 for word in negative_words if word in text_lower)
        
        total = positive_count + negative_count
        if total == 0:
            return 0.5
        
        return (positive_count / total) * 0.5 + 0.25
    
    def _hash_to_xyz(self, hash_value: str) -> Tuple[float, float, float]:
        """Convert hash to 3D coordinates."""
        hash_bytes = bytes.fromhex(hash_value)
        
        x = int.from_bytes(hash_bytes[:8], 'big') / 0xFFFFFFFFFFFFFFFF
        y = int.from_bytes(hash_bytes[8:16], 'big') / 0xFFFFFFFFFFFFFFFF
        z = int.from_bytes(hash_bytes[16:24], 'big') / 0xFFFFFFFFFFFFFFFF
        
        return (x, y, z)
    
    def _extract_anchors(self, text: str) -> List[str]:
        """Extract semantic anchors from text."""
        import re
        anchors = []
        text_lower = text.lower()
        
        anchor_patterns = [
            r'\b(important|critical|key|essential|vital)\b',
            r'\b(always|never|must|should|need)\b',
            r'\b(goal|objective|target|aim)\b',
        ]
        
        for pattern in anchor_patterns:
            matches = re.findall(pattern, text_lower)
            anchors.extend(matches)
        
        return list(set(anchors))[:10]
    
    def calculate_resonance(self, hash1: SemanticHash, hash2: SemanticHash) -> float:
        """Calculate resonance between two semantic hashes."""
        # Euclidean distance in 3D space
        distance = math.sqrt(
            (hash1.x - hash2.x) ** 2 +
            (hash1.y - hash2.y) ** 2 +
            (hash1.z - hash2.z) ** 2
        )
        
        # Convert distance to similarity (0-1)
        proximity = math.exp(-distance)
        
        # Factor in energy and spin alignment
        energy_alignment = 1 - abs(hash1.energy - hash2.energy)
        spin_alignment = 1 - abs(hash1.spin - hash2.spin)
        
        # Combined resonance
        resonance = (proximity * 0.6) + (energy_alignment * 0.2) + (spin_alignment * 0.2)
        
        return resonance
    
    async def store_in_sphere(
        self,
        agent_id: str,
        text: str,
        metadata: Dict[str, Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """Store text in the Hash Sphere via service."""
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self.service_url}/store",
                json={
                    "agent_id": agent_id,
                    "text": text,
                    "metadata": metadata or {},
                },
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.warning(f"Hash Sphere store failed: {e}")
        return None
    
    async def search_sphere(
        self,
        query: str,
        agent_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search the Hash Sphere for similar content."""
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self.service_url}/search",
                json={
                    "query": query,
                    "agent_id": agent_id,
                    "limit": limit,
                },
            )
            if response.status_code == 200:
                return response.json().get("results", [])
        except Exception as e:
            logger.warning(f"Hash Sphere search failed: {e}")
        return []


# Singleton instance
_state_physics_client: Optional[StatePhysicsClient] = None


def get_state_physics_client() -> StatePhysicsClient:
    """Get or create singleton State Physics client."""
    global _state_physics_client
    if _state_physics_client is None:
        _state_physics_client = StatePhysicsClient()
    return _state_physics_client
