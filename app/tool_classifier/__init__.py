"""
Neural Tool Classifier for Agent Engine
========================================

Production-grade ML classifier for tool routing in autonomous agents.
Same architecture as RG_Chat's proven classifier, adapted for Agent Engine.

Architecture:
  1. Sentence-transformer encodes goal/message → 384-dim embedding
  2. Trained MLP classification head → tool probabilities
  3. Per-agent tool filtering (tool_mode + tools array)
  4. Active learning: every prediction saved to DB
  5. Model stored in PostgreSQL — survives container restarts
  6. Custom tool retraining support

Usage:
    from .tool_classifier import tool_classifier, preload_tool_classifier

    # At startup:
    await preload_tool_classifier()

    # Per request:
    prediction = await tool_classifier.predict(
        goal="search the web for Python tutorials",
        enabled_tool_ids={"web_search", "fetch_url", ...},
    )
    # prediction.tool_id = "web_search", prediction.confidence = 0.87
"""

from .classifier import (
    ToolClassifier,
    ToolPrediction,
    ALL_TOOLS,
    TOOL_TO_IDX,
    IDX_TO_TOOL,
    tool_classifier,
    preload_tool_classifier,
)

__all__ = [
    "ToolClassifier",
    "ToolPrediction",
    "ALL_TOOLS",
    "TOOL_TO_IDX",
    "IDX_TO_TOOL",
    "tool_classifier",
    "preload_tool_classifier",
]
