from pydantic import BaseModel, Field
from typing import Dict, Any, Optional
from uuid import UUID
from enum import Enum

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class Task(BaseModel):
    id: UUID
    organization_id: UUID
    initiator_agent_id: UUID
    assigned_agent_id: Optional[UUID]
    status: TaskStatus = TaskStatus.PENDING
    payload: Dict[str, Any]

class TaskContext(BaseModel):
    organization_id: UUID
    context_data: Dict[str, Any] = Field(default_factory=dict)
