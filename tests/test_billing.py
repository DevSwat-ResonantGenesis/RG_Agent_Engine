"""Tests for Billing and Usage Tracking Pipeline."""

import pytest
from datetime import datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock, patch, MagicMock

from sqlalchemy.ext.asyncio import AsyncSession

# Import models and services

# Add shared modules to path
SHARED_PATH = Path(__file__).resolve().parents[2] / "shared"
if str(SHARED_PATH) not in sys.path:
    sys.path.insert(0, str(SHARED_PATH))
import sys
sys.path.insert(0, '..')

from app.models_billing import (
    Organization, UsageRecord, Subscription, Invoice,
    PlanTier, SubscriptionStatus, UsageType, TOKEN_COSTS, PLAN_TOKENS
)
from app.services.usage_service import UsageService, record_agent_execution, record_workflow_run
from app.services.billing_service import BillingService


class MockDB:
    """Mock database session for testing."""
    
    def __init__(self):
        self.objects = {}
        self.added = []
        self.committed = False
    
    async def get(self, model, id):
        key = f"{model.__name__}_{id}"
        return self.objects.get(key)
    
    def add(self, obj):
        self.added.append(obj)
    
    async def commit(self):
        self.committed = True
    
    async def execute(self, query):
        return MockResult([])
    
    def set_org(self, org):
        self.objects[f"Organization_{org.id}"] = org


class MockResult:
    def __init__(self, data):
        self.data = data
    
    def __iter__(self):
        return iter(self.data)
    
    def scalars(self):
        return self
    
    def scalar_one_or_none(self):
        return self.data[0] if self.data else None


def create_test_org(
    plan_tier=PlanTier.BUILDER,
    tokens_used=0,
    overage_enabled=False,
):
    """Create a test organization."""
    org = Organization(
        id=uuid4(),
        name="Test Org",
        slug="test-org",
        plan_tier=plan_tier.value,
        subscription_status=SubscriptionStatus.ACTIVE.value,
        monthly_token_limit=PLAN_TOKENS[plan_tier],
        tokens_used_this_period=tokens_used,
        overage_tokens_used=0,
        overage_enabled=overage_enabled,
        alert_threshold=80,
        billing_period_start=datetime.utcnow(),
        billing_period_end=datetime.utcnow() + timedelta(days=30),
    )
    return org


