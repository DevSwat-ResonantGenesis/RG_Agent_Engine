"""
Tests for Autonomy Mode System
==============================

Tests for:
- AutonomyModeManager
- ExecutionGate
- Mode switching
- Configuration retrieval
"""

import pytest
from datetime import datetime

import sys
sys.path.insert(0, '/Users/devswat/resonantgenesis_backend')

from shared.agent.autonomy_mode import (
    AutonomyMode,
    AutonomyModeManager,
    UnboundedModeConfig,
    GovernedModeConfig,
    RiskLevel,
)
from shared.agent.execution_gate import (
    ExecutionGate,
    ExecutionRequest,
    ExecutionDecision,
    DecisionType,
)


class TestAutonomyModeManager:
    """Tests for AutonomyModeManager."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.manager = AutonomyModeManager()
        self.agent_id = "test_agent_123"
        self.user_id = "test_user_456"
        self.admin_user_id = "admin_user_789"
        
        # Add admin to unbounded allowlist
        self.manager.add_unbounded_user(self.admin_user_id)
    
    def test_default_mode_is_governed(self):
        """Test that default mode is GOVERNED."""
        mode = self.manager.get_mode(self.agent_id)
        assert mode == AutonomyMode.GOVERNED
    
    def test_get_config_for_governed_mode(self):
        """Test getting config for GOVERNED mode."""
        config = self.manager.get_config(self.agent_id)
        assert isinstance(config, GovernedModeConfig)
        assert config.require_approval == True
        assert config.can_set_own_goals == False
        assert config.max_budget_per_day == 100.0
    
    def test_switch_to_unbounded_requires_permission(self):
        """Test that switching to UNBOUNDED requires permission."""
        success, message = self.manager.switch_mode(
            agent_id=self.agent_id,
            new_mode=AutonomyMode.UNBOUNDED,
            user_id=self.user_id,  # Regular user
        )
        assert success == False
        assert "Unauthorized" in message
    
    def test_admin_can_switch_to_unbounded(self):
        """Test that admin can switch to UNBOUNDED."""
        success, message = self.manager.switch_mode(
            agent_id=self.agent_id,
            new_mode=AutonomyMode.UNBOUNDED,
            user_id=self.admin_user_id,
        )
        assert success == True
        assert self.manager.get_mode(self.agent_id) == AutonomyMode.UNBOUNDED
    
    def test_get_config_for_unbounded_mode(self):
        """Test getting config for UNBOUNDED mode."""
        # Switch to unbounded first
        self.manager.switch_mode(
            agent_id=self.agent_id,
            new_mode=AutonomyMode.UNBOUNDED,
            user_id=self.admin_user_id,
        )
        
        config = self.manager.get_config(self.agent_id)
        assert isinstance(config, UnboundedModeConfig)
        assert config.require_approval == False
        assert config.can_set_own_goals == True
        assert config.max_budget_per_day == float('inf')
    
    def test_switch_back_to_governed(self):
        """Test switching back to GOVERNED from UNBOUNDED."""
        # First switch to unbounded
        self.manager.switch_mode(
            agent_id=self.agent_id,
            new_mode=AutonomyMode.UNBOUNDED,
            user_id=self.admin_user_id,
        )
        
        # Any user can switch back to governed
        success, message = self.manager.switch_mode(
            agent_id=self.agent_id,
            new_mode=AutonomyMode.GOVERNED,
            user_id=self.user_id,
        )
        assert success == True
        assert self.manager.get_mode(self.agent_id) == AutonomyMode.GOVERNED
    
    def test_transition_history(self):
        """Test that transitions are recorded."""
        # Make some transitions
        self.manager.switch_mode(
            agent_id=self.agent_id,
            new_mode=AutonomyMode.UNBOUNDED,
            user_id=self.admin_user_id,
            reason="Testing",
        )
        self.manager.switch_mode(
            agent_id=self.agent_id,
            new_mode=AutonomyMode.GOVERNED,
            user_id=self.user_id,
            reason="Back to safe",
        )
        
        transitions = self.manager.get_transitions(self.agent_id)
        assert len(transitions) == 2
        assert transitions[0].from_mode == AutonomyMode.GOVERNED
        assert transitions[0].to_mode == AutonomyMode.UNBOUNDED
        assert transitions[1].from_mode == AutonomyMode.UNBOUNDED
        assert transitions[1].to_mode == AutonomyMode.GOVERNED
    
    def test_role_based_access(self):
        """Test role-based access to UNBOUNDED mode."""
        success, _ = self.manager.switch_mode(
            agent_id=self.agent_id,
            new_mode=AutonomyMode.UNBOUNDED,
            user_id="random_user",
            user_role="admin",  # Admin role
        )
        assert success == True
    
    def test_is_unbounded_helper(self):
        """Test is_unbounded helper method."""
        assert self.manager.is_unbounded(self.agent_id) == False
        
        self.manager.switch_mode(
            agent_id=self.agent_id,
            new_mode=AutonomyMode.UNBOUNDED,
            user_id=self.admin_user_id,
        )
        
        assert self.manager.is_unbounded(self.agent_id) == True
        assert self.manager.is_governed(self.agent_id) == False


class TestExecutionGate:
    """Tests for ExecutionGate."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mode_manager = AutonomyModeManager()
        self.gate = ExecutionGate(self.mode_manager)
        self.agent_id = "test_agent_123"
        self.admin_user_id = "admin_user_789"
        
        self.mode_manager.add_unbounded_user(self.admin_user_id)
    
    def test_governed_mode_allows_low_cost_action(self):
        """Test that GOVERNED mode allows low-cost actions."""
        request = ExecutionRequest(
            id="req_1",
            agent_id=self.agent_id,
            action="read_data",
            action_type="general",
            risk_level=RiskLevel.LOW,
            estimated_cost=5.0,
        )
        
        decision = self.gate.evaluate(request)
        
        assert decision.allowed == True
        assert decision.requires_approval == False
        assert decision.mode == AutonomyMode.GOVERNED
    
    def test_governed_mode_requires_approval_for_high_cost(self):
        """Test that GOVERNED mode requires approval for high-cost actions."""
        request = ExecutionRequest(
            id="req_2",
            agent_id=self.agent_id,
            action="expensive_api_call",
            action_type="general",
            risk_level=RiskLevel.LOW,
            estimated_cost=75.0,  # Above $50 threshold
        )
        
        decision = self.gate.evaluate(request)
        
        assert decision.allowed == True
        assert decision.requires_approval == True
    
    def test_governed_mode_requires_approval_for_high_risk(self):
        """Test that GOVERNED mode requires approval for high-risk actions."""
        request = ExecutionRequest(
            id="req_3",
            agent_id=self.agent_id,
            action="dangerous_action",
            action_type="general",
            risk_level=RiskLevel.HIGH,
            estimated_cost=10.0,
        )
        
        decision = self.gate.evaluate(request)
        
        assert decision.allowed == True
        assert decision.requires_approval == True
    
    def test_governed_mode_blocks_over_budget(self):
        """Test that GOVERNED mode blocks actions over daily budget."""
        request = ExecutionRequest(
            id="req_4",
            agent_id=self.agent_id,
            action="very_expensive",
            action_type="general",
            risk_level=RiskLevel.LOW,
            estimated_cost=150.0,  # Over $100 daily limit
        )
        
        decision = self.gate.evaluate(request)
        
        assert decision.allowed == False
        assert decision.decision == DecisionType.BLOCKED
        assert "budget" in decision.reason.lower()
    
    def test_unbounded_mode_allows_everything(self):
        """Test that UNBOUNDED mode allows all actions."""
        # Switch to unbounded
        self.mode_manager.switch_mode(
            agent_id=self.agent_id,
            new_mode=AutonomyMode.UNBOUNDED,
            user_id=self.admin_user_id,
        )
        
        request = ExecutionRequest(
            id="req_5",
            agent_id=self.agent_id,
            action="anything",
            action_type="governance",
            risk_level=RiskLevel.CRITICAL,
            estimated_cost=10000.0,
        )
        
        decision = self.gate.evaluate(request)
        
        assert decision.allowed == True
        assert decision.requires_approval == False
        assert decision.mode == AutonomyMode.UNBOUNDED
    
    def test_daily_spend_tracking(self):
        """Test that daily spend is tracked."""
        # Execute some actions
        for i in range(3):
            request = ExecutionRequest(
                id=f"req_{i}",
                agent_id=self.agent_id,
                action="action",
                action_type="general",
                risk_level=RiskLevel.LOW,
                estimated_cost=20.0,
            )
            self.gate.evaluate(request)
        
        daily_spend = self.gate.get_daily_spend(self.agent_id)
        assert daily_spend == 60.0  # 3 * 20
    
    def test_remaining_budget(self):
        """Test remaining budget calculation."""
        # Execute an action
        request = ExecutionRequest(
            id="req_1",
            agent_id=self.agent_id,
            action="action",
            action_type="general",
            risk_level=RiskLevel.LOW,
            estimated_cost=30.0,
        )
        self.gate.evaluate(request)
        
        remaining = self.gate.get_remaining_budget(self.agent_id)
        assert remaining == 70.0  # 100 - 30
    
    def test_governance_action_blocked_in_governed(self):
        """Test that governance actions are blocked in GOVERNED mode."""
        request = ExecutionRequest(
            id="req_gov",
            agent_id=self.agent_id,
            action="modify_permissions",
            action_type="governance",
            risk_level=RiskLevel.LOW,
            estimated_cost=0.0,
        )
        
        decision = self.gate.evaluate(request)
        
        assert decision.allowed == False
        assert "governance" in decision.reason.lower()


class TestModeConfigurations:
    """Tests for mode configurations."""
    
    def test_unbounded_config_defaults(self):
        """Test UnboundedModeConfig defaults."""
        config = UnboundedModeConfig()
        
        assert config.max_budget_per_day == float('inf')
        assert config.can_modify_own_permissions == True
        assert config.can_set_own_goals == True
        assert config.require_approval == False
        assert config.auto_execute_all == True
    
    def test_governed_config_defaults(self):
        """Test GovernedModeConfig defaults."""
        config = GovernedModeConfig()
        
        assert config.max_budget_per_day == 100.0
        assert config.can_modify_own_permissions == False
        assert config.can_set_own_goals == False
        assert config.require_approval == True
        assert config.approval_threshold_usd == 50.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
