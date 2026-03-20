"""Models Module"""
from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel

class Agent(BaseModel):
    """Agent model"""
    id: str
    name: str
    type: str
    status: str = "active"
    created_at: datetime = datetime.now()
    updated_at: datetime = datetime.now()
    config: Dict[str, Any] = {}

class Task(BaseModel):
    """Task model"""
    id: str
    agent_id: str
    name: str
    description: str
    status: str = "pending"
    created_at: datetime = datetime.now()
    updated_at: datetime = datetime.now()
    result: Optional[Dict[str, Any]] = None
