"""
Vector database schema definitions for deterministic RAG.
Defines the structure for embeddings storage and indexing.
"""

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
import uuid


class VectorIndexType(Enum):
    FLAT = "flat"           # Exact search (slow, accurate)
    HNSW = "hnsw"           # Hierarchical Navigable Small World
    IVF = "ivf"             # Inverted File Index
    PQ = "pq"               # Product Quantization


class DistanceMetric(Enum):
    COSINE = "cosine"
    EUCLIDEAN = "euclidean"
    DOT_PRODUCT = "dot_product"


@dataclass
class VectorSchema:
    """
    Schema definition for vector storage.
    Ensures consistent structure across the system.
    """
    name: str
    dimension: int
    metric: DistanceMetric = DistanceMetric.COSINE
    index_type: VectorIndexType = VectorIndexType.HNSW
    
    # HNSW parameters
    hnsw_m: int = 16                    # Max connections per node
    hnsw_ef_construction: int = 200     # Construction time accuracy
    hnsw_ef_search: int = 100           # Search time accuracy
    
    # IVF parameters
    ivf_nlist: int = 100                # Number of clusters
    ivf_nprobe: int = 10                # Clusters to search
    
    # Metadata fields
    metadata_fields: List[str] = field(default_factory=lambda: [
        "user_id", "content_type", "created_at", "embedding_version"
    ])
    
    def validate_embedding(self, embedding: List[float]) -> bool:
        """Validate embedding dimensions."""
        return len(embedding) == self.dimension
    
    def get_index_params(self) -> Dict[str, Any]:
        """Get index-specific parameters."""
        if self.index_type == VectorIndexType.HNSW:
            return {
                "M": self.hnsw_m,
                "efConstruction": self.hnsw_ef_construction,
            }
        elif self.index_type == VectorIndexType.IVF:
            return {
                "nlist": self.ivf_nlist,
            }
        return {}
    
    def get_search_params(self) -> Dict[str, Any]:
        """Get search-specific parameters."""
        if self.index_type == VectorIndexType.HNSW:
            return {"ef": self.hnsw_ef_search}
        elif self.index_type == VectorIndexType.IVF:
            return {"nprobe": self.ivf_nprobe}
        return {}


@dataclass
class EmbeddingRecord:
    """
    Individual embedding record with full provenance.
    """
    id: str
    embedding: List[float]
    content_hash: str           # SHA-256 of original content
    embedding_version: str      # Model version used
    
    # Source information
    source_id: str              # Original content ID
    source_type: str            # memory, document, message, etc.
    user_id: Optional[str] = None
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Timestamps
    created_at: float = field(default_factory=time.time)
    updated_at: Optional[float] = None
    
    # Shard information (for distributed storage)
    shard_id: Optional[str] = None
    
    @classmethod
    def create(
        cls,
        content: str,
        embedding: List[float],
        embedding_version: str,
        source_id: str,
        source_type: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "EmbeddingRecord":
        """Create a new embedding record with computed hash."""
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        record_id = str(uuid.uuid4())
        
        return cls(
            id=record_id,
            embedding=embedding,
            content_hash=content_hash,
            embedding_version=embedding_version,
            source_id=source_id,
            source_type=source_type,
            user_id=user_id,
            metadata=metadata or {},
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "embedding": self.embedding,
            "content_hash": self.content_hash,
            "embedding_version": self.embedding_version,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "user_id": self.user_id,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "shard_id": self.shard_id,
        }


@dataclass
class VectorIndex:
    """
    Vector index configuration and state.
    """
    name: str
    schema: VectorSchema
    
    # Index state
    total_vectors: int = 0
    is_trained: bool = False
    last_updated: Optional[float] = None
    
    # Sharding configuration
    num_shards: int = 1
    shard_strategy: str = "hash"  # hash, range, round_robin
    
    # Durability
    checkpoint_interval: int = 1000  # Vectors between checkpoints
    last_checkpoint: Optional[float] = None
    
    def get_shard_id(self, record_id: str) -> int:
        """Determine shard for a record."""
        if self.num_shards == 1:
            return 0
        
        if self.shard_strategy == "hash":
            hash_val = int(hashlib.md5(record_id.encode()).hexdigest(), 16)
            return hash_val % self.num_shards
        
        return 0
    
    def should_checkpoint(self, vectors_since_checkpoint: int) -> bool:
        """Check if checkpoint is needed."""
        return vectors_since_checkpoint >= self.checkpoint_interval


# Default schemas for ResonantGenesis
DEFAULT_SCHEMAS = {
    "memory_embeddings": VectorSchema(
        name="memory_embeddings",
        dimension=1536,  # OpenAI ada-002
        metric=DistanceMetric.COSINE,
        index_type=VectorIndexType.HNSW,
        hnsw_m=32,
        hnsw_ef_construction=400,
        hnsw_ef_search=200,
    ),
    "document_embeddings": VectorSchema(
        name="document_embeddings",
        dimension=1536,
        metric=DistanceMetric.COSINE,
        index_type=VectorIndexType.HNSW,
    ),
    "message_embeddings": VectorSchema(
        name="message_embeddings",
        dimension=1536,
        metric=DistanceMetric.COSINE,
        index_type=VectorIndexType.HNSW,
        hnsw_ef_search=50,  # Faster for real-time
    ),
}
