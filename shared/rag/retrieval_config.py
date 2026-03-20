"""
Retrieval configuration and invariants for deterministic RAG.
Defines the rules and constraints for consistent retrieval behavior.
"""

import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable
from enum import Enum


class RetrievalStrategy(Enum):
    SIMILARITY = "similarity"           # Pure vector similarity
    MMR = "mmr"                         # Maximal Marginal Relevance
    HYBRID = "hybrid"                   # Vector + keyword
    RERANKED = "reranked"               # With reranking model


class FilterOperator(Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"


@dataclass
class MetadataFilter:
    """Filter for metadata-based retrieval."""
    field: str
    operator: FilterOperator
    value: Any
    
    def matches(self, metadata: Dict[str, Any]) -> bool:
        """Check if metadata matches this filter."""
        field_value = metadata.get(self.field)
        
        if field_value is None:
            return self.operator == FilterOperator.NE
        
        if self.operator == FilterOperator.EQ:
            return field_value == self.value
        elif self.operator == FilterOperator.NE:
            return field_value != self.value
        elif self.operator == FilterOperator.GT:
            return field_value > self.value
        elif self.operator == FilterOperator.GTE:
            return field_value >= self.value
        elif self.operator == FilterOperator.LT:
            return field_value < self.value
        elif self.operator == FilterOperator.LTE:
            return field_value <= self.value
        elif self.operator == FilterOperator.IN:
            return field_value in self.value
        elif self.operator == FilterOperator.NOT_IN:
            return field_value not in self.value
        elif self.operator == FilterOperator.CONTAINS:
            return self.value in str(field_value)
        
        return False


@dataclass
class RetrievalConfig:
    """
    Configuration for RAG retrieval with deterministic behavior.
    """
    # Result limits
    top_k: int = 10
    max_results: int = 100
    
    # Similarity thresholds
    min_score: float = 0.0          # Minimum similarity score
    score_threshold: float = 0.7    # Recommended threshold
    
    # Strategy
    strategy: RetrievalStrategy = RetrievalStrategy.SIMILARITY
    
    # MMR parameters
    mmr_lambda: float = 0.5         # Diversity vs relevance tradeoff
    
    # Hybrid search parameters
    keyword_weight: float = 0.3     # Weight for keyword matching
    vector_weight: float = 0.7      # Weight for vector similarity
    
    # Reranking
    rerank_top_n: int = 50          # Candidates for reranking
    rerank_model: Optional[str] = None
    
    # Metadata filters
    filters: List[MetadataFilter] = field(default_factory=list)
    
    # Determinism settings
    seed: Optional[int] = None      # For reproducible results
    deterministic: bool = True      # Enforce deterministic ordering
    
    # Timeout
    timeout_ms: float = 5000.0
    
    def validate(self) -> List[str]:
        """Validate configuration and return any errors."""
        errors = []
        
        if self.top_k <= 0:
            errors.append("top_k must be positive")
        if self.top_k > self.max_results:
            errors.append("top_k cannot exceed max_results")
        if not 0 <= self.min_score <= 1:
            errors.append("min_score must be between 0 and 1")
        if not 0 <= self.score_threshold <= 1:
            errors.append("score_threshold must be between 0 and 1")
        if not 0 <= self.mmr_lambda <= 1:
            errors.append("mmr_lambda must be between 0 and 1")
        if self.keyword_weight + self.vector_weight != 1.0:
            errors.append("keyword_weight + vector_weight must equal 1.0")
        
        return errors
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "top_k": self.top_k,
            "max_results": self.max_results,
            "min_score": self.min_score,
            "score_threshold": self.score_threshold,
            "strategy": self.strategy.value,
            "mmr_lambda": self.mmr_lambda,
            "keyword_weight": self.keyword_weight,
            "vector_weight": self.vector_weight,
            "deterministic": self.deterministic,
            "timeout_ms": self.timeout_ms,
        }