class TestUsageService:
    """Tests for UsageService."""
    
    @pytest.mark.asyncio
    async def test_record_agent_execution(self):
        """Test recording an agent execution."""
        db = MockDB()
        org = create_test_org(plan_tier=PlanTier.BUILDER)
        db.set_org(org)
        
        service = UsageService(db)
        result = await service.record_usage(
            org_id=org.id,
            usage_type=UsageType.AGENT_EXECUTION,
            quantity=1,
        )
        
        assert result["success"] == True
        assert result["tokens_consumed"] == TOKEN_COSTS[UsageType.AGENT_EXECUTION]
        assert result["tokens_remaining"] == PLAN_TOKENS[PlanTier.BUILDER] - TOKEN_COSTS[UsageType.AGENT_EXECUTION]
        assert db.committed == True
        assert len(db.added) == 1
    
    @pytest.mark.asyncio
    async def test_record_workflow_run(self):
        """Test recording a workflow run."""
        db = MockDB()
        org = create_test_org(plan_tier=PlanTier.PRO)
        db.set_org(org)
        
        service = UsageService(db)
        result = await service.record_usage(
            org_id=org.id,
            usage_type=UsageType.WORKFLOW_RUN,
            quantity=1,
        )
        
        assert result["success"] == True
        assert result["tokens_consumed"] == TOKEN_COSTS[UsageType.WORKFLOW_RUN]
    
    @pytest.mark.asyncio
    async def test_token_limit_exceeded(self):
        """Test that usage is blocked when token limit is exceeded."""
        db = MockDB()
        # Create org that's almost at limit
        org = create_test_org(
            plan_tier=PlanTier.FREE,
            tokens_used=9500,  # 9,500 of 10,000 used
            overage_enabled=False,
        )
        db.set_org(org)
        
        service = UsageService(db)
        result = await service.record_usage(
            org_id=org.id,
            usage_type=UsageType.AGENT_EXECUTION,  # Costs 1,000 tokens
            quantity=1,
        )
        
        assert result["success"] == False
        assert result["error"] == "token_limit_exceeded"
        assert result["tokens_remaining"] == 500
    
    @pytest.mark.asyncio
    async def test_overage_allowed(self):
        """Test that overage is allowed when enabled."""
        db = MockDB()
        org = create_test_org(
            plan_tier=PlanTier.FREE,
            tokens_used=9500,
            overage_enabled=True,
        )
        db.set_org(org)
        
        service = UsageService(db)
        result = await service.record_usage(
            org_id=org.id,
            usage_type=UsageType.AGENT_EXECUTION,
            quantity=1,
        )
        
        assert result["success"] == True
        assert result["is_overage"] == True
        assert result["overage_tokens"] == 500  # 1000 cost - 500 remaining
    
    @pytest.mark.asyncio
    async def test_unlimited_plan(self):
        """Test that unlimited plans have no restrictions."""
        db = MockDB()
        org = create_test_org(plan_tier=PlanTier.ENTERPRISE)
        org.monthly_token_limit = -1  # Unlimited
        db.set_org(org)
        
        service = UsageService(db)
        result = await service.record_usage(
            org_id=org.id,
            usage_type=UsageType.WORKFLOW_RUN,
            quantity=100,
        )
        
        assert result["success"] == True
        assert result["tokens_remaining"] == -1
    
    @pytest.mark.asyncio
    async def test_alert_threshold(self):
        """Test that alert is triggered at threshold."""
        db = MockDB()
        org = create_test_org(
            plan_tier=PlanTier.BUILDER,
            tokens_used=400000,  # 80% of 500K
        )
        org.alert_threshold = 80
        db.set_org(org)
        
        service = UsageService(db)
        result = await service.record_usage(
            org_id=org.id,
            usage_type=UsageType.AGENT_EXECUTION,
            quantity=1,
        )
        
        assert result["success"] == True
        assert result["alert"] is not None
        assert result["alert"]["type"] == "usage_warning"
    
    @pytest.mark.asyncio
    async def test_check_can_execute(self):
        """Test checking if execution is possible."""
        db = MockDB()
        org = create_test_org(plan_tier=PlanTier.PRO)
        db.set_org(org)
        
        service = UsageService(db)
        result = await service.check_can_execute(
            org_id=org.id,
            usage_type=UsageType.AGENT_EXECUTION,
            quantity=1,
        )
        
        assert result["can_execute"] == True
        assert result["tokens_needed"] == TOKEN_COSTS[UsageType.AGENT_EXECUTION]
    
    @pytest.mark.asyncio
    async def test_check_cannot_execute(self):
        """Test checking when execution is not possible."""
        db = MockDB()
        org = create_test_org(
            plan_tier=PlanTier.FREE,
            tokens_used=10000,  # At limit
        )
        db.set_org(org)
        
        service = UsageService(db)
        result = await service.check_can_execute(
            org_id=org.id,
            usage_type=UsageType.AGENT_EXECUTION,
            quantity=1,
        )
        
        assert result["can_execute"] == False
        assert result["error"] == "insufficient_tokens"


class TestBillingService:
    """Tests for BillingService."""
    
    @pytest.mark.asyncio
    @patch('stripe.Customer.create')
    async def test_create_stripe_customer(self, mock_create):
        """Test creating a Stripe customer."""
        mock_create.return_value = MagicMock(id="cus_test123")
        
        db = MockDB()
        org = create_test_org()
        db.set_org(org)
        
        service = BillingService(db)
        customer_id = await service.get_or_create_stripe_customer(
            org_id=org.id,
            email="test@example.com",
        )
        
        assert customer_id == "cus_test123"
        mock_create.assert_called_once()
    
    @pytest.mark.asyncio
    @patch('stripe.checkout.Session.create')
    @patch('stripe.Customer.create')
    async def test_create_checkout_session(self, mock_customer, mock_session):
        """Test creating a checkout session."""
        mock_customer.return_value = MagicMock(id="cus_test123")
        mock_session.return_value = MagicMock(
            id="cs_test123",
            url="https://checkout.stripe.com/test",
        )
        
        db = MockDB()
        org = create_test_org()
        db.set_org(org)
        
        service = BillingService(db)
        result = await service.create_checkout_session(
            org_id=org.id,
            plan_tier=PlanTier.PRO,
            success_url="https://app.com/success",
            cancel_url="https://app.com/cancel",
            email="test@example.com",
        )
        
        assert result["session_id"] == "cs_test123"
        assert result["checkout_url"] == "https://checkout.stripe.com/test"


