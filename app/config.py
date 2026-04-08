"""Agent Engine configuration."""

import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@db:5432/resonant_agents"
    )
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    LLM_SERVICE_URL: str = os.getenv("LLM_SERVICE_URL", "http://llm_service:8000")
    MEMORY_SERVICE_URL: str = os.getenv("MEMORY_SERVICE_URL", "http://memory_service:8000")
    CODE_EXECUTION_SERVICE_URL: str = os.getenv(
        "CODE_EXECUTION_SERVICE_URL",
        "http://code_execution_service:8002",
    )
    CHAT_SERVICE_URL: str = os.getenv("CHAT_SERVICE_URL", "http://chat_service:8000")
    ED_SERVICE_URL: str = os.getenv("ED_SERVICE_URL", "http://ed_service:8000")
    
    # Safety limits
    MAX_LOOP_ITERATIONS: int = 200
    MAX_TOKENS_PER_RUN: int = 500000
    MAX_TOOL_CALLS_PER_STEP: int = 20
    SAFETY_TIMEOUT_SECONDS: int = 300
    
    # Agent defaults
    DEFAULT_MODEL: str = "gpt-4o"

    SANDBOX_RUNNER_URL: str = os.getenv(
        "SANDBOX_RUNNER_URL",
        "http://sandbox_runner_service:9001",
    )
    SANDBOX_RUNNER_API_KEY: str = os.getenv(
        "SANDBOX_RUNNER_API_KEY",
        "",
    )

    # Internal service authentication
    INTERNAL_SERVICE_KEY: str = os.getenv("INTERNAL_SERVICE_KEY", "")

    AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_ENABLED: bool = os.getenv(
        "AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_ENABLED",
        "false",
    ).strip().lower() in ("1", "true", "yes", "y", "on")
    AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_IMAGE: str = os.getenv(
        "AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_IMAGE",
        "python:3.11-alpine",
    )
    AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_TIMEOUT_SECONDS: int = int(
        os.getenv("AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_TIMEOUT_SECONDS", "20")
    )
    AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_MEMORY: str = os.getenv(
        "AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_MEMORY",
        "256m",
    )
    AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_CPUS: str = os.getenv(
        "AGENT_ENGINE_DOCKER_PER_RUN_SANDBOX_CPUS",
        "0.5",
    )
    
    class Config:
        env_file = ".env"


settings = Settings()