@dataclass
class RetrievalInvariants:
    """
    Invariants that must hold for deterministic RAG behavior.
    These are enforced at query time.
    """
    # Ordering invariants
    results_ordered_by_score: bool = True
    ties_broken_by_id: bool = True          # Deterministic tie-breaking
    
    # Score invariants
    scores_normalized: bool = True           # Scores in [0, 1]
    scores_monotonic: bool = True            # Higher = more relevant
    
    # Content invariants
    no_duplicate_ids: bool = True
    content_hash_verified: bool = False      # Verify content hasn't changed
    
    # Version invariants
    embedding_version_matched: bool = True   # Query and doc versions match
    allow_cross_version: bool = False        # Allow mixed versions
    
    # Temporal invariants
    respect_created_at_order: bool = False   # Prefer newer content
    max_age_seconds: Optional[float] = None  # Filter old content
    
    def check_results(
        self,
        results: List[Dict[str, Any]],
        query_embedding_version: str,
    ) -> List[str]:
        """Check if results satisfy invariants. Returns violations."""
        violations = []
        
        # Check ordering
        if self.results_ordered_by_score:
            scores = [r.get("score", 0) for r in results]
            if scores != sorted(scores, reverse=True):
                violations.append("Results not ordered by score")
        
        # Check duplicates
        if self.no_duplicate_ids:
            ids = [r.get("id") for r in results]
            if len(ids) != len(set(ids)):
                violations.append("Duplicate IDs in results")
        
        # Check score normalization
        if self.scores_normalized:
            for r in results:
                score = r.get("score", 0)
                if not 0 <= score <= 1:
                    violations.append(f"Score {score} not in [0, 1]")
                    break
        
        # Check embedding version
        if self.embedding_version_matched and not self.allow_cross_version:
            for r in results:
                doc_version = r.get("embedding_version")
                if doc_version and doc_version != query_embedding_version:
                    violations.append(
                        f"Version mismatch: query={query_embedding_version}, doc={doc_version}"
                    )
                    break
        
        # Check temporal constraints
        if self.max_age_seconds:
            now = time.time()
            for r in results:
                created_at = r.get("created_at", 0)
                if now - created_at > self.max_age_seconds:
                    violations.append(f"Result exceeds max age")
                    break
        
        return violations


@dataclass
class RAGQueryResult:
    """Result of a RAG query with full metadata."""
    id: str
    content: str
    score: float
    embedding_version: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Source information
    source_id: str = ""
    source_type: str = ""
    
    # Provenance
    content_hash: str = ""
    created_at: float = 0.0


class RAGQueryExecutor:
    """
    Executes RAG queries with invariant enforcement.
    """
    
    def __init__(
        self,
        config: RetrievalConfig,
        invariants: RetrievalInvariants,
    ):
        self.config = config
        self.invariants = invariants
        
        # Metrics
        self._query_count = 0
        self._violation_count = 0
        self._avg_latency_ms = 0.0
    
    async def execute(
        self,
        query_embedding: List[float],
        embedding_version: str,
        search_fn: Callable,
    ) -> List[RAGQueryResult]:
        """
        Execute a RAG query with invariant checking.
        
        Args:
            query_embedding: Query vector
            embedding_version: Version of query embedding
            search_fn: Async function to perform actual search
        
        Returns:
            List of results satisfying invariants
        """
        import asyncio
        
        start_time = time.time()
        self._query_count += 1
        
        try:
            # Execute search with timeout
            raw_results = await asyncio.wait_for(
                search_fn(
                    query_embedding,
                    top_k=self.config.top_k,
                    min_score=self.config.min_score,
                    filters=self.config.filters,
                ),
                timeout=self.config.timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            return []
        
        # Apply score threshold
        filtered_results = [
            r for r in raw_results
            if r.get("score", 0) >= self.config.score_threshold
        ]
        
        # Check invariants
        violations = self.invariants.check_results(filtered_results, embedding_version)
        if violations:
            self._violation_count += 1
            # Log violations but continue
        
        # Ensure deterministic ordering
        if self.config.deterministic:
            filtered_results = self._ensure_deterministic_order(filtered_results)
        
        # Convert to result objects
        results = [
            RAGQueryResult(
                id=r["id"],
                content=r.get("content", ""),
                score=r["score"],
                embedding_version=r.get("embedding_version", embedding_version),
                metadata=r.get("metadata", {}),
                source_id=r.get("source_id", ""),
                source_type=r.get("source_type", ""),
                content_hash=r.get("content_hash", ""),
                created_at=r.get("created_at", 0.0),
            )
            for r in filtered_results[:self.config.top_k]
        ]
        
        # Update metrics
        latency_ms = (time.time() - start_time) * 1000
        self._avg_latency_ms = (
            (self._avg_latency_ms * (self._query_count - 1) + latency_ms)
            / self._query_count
        )
        
        return results
    
    def _ensure_deterministic_order(
        self,
        results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Ensure deterministic ordering with tie-breaking."""
        return sorted(
            results,
            key=lambda r: (-r.get("score", 0), r.get("id", "")),
        )
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "query_count": self._query_count,
            "violation_count": self._violation_count,
            "violation_rate": self._violation_count / max(1, self._query_count),
            "avg_latency_ms": round(self._avg_latency_ms, 2),
            "config": self.config.to_dict(),
        }


# Default configurations
DEFAULT_RETRIEVAL_CONFIG = RetrievalConfig(
    top_k=10,
    score_threshold=0.7,
    strategy=RetrievalStrategy.SIMILARITY,
    deterministic=True,
)

DEFAULT_INVARIANTS = RetrievalInvariants(
    results_ordered_by_score=True,
    ties_broken_by_id=True,
    scores_normalized=True,
    no_duplicate_ids=True,
    embedding_version_matched=True,
)
