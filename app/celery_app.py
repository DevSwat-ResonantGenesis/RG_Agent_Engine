"""
Celery Application Configuration
================================

Background task processing for autonomous agent execution.
Enables agents to run without blocking API requests.
"""

import os
from celery import Celery
from kombu import Queue

# Redis URL for broker and backend
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)

# Create Celery app
celery_app = Celery(
    "agent_engine",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["app.tasks"],
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    
    # Task execution
    task_acks_late=True,  # Acknowledge after task completes
    task_reject_on_worker_lost=True,  # Requeue if worker dies
    task_time_limit=3600,  # 1 hour hard limit
    task_soft_time_limit=3300,  # 55 min soft limit (allows cleanup)
    
    # Worker settings
    worker_prefetch_multiplier=1,  # One task at a time per worker
    worker_concurrency=4,  # 4 concurrent tasks per worker
    worker_max_tasks_per_child=100,  # Restart worker after 100 tasks
    
    # Result backend
    result_expires=86400,  # Results expire after 24 hours
    result_extended=True,  # Store task args in result
    
    # Task routing
    task_default_queue="default",
    task_queues=(
        Queue("default", routing_key="default"),
        Queue("agents", routing_key="agents.#"),
        Queue("agents.high", routing_key="agents.high"),
        Queue("agents.low", routing_key="agents.low"),
        Queue("scheduled", routing_key="scheduled"),
    ),
    task_routes={
        "app.tasks.execute_agent_session": {"queue": "agents"},
        "app.tasks.execute_agent_step": {"queue": "agents"},
        "app.tasks.scheduled_agent_trigger": {"queue": "scheduled"},
        "app.tasks.high_priority_agent": {"queue": "agents.high"},
    },
    
    # Beat scheduler (for periodic tasks)
    beat_scheduler="celery.beat:PersistentScheduler",
    beat_schedule_filename="/tmp/celerybeat-schedule",
    
    # Monitoring
    worker_send_task_events=True,
    task_send_sent_event=True,
)

# Beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    # Health check every minute
    "agent-health-check": {
        "task": "app.tasks.check_agent_health",
        "schedule": 60.0,
    },
    # Process scheduled agents every 30 seconds
    "process-scheduled-agents": {
        "task": "app.tasks.process_scheduled_triggers",
        "schedule": 30.0,
    },
    # Cleanup stale sessions every 5 minutes
    "cleanup-stale-sessions": {
        "task": "app.tasks.cleanup_stale_sessions",
        "schedule": 300.0,
    },
}


def get_celery_app() -> Celery:
    """Get the Celery application instance."""
    return celery_app
