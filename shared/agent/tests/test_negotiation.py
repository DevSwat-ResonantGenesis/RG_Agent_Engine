"""
Tests for Agent Negotiation Protocol
====================================

Tests for:
- Task auctions
- Bid submission
- Contract creation
- Contract completion
- Mode-aware behavior
"""

import pytest
import asyncio
from datetime import datetime, timedelta

import sys
sys.path.insert(0, '/Users/devswat/resonantgenesis_backend')

from shared.agent.autonomy_mode import (
    AutonomyMode,
    AutonomyModeManager,
)
from shared.agent.negotiation import (
    NegotiationEngine,
    Negotiation,
    NegotiationType,
    NegotiationStatus,
    Bid,
    AgentContract,
    ContractStatus,
)


class TestNegotiationEngine:
    """Tests for NegotiationEngine."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mode_manager = AutonomyModeManager()
        self.engine = NegotiationEngine(self.mode_manager)
        
        self.initiator_id = "agent_initiator"
        self.bidder_1 = "agent_bidder_1"
        self.bidder_2 = "agent_bidder_2"
        self.admin_user_id = "admin_user"
        
        self.mode_manager.add_unbounded_user(self.admin_user_id)
    
    @pytest.mark.asyncio
    async def test_create_task_auction(self):
        """Test creating a task auction."""
        auction = await self.engine.create_task_auction(
            initiator_id=self.initiator_id,
            task={"description": "Analyze data", "type": "analysis"},
            target_agents=[self.bidder_1, self.bidder_2],
            deadline_hours=24.0,
            min_bid=10.0,
            max_bid=100.0,
        )
        
        assert auction is not None
        assert auction.type == NegotiationType.TASK_BID
        assert auction.status == NegotiationStatus.BIDDING
        assert auction.initiator_agent_id == self.initiator_id
        assert len(auction.target_agent_ids) == 2
    
    @pytest.mark.asyncio
    async def test_submit_bid(self):
        """Test submitting a bid."""
        auction = await self.engine.create_task_auction(
            initiator_id=self.initiator_id,
            task={"description": "Test task"},
            target_agents=[self.bidder_1],
            deadline_hours=24.0,
        )
        
        bid = await self.engine.submit_bid(
            agent_id=self.bidder_1,
            negotiation_id=auction.id,
            offer={"approach": "ML analysis"},
            price=50.0,
            confidence=0.9,
            estimated_duration_hours=2.0,
        )
        
        assert bid is not None
        assert bid.agent_id == self.bidder_1
        assert bid.price == 50.0
        assert bid.confidence == 0.9
    
    @pytest.mark.asyncio
    async def test_uninvited_agent_cannot_bid(self):
        """Test that uninvited agents cannot bid."""
        auction = await self.engine.create_task_auction(
            initiator_id=self.initiator_id,
            task={"description": "Test task"},
            target_agents=[self.bidder_1],  # Only bidder_1 invited
            deadline_hours=24.0,
        )
        
        with pytest.raises(PermissionError, match="not invited"):
            await self.engine.submit_bid(
                agent_id=self.bidder_2,  # Not invited
                negotiation_id=auction.id,
                offer={},
                price=50.0,
            )
    
    @pytest.mark.asyncio
    async def test_bid_below_minimum_rejected(self):
        """Test that bids below minimum are rejected."""
        auction = await self.engine.create_task_auction(
            initiator_id=self.initiator_id,
            task={"description": "Test task"},
            target_agents=[self.bidder_1],
            deadline_hours=24.0,
            min_bid=50.0,
        )
        
        with pytest.raises(ValueError, match="below minimum"):
            await self.engine.submit_bid(
                agent_id=self.bidder_1,
                negotiation_id=auction.id,
                offer={},
                price=25.0,  # Below $50 minimum
            )
    
    @pytest.mark.asyncio
    async def test_accept_bid_creates_contract(self):
        """Test that accepting a bid creates a contract."""
        auction = await self.engine.create_task_auction(
            initiator_id=self.initiator_id,
            task={"description": "Test task"},
            target_agents=[self.bidder_1],
            deadline_hours=24.0,
        )
        
        bid = await self.engine.submit_bid(
            agent_id=self.bidder_1,
            negotiation_id=auction.id,
            offer={"approach": "Test"},
            price=50.0,
        )
        
        contract = await self.engine.accept_bid(
            negotiation_id=auction.id,
            bid_id=bid.id,
            acceptor_id=self.initiator_id,
        )
        
        assert contract is not None
        assert contract.total_value == 50.0
        assert self.initiator_id in contract.parties
        assert self.bidder_1 in contract.parties
    
    @pytest.mark.asyncio
    async def test_only_initiator_can_accept_bid(self):
        """Test that only the initiator can accept bids."""
        auction = await self.engine.create_task_auction(
            initiator_id=self.initiator_id,
            task={"description": "Test task"},
            target_agents=[self.bidder_1, self.bidder_2],
            deadline_hours=24.0,
        )
        
        bid = await self.engine.submit_bid(
            agent_id=self.bidder_1,
            negotiation_id=auction.id,
            offer={},
            price=50.0,
        )
        
        with pytest.raises(PermissionError, match="Only initiator"):
            await self.engine.accept_bid(
                negotiation_id=auction.id,
                bid_id=bid.id,
                acceptor_id=self.bidder_2,  # Not the initiator
            )
    
    @pytest.mark.asyncio
    async def test_high_value_contract_requires_approval_in_governed(self):
        """Test that high-value contracts require approval in GOVERNED mode."""
        # Governed mode config has contract_value_limit of $500
        auction = await self.engine.create_task_auction(
            initiator_id=self.initiator_id,
            task={"description": "Expensive task"},
            target_agents=[self.bidder_1],
            deadline_hours=24.0,
        )
        
        bid = await self.engine.submit_bid(
            agent_id=self.bidder_1,
            negotiation_id=auction.id,
            offer={},
            price=600.0,  # Over $500 limit
        )
        
        contract = await self.engine.accept_bid(
            negotiation_id=auction.id,
            bid_id=bid.id,
            acceptor_id=self.initiator_id,
        )
        
        assert contract.requires_approval == True
        assert contract.status == ContractStatus.PENDING_APPROVAL
    
    @pytest.mark.asyncio
    async def test_unbounded_mode_auto_activates_contract(self):
        """Test that UNBOUNDED mode auto-activates contracts."""
        # Switch to unbounded
        self.mode_manager.switch_mode(
            agent_id=self.initiator_id,
            new_mode=AutonomyMode.UNBOUNDED,
            user_id=self.admin_user_id,
        )
        
        auction = await self.engine.create_task_auction(
            initiator_id=self.initiator_id,
            task={"description": "Expensive task"},
            target_agents=[self.bidder_1],
            deadline_hours=24.0,
        )
        
        bid = await self.engine.submit_bid(
            agent_id=self.bidder_1,
            negotiation_id=auction.id,
            offer={},
            price=1000.0,  # High value
        )
        
        contract = await self.engine.accept_bid(
            negotiation_id=auction.id,
            bid_id=bid.id,
            acceptor_id=self.initiator_id,
        )
        
        assert contract.requires_approval == False
        assert contract.status == ContractStatus.ACTIVE
    
    @pytest.mark.asyncio
    async def test_complete_contract(self):
        """Test completing a contract."""
        auction = await self.engine.create_task_auction(
            initiator_id=self.initiator_id,
            task={"description": "Test task"},
            target_agents=[self.bidder_1],
            deadline_hours=24.0,
        )
        
        bid = await self.engine.submit_bid(
            agent_id=self.bidder_1,
            negotiation_id=auction.id,
            offer={},
            price=50.0,
        )
        
        contract = await self.engine.accept_bid(
            negotiation_id=auction.id,
            bid_id=bid.id,
            acceptor_id=self.initiator_id,
        )
        
        # Complete by bidder
        await self.engine.complete_contract(
            contract_id=contract.id,
            completing_agent_id=self.bidder_1,
            deliverables={"result": "done"},
        )
        
        # Complete by initiator (payment)
        completed = await self.engine.complete_contract(
            contract_id=contract.id,
            completing_agent_id=self.initiator_id,
            deliverables={},
        )
        
        assert completed == True
        
        updated = self.engine.get_contract(contract.id)
        assert updated.status == ContractStatus.COMPLETED
    
    @pytest.mark.asyncio
    async def test_breach_contract(self):
        """Test breaching a contract."""
        auction = await self.engine.create_task_auction(
            initiator_id=self.initiator_id,
            task={"description": "Test task"},
            target_agents=[self.bidder_1],
            deadline_hours=24.0,
        )
        
        bid = await self.engine.submit_bid(
            agent_id=self.bidder_1,
            negotiation_id=auction.id,
            offer={},
            price=50.0,
        )
        
        contract = await self.engine.accept_bid(
            negotiation_id=auction.id,
            bid_id=bid.id,
            acceptor_id=self.initiator_id,
        )
        
        await self.engine.breach_contract(
            contract_id=contract.id,
            breaching_agent_id=self.bidder_1,
            reason="Failed to deliver",
        )
        
        updated = self.engine.get_contract(contract.id)
        assert updated.status == ContractStatus.BREACHED
    
    def test_get_agent_negotiations(self):
        """Test getting negotiations for an agent."""
        # Create some auctions
        asyncio.get_event_loop().run_until_complete(
            self.engine.create_task_auction(
                initiator_id=self.initiator_id,
                task={"description": "Task 1"},
                target_agents=[self.bidder_1],
                deadline_hours=24.0,
            )
        )
        asyncio.get_event_loop().run_until_complete(
            self.engine.create_task_auction(
                initiator_id=self.initiator_id,
                task={"description": "Task 2"},
                target_agents=[self.bidder_1],
                deadline_hours=24.0,
            )
        )
        
        # Get negotiations for initiator
        negotiations = self.engine.get_agent_negotiations(self.initiator_id)
        assert len(negotiations) == 2
        
        # Get negotiations for bidder
        negotiations = self.engine.get_agent_negotiations(self.bidder_1)
        assert len(negotiations) == 2
    
    def test_get_bids(self):
        """Test getting bids for a negotiation."""
        auction = asyncio.get_event_loop().run_until_complete(
            self.engine.create_task_auction(
                initiator_id=self.initiator_id,
                task={"description": "Task"},
                target_agents=[self.bidder_1, self.bidder_2],
                deadline_hours=24.0,
            )
        )
        
        # Submit bids
        asyncio.get_event_loop().run_until_complete(
            self.engine.submit_bid(
                agent_id=self.bidder_1,
                negotiation_id=auction.id,
                offer={},
                price=50.0,
            )
        )
        asyncio.get_event_loop().run_until_complete(
            self.engine.submit_bid(
                agent_id=self.bidder_2,
                negotiation_id=auction.id,
                offer={},
                price=60.0,
            )
        )
        
        bids = self.engine.get_bids(auction.id)
        assert len(bids) == 2
    
    def test_approve_pending_contract(self):
        """Test approving a pending contract."""
        auction = asyncio.get_event_loop().run_until_complete(
            self.engine.create_task_auction(
                initiator_id=self.initiator_id,
                task={"description": "Expensive task"},
                target_agents=[self.bidder_1],
                deadline_hours=24.0,
            )
        )
        
        bid = asyncio.get_event_loop().run_until_complete(
            self.engine.submit_bid(
                agent_id=self.bidder_1,
                negotiation_id=auction.id,
                offer={},
                price=600.0,  # Over limit
            )
        )
        
        contract = asyncio.get_event_loop().run_until_complete(
            self.engine.accept_bid(
                negotiation_id=auction.id,
                bid_id=bid.id,
                acceptor_id=self.initiator_id,
            )
        )
        
        assert contract.status == ContractStatus.PENDING_APPROVAL
        
        # Approve
        approved = self.engine.approve_contract(contract.id, "admin")
        
        assert approved.status == ContractStatus.ACTIVE
    
    def test_reject_pending_contract(self):
        """Test rejecting a pending contract."""
        auction = asyncio.get_event_loop().run_until_complete(
            self.engine.create_task_auction(
                initiator_id=self.initiator_id,
                task={"description": "Expensive task"},
                target_agents=[self.bidder_1],
                deadline_hours=24.0,
            )
        )
        
        bid = asyncio.get_event_loop().run_until_complete(
            self.engine.submit_bid(
                agent_id=self.bidder_1,
                negotiation_id=auction.id,
                offer={},
                price=600.0,
            )
        )
        
        contract = asyncio.get_event_loop().run_until_complete(
            self.engine.accept_bid(
                negotiation_id=auction.id,
                bid_id=bid.id,
                acceptor_id=self.initiator_id,
            )
        )
        
        # Reject
        rejected = self.engine.reject_contract(contract.id, "admin", "Too expensive")
        
        assert rejected.status == ContractStatus.CANCELLED
        
        # Negotiation should be reopened
        negotiation = self.engine.get_negotiation(auction.id)
        assert negotiation.status == NegotiationStatus.BIDDING


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
