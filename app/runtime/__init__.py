"""
Runtime Layer for Resonant Assistant
=====================================
Intelligent middleware between user messages and LLM calls.

Components:
- context_manager: Token-aware context window management
- smart_memory: Relevance-scored memory retrieval
(tool_selector removed — registry handles priority-based selection)
"""
