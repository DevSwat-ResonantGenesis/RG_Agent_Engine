"""
Platform Integration Tests
===========================

Tests to verify the GTM platform fixes are working correctly.
These tests verify the connections between components without breaking anything.

Run with: pytest tests/test_platform_integration.py -v
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


class TestDaemonRaraIntegration:
    """Test daemon integration with RARA kill switch."""
    
    def test_rara_url_configured(self):
        """Verify RARA URL is configured in daemon."""
        from app.autonomous_daemon import RARA_SERVICE_URL
        assert RARA_SERVICE_URL is not None
        assert "rara" in RARA_SERVICE_URL.lower()
    
    @pytest.mark.asyncio
    async def test_check_rara_kill_switch_method_exists(self):
        """Verify daemon has RARA kill switch check method."""
        from app.autonomous_daemon import AutonomousDaemon
        daemon = AutonomousDaemon()
        assert hasattr(daemon, '_check_rara_kill_switch')
        assert callable(daemon._check_rara_kill_switch)


class TestSelfTriggerIntegration:
    """Test self-trigger system integration."""
    
    def test_self_trigger_import(self):
        """Verify self-trigger can be imported."""
        from app.self_trigger import get_self_trigger, SelfTriggerSystem
        assert get_self_trigger is not None
        assert SelfTriggerSystem is not None
    
    def test_self_trigger_creation(self):
        """Verify self-trigger can be created for an agent."""
        from app.self_trigger import get_self_trigger
        trigger = get_self_trigger("test-agent-123")
        assert trigger is not None
        assert trigger.agent_id == "test-agent-123"
    
    def test_self_trigger_internal_state(self):
        """Verify self-trigger has internal state."""
        from app.self_trigger import get_self_trigger
        trigger = get_self_trigger("test-agent-456")
        assert hasattr(trigger, 'internal_state')
        assert hasattr(trigger.internal_state, 'goal_urgency')
        assert hasattr(trigger.internal_state, 'motivation')
    
    def test_daemon_has_self_trigger_callback(self):
        """Verify daemon has self-trigger callback method."""
        from app.autonomous_daemon import AutonomousDaemon
        daemon = AutonomousDaemon()
        assert hasattr(daemon, '_on_agent_self_trigger')


class TestWalletBinding:
    """Test wallet is binding (spend actually deducts)."""
    
    def test_wallet_manager_import(self):
        """Verify wallet manager can be imported."""
        from app.agent_wallet import get_wallet_manager, WalletManager
        assert get_wallet_manager is not None
        assert WalletManager is not None
    
    def test_wallet_has_spend_method(self):
        """Verify wallet manager has spend method."""
        from app.agent_wallet import WalletManager
        manager = WalletManager()
        assert hasattr(manager, 'spend')
    
    @pytest.mark.asyncio
    async def test_spend_deducts_balance(self):
        """Verify spend actually deducts from balance."""
        from app.agent_wallet import WalletManager
        from decimal import Decimal
        
        manager = WalletManager()
        
        # Create wallet and deposit
        wallet = await manager.create_wallet("test-agent-spend")
        await manager.deposit("test-agent-spend", Decimal("100"), "RGT")
        
        initial_balance = await manager.get_balance("test-agent-spend", "RGT")
        assert initial_balance == Decimal("100")
        
        # Spend
        tx = await manager.spend(
            agent_id="test-agent-spend",
            amount=Decimal("25"),
            purpose="test_action",
        )
        
        assert tx is not None
        
        # Verify balance reduced
        final_balance = await manager.get_balance("test-agent-spend", "RGT")
        assert final_balance == Decimal("75")


class TestHashSphereClient:
    """Test Hash Sphere client integration."""
    
    def test_hash_sphere_client_import(self):
        """Verify hash sphere client can be imported."""
        from app.services.hash_sphere_client import get_hash_sphere_client, HashSphereClient
        assert get_hash_sphere_client is not None
        assert HashSphereClient is not None
    
    def test_hash_text_returns_semantic_hash(self):
        """Verify hash_text returns proper semantic hash."""
        from app.services.hash_sphere_client import get_hash_sphere_client
        
        client = get_hash_sphere_client()
        result = client.hash_text("This is a test message")
        
        assert result is not None
        assert hasattr(result, 'hash_value')
        assert hasattr(result, 'x')
        assert hasattr(result, 'y')
        assert hasattr(result, 'z')
        assert 0 <= result.x <= 1
        assert 0 <= result.y <= 1
        assert 0 <= result.z <= 1
    
    def test_resonance_calculation(self):
        """Verify resonance can be calculated between hashes."""
        from app.services.hash_sphere_client import get_hash_sphere_client
        
        client = get_hash_sphere_client()
        hash1 = client.hash_text("Hello world")
        hash2 = client.hash_text("Hello world")  # Same text
        hash3 = client.hash_text("Completely different text about cats")
        
        # Same text should have high resonance
        resonance_same = client.calculate_resonance(hash1, hash2)
        assert resonance_same > 0.9
        
        # Different text should have lower resonance
        resonance_diff = client.calculate_resonance(hash1, hash3)
        assert resonance_diff < resonance_same


class TestExecutionGate:
    """Test execution gate integration."""
    
    def test_execution_gate_import(self):
        """Verify execution gate can be imported."""
        try:
            from shared.agent.execution_gate import get_execution_gate, ExecutionGate
            assert get_execution_gate is not None
            assert ExecutionGate is not None
        except ImportError:
            pytest.skip("ExecutionGate not available in this environment")
    
    def test_execution_gate_evaluate(self):
        """Verify execution gate can evaluate requests."""
        try:
            from shared.agent.execution_gate import get_execution_gate, ExecutionRequest
            from shared.agent.autonomy_mode import RiskLevel
            from uuid import uuid4
            
            gate = get_execution_gate()
            
            request = ExecutionRequest(
                id=str(uuid4()),
                agent_id="test-agent",
                action="test_action",
                action_type="general",
                risk_level=RiskLevel.LOW,
                estimated_cost=0.0,
            )
            
            decision = gate.evaluate(request)
            
            assert decision is not None
            assert hasattr(decision, 'allowed')
            assert hasattr(decision, 'requires_approval')
            assert hasattr(decision, 'mode')
        except ImportError:
            pytest.skip("ExecutionGate not available in this environment")


class TestChainClient:
    """Test blockchain chain client."""
    
    def test_chain_client_import(self):
        """Verify chain client can be imported."""
        import sys
        sys.path.insert(0, '/Users/devswat/resonantgenesis_backend/node/src/resonant_node')
        from chain.client import ChainClient
        assert ChainClient is not None
    
    def test_chain_client_has_write_methods(self):
        """Verify chain client has write methods."""
        import sys
        sys.path.insert(0, '/Users/devswat/resonantgenesis_backend/node/src/resonant_node')
        from chain.client import ChainClient
        
        client = ChainClient(rpc_url="http://localhost:8545")
        
        assert hasattr(client, 'register_identity')
        assert hasattr(client, 'register_agent')
        assert hasattr(client, 'anchor_memory')


class TestExternalAnchorManager:
    """Test external anchor manager for blockchain anchoring."""
    
    def test_external_anchor_manager_import(self):
        """Verify external anchor manager can be imported."""
        import sys
        import os
        blockchain_path = '/Users/devswat/resonantgenesis_backend/blockchain_service'
        if blockchain_path not in sys.path:
            sys.path.insert(0, blockchain_path)
        os.chdir(blockchain_path)
        try:
            from app.audit import external_anchor_manager
            assert external_anchor_manager is not None
        except ImportError as e:
            pytest.skip(f"blockchain_service not available: {e}")
    
    def test_anchor_manager_has_methods(self):
        """Verify anchor manager has required methods."""
        import sys
        import os
        blockchain_path = '/Users/devswat/resonantgenesis_backend/blockchain_service'
        if blockchain_path not in sys.path:
            sys.path.insert(0, blockchain_path)
        os.chdir(blockchain_path)
        try:
            from app.audit import ExternalAnchorManager
            
            manager = ExternalAnchorManager()
            
            assert hasattr(manager, 'should_anchor')
            assert hasattr(manager, 'create_anchor_hash')
            assert hasattr(manager, 'anchor_to_external_chain')
            assert hasattr(manager, 'verify_external_anchor')
        except ImportError as e:
            pytest.skip(f"blockchain_service not available: {e}")


class TestAutonomyModeAPI:
    """Test autonomy mode API endpoints exist."""
    
    def test_autonomy_router_import(self):
        """Verify autonomy router can be imported."""
        from app.routers_autonomy import autonomy_router
        assert autonomy_router is not None
    
    def test_mode_switch_endpoint_exists(self):
        """Verify mode switch endpoint is defined."""
        from app.routers_autonomy import autonomy_router
        
        routes = [r.path for r in autonomy_router.routes]
        # Routes include the router prefix
        assert any("mode" in r and "agent_id" in r for r in routes)


# ============== Run Tests ==============

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
