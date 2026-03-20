"""
Tests for Agent Wallet System
=============================

Tests for:
- Wallet creation
- Spend operations
- Transfers
- Limit enforcement
- Mode-aware behavior
"""

import pytest
import asyncio

import sys
sys.path.insert(0, '/Users/devswat/resonantgenesis_backend')

from shared.agent.autonomy_mode import (
    AutonomyMode,
    AutonomyModeManager,
)
from shared.agent.wallet import (
    AgentWalletSystem,
    AgentWallet,
    WalletTransaction,
    TransactionType,
    TransactionStatus,
    SpendRequest,
    SpendResult,
)


class TestAgentWalletSystem:
    """Tests for AgentWalletSystem."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mode_manager = AutonomyModeManager()
        self.wallet_system = AgentWalletSystem(self.mode_manager)
        self.agent_id = "test_agent_123"
        self.admin_user_id = "admin_user_789"
        
        self.mode_manager.add_unbounded_user(self.admin_user_id)
    
    def test_create_wallet(self):
        """Test wallet creation."""
        wallet = self.wallet_system.create_wallet(
            agent_id=self.agent_id,
            initial_balance=100.0,
        )
        
        assert wallet is not None
        assert wallet.agent_id == self.agent_id
        assert wallet.balance == 100.0
        assert wallet.is_active == True
        assert wallet.is_frozen == False
    
    def test_create_wallet_with_limits(self):
        """Test wallet creation with custom limits."""
        wallet = self.wallet_system.create_wallet(
            agent_id=self.agent_id,
            initial_balance=500.0,
            daily_limit=200.0,
            transaction_limit=100.0,
            monthly_limit=2000.0,
        )
        
        assert wallet.daily_limit == 200.0
        assert wallet.transaction_limit == 100.0
        assert wallet.monthly_limit == 2000.0
    
    def test_cannot_create_duplicate_wallet(self):
        """Test that duplicate wallets are rejected."""
        self.wallet_system.create_wallet(agent_id=self.agent_id)
        
        with pytest.raises(ValueError, match="already has a wallet"):
            self.wallet_system.create_wallet(agent_id=self.agent_id)
    
    def test_get_wallet_by_agent(self):
        """Test getting wallet by agent ID."""
        created = self.wallet_system.create_wallet(agent_id=self.agent_id)
        retrieved = self.wallet_system.get_wallet_by_agent(self.agent_id)
        
        assert retrieved is not None
        assert retrieved.id == created.id
    
    @pytest.mark.asyncio
    async def test_spend_success(self):
        """Test successful spend operation."""
        wallet = self.wallet_system.create_wallet(
            agent_id=self.agent_id,
            initial_balance=100.0,
        )
        
        result = await self.wallet_system.spend(SpendRequest(
            wallet_id=wallet.id,
            amount=25.0,
            description="Test spend",
        ))
        
        assert result.success == True
        assert result.transaction is not None
        assert result.transaction.amount == 25.0
        assert result.transaction.status == TransactionStatus.COMPLETED
        
        # Check balance updated
        updated_wallet = self.wallet_system.get_wallet(wallet.id)
        assert updated_wallet.balance == 75.0
    
    @pytest.mark.asyncio
    async def test_spend_insufficient_balance(self):
        """Test spend with insufficient balance."""
        wallet = self.wallet_system.create_wallet(
            agent_id=self.agent_id,
            initial_balance=50.0,
        )
        
        result = await self.wallet_system.spend(SpendRequest(
            wallet_id=wallet.id,
            amount=100.0,
            description="Too much",
        ))
        
        assert result.success == False
        assert "Insufficient balance" in result.error
    
    @pytest.mark.asyncio
    async def test_spend_over_transaction_limit_requires_approval(self):
        """Test that spending over transaction limit requires approval."""
        wallet = self.wallet_system.create_wallet(
            agent_id=self.agent_id,
            initial_balance=200.0,
            transaction_limit=50.0,
        )
        
        result = await self.wallet_system.spend(SpendRequest(
            wallet_id=wallet.id,
            amount=75.0,  # Over $50 limit
            description="Large transaction",
        ))
        
        assert result.success == False
        assert result.requires_approval == True
        assert result.transaction is not None
        assert result.transaction.status == TransactionStatus.PENDING_APPROVAL
    
    @pytest.mark.asyncio
    async def test_spend_in_unbounded_mode_no_limits(self):
        """Test that UNBOUNDED mode ignores limits."""
        # Switch to unbounded
        self.mode_manager.switch_mode(
            agent_id=self.agent_id,
            new_mode=AutonomyMode.UNBOUNDED,
            user_id=self.admin_user_id,
        )
        
        wallet = self.wallet_system.create_wallet(
            agent_id=self.agent_id,
            initial_balance=1000.0,
            transaction_limit=50.0,  # This should be ignored
        )
        
        result = await self.wallet_system.spend(SpendRequest(
            wallet_id=wallet.id,
            amount=500.0,  # Way over limit
            description="Large unbounded spend",
        ))
        
        assert result.success == True
        assert result.requires_approval == False
    
    @pytest.mark.asyncio
    async def test_transfer_between_wallets(self):
        """Test transfer between agent wallets."""
        wallet1 = self.wallet_system.create_wallet(
            agent_id="agent_1",
            initial_balance=100.0,
        )
        wallet2 = self.wallet_system.create_wallet(
            agent_id="agent_2",
            initial_balance=50.0,
        )
        
        result = await self.wallet_system.transfer(
            from_wallet_id=wallet1.id,
            to_wallet_id=wallet2.id,
            amount=30.0,
            description="Test transfer",
        )
        
        assert result.success == True
        
        # Check balances
        w1 = self.wallet_system.get_wallet(wallet1.id)
        w2 = self.wallet_system.get_wallet(wallet2.id)
        assert w1.balance == 70.0
        assert w2.balance == 80.0
    
    def test_credit_wallet(self):
        """Test crediting a wallet."""
        wallet = self.wallet_system.create_wallet(
            agent_id=self.agent_id,
            initial_balance=100.0,
        )
        
        transaction = self.wallet_system.credit(
            wallet_id=wallet.id,
            amount=50.0,
            description="Test credit",
        )
        
        assert transaction.type == TransactionType.CREDIT
        assert transaction.amount == 50.0
        
        updated = self.wallet_system.get_wallet(wallet.id)
        assert updated.balance == 150.0
    
    def test_freeze_wallet(self):
        """Test freezing a wallet."""
        wallet = self.wallet_system.create_wallet(agent_id=self.agent_id)
        
        success = self.wallet_system.freeze_wallet(
            wallet_id=wallet.id,
            reason="Suspicious activity",
            frozen_by="admin",
        )
        
        assert success == True
        
        updated = self.wallet_system.get_wallet(wallet.id)
        assert updated.is_frozen == True
    
    @pytest.mark.asyncio
    async def test_frozen_wallet_cannot_spend(self):
        """Test that frozen wallet cannot spend."""
        wallet = self.wallet_system.create_wallet(
            agent_id=self.agent_id,
            initial_balance=100.0,
        )
        
        self.wallet_system.freeze_wallet(wallet.id, "Test", "admin")
        
        result = await self.wallet_system.spend(SpendRequest(
            wallet_id=wallet.id,
            amount=10.0,
            description="Should fail",
        ))
        
        assert result.success == False
        assert "frozen" in result.error.lower()
    
    def test_unfreeze_wallet(self):
        """Test unfreezing a wallet."""
        wallet = self.wallet_system.create_wallet(agent_id=self.agent_id)
        self.wallet_system.freeze_wallet(wallet.id, "Test", "admin")
        
        success = self.wallet_system.unfreeze_wallet(wallet.id, "admin")
        
        assert success == True
        
        updated = self.wallet_system.get_wallet(wallet.id)
        assert updated.is_frozen == False
    
    def test_get_transactions(self):
        """Test getting transaction history."""
        wallet = self.wallet_system.create_wallet(
            agent_id=self.agent_id,
            initial_balance=100.0,
        )
        
        # Add some credits
        self.wallet_system.credit(wallet.id, 10.0, "Credit 1")
        self.wallet_system.credit(wallet.id, 20.0, "Credit 2")
        
        transactions = self.wallet_system.get_transactions(wallet.id)
        
        assert len(transactions) == 2
    
    def test_daily_spend_tracking(self):
        """Test daily spend tracking."""
        wallet = self.wallet_system.create_wallet(
            agent_id=self.agent_id,
            initial_balance=200.0,
        )
        
        # Spend some money
        asyncio.get_event_loop().run_until_complete(
            self.wallet_system.spend(SpendRequest(
                wallet_id=wallet.id,
                amount=30.0,
                description="Spend 1",
            ))
        )
        
        updated = self.wallet_system.get_wallet(wallet.id)
        assert updated.daily_spent == 30.0
    
    def test_remaining_daily_budget(self):
        """Test remaining daily budget calculation."""
        wallet = self.wallet_system.create_wallet(
            agent_id=self.agent_id,
            initial_balance=200.0,
            daily_limit=100.0,
        )
        
        # Spend some
        asyncio.get_event_loop().run_until_complete(
            self.wallet_system.spend(SpendRequest(
                wallet_id=wallet.id,
                amount=40.0,
                description="Spend",
            ))
        )
        
        remaining = self.wallet_system.get_remaining_daily_budget(wallet.id)
        assert remaining == 60.0  # 100 - 40
    
    def test_approve_pending_transaction(self):
        """Test approving a pending transaction."""
        wallet = self.wallet_system.create_wallet(
            agent_id=self.agent_id,
            initial_balance=200.0,
            transaction_limit=50.0,
        )
        
        # Create pending transaction
        result = asyncio.get_event_loop().run_until_complete(
            self.wallet_system.spend(SpendRequest(
                wallet_id=wallet.id,
                amount=75.0,
                description="Large spend",
            ))
        )
        
        assert result.requires_approval == True
        
        # Approve it
        approve_result = self.wallet_system.approve_transaction(
            transaction_id=result.transaction.id,
            approver_id="admin",
        )
        
        assert approve_result.success == True
        
        # Check balance was deducted
        updated = self.wallet_system.get_wallet(wallet.id)
        assert updated.balance == 125.0  # 200 - 75


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
