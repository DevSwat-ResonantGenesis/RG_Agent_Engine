"""Usage Tracking Service - Track token consumption for billing."""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from uuid import UUID
import logging

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models_billing import (
    UsageRecord, UsageSummary, Organization, 
    UsageType, TOKEN_COSTS, PLAN_TOKENS, PlanTier
)

logger = logging.getLogger(__name__)


class UsageService:
    """Service for tracking and managing usage/token consumption."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def record_usage(
        self,
        org_id: UUID,
        usage_type: UsageType,
        quantity: int = 1,
        user_id: Optional[UUID] = None,
        resource_id: Optional[UUID] = None,
        resource_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Record usage and deduct tokens.
        
        Args:
            org_id: Organization ID
            usage_type: Type of usage (agent_execution, workflow_run, etc.)
            quantity: Number of units (executions, GB, 1K tokens, etc.)
            user_id: Optional user who performed the action
            resource_id: Optional resource ID (agent, workflow)
            resource_name: Optional resource name
            metadata: Additional context
            
        Returns:
            Dict with usage details and remaining tokens
        """
        # Calculate tokens consumed
        token_cost = TOKEN_COSTS.get(usage_type, 0)
        tokens_consumed = token_cost * quantity
        
        # Get organization
        org = await self.db.get(Organization, org_id)
        if not org:
            raise ValueError(f"Organization {org_id} not found")
        
        # Check if unlimited plan
        is_unlimited = org.monthly_token_limit == -1
        
        # Check usage limits
        if not is_unlimited:
            total_used = org.tokens_used_this_period + tokens_consumed
            
            # Check if over limit
            if total_used > org.monthly_token_limit:
                # Check if overage is enabled
                if not org.overage_enabled:
                    return {
                        "success": False,
                        "error": "token_limit_exceeded",
                        "tokens_remaining": max(0, org.monthly_token_limit - org.tokens_used_this_period),
                        "tokens_required": tokens_consumed,
                        "message": "Monthly token limit exceeded. Upgrade your plan or enable overage.",
                    }
                
                # Check overage limit
                overage_needed = total_used - org.monthly_token_limit
                if org.overage_limit and (org.overage_tokens_used + overage_needed) > org.overage_limit:
                    return {
                        "success": False,
                        "error": "overage_limit_exceeded",
                        "message": "Overage limit exceeded.",
                    }
                
                # Record as overage
                org.overage_tokens_used += overage_needed
        
        # Update organization usage
        org.tokens_used_this_period += tokens_consumed
        
        # Create usage record
        usage_record = UsageRecord(
            org_id=org_id,
            user_id=user_id,
            usage_type=usage_type.value,
            tokens_consumed=tokens_consumed,
            resource_id=resource_id,
            resource_name=resource_name,
            metadata=metadata,
            billing_period_start=org.billing_period_start,
        )
        self.db.add(usage_record)
        
        await self.db.commit()
        
        # Calculate remaining
        if is_unlimited:
            tokens_remaining = -1
            usage_percent = 0
        else:
            tokens_remaining = max(0, org.monthly_token_limit - org.tokens_used_this_period)
            usage_percent = (org.tokens_used_this_period / org.monthly_token_limit) * 100
        
        # Check alert threshold
        alert = None
        if not is_unlimited and usage_percent >= org.alert_threshold:
            alert = {
                "type": "usage_warning",
                "threshold": org.alert_threshold,
                "current_percent": round(usage_percent, 1),
            }
        
        return {
            "success": True,
            "tokens_consumed": tokens_consumed,
            "tokens_remaining": tokens_remaining,
            "tokens_used_this_period": org.tokens_used_this_period,
            "usage_percent": round(usage_percent, 1) if not is_unlimited else 0,
            "is_overage": org.overage_tokens_used > 0,
            "overage_tokens": org.overage_tokens_used,
            "alert": alert,
        }
    
    async def get_usage_summary(
        self,
        org_id: UUID,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Get usage summary for an organization."""
        org = await self.db.get(Organization, org_id)
        if not org:
            raise ValueError(f"Organization {org_id} not found")
        
        # Default to current billing period
        if not period_start:
            period_start = org.billing_period_start or datetime.utcnow().replace(day=1)
        if not period_end:
            period_end = org.billing_period_end or datetime.utcnow()
        
        # Query usage records
        query = select(
            UsageRecord.usage_type,
            func.count(UsageRecord.id).label("count"),
            func.sum(UsageRecord.tokens_consumed).label("tokens"),
        ).where(
            and_(
                UsageRecord.org_id == org_id,
                UsageRecord.created_at >= period_start,
                UsageRecord.created_at <= period_end,
            )
        ).group_by(UsageRecord.usage_type)
        
        result = await self.db.execute(query)
        usage_by_type = {row.usage_type: {"count": row.count, "tokens": row.tokens} for row in result}
        
        # Calculate totals
        total_tokens = sum(u["tokens"] or 0 for u in usage_by_type.values())
        
        is_unlimited = org.monthly_token_limit == -1
        
        return {
            "org_id": str(org_id),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "plan_tier": org.plan_tier,
            "token_limit": org.monthly_token_limit if not is_unlimited else "unlimited",
            "tokens_used": org.tokens_used_this_period,
            "tokens_remaining": max(0, org.monthly_token_limit - org.tokens_used_this_period) if not is_unlimited else "unlimited",
            "usage_percent": round((org.tokens_used_this_period / org.monthly_token_limit) * 100, 1) if not is_unlimited else 0,
            "overage_enabled": org.overage_enabled,
            "overage_tokens": org.overage_tokens_used,
            "breakdown": {
                "agent_executions": usage_by_type.get(UsageType.AGENT_EXECUTION.value, {"count": 0, "tokens": 0}),
                "workflow_runs": usage_by_type.get(UsageType.WORKFLOW_RUN.value, {"count": 0, "tokens": 0}),
                "storage": usage_by_type.get(UsageType.STORAGE.value, {"count": 0, "tokens": 0}),
                "llm_input": usage_by_type.get(UsageType.LLM_INPUT.value, {"count": 0, "tokens": 0}),
                "llm_output": usage_by_type.get(UsageType.LLM_OUTPUT.value, {"count": 0, "tokens": 0}),
            },
            "total_tokens": total_tokens,
        }
    
    async def get_usage_history(
        self,
        org_id: UUID,
        days: int = 30,
        usage_type: Optional[UsageType] = None,
    ) -> List[Dict[str, Any]]:
        """Get daily usage history."""
        start_date = datetime.utcnow() - timedelta(days=days)
        
        conditions = [
            UsageRecord.org_id == org_id,
            UsageRecord.created_at >= start_date,
        ]
        if usage_type:
            conditions.append(UsageRecord.usage_type == usage_type.value)
        
        query = select(
            func.date(UsageRecord.created_at).label("date"),
            UsageRecord.usage_type,
            func.count(UsageRecord.id).label("count"),
            func.sum(UsageRecord.tokens_consumed).label("tokens"),
        ).where(and_(*conditions)).group_by(
            func.date(UsageRecord.created_at),
            UsageRecord.usage_type,
        ).order_by(func.date(UsageRecord.created_at))
        
        result = await self.db.execute(query)
        
        history = []
        for row in result:
            history.append({
                "date": row.date.isoformat() if row.date else None,
                "usage_type": row.usage_type,
                "count": row.count,
                "tokens": row.tokens,
            })
        
        return history
    
    async def check_can_execute(
        self,
        org_id: UUID,
        usage_type: UsageType,
        quantity: int = 1,
    ) -> Dict[str, Any]:
        """Check if organization has enough tokens for an operation."""
        token_cost = TOKEN_COSTS.get(usage_type, 0)
        tokens_needed = token_cost * quantity
        
        org = await self.db.get(Organization, org_id)
        if not org:
            return {"can_execute": False, "error": "organization_not_found"}
        
        is_unlimited = org.monthly_token_limit == -1
        if is_unlimited:
            return {"can_execute": True, "tokens_needed": tokens_needed}
        
        tokens_remaining = org.monthly_token_limit - org.tokens_used_this_period
        
        if tokens_remaining >= tokens_needed:
            return {
                "can_execute": True,
                "tokens_needed": tokens_needed,
                "tokens_remaining": tokens_remaining,
            }
        
        # Check overage
        if org.overage_enabled:
            overage_available = (org.overage_limit or float('inf')) - org.overage_tokens_used
            if overage_available >= (tokens_needed - tokens_remaining):
                return {
                    "can_execute": True,
                    "tokens_needed": tokens_needed,
                    "tokens_remaining": tokens_remaining,
                    "will_use_overage": True,
                    "overage_tokens": tokens_needed - tokens_remaining,
                }
        
        return {
            "can_execute": False,
            "error": "insufficient_tokens",
            "tokens_needed": tokens_needed,
            "tokens_remaining": tokens_remaining,
        }
    
    async def reset_monthly_usage(self, org_id: UUID) -> None:
        """Reset monthly usage for billing period rollover."""
        org = await self.db.get(Organization, org_id)
        if org:
            org.tokens_used_this_period = 0
            org.overage_tokens_used = 0
            org.billing_period_start = datetime.utcnow()
            org.billing_period_end = datetime.utcnow() + timedelta(days=30)
            await self.db.commit()


# Convenience functions for recording specific usage types
async def record_agent_execution(
    db: AsyncSession,
    org_id: UUID,
    agent_id: UUID,
    agent_name: str,
    user_id: Optional[UUID] = None,
    metadata: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Record an agent execution."""
    service = UsageService(db)
    return await service.record_usage(
        org_id=org_id,
        usage_type=UsageType.AGENT_EXECUTION,
        quantity=1,
        user_id=user_id,
        resource_id=agent_id,
        resource_name=agent_name,
        metadata=metadata,
    )


async def record_workflow_run(
    db: AsyncSession,
    org_id: UUID,
    workflow_id: UUID,
    workflow_name: str,
    user_id: Optional[UUID] = None,
    metadata: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Record a workflow run."""
    service = UsageService(db)
    return await service.record_usage(
        org_id=org_id,
        usage_type=UsageType.WORKFLOW_RUN,
        quantity=1,
        user_id=user_id,
        resource_id=workflow_id,
        resource_name=workflow_name,
        metadata=metadata,
    )


async def record_llm_usage(
    db: AsyncSession,
    org_id: UUID,
    input_tokens: int,
    output_tokens: int,
    user_id: Optional[UUID] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Record LLM token usage."""
    service = UsageService(db)
    
    # Record input tokens (per 1K)
    input_result = await service.record_usage(
        org_id=org_id,
        usage_type=UsageType.LLM_INPUT,
        quantity=max(1, input_tokens // 1000),
        user_id=user_id,
        metadata={"model": model, "raw_tokens": input_tokens},
    )
    
    if not input_result["success"]:
        return input_result
    
    # Record output tokens (per 1K)
    output_result = await service.record_usage(
        org_id=org_id,
        usage_type=UsageType.LLM_OUTPUT,
        quantity=max(1, output_tokens // 1000),
        user_id=user_id,
        metadata={"model": model, "raw_tokens": output_tokens},
    )
    
    return output_result
