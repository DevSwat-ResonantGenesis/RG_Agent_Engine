#!/bin/bash
# ============================================================================
# PLAN: Agent Engine Tool System Rewrite — Full Neural Classifier
# ============================================================================
# Created: Apr 22, 2026
# Status: IN PROGRESS
#
# GOAL: Replace the legacy text-based tool selection with the same neural
# classifier architecture proven in RG_Chat. Full production-grade system
# with per-agent tool config, custom tool retraining, clean modularity.
#
# ============================================================================
# CURRENT STATE (PROBLEMS)
# ============================================================================
#
# 1. EXECUTION_FRAME hardcodes tool names as plain text — LLM guesses
# 2. agent_executor.py is 100% DEAD CODE (681 lines, only goal_pursuit.py
#    imports get_agent_executor, but it uses a separate AgentExecutor class
#    that duplicates executor.py's class)
# 3. rg_tool_registry/ is a stale vendored copy (10 files) — used only for
#    build_registry() to get tool definitions and api_catalog for discover_*
# 4. No neural tool selection — LLM picks tools from text, wastes tokens
# 5. tool_mode='smart' vs 'manual' exists in model but executor ignores it
# 6. AgentDefinition.tools array exists but executor never reads it
# 7. No way to retrain classifier with custom tools
#
# ============================================================================
# TARGET ARCHITECTURE
# ============================================================================
#
# app/tool_classifier/
#   __init__.py              — Exports: ToolClassifier, ToolPrediction, ALL_TOOLS
#   classifier.py            — Neural classifier (sentence-transformer → MLP)
#   training_data.py         — Seed training data (copied from RG_Chat)
#
# executor.py changes:
#   - __init__: Load ToolClassifier alongside handler_map
#   - _get_next_action: Predict top-N tools → inject into EXECUTION_FRAME
#   - Per-agent filtering: Read agent.tool_mode + agent.tools
#   - Custom tool discovery: Merge user custom tools into enabled set
#
# Model already has (no migration needed):
#   - tool_mode: "smart" (classifier picks) or "manual" (only agent.tools)
#   - tools: ARRAY(String) — list of enabled tool IDs
#   - tool_config: JSON — per-tool config overrides
#
# ============================================================================
# DEAD CODE TO NUKE
# ============================================================================
#
# [ ] agent_executor.py          — 681 lines, 100% dead (separate AgentExecutor)
# [ ] rg_tool_registry/          — 10 files, stale vendored copy
#     KEEP: api_catalog.py (discover_services/discover_api still used)
#     KEEP: observability.py (ToolObserver still used)
#     DELETE: builtin_tools.py, builtin_tools_ide.py, registry.py,
#             native_fc.py, streaming.py, builder.py,
#             autonomous_tool_builder.py, __init__.py
# [ ] goal_pursuit.py reference to agent_executor → fix to use executor.py
#
# ============================================================================
# PHASES
# ============================================================================
#
# PHASE 1: Create app/tool_classifier/ module
#   - Copy classifier.py from RG_Chat (adapt DB imports)
#   - Copy training_data.py from RG_Chat
#   - Adapt to Agent Engine's DB session factory
#   - Add sentence-transformers + scikit-learn to requirements.txt
#
# PHASE 2: Wire classifier into executor.py
#   - Load classifier on init
#   - _get_next_action: predict tools → dynamic EXECUTION_FRAME
#   - Per-agent tool filtering (tool_mode + tools array)
#   - Custom tools merged into enabled set
#
# PHASE 3: Nuke dead code
#   - Delete agent_executor.py
#   - Fix goal_pursuit.py to use executor.py
#   - Delete unused rg_tool_registry files (keep api_catalog + observability)
#   - Clean up custom_tools.py reference to rg_tool_registry
#
# PHASE 4: Custom tool retraining
#   - When user creates custom tool, add training samples
#   - Retrain classifier to include new custom tools
#   - API endpoint: POST /tools/retrain-classifier
#
# PHASE 5: Update README + deploy
#   - Document new tool system architecture
#   - Deploy to production
#   - Verify in logs
#
# ============================================================================
# CHECKPOINTS
# ============================================================================
#
# [x] CP1: tool_classifier/ module created and compiles
#     - app/tool_classifier/__init__.py
#     - app/tool_classifier/classifier.py (ToolClassifier, ALL_TOOLS, predict_top_n)
#     - app/tool_classifier/training_data.py (1588-line seed dataset, copied from RG_Chat)
#
# [x] CP2: Classifier wired into executor._get_next_action
#     - EXECUTION_FRAME now uses {tools_section} placeholder
#     - _build_tools_section() generates dynamic tool list from predictions
#     - predict_top_n() called before every LLM call
#
# [x] CP3: Per-agent tool filtering working (tool_mode + tools)
#     - tool_mode="smart" → classifier picks from ALL tools
#     - tool_mode="manual" → classifier restricted to agent.tools array
#     - AgentDefinition.tools already existed in model (ARRAY(String))
#
# [x] CP4: Dead code nuked
#     - DELETED: agent_executor.py (681 lines, 100% dead)
#     - DELETED: rg_tool_registry/native_fc.py
#     - DELETED: rg_tool_registry/streaming.py
#     - DELETED: rg_tool_registry/builder.py
#     - DELETED: rg_tool_registry/autonomous_tool_builder.py
#     - DELETED: rg_tool_registry/builtin_tools_ide.py
#     - FIXED: goal_pursuit.py → now uses executor.py instead of dead agent_executor.py
#     - CLEANED: rg_tool_registry/__init__.py — removed dead exports
#
# [x] CP5: Custom tool retraining endpoints
#     - POST /tools/classifier/predict — predict tools for a goal
#     - POST /tools/classifier/retrain — retrain with seed + active + custom samples
#     - GET  /tools/classifier/stats — model stats
#     - POST /tools/classifier/add-custom-tools — expand label space
#
# [x] CP6: README updated, plan file updated
#
# [ ] CP7: Deployed to production, verified in logs
#
# ============================================================================
# FILES CREATED / MODIFIED
# ============================================================================
#
# CREATED:
#   app/tool_classifier/__init__.py
#   app/tool_classifier/classifier.py
#   app/tool_classifier/training_data.py
#   PLAN_TOOL_SYSTEM_REWRITE.sh (this file)
#
# MODIFIED:
#   app/executor.py — neural classifier integration
#   app/main.py — startup preload hook
#   app/routers.py — 4 classifier API endpoints
#   app/goal_pursuit.py — fixed dead import
#   app/rg_tool_registry/__init__.py — cleaned dead exports
#   requirements.txt — added sentence-transformers, scikit-learn, numpy
#
# DELETED:
#   app/agent_executor.py (681 lines)
#   app/rg_tool_registry/native_fc.py
#   app/rg_tool_registry/streaming.py
#   app/rg_tool_registry/builder.py
#   app/rg_tool_registry/autonomous_tool_builder.py
#   app/rg_tool_registry/builtin_tools_ide.py
#
# ============================================================================
echo "This is a plan file. Read it, don't run it."
