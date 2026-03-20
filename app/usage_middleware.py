"""Usage Tracking Middleware - Hook into agent execution to track tokens."""

from typing import Optional, Dict, Any
from uuid import UUID
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from .models_billing import UsageType, Organization
from .services.usage_service import UsageService

logger = logging.getLogger(__name__)


class UsageMiddleware:
    """Middleware to check and record usage before/after agent operations."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.service = UsageService(db)
    
    async def check_before_execution(
        self,
        org_id: UUID,
        usage_type: UsageType = UsageType.AGENT_EXECUTION,
        quantity: int = 1,
    ) -> Dict[str, Any]:
        """
        Check if organization has enough tokens before execution.
        Returns result with can_execute flag.
        """
        result = await self.service.check_can_execute(
            org_id=org_id,
            usage_type=usage_type,
            quantity=quantity,
        )
        
        if not result["can_execute"]:
            logger.warning(f"Org {org_id} blocked: {result.get('error')}")
        
        return result
    
    async def record_after_execution(
        self,
        org_id: UUID,
        usage_type: UsageType,
        quantity: int = 1,
        user_id: Optional[UUID] = None,
        resource_id: Optional[UUID] = None,
        resource_name: Optional[str] = None,
        llm_input_tokens: int = 0,
        llm_output_tokens: int = 0,
        metadata: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Record usage after successful execution.
        Also records LLM token usage if provided.
        """
        # Record the main operation
        result = await self.service.record_usage(
            org_id=org_id,
            usage_type=usage_type,
            quantity=quantity,
            user_id=user_id,
            resource_id=resource_id,
            resource_name=resource_name,
            metadata=metadata,
        )
        
        if not result["success"]:
            return result
        
        # Record LLM usage if provided
        if llm_input_tokens > 0:
            await self.service.record_usage(
                org_id=org_id,
                usage_type=UsageType.LLM_INPUT,
                quantity=max(1, llm_input_tokens // 1000),
                user_id=user_id,
                metadata={"raw_tokens": llm_input_tokens},
            )
        
        if llm_output_tokens > 0:
            await self.service.record_usage(
                org_id=org_id,
                usage_type=UsageType.LLM_OUTPUT,
                quantity=max(1, llm_output_tokens // 1000),
                user_id=user_id,
                metadata={"raw_tokens": llm_output_tokens},
            )
        
        return result


async def check_usage_limit(
    db: AsyncSession,
    org_id: UUID,
    usage_type: UsageType = UsageType.AGENT_EXECUTION,
) -> bool:
    """Quick check if org can execute. Returns True if allowed."""
    middleware = UsageMiddleware(db)
    result = await middleware.check_before_execution(org_id, usage_type)
    return result.get("can_execute", False)


async def record_execution(
    db: AsyncSession,
    org_id: UUID,
    agent_id: UUID,
    agent_name: str,
    user_id: Optional[UUID] = None,
    llm_input_tokens: int = 0,
    llm_output_tokens: int = 0,
) -> Dict[str, Any]:
    """Record an agent execution with LLM usage."""
    middleware = UsageMiddleware(db)
    return await middleware.record_after_execution(
        org_id=org_id,
        usage_type=UsageType.AGENT_EXECUTION,
        user_id=user_id,
        resource_id=agent_id,
        resource_name=agent_name,
        llm_input_tokens=llm_input_tokens,
        llm_output_tokens=llm_output_tokens,
    )


async def record_workflow(
    db: AsyncSession,
    org_id: UUID,
    workflow_id: UUID,
    workflow_name: str,
    user_id: Optional[UUID] = None,
) -> Dict[str, Any]:
    """Record a workflow run."""
    middleware = UsageMiddleware(db)
    return await middleware.record_after_execution(
        org_id=org_id,
        usage_type=UsageType.WORKFLOW_RUN,
        user_id=user_id,
        resource_id=workflow_id,
        resource_name=workflow_name,
    )


# Decorator for automatic usage tracking
def track_usage(usage_type: UsageType = UsageType.AGENT_EXECUTION):
    """Decorator to automatically track usage for async functions."""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Extract org_id from kwargs or first arg
            org_id = kwargs.get("org_id")
            db = kwargs.get("db") or kwargs.get("db_session")
            
            if org_id and db:
                # Check before execution
                can_execute = await check_usage_limit(db, org_id, usage_type)
                if not can_execute:
                    raise PermissionError("Token limit exceeded")
            
            # Execute function
            result = await func(*args, **kwargs)
            
            # Record usage after
            if org_id and db:
                service = UsageService(db)
                await service.record_usage(
                    org_id=org_id,
                    usage_type=usage_type,
                    quantity=1,
                )
            
            return result
        return wrapper
    return decorator
