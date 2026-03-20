"""Configuration Module"""
import os
from pathlib import Path

# Service configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./agent_engine.db")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

# Agent configuration
MAX_AGENTS = int(os.getenv("MAX_AGENTS", "100"))
AGENT_TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "300"))
