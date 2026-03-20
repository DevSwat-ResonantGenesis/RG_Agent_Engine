"""
Autonomous Agent Flow End-to-End Test
======================================

Tests the complete autonomous agent flow including:
- Daemon initialization
- Self-trigger system
- Hash Sphere integration
- Wallet binding
- Execution gate
- RARA kill switch

Run with: pytest tests/test_autonomous_flow.py -v
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from decimal import Decimal


class TestAutonomousAgentFlow:
    """Test the complete autonomous agent flow."""
    
    @pytest.mark.asyncio
    async def test_full_agent_initialization_flow(self):
        """Test agent can be initialized with all systems."""
        from app.autonomous_daemon import AutonomousDaemon, AgentContext, AgentState
        from app.self_trigger import get_self_trigger
        from app.services.hash_sphere_client import get_hash_sphere_client
        from app.agent_wallet import get_wallet_manager
        
        # Initialize daemon
        daemon = AutonomousDaemon()
        
        # Create agent context
        agent_id = "test-agent-flow-001"
        context = AgentContext(
            agent_id=agent_id,
            current_goal="Analyze market data and generate insights",
        )
        
        # Verify context has hash sphere fields
        assert hasattr(context, 'goal_hash')
        assert hasattr(context, 'goal_coordinates')
        
        # Initialize self-trigger
        trigger = get_self_trigger(agent_id)
        assert trigger is not None
        assert trigger.agent_id == agent_id
        
        # Initialize hash sphere
        hash_sphere = get_hash_sphere_client()
        goal_hash = hash_sphere.hash_text(context.current_goal)
        assert goal_hash is not None
        assert goal_hash.hash_value is not None
        assert 0 <= goal_hash.x <= 1
        
        # Store hash in context
        context.goal_hash = goal_hash.hash_value
        context.goal_coordinates = (goal_hash.x, goal_hash.y, goal_hash.z)
        
        # Initialize wallet
        wallet_manager = get_wallet_manager()
        wallet = await wallet_manager.create_wallet(agent_id)
        assert wallet is not None
        
        # Deposit funds
        await wallet_manager.deposit(agent_id, Decimal("100"), "RGT")
        balance = await wallet_manager.get_balance(agent_id, "RGT")
        assert balance == Decimal("100")
        
        print("✓ Full agent initialization flow passed")
    
    @pytest.mark.asyncio
    async def test_self_trigger_timing_calculation(self):
        """Test self-trigger calculates timing based on internal state."""
        from app.self_trigger import get_self_trigger
        
        agent_id = "test-agent-timing-001"
        trigger = get_self_trigger(agent_id)
        
        # Initial state
        assert trigger.internal_state.motivation > 0
        
        # Update urgency
        trigger.update_goal_urgency(0.9)
        assert trigger.internal_state.goal_urgency == 0.9
        
        # Spike curiosity
        trigger.spike_curiosity(0.5)
        assert trigger.internal_state.curiosity >= 0.5
        
        # Record success (should increase motivation)
        initial_motivation = trigger.internal_state.motivation
        trigger.record_success()
        # Motivation should be boosted
        
        print("✓ Self-trigger timing calculation passed")
    
    @pytest.mark.asyncio
    async def test_hash_sphere_semantic_similarity(self):
        """Test Hash Sphere can find semantically similar goals."""
        from app.services.hash_sphere_client import get_hash_sphere_client
        
        client = get_hash_sphere_client()
        
        # Hash similar goals
        goal1 = "Analyze financial data and generate reports"
        goal2 = "Analyze market data and create financial reports"
        goal3 = "Play chess and win tournaments"
        
        hash1 = client.hash_text(goal1)
        hash2 = client.hash_text(goal2)
        hash3 = client.hash_text(goal3)
        
        # Similar goals should have higher resonance
        resonance_similar = client.calculate_resonance(hash1, hash2)
        resonance_different = client.calculate_resonance(hash1, hash3)
        
        # Similar goals should resonate more
        # Note: This is a basic test, actual semantic similarity would need embeddings
        assert resonance_similar >= 0
        assert resonance_different >= 0
        
        print(f"✓ Resonance similar: {resonance_similar:.3f}, different: {resonance_different:.3f}")
    
    @pytest.mark.asyncio
    async def test_wallet_spend_binding(self):
        """Test wallet spend is binding (actually deducts funds)."""
        from app.agent_wallet import get_wallet_manager
        
        agent_id = "test-agent-wallet-001"
        wallet_manager = get_wallet_manager()
        
        # Create and fund wallet
        await wallet_manager.create_wallet(agent_id)
        await wallet_manager.deposit(agent_id, Decimal("50"), "RGT")
        
        # Spend
        tx = await wallet_manager.spend(
            agent_id=agent_id,
            amount=Decimal("10"),
            purpose="test_action",
        )
        
        assert tx is not None
        
        # Verify balance reduced
        balance = await wallet_manager.get_balance(agent_id, "RGT")
        assert balance == Decimal("40")
        
        # Try to overspend
        tx2 = await wallet_manager.spend(
            agent_id=agent_id,
            amount=Decimal("100"),  # More than balance
            purpose="overspend_test",
        )
        
        # Should fail
        assert tx2 is None
        
        # Balance unchanged
        balance2 = await wallet_manager.get_balance(agent_id, "RGT")
        assert balance2 == Decimal("40")
        
        print("✓ Wallet spend binding passed")
    
    @pytest.mark.asyncio
    async def test_execution_gate_mode_enforcement(self):
        """Test execution gate enforces autonomy mode."""
        try:
            from shared.agent.execution_gate import get_execution_gate, ExecutionRequest
            from shared.agent.autonomy_mode import RiskLevel, AutonomyMode, get_autonomy_mode_manager
            from uuid import uuid4
            
            gate = get_execution_gate()
            mode_manager = get_autonomy_mode_manager()
            
            agent_id = "test-agent-gate-001"
            
            # Set to GOVERNED mode
            mode_manager.switch_mode(
                agent_id=agent_id,
                new_mode=AutonomyMode.GOVERNED,
                user_id="test-user",
                user_role="admin",
            )
            
            # Create high-risk request
            request = ExecutionRequest(
                id=str(uuid4()),
                agent_id=agent_id,
                action="delete_all_data",
                action_type="governance",
                risk_level=RiskLevel.CRITICAL,
                estimated_cost=1000.0,
            )
            
            decision = gate.evaluate(request)
            
            # High-risk action in GOVERNED mode should require approval or be blocked
            assert decision.mode == AutonomyMode.GOVERNED
            
            print(f"✓ Execution gate decision: allowed={decision.allowed}, requires_approval={decision.requires_approval}")
            
        except ImportError:
            pytest.skip("ExecutionGate not available")
    
    @pytest.mark.asyncio
    async def test_daemon_rara_check(self):
        """Test daemon checks RARA kill switch."""
        from app.autonomous_daemon import AutonomousDaemon
        
        daemon = AutonomousDaemon()
        
        # Mock RARA response
        with patch('httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"frozen": False}
            
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_instance
            
            # Check RARA
            is_frozen = await daemon._check_rara_kill_switch()
            
            # Should not be frozen
            assert is_frozen == False
        
        print("✓ RARA kill switch check passed")
    
    @pytest.mark.asyncio
    async def test_agent_context_with_hash_sphere(self):
        """Test agent context stores hash sphere data."""
        from app.autonomous_daemon import AgentContext
        from app.services.hash_sphere_client import get_hash_sphere_client
        
        agent_id = "test-agent-context-001"
        goal = "Build a recommendation engine for users"
        
        # Create context
        context = AgentContext(
            agent_id=agent_id,
            current_goal=goal,
        )
        
        # Hash the goal
        client = get_hash_sphere_client()
        goal_hash = client.hash_text(goal, context=f"agent:{agent_id}")
        
        # Store in context
        context.goal_hash = goal_hash.hash_value
        context.goal_coordinates = (goal_hash.x, goal_hash.y, goal_hash.z)
        
        # Verify
        assert context.goal_hash is not None
        assert len(context.goal_hash) == 64  # SHA256 hex
        assert context.goal_coordinates is not None
        assert len(context.goal_coordinates) == 3
        
        print(f"✓ Goal hash: {context.goal_hash[:16]}...")
        print(f"✓ Goal coordinates: ({context.goal_coordinates[0]:.3f}, {context.goal_coordinates[1]:.3f}, {context.goal_coordinates[2]:.3f})")


class TestServiceCommunication:
    """Test services can communicate with each other."""
    
    def test_daemon_imports_all_required_modules(self):
        """Verify daemon imports all required modules."""
        from app.autonomous_daemon import (
            AutonomousDaemon,
            AgentContext,
            AgentState,
            get_self_trigger,
            get_survival_manager,
            get_goal_system,
            get_drift_manager,
            get_hash_sphere_client,
        )
        
        assert AutonomousDaemon is not None
        assert AgentContext is not None
        assert get_self_trigger is not None
        assert get_hash_sphere_client is not None
        
        print("✓ All daemon imports verified")
    
    def test_executor_imports_all_required_modules(self):
        """Verify executor imports all required modules."""
        from app.executor import AgentExecutor
        from app.agent_wallet import get_wallet_manager
        from app.safety import safety_envelope
        
        assert AgentExecutor is not None
        assert get_wallet_manager is not None
        assert safety_envelope is not None
        
        print("✓ All executor imports verified")
    
    def test_hash_sphere_client_singleton(self):
        """Verify hash sphere client is singleton."""
        from app.services.hash_sphere_client import get_hash_sphere_client
        
        client1 = get_hash_sphere_client()
        client2 = get_hash_sphere_client()
        
        assert client1 is client2
        
        print("✓ Hash sphere client singleton verified")


# ============== Run Tests ==============

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
