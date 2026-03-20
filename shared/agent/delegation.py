"""Cross-service agent delegation for distributed autonomous execution.

Enables agents to delegate tasks to other agents across services:
- Chat → IDE task delegation
- IDE → Backend retrieval delegation
- Worker → Safety agent delegation
- Any agent → Specialized agent delegation

Uses internal gateway messaging for reliable cross-service communication.
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import httpx


class DelegationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


class AgentRole(str, Enum):
    WORKER = "worker"
    SUPERVISOR = "supervisor"
    REVIEWER = "reviewer"
    SAFETY = "safety"
    PLANNER = "planner"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    MEMORY = "memory"
    CODE = "code"
    RESEARCH = "research"


@dataclass
class DelegationRequest:
    """Request to delegate a task to another agent."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_agent_id: str = ""
    source_service: str = ""
    target_role: AgentRole = AgentRole.WORKER
    target_service: Optional[str] = None
    target_agent_id: Optional[str] = None
    task_description: str = ""
    task_input: Dict[str, Any] = field(default_factory=dict)
    priority: int = 5  # 1-10, higher is more urgent
    timeout_seconds: int = 300
    callback_url: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class DelegationResponse:
    """Response from a delegated task."""
    request_id: str
    status: DelegationStatus
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    agent_id: Optional[str] = None
    service: Optional[str] = None
    duration_ms: int = 0
    completed_at: Optional[str] = None


