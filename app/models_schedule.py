"""
Agent Schedule Models
=====================

Database models for scheduled agent execution.
"""

from datetime import datetime
from typing import Optional, Dict, Any
from uuid import UUID, uuid4

from sqlalchemy import Column, String, Boolean, Integer, DateTime, ForeignKey, JSON, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship

from .db import Base


class AgentSchedule(Base):
    """Schedule for periodic agent execution."""
    
    __tablename__ = "agent_schedules"
    
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    agent_id = Column(PGUUID(as_uuid=True), ForeignKey("agent_definitions.id"), nullable=False)
    user_id = Column(String(255), nullable=True)
    
    # Schedule configuration
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, default=True)
    
    # Trigger configuration (cron OR interval)
    cron_expression = Column(String(100), nullable=True)  # "0 9 * * *" = 9 AM daily
    interval_seconds = Column(Integer, nullable=True)  # Alternative: run every N seconds
    
    # Execution configuration
    goal = Column(Text, nullable=False)
    context = Column(JSON, default=dict)
    max_retries = Column(Integer, default=3)
    timeout_seconds = Column(Integer, default=3600)
    
    # Tracking
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    run_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    
    # Metadata
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    agent = relationship("AgentDefinition", back_populates="schedules")


class AgentTrigger(Base):
    """Event-based trigger for agent execution."""
    
    __tablename__ = "agent_triggers"
    
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    agent_id = Column(PGUUID(as_uuid=True), ForeignKey("agent_definitions.id"), nullable=False)
    user_id = Column(String(255), nullable=True)
    
    # Trigger configuration
    name = Column(String(255), nullable=False)
    trigger_type = Column(String(50), nullable=False)  # webhook, file_change, message, api
    enabled = Column(Boolean, default=True)
    
    # Webhook configuration
    webhook_secret = Column(String(255), nullable=True)
    webhook_path = Column(String(255), nullable=True)  # /webhooks/agent/{id}/trigger
    
    # File change configuration
    watch_path = Column(String(500), nullable=True)
    file_patterns = Column(JSON, default=list)  # ["*.py", "*.js"]
    
    # Message trigger configuration
    message_topic = Column(String(255), nullable=True)
    message_filter = Column(JSON, default=dict)
    
    # Execution configuration
    goal_template = Column(Text, nullable=False)  # Can include {event} placeholders
    context_template = Column(JSON, default=dict)
    debounce_seconds = Column(Integer, default=5)
    
    # Tracking
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)
    trigger_count = Column(Integer, default=0)
    
    # Metadata
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    agent = relationship("AgentDefinition", back_populates="triggers")


class AgentExecution(Base):
    """Record of agent execution for auditing."""
    
    __tablename__ = "agent_executions"
    
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id = Column(PGUUID(as_uuid=True), ForeignKey("agent_sessions.id"), nullable=False)
    schedule_id = Column(PGUUID(as_uuid=True), ForeignKey("agent_schedules.id"), nullable=True)
    trigger_id = Column(PGUUID(as_uuid=True), ForeignKey("agent_triggers.id"), nullable=True)
    
    # Execution details
    execution_type = Column(String(50), nullable=False)  # manual, scheduled, triggered
    celery_task_id = Column(String(255), nullable=True)
    
    # Status
    status = Column(String(50), default="pending")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    
    # Results
    steps_executed = Column(Integer, default=0)
    output = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    
    # Metadata
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