class TestTokenCosts:
    """Test token cost calculations."""
    
    def test_agent_execution_cost(self):
        """Test agent execution costs 1000 tokens."""
        assert TOKEN_COSTS[UsageType.AGENT_EXECUTION] == 1000
    
    def test_workflow_run_cost(self):
        """Test workflow run costs 5000 tokens."""
        assert TOKEN_COSTS[UsageType.WORKFLOW_RUN] == 5000
    
    def test_storage_cost(self):
        """Test storage costs 10000 tokens per GB."""
        assert TOKEN_COSTS[UsageType.STORAGE] == 10000
    
    def test_llm_input_cost(self):
        """Test LLM input costs 100 tokens per 1K."""
        assert TOKEN_COSTS[UsageType.LLM_INPUT] == 100
    
    def test_llm_output_cost(self):
        """Test LLM output costs 300 tokens per 1K."""
        assert TOKEN_COSTS[UsageType.LLM_OUTPUT] == 300


class TestPlanLimits:
    """Test plan token limits."""
    
    def test_free_plan_tokens(self):
        """Test free plan has 10K tokens."""
        assert PLAN_TOKENS[PlanTier.FREE] == 10_000
    
    def test_builder_plan_tokens(self):
        """Test builder plan has 500K tokens."""
        assert PLAN_TOKENS[PlanTier.BUILDER] == 500_000
    
    def test_pro_plan_tokens(self):
        """Test pro plan has 2M tokens."""
        assert PLAN_TOKENS[PlanTier.PRO] == 2_000_000
    
    def test_team_plan_tokens(self):
        """Test team plan has 10M tokens."""
        assert PLAN_TOKENS[PlanTier.TEAM] == 10_000_000
    
    def test_enterprise_unlimited(self):
        """Test enterprise plan is unlimited."""
        assert PLAN_TOKENS[PlanTier.ENTERPRISE] == -1


class TestUsageCalculations:
    """Test real-world usage calculations."""
    
    def test_builder_executions(self):
        """Test how many executions Builder plan allows."""
        tokens = PLAN_TOKENS[PlanTier.BUILDER]  # 500K
        cost = TOKEN_COSTS[UsageType.AGENT_EXECUTION]  # 1000
        executions = tokens // cost
        assert executions == 500
    
    def test_builder_workflows(self):
        """Test how many workflows Builder plan allows."""
        tokens = PLAN_TOKENS[PlanTier.BUILDER]  # 500K
        cost = TOKEN_COSTS[UsageType.WORKFLOW_RUN]  # 5000
        workflows = tokens // cost
        assert workflows == 100
    
    def test_pro_executions(self):
        """Test how many executions Pro plan allows."""
        tokens = PLAN_TOKENS[PlanTier.PRO]  # 2M
        cost = TOKEN_COSTS[UsageType.AGENT_EXECUTION]  # 1000
        executions = tokens // cost
        assert executions == 2000
    
    def test_team_executions(self):
        """Test how many executions Team plan allows."""
        tokens = PLAN_TOKENS[PlanTier.TEAM]  # 10M
        cost = TOKEN_COSTS[UsageType.AGENT_EXECUTION]  # 1000
        executions = tokens // cost
        assert executions == 10000
    
    def test_mixed_usage_scenario(self):
        """Test a realistic mixed usage scenario."""
        # Builder with 500K tokens
        tokens = PLAN_TOKENS[PlanTier.BUILDER]
        
        # Use 200 agent executions
        tokens -= 200 * TOKEN_COSTS[UsageType.AGENT_EXECUTION]  # 200K
        
        # Use 30 workflows
        tokens -= 30 * TOKEN_COSTS[UsageType.WORKFLOW_RUN]  # 150K
        
        # Use 100K LLM input tokens (100 x 1K units)
        tokens -= 100 * TOKEN_COSTS[UsageType.LLM_INPUT]  # 10K
        
        # Use 50K LLM output tokens (50 x 1K units)
        tokens -= 50 * TOKEN_COSTS[UsageType.LLM_OUTPUT]  # 15K
        
        # Should have 125K tokens remaining
        assert tokens == 125_000


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