class AgentDelegator:
    """
    Handles cross-service agent delegation.
    
    Routes tasks to appropriate agents based on:
    - Role requirements
    - Service availability
    - Agent capacity
    - Priority
    """

    # Service registry - maps roles to services
    SERVICE_REGISTRY = {
        AgentRole.WORKER: ["agent-engine", "ed-service"],
        AgentRole.SUPERVISOR: ["agent-engine"],
        AgentRole.REVIEWER: ["agent-engine", "ide-service"],
        AgentRole.SAFETY: ["agent-engine"],
        AgentRole.PLANNER: ["agent-engine", "ide-service"],
        AgentRole.EXECUTOR: ["agent-engine", "ed-service"],
        AgentRole.VERIFIER: ["agent-engine"],
        AgentRole.MEMORY: ["memory-service"],
        AgentRole.CODE: ["ed-service", "ide-service"],
        AgentRole.RESEARCH: ["memory-service", "chat-service"],
    }

    # Service endpoints
    SERVICE_ENDPOINTS = {
        "agent-engine": "http://agent-engine:8000",
        "ed-service": "http://ed-service:8000",
        "ide-service": "http://ide-service:8000",
        "memory-service": "http://memory-service:8000",
        "chat-service": "http://chat-service:8000",
    }

    def __init__(self, gateway_url: Optional[str] = None):
        self.gateway_url = gateway_url or "http://gateway:8000"
        self.pending_requests: Dict[str, DelegationRequest] = {}
        self.completed_requests: Dict[str, DelegationResponse] = {}
        self.callbacks: Dict[str, Callable] = {}
        self._lock = asyncio.Lock()

    async def delegate(
        self,
        source_agent_id: str,
        source_service: str,
        target_role: AgentRole,
        task_description: str,
        task_input: Dict[str, Any],
        priority: int = 5,
        timeout_seconds: int = 300,
        target_service: Optional[str] = None,
        target_agent_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        wait_for_result: bool = True,
    ) -> DelegationResponse:
        """
        Delegate a task to another agent.
        
        Args:
            source_agent_id: ID of the delegating agent
            source_service: Service name of the delegating agent
            target_role: Role required for the task
            task_description: Human-readable task description
            task_input: Input data for the task
            priority: Task priority (1-10)
            timeout_seconds: Maximum time to wait
            target_service: Specific service to target (optional)
            target_agent_id: Specific agent to target (optional)
            context: Additional context for the task
            wait_for_result: Whether to wait for completion
            
        Returns:
            DelegationResponse with result or status
        """
        request = DelegationRequest(
            source_agent_id=source_agent_id,
            source_service=source_service,
            target_role=target_role,
            target_service=target_service,
            target_agent_id=target_agent_id,
            task_description=task_description,
            task_input=task_input,
            priority=priority,
            timeout_seconds=timeout_seconds,
            context=context or {},
        )

        async with self._lock:
            self.pending_requests[request.id] = request

        # Find target service
        service = target_service or self._select_service(target_role)
        if not service:
            return DelegationResponse(
                request_id=request.id,
                status=DelegationStatus.FAILED,
                error=f"No service available for role: {target_role}",
            )

        # Send delegation request
        start_time = time.time()
        
        try:
            endpoint = self.SERVICE_ENDPOINTS.get(service, f"http://{service}:8000")
            
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{endpoint}/agent/delegate",
                    json={
                        "request_id": request.id,
                        "source_agent_id": source_agent_id,
                        "source_service": source_service,
                        "target_role": target_role.value,
                        "task_description": task_description,
                        "task_input": task_input,
                        "priority": priority,
                        "context": context or {},
                    },
                    headers={
                        "X-Delegation-ID": request.id,
                        "X-Source-Service": source_service,
                    },
                )

                duration_ms = int((time.time() - start_time) * 1000)

                if response.status_code == 200:
                    result = response.json()
                    delegation_response = DelegationResponse(
                        request_id=request.id,
                        status=DelegationStatus(result.get("status", "completed")),
                        result=result.get("result"),
                        error=result.get("error"),
                        agent_id=result.get("agent_id"),
                        service=service,
                        duration_ms=duration_ms,
                        completed_at=datetime.utcnow().isoformat(),
                    )
                elif response.status_code == 202:
                    # Accepted but not completed - async processing
                    if wait_for_result:
                        return await self._wait_for_result(
                            request.id,
                            timeout_seconds - (time.time() - start_time),
                        )
                    return DelegationResponse(
                        request_id=request.id,
                        status=DelegationStatus.ACCEPTED,
                        service=service,
                    )
                else:
                    delegation_response = DelegationResponse(
                        request_id=request.id,
                        status=DelegationStatus.FAILED,
                        error=f"Service returned {response.status_code}: {response.text[:500]}",
                        service=service,
                        duration_ms=duration_ms,
                    )

        except asyncio.TimeoutError:
            delegation_response = DelegationResponse(
                request_id=request.id,
                status=DelegationStatus.TIMEOUT,
                error=f"Delegation timed out after {timeout_seconds}s",
            )
        except Exception as e:
            delegation_response = DelegationResponse(
                request_id=request.id,
                status=DelegationStatus.FAILED,
                error=str(e),
            )

        # Store completed response
        async with self._lock:
            self.completed_requests[request.id] = delegation_response
            if request.id in self.pending_requests:
                del self.pending_requests[request.id]

        return delegation_response

    async def delegate_to_supervisor(
        self,
        agent_id: str,
        service: str,
        issue: str,
        context: Dict[str, Any],
    ) -> DelegationResponse:
        """Escalate an issue to a supervisor agent."""
        return await self.delegate(
            source_agent_id=agent_id,
            source_service=service,
            target_role=AgentRole.SUPERVISOR,
            task_description=f"Escalation: {issue}",
            task_input={"issue": issue, "context": context},
            priority=8,
            timeout_seconds=60,
        )

    async def delegate_to_verifier(
        self,
        agent_id: str,
        service: str,
        step_output: Dict[str, Any],
        expected_outcome: str,
    ) -> DelegationResponse:
        """Request verification from a verifier agent."""
        return await self.delegate(
            source_agent_id=agent_id,
            source_service=service,
            target_role=AgentRole.VERIFIER,
            task_description="Verify step output",
            task_input={
                "step_output": step_output,
                "expected_outcome": expected_outcome,
            },
            priority=7,
            timeout_seconds=30,
        )

    async def delegate_to_memory(
        self,
        agent_id: str,
        service: str,
        query: str,
        operation: str = "retrieve",
    ) -> DelegationResponse:
        """Delegate memory operation to memory service."""
        return await self.delegate(
            source_agent_id=agent_id,
            source_service=service,
            target_role=AgentRole.MEMORY,
            task_description=f"Memory {operation}: {query[:100]}",
            task_input={"query": query, "operation": operation},
            priority=5,
            timeout_seconds=30,
        )

    async def delegate_code_task(
        self,
        agent_id: str,
        service: str,
        task: str,
        code_context: Dict[str, Any],
    ) -> DelegationResponse:
        """Delegate a coding task to a code agent."""
        return await self.delegate(
            source_agent_id=agent_id,
            source_service=service,
            target_role=AgentRole.CODE,
            task_description=task,
            task_input=code_context,
            priority=6,
            timeout_seconds=300,
        )

    def _select_service(self, role: AgentRole) -> Optional[str]:
        """Select the best service for a role."""
        services = self.SERVICE_REGISTRY.get(role, [])
        if not services:
            return None
        
        # For now, just return the first available
        # TODO: Add load balancing and health checks
        return services[0]

    async def _wait_for_result(
        self,
        request_id: str,
        timeout: float,
    ) -> DelegationResponse:
        """Wait for an async delegation to complete."""
        start = time.time()
        
        while time.time() - start < timeout:
            async with self._lock:
                if request_id in self.completed_requests:
                    return self.completed_requests[request_id]
            
            await asyncio.sleep(0.5)
        
        return DelegationResponse(
            request_id=request_id,
            status=DelegationStatus.TIMEOUT,
            error=f"Timed out waiting for result after {timeout:.0f}s",
        )

    async def handle_delegation_request(
        self,
        request_data: Dict[str, Any],
        handler: Callable,
    ) -> Dict[str, Any]:
        """
        Handle an incoming delegation request.
        
        This should be called by services that receive delegation requests.
        """
        request_id = request_data.get("request_id")
        
        try:
            result = await handler(request_data)
            return {
                "request_id": request_id,
                "status": "completed",
                "result": result,
            }
        except Exception as e:
            return {
                "request_id": request_id,
                "status": "failed",
                "error": str(e),
            }

    def get_pending_count(self) -> int:
        """Get count of pending delegations."""
        return len(self.pending_requests)

    def get_stats(self) -> Dict[str, Any]:
        """Get delegation statistics."""
        completed = list(self.completed_requests.values())
        
        if not completed:
            return {"total": 0}
        
        statuses = [r.status.value for r in completed]
        durations = [r.duration_ms for r in completed if r.duration_ms > 0]
        
        return {
            "total": len(completed),
            "pending": len(self.pending_requests),
            "completed": statuses.count("completed"),
            "failed": statuses.count("failed"),
            "timeout": statuses.count("timeout"),
            "avg_duration_ms": sum(durations) / len(durations) if durations else 0,
        }


# Global delegator instance
agent_delegator = AgentDelegator()
