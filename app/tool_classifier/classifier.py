"""
Trained Neural Tool Classifier — Agent Engine Edition
=======================================================

Same proven architecture as RG_Chat's classifier:
  1. Sentence-transformer encodes (goal + context) → 384-dim embedding
  2. Trained MLP classification head → tool probabilities
  3. Active learning: every prediction saved to PostgreSQL
  4. Model stored in PostgreSQL — survives container restarts
  5. Retraining merges seed data + active learning samples

Adapted for Agent Engine:
  - Uses Agent Engine's DB (not Chat's DB)
  - Separate model tables (agent_tool_classifier_models, agent_tool_active_samples)
  - predict() takes goal string (not chat message)
  - predict_top_n() returns ranked list for EXECUTION_FRAME injection
  - Supports custom tool label expansion + retraining
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pickle
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# Tool labels (None = general chat / no tool needed)
# ---------------------------------------------------------------
# Canonical list — same as RG_Chat's ALL_TOOLS.
# When custom tools are added, they get appended and model retrains.

ALL_TOOLS = [
    None,  # index 0 = general / no tool
    # ── Search & Web ──
    "web_search",
    "fetch_url",
    "read_webpage",
    "read_many_pages",
    "scrape_page",
    "deep_research",
    "reddit_search",
    "image_search",
    "news_search",
    "places_search",
    "youtube_search",
    "wikipedia",
    # ── Memory / Hash Sphere ──
    "memory_read",
    "memory_write",
    "memory_stats",
    "hash_sphere_search",
    "hash_sphere_anchor",
    "hash_sphere_list_anchors",
    "hash_sphere_hash",
    "hash_sphere_resonance",
    # ── Utilities ──
    "weather",
    "stock_crypto",
    "generate_chart",
    "visualize",
    "get_current_time",
    "get_system_info",
    # ── Code Visualizer ──
    "code_visualizer",
    "code_visualizer_scan",
    "code_visualizer_functions",
    "code_visualizer_trace",
    "code_visualizer_governance",
    "code_visualizer_graph",
    "code_visualizer_pipeline",
    "code_visualizer_filter",
    "code_visualizer_by_type",
    # ── Agent Operations ──
    "agents_list",
    "agents_create",
    "agents_start",
    "agents_stop",
    "agents_status",
    "agents_delete",
    "agents_sessions",
    "agents_session_steps",
    "agents_session_trace",
    "agents_metrics",
    "agents_session_detail",
    "agents_session_cancel",
    "agents_update",
    "agents_available_tools",
    "agents_templates",
    "agents_versions",
    "schedule_agent",
    "run_snapshot",
    "list_workspace_tools",
    "agent_snapshot",
    "session_log",
    "workspace_snapshot",
    "run_agent",
    "present_options",
    # ── Media ──
    "generate_image",
    "generate_audio",
    "generate_music",
    "generate_video",
    "image_generation",
    # ── Integrations ──
    "gmail_send",
    "gmail_read",
    "slack_send",
    "slack_read",
    "send_email",
    "configure_smtp",
    "delete_smtp",
    "figma",
    "sigma",
    "google_calendar",
    "google_drive",
    # ── Community / Rabbit ──
    "create_rabbit_post",
    "list_rabbit_communities",
    "create_rabbit_community",
    "list_rabbit_posts",
    "get_rabbit_post",
    "get_rabbit_community",
    "delete_rabbit_post",
    "create_rabbit_comment",
    "list_rabbit_comments",
    "delete_rabbit_comment",
    "search_rabbit_posts",
    "rabbit_vote",
    # ── Developer ──
    "execute_code",
    "http_request",
    "external_http_request",
    "dev_tool",
    "run_command",
    # ── GitHub ──
    "github_create_repo",
    "github_list_repos",
    "github_list_files",
    "github_download_file",
    "github_upload_file",
    "github_pull_request",
    "github_issue",
    "github_commit",
    "github_comment",
    # ── Git ──
    "git_clone",
    "git_branch",
    "git_merge",
    "git_push",
    "git_pull",
    # ── Tool Management ──
    "create_tool",
    "list_tools",
    "delete_tool",
    "update_tool",
    "auto_build_tool",
    "check_tool_exists",
    # ── Platform API ──
    "platform_api",
    "platform_api_call",
    "platform_api_search",
    "discover_services",
    "discover_api",
    # ── Filesystem / IDE ──
    "file_read",
    "file_write",
    "file_edit",
    "multi_edit",
    "file_list",
    "file_delete",
    "grep_search",
    "find_by_name",
    "command_status",
    "ide_workspace",
    # ── Scraping ──
    "scrape_platforms",
    # ── Documents ──
    "google_sheets",
    "google_docs",
    "create_presentation",
    # ── Orchestrator / Architect ──
    "agent_architect",
    "build_agent",
    "continue_build",
    "message_build",
    "stop_run",
    "set_trigger",
    "set_workspace_name",
    "open_interface_editor",
    "get_user_memory",
    "update_user_memory",
    "list_workspace_databases",
    "query_cross_agent_database",
    "get_credits_info",
    "present_billing_offer",
    # ── State Physics ──
    "state_physics",
    "sp_state",
    "sp_reset",
    "sp_nodes",
    "sp_metrics",
    "sp_identity",
    "sp_simulate",
    "sp_galaxy",
    "sp_demo",
    "sp_asymmetry",
    "sp_physics_config",
    "sp_entropy_config",
    "sp_entropy_toggle",
    "sp_entropy_perturbation",
    "sp_agent_spawn",
    "sp_agent_step",
    "sp_agent_kill",
    "sp_agents_spawn",
    "sp_agents_kill_all",
    "sp_experiment",
    "sp_memory_cost",
    "sp_metrics_record",
    # ── Stock Market ──
    "stock_market_data",
    # ── OAuth Integrations ──
    "notion",
    "discord",
    "asana",
    "clickup",
    "linear",
    "monday",
    "miro",
    "atlassian",
    "zoom",
    "calendly",
    "dropbox",
    "dribbble",
    "typeform",
    "hubspot",
    "salesforce",
    "pipedrive",
    "attio",
    "zoho_crm",
    "mailchimp",
    "airtable",
    "gitlab",
    "linkedin",
    "twitter_x",
    "xero",
    "microsoft",
    "youtube",
    # ── Autonomous Builder ──
    "list_built_tools",
    "execute_built_tool",
    # ── Filesystem (extended) ──
    "file_download_curl",
    "file_upload_curl",
    "file_extract_zip",
    # ── Memory Library ──
    "memory_search",
    "memory_library",
    # ── Rabbit (alias) ──
    "rabbit_post",
]

TOOL_TO_IDX = {s: i for i, s in enumerate(ALL_TOOLS)}
IDX_TO_TOOL = {i: s for i, s in enumerate(ALL_TOOLS)}

_FLUSH_BATCH = 50


@dataclass
class ToolPrediction:
    """Result of the classifier."""
    tool_id: Optional[str]
    confidence: float
    probabilities: Dict[str, float]
    method: str  # "classifier", "fallback"
    latency_ms: float = 0.0
    top_n: List[Tuple[str, float]] = field(default_factory=list)


# ---------------------------------------------------------------
# DB helpers — Agent Engine's own tables
# ---------------------------------------------------------------

async def _load_model_from_db():
    """Load the latest active tool classifier model from Agent Engine DB."""
    from ..db import async_session
    from sqlalchemy import text
    try:
        async with async_session() as session:
            row = await session.execute(
                text(
                    "SELECT model_blob, stats_json, n_samples, version "
                    "FROM agent_tool_classifier_models "
                    "WHERE is_active = true "
                    "ORDER BY version DESC LIMIT 1"
                )
            )
            result = row.fetchone()
            if result:
                blob, stats, n_samples, version = result
                clf = pickle.loads(blob)
                return clf, stats or {}, n_samples, version
    except Exception as e:
        logger.warning(f"[ToolClassifier] DB load failed: {e}")
    return None, {}, 0, 0


async def _save_model_to_db(classifier, stats: dict, n_samples: int, version: int):
    """Save the trained tool classifier to Agent Engine DB."""
    from ..db import async_session
    from sqlalchemy import text
    try:
        blob = pickle.dumps(classifier)
        async with async_session() as session:
            await session.execute(
                text("UPDATE agent_tool_classifier_models SET is_active = false WHERE is_active = true")
            )
            await session.execute(
                text(
                    "INSERT INTO agent_tool_classifier_models "
                    "(version, model_blob, n_samples, train_accuracy, cv_accuracy, stats_json, is_active) "
                    "VALUES (:ver, :blob, :ns, :ta, :ca, CAST(:sj AS jsonb), true)"
                ),
                {
                    "ver": version,
                    "blob": blob,
                    "ns": n_samples,
                    "ta": stats.get("train_accuracy", 0),
                    "ca": stats.get("cv_accuracy", 0),
                    "sj": json.dumps(stats),
                },
            )
            await session.commit()
            logger.info(
                f"[ToolClassifier] Model v{version} saved to DB "
                f"({len(blob)} bytes, {n_samples} samples)"
            )
    except Exception as e:
        logger.error(f"[ToolClassifier] DB save failed: {e}", exc_info=True)


async def _save_active_samples(samples: List[Dict]):
    """Batch-insert active learning samples into Agent Engine DB."""
    from ..db import async_session
    from sqlalchemy import text
    try:
        async with async_session() as session:
            for s in samples:
                await session.execute(
                    text(
                        "INSERT INTO agent_tool_active_samples "
                        "(user_message, predicted_tool, confidence, method, probabilities, user_id) "
                        "VALUES (:msg, :pred, :conf, :meth, CAST(:probs AS jsonb), :uid)"
                    ),
                    {
                        "msg": s["msg"][:500],
                        "pred": s.get("predicted"),
                        "conf": s.get("conf", 0),
                        "meth": s.get("method", ""),
                        "probs": json.dumps(s.get("probs", {})),
                        "uid": s.get("user_id"),
                    },
                )
            await session.commit()
            logger.info(f"[ToolClassifier] Flushed {len(samples)} active samples to DB")
    except Exception as e:
        logger.warning(f"[ToolClassifier] Active sample flush failed: {e}")


async def _load_active_samples_from_db(min_confidence: float = 0.6) -> List[Tuple]:
    """Load high-confidence active learning samples for retraining."""
    from ..db import async_session
    from sqlalchemy import text
    samples = []
    try:
        async with async_session() as session:
            rows = await session.execute(
                text(
                    "SELECT user_message, predicted_tool "
                    "FROM agent_tool_active_samples "
                    "WHERE confidence >= :conf "
                    "ORDER BY created_at DESC "
                    "LIMIT 5000"
                ),
                {"conf": min_confidence},
            )
            for row in rows.fetchall():
                msg, tool = row
                samples.append((msg, [], tool))
    except Exception as e:
        logger.warning(f"[ToolClassifier] Active sample load failed: {e}")
    return samples


async def _count_active_samples() -> int:
    """Count total active learning samples in DB."""
    from ..db import async_session
    from sqlalchemy import text
    try:
        async with async_session() as session:
            result = await session.execute(
                text("SELECT count(*) FROM agent_tool_active_samples")
            )
            return result.scalar() or 0
    except Exception:
        return 0


async def _ensure_tables():
    """Create classifier tables if they don't exist (idempotent)."""
    from ..db import async_session
    from sqlalchemy import text
    try:
        async with async_session() as session:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS agent_tool_classifier_models (
                    id SERIAL PRIMARY KEY,
                    version INTEGER NOT NULL DEFAULT 1,
                    model_blob BYTEA NOT NULL,
                    n_samples INTEGER NOT NULL DEFAULT 0,
                    train_accuracy FLOAT DEFAULT 0,
                    cv_accuracy FLOAT DEFAULT 0,
                    stats_json JSONB,
                    is_active BOOLEAN DEFAULT true,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS agent_tool_active_samples (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_message TEXT NOT NULL,
                    predicted_tool VARCHAR(128),
                    confidence FLOAT DEFAULT 0,
                    method VARCHAR(64),
                    probabilities JSONB,
                    user_id VARCHAR(128),
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_agent_tool_active_conf
                ON agent_tool_active_samples (confidence)
            """))
            await session.commit()
            logger.info("[ToolClassifier] DB tables ensured")
    except Exception as e:
        logger.warning(f"[ToolClassifier] Table creation failed: {e}")


# ---------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------

class ToolClassifier:
    """
    Trained neural tool classifier for Agent Engine.

    Uses sentence-transformers for encoding + sklearn MLP for classification.
    Model + active learning data stored in PostgreSQL — container-independent.

    Key differences from RG_Chat's classifier:
    - predict_top_n() for injecting tool lists into EXECUTION_FRAME
    - add_custom_tools() for expanding label space with user tools
    - No active-tool continuity (agents don't have chat history context)
    """

    def __init__(self):
        self._encoder = None
        self._classifier = None
        self._is_trained = False
        self._load_lock = asyncio.Lock()
        self._pending_samples: List[Dict] = []
        self._model_version = 0
        self._train_stats: Dict[str, Any] = {}
        self._custom_tools: List[str] = []  # dynamically added tools

    async def ensure_ready(self) -> bool:
        """Load encoder + classifier, training from seed if needed."""
        if self._is_trained and self._encoder is not None:
            return True
        async with self._load_lock:
            if self._is_trained and self._encoder is not None:
                return True
            try:
                await _ensure_tables()

                if self._encoder is None:
                    print("[ToolClassifier] Loading encoder...", flush=True)
                    ok = self._load_encoder()
                    if not ok:
                        print("[ToolClassifier] Encoder load FAILED", flush=True)
                        return False

                clf, stats, n_samples, version = await _load_model_from_db()
                if clf is not None:
                    try:
                        n_model_classes = len(clf.classes_)
                    except Exception:
                        n_model_classes = -1

                    from .training_data import get_training_data
                    _seed_count = len(get_training_data())

                    if n_model_classes != len(ALL_TOOLS):
                        logger.warning(
                            f"[ToolClassifier] DB model has {n_model_classes} classes "
                            f"but ALL_TOOLS has {len(ALL_TOOLS)} — retraining..."
                        )
                    elif n_samples < _seed_count:
                        logger.warning(
                            f"[ToolClassifier] DB model trained on {n_samples} samples "
                            f"but seed has {_seed_count} — retraining with new data..."
                        )
                    else:
                        self._classifier = clf
                        self._train_stats = stats
                        self._model_version = version
                        self._is_trained = True
                        logger.info(
                            f"[ToolClassifier] Loaded model v{version} from DB "
                            f"({n_samples} samples, seed={_seed_count}, "
                            f"acc={stats.get('train_accuracy', '?')})"
                        )
                        return True

                print("[ToolClassifier] Training from seed...", flush=True)
                await self._train_and_save(source="seed")
                return True

            except Exception as e:
                print(f"[ToolClassifier] Init failed: {e}", flush=True)
                logger.error(f"[ToolClassifier] Init failed: {e}", exc_info=True)
                return False

    def _load_encoder(self) -> bool:
        """Load the sentence-transformer encoder (synchronous)."""
        try:
            from sentence_transformers import SentenceTransformer
            model_name = os.getenv("TOOL_CLASSIFIER_MODEL", "all-MiniLM-L6-v2")
            logger.info(f"[ToolClassifier] Loading encoder: {model_name}")
            self._encoder = SentenceTransformer(model_name)
            return True
        except ImportError:
            logger.warning("[ToolClassifier] sentence-transformers not installed")
            return False
        except Exception as e:
            logger.error(f"[ToolClassifier] Encoder load error: {e}")
            return False

    def _encode_sample(
        self, message: str, context: List[Dict[str, str]]
    ) -> np.ndarray:
        """Encode a (message, context) pair to embedding."""
        parts = []
        if context:
            for msg in context[-3:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if content:
                    parts.append(f"{role}: {content[:200]}")
        parts.append(f"user: {message}")
        text = "\n".join(parts)
        return self._encoder.encode([text], normalize_embeddings=True)[0]

    def _train_on_samples(
        self, samples: List[Tuple], source: str = "unknown"
    ) -> Dict[str, Any]:
        """Train the MLP classifier on labeled samples (synchronous)."""
        from sklearn.neural_network import MLPClassifier
        from sklearn.model_selection import cross_val_score

        logger.info(f"[ToolClassifier] Encoding {len(samples)} samples (batched)...")
        # Batch-encode all samples at once for ~10x speedup vs one-by-one
        texts = []
        y_list = []
        for msg, ctx, tool_id in samples:
            parts = []
            if ctx:
                for m in ctx[-3:]:
                    role = m.get("role", "user")
                    content = m.get("content", "")
                    if content:
                        parts.append(f"{role}: {content[:200]}")
            parts.append(f"user: {msg}")
            texts.append("\n".join(parts))
            y_list.append(TOOL_TO_IDX.get(tool_id, 0))

        X_list = self._encoder.encode(
            texts, normalize_embeddings=True, batch_size=128, show_progress_bar=True
        )

        X = np.array(X_list)
        y = np.array(y_list)

        clf = MLPClassifier(
            hidden_layer_sizes=(256, 128),
            activation="relu",
            solver="adam",
            alpha=0.001,
            max_iter=500,
            early_stopping=True,
            validation_fraction=0.15,
            n_iter_no_change=20,
            random_state=42,
            verbose=False,
        )

        cv_mean, cv_std = 0.0, 0.0
        if len(samples) > 30:
            n_cv = min(5, len(samples) // 10)
            try:
                cv_scores = cross_val_score(clf, X, y, cv=n_cv, scoring="accuracy")
                cv_mean = float(cv_scores.mean())
                cv_std = float(cv_scores.std())
            except Exception:
                pass

        clf.fit(X, y)
        train_acc = float(clf.score(X, y))
        self._classifier = clf
        self._is_trained = True

        class_dist = Counter(y_list)
        class_stats = {
            (IDX_TO_TOOL.get(k) or "none"): v
            for k, v in sorted(class_dist.items())
        }

        self._train_stats = {
            "n_samples": len(samples),
            "n_classes": len(set(y_list)),
            "train_accuracy": round(train_acc, 4),
            "cv_accuracy": round(cv_mean, 4),
            "cv_std": round(cv_std, 4),
            "class_distribution": class_stats,
            "source": source,
            "timestamp": time.time(),
        }

        logger.info(
            f"[ToolClassifier] Training complete: "
            f"accuracy={train_acc:.3f}, cv={cv_mean:.3f}±{cv_std:.3f}, "
            f"classes={len(set(y_list))}, samples={len(samples)}, source={source}"
        )
        return self._train_stats

    async def _train_and_save(self, source: str = "seed") -> Dict[str, Any]:
        """Train from seed (+ active data) and save to DB."""
        from .training_data import get_training_data
        samples = get_training_data()

        active = await _load_active_samples_from_db(min_confidence=0.6)
        if active:
            samples.extend(active)
            logger.info(f"[ToolClassifier] Added {len(active)} active samples from DB")

        loop = asyncio.get_running_loop()
        stats = await loop.run_in_executor(
            None, self._train_on_samples, samples, source
        )

        self._model_version += 1
        await _save_model_to_db(
            self._classifier, stats, len(samples), self._model_version
        )
        return stats

    async def predict(
        self,
        goal: str,
        enabled_tool_ids: Optional[Set[str]] = None,
        context: List[Dict[str, str]] = None,
        user_id: str = None,
    ) -> ToolPrediction:
        """
        Predict which tool best matches this goal.

        Args:
            goal: The agent's current goal/task text
            enabled_tool_ids: Set of tool IDs to choose from.
                              If None, all tools are enabled.
            context: Optional recent context messages
            user_id: For active learning tracking
        """
        t0 = time.time()

        ready = await self.ensure_ready()
        if not ready:
            return ToolPrediction(
                tool_id=None,
                confidence=0.0,
                probabilities={},
                method="model_unavailable",
                latency_ms=(time.time() - t0) * 1000,
            )

        if enabled_tool_ids is None:
            enabled_tool_ids = {s for s in ALL_TOOLS if s is not None}

        loop = asyncio.get_running_loop()
        emb = await loop.run_in_executor(
            None, self._encode_sample, goal, context or []
        )

        proba = self._classifier.predict_proba(emb.reshape(1, -1))[0]

        prob_dict: Dict[str, float] = {}
        for idx, prob in enumerate(proba):
            tool = IDX_TO_TOOL.get(idx)
            label = tool if tool else "none"
            if tool is None or tool in enabled_tool_ids:
                prob_dict[label] = round(float(prob), 4)

        MIN_TOOL_CONFIDENCE = 0.15

        best_tool = None
        none_prob = prob_dict.get("none", 0.0)
        best_prob = none_prob
        for tid in enabled_tool_ids:
            sp = prob_dict.get(tid, 0.0)
            if sp > best_prob and sp >= MIN_TOOL_CONFIDENCE:
                best_prob = sp
                best_tool = tid

        # Build top-N ranked list
        ranked = sorted(
            [(tid, prob_dict.get(tid, 0.0)) for tid in enabled_tool_ids],
            key=lambda x: -x[1],
        )
        top_n = [(tid, score) for tid, score in ranked if score >= 0.01][:15]

        latency = (time.time() - t0) * 1000

        result = ToolPrediction(
            tool_id=best_tool,
            confidence=best_prob,
            probabilities=prob_dict,
            method="classifier",
            latency_ms=latency,
            top_n=top_n,
        )

        # Active learning: queue sample for DB
        self._pending_samples.append({
            "msg": goal[:500],
            "predicted": result.tool_id,
            "conf": round(result.confidence, 4),
            "method": result.method,
            "probs": {k: v for k, v in sorted(prob_dict.items(), key=lambda x: -x[1])[:5]},
            "user_id": user_id,
        })
        if len(self._pending_samples) >= _FLUSH_BATCH:
            asyncio.create_task(self._flush_to_db())

        logger.info(
            f"[ToolClassifier] tool={result.tool_id} conf={result.confidence:.3f} "
            f"method={result.method} latency={latency:.1f}ms "
            f"goal={goal[:60]!r}"
        )

        return result

    async def predict_top_n(
        self,
        goal: str,
        n: int = 10,
        enabled_tool_ids: Optional[Set[str]] = None,
        context: List[Dict[str, str]] = None,
        user_id: str = None,
    ) -> List[Tuple[str, float]]:
        """
        Predict top-N tools for a goal. Used to build dynamic EXECUTION_FRAME.

        Returns list of (tool_id, confidence) sorted by confidence.
        Always includes platform meta-tools (platform_api, discover_services).
        """
        prediction = await self.predict(goal, enabled_tool_ids, context, user_id)

        top = prediction.top_n[:n]
        top_ids = {t[0] for t in top}

        # Always include platform meta-tools so agents can discover services
        META_TOOLS = ["platform_api", "discover_services", "discover_api"]
        for mt in META_TOOLS:
            if mt not in top_ids and (enabled_tool_ids is None or mt in enabled_tool_ids):
                top.append((mt, 0.01))

        return top

    def add_custom_tools(self, tool_names: List[str]):
        """
        Expand the label space with custom tool names.

        After calling this, retrain() must be called for the model
        to learn when to predict these new tools.
        """
        global ALL_TOOLS, TOOL_TO_IDX, IDX_TO_TOOL
        added = []
        for name in tool_names:
            if name and name not in TOOL_TO_IDX:
                ALL_TOOLS.append(name)
                idx = len(ALL_TOOLS) - 1
                TOOL_TO_IDX[name] = idx
                IDX_TO_TOOL[idx] = name
                self._custom_tools.append(name)
                added.append(name)
        if added:
            logger.info(f"[ToolClassifier] Added {len(added)} custom tools: {added}")
            self._is_trained = False  # Force retrain
        return added

    async def retrain(self, custom_samples: List[Tuple] = None) -> Dict[str, Any]:
        """
        Retrain classifier using seed data + active learning + custom samples.

        Args:
            custom_samples: Optional extra training samples for custom tools.
                           Format: [(message, context_list, tool_id), ...]
        """
        await self._flush_to_db()
        from .training_data import get_training_data
        samples = get_training_data()

        active = await _load_active_samples_from_db(min_confidence=0.6)
        if active:
            samples.extend(active)
            logger.info(f"[ToolClassifier] Retrain: {len(active)} active samples from DB")

        if custom_samples:
            samples.extend(custom_samples)
            logger.info(f"[ToolClassifier] Retrain: {len(custom_samples)} custom samples")

        loop = asyncio.get_running_loop()
        stats = await loop.run_in_executor(
            None, self._train_on_samples, samples, "retrain"
        )

        self._model_version += 1
        await _save_model_to_db(
            self._classifier, stats, len(samples), self._model_version
        )
        return stats

    async def _flush_to_db(self) -> None:
        """Flush pending active learning samples to PostgreSQL."""
        if not self._pending_samples:
            return
        batch = self._pending_samples[:]
        self._pending_samples.clear()
        await _save_active_samples(batch)

    async def get_stats(self) -> Dict[str, Any]:
        """Get classifier statistics including DB counts."""
        active_count = await _count_active_samples()
        return {
            "is_trained": self._is_trained,
            "model_version": self._model_version,
            "n_tools": len(ALL_TOOLS),
            "n_custom_tools": len(self._custom_tools),
            "custom_tools": self._custom_tools,
            "train_stats": self._train_stats,
            "pending_samples": len(self._pending_samples),
            "active_samples_in_db": active_count,
        }


# Global singleton
tool_classifier = ToolClassifier()


async def _preload_inner() -> None:
    """Actual preload logic (runs in background)."""
    t0 = time.time()
    try:
        ok = await tool_classifier.ensure_ready()
        elapsed = (time.time() - t0) * 1000
        if ok:
            stats = await tool_classifier.get_stats()
            print(
                f"[ToolClassifier] Ready in {elapsed:.0f}ms — "
                f"v{stats['model_version']}, {stats['n_tools']} tools, "
                f"{stats['train_stats'].get('n_samples', 0)} samples, "
                f"acc={stats['train_stats'].get('train_accuracy', 0)}",
                flush=True,
            )
        else:
            print(f"[ToolClassifier] Preload FAILED in {elapsed:.0f}ms", flush=True)
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        print(f"[ToolClassifier] Preload error ({elapsed:.0f}ms): {e}", flush=True)


async def preload_tool_classifier() -> None:
    """Call at app startup — fires training in background so startup isn't blocked."""
    asyncio.create_task(_preload_inner())
