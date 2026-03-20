"""
Embedding versioning for deterministic RAG across model updates.
Ensures reproducibility and migration support.
"""

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable
from enum import Enum


class EmbeddingModelProvider(Enum):
    OPENAI = "openai"
    COHERE = "cohere"
    HUGGINGFACE = "huggingface"
    LOCAL = "local"


@dataclass
class EmbeddingVersion:
    """
    Embedding model version with full specification.
    """
    version_id: str
    provider: EmbeddingModelProvider
    model_name: str
    dimension: int
    
    # Model specification
    model_hash: Optional[str] = None  # Hash of model weights (for local)
    api_version: Optional[str] = None  # API version (for cloud)
    
    # Normalization settings
    normalize_embeddings: bool = True
    truncate_input: bool = True
    max_input_tokens: int = 8191
    
    # Version metadata
    created_at: float = field(default_factory=time.time)
    deprecated_at: Optional[float] = None
    successor_version: Optional[str] = None
    
    # Performance characteristics
    avg_latency_ms: Optional[float] = None
    cost_per_1k_tokens: Optional[float] = None
    
    @property
    def is_deprecated(self) -> bool:
        return self.deprecated_at is not None
    
    def get_version_hash(self) -> str:
        """Generate deterministic hash for this version."""
        spec = f"{self.provider.value}:{self.model_name}:{self.dimension}:{self.normalize_embeddings}"
        return hashlib.sha256(spec.encode()).hexdigest()[:16]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_id": self.version_id,
            "provider": self.provider.value,
            "model_name": self.model_name,
            "dimension": self.dimension,
            "model_hash": self.model_hash,
            "api_version": self.api_version,
            "normalize_embeddings": self.normalize_embeddings,
            "max_input_tokens": self.max_input_tokens,
            "created_at": self.created_at,
            "deprecated_at": self.deprecated_at,
            "successor_version": self.successor_version,
        }


class EmbeddingVersionManager:
    """
    Manages embedding versions for deterministic retrieval.
    Handles version migrations and compatibility.
    """
    
    def __init__(self):
        self._versions: Dict[str, EmbeddingVersion] = {}
        self._current_version: Optional[str] = None
        self._migration_handlers: Dict[tuple, Callable] = {}
    
    def register_version(self, version: EmbeddingVersion) -> None:
        """Register a new embedding version."""
        self._versions[version.version_id] = version
        
        if self._current_version is None:
            self._current_version = version.version_id
    
    def set_current_version(self, version_id: str) -> None:
        """Set the current active version."""
        if version_id not in self._versions:
            raise ValueError(f"Unknown version: {version_id}")
        self._current_version = version_id
    
    def get_version(self, version_id: str) -> Optional[EmbeddingVersion]:
        """Get a specific version."""
        return self._versions.get(version_id)
    
    def get_current_version(self) -> Optional[EmbeddingVersion]:
        """Get the current active version."""
        if self._current_version:
            return self._versions.get(self._current_version)
        return None
    
    def deprecate_version(
        self,
        version_id: str,
        successor_id: Optional[str] = None,
    ) -> None:
        """Mark a version as deprecated."""
        version = self._versions.get(version_id)
        if version:
            version.deprecated_at = time.time()
            version.successor_version = successor_id
    
    def register_migration(
        self,
        from_version: str,
        to_version: str,
        handler: Callable[[List[float]], List[float]],
    ) -> None:
        """Register a migration handler between versions."""
        self._migration_handlers[(from_version, to_version)] = handler
    
    def can_migrate(self, from_version: str, to_version: str) -> bool:
        """Check if migration path exists."""
        return (from_version, to_version) in self._migration_handlers
    
    def migrate_embedding(
        self,
        embedding: List[float],
        from_version: str,
        to_version: str,
    ) -> Optional[List[float]]:
        """Migrate an embedding between versions."""
        handler = self._migration_handlers.get((from_version, to_version))
        if handler:
            return handler(embedding)
        return None
    
    def get_compatible_versions(self, version_id: str) -> List[str]:
        """Get versions compatible with the given version."""
        compatible = [version_id]
        
        # Add versions we can migrate to
        for (from_v, to_v) in self._migration_handlers.keys():
            if from_v == version_id:
                compatible.append(to_v)
        
        return compatible
    
    def list_versions(self, include_deprecated: bool = False) -> List[EmbeddingVersion]:
        """List all registered versions."""
        versions = list(self._versions.values())
        if not include_deprecated:
            versions = [v for v in versions if not v.is_deprecated]
        return sorted(versions, key=lambda v: v.created_at, reverse=True)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get version manager statistics."""
        active = [v for v in self._versions.values() if not v.is_deprecated]
        deprecated = [v for v in self._versions.values() if v.is_deprecated]
        
        return {
            "total_versions": len(self._versions),
            "active_versions": len(active),
            "deprecated_versions": len(deprecated),
            "current_version": self._current_version,
            "migration_paths": len(self._migration_handlers),
        }


# Default versions for ResonantGenesis
DEFAULT_VERSIONS = [
    EmbeddingVersion(
        version_id="openai-ada-002-v1",
        provider=EmbeddingModelProvider.OPENAI,
        model_name="text-embedding-ada-002",
        dimension=1536,
        api_version="2023-05-15",
        normalize_embeddings=True,
        max_input_tokens=8191,
        cost_per_1k_tokens=0.0001,
    ),
    EmbeddingVersion(
        version_id="openai-3-small-v1",
        provider=EmbeddingModelProvider.OPENAI,
        model_name="text-embedding-3-small",
        dimension=1536,
        api_version="2024-01-25",
        normalize_embeddings=True,
        max_input_tokens=8191,
        cost_per_1k_tokens=0.00002,
    ),
    EmbeddingVersion(
        version_id="openai-3-large-v1",
        provider=EmbeddingModelProvider.OPENAI,
        model_name="text-embedding-3-large",
        dimension=3072,
        api_version="2024-01-25",
        normalize_embeddings=True,
        max_input_tokens=8191,
        cost_per_1k_tokens=0.00013,
    ),
]


def create_default_manager() -> EmbeddingVersionManager:
    """Create manager with default versions."""
    manager = EmbeddingVersionManager()
    for version in DEFAULT_VERSIONS:
        manager.register_version(version)
    manager.set_current_version("openai-ada-002-v1")
    return manager
