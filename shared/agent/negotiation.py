"""
Agent Negotiation Protocol
==========================

Agent-to-agent negotiation for tasks, resources, and contracts.

UNBOUNDED MODE: Full negotiation autonomy, binding contracts auto-execute
GOVERNED MODE: Negotiation allowed, contracts require approval above threshold

Features:
- Task auctions (agents bid for tasks)
- Resource trading
- Capability sharing
- Binding contracts
- Contract execution and settlement
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime, timedelta
from enum import Enum
import uuid
import logging

from .autonomy_mode import (
    AutonomyMode,
    AutonomyModeManager,
    get_autonomy_mode_manager,
)

logger = logging.getLogger(__name__)


class NegotiationType(str, Enum):
    """Types of negotiations."""
    TASK_BID = "task_bid"              # Bidding for a task
    RESOURCE_TRADE = "resource_trade"  # Trading resources
    CAPABILITY_SHARE = "capability_share"  # Sharing capabilities
    CONTRACT = "contract"              # General binding agreement


class NegotiationStatus(str, Enum):
    """Status of a negotiation."""
    OPEN = "open"
    BIDDING = "bidding"
    NEGOTIATING = "negotiating"
    AGREED = "agreed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    CANCELLED = "cancelled"


class ContractStatus(str, Enum):
    """Status of a contract."""
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    COMPLETED = "completed"
    BREACHED = "breached"
    CANCELLED = "cancelled"
    DISPUTED = "disputed"


@dataclass
class Bid:
    """A bid in a negotiation."""
    id: str
    agent_id: str
    negotiation_id: str
    
    # Offer details
    offer: Dict[str, Any]
    price: float
    currency: str = "USD"
    
    # Agent's assessment
    confidence: float = 0.8  # 0-1
    estimated_completion: str = ""
    estimated_duration_hours: float = 1.0
    
    # Status
    is_winning: bool = False
    is_rejected: bool = False
    rejection_reason: Optional[str] = None
    
    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Negotiation:
    """A negotiation between agents."""
    id: str
    type: NegotiationType
    
    # Participants
    initiator_agent_id: str
    target_agent_ids: List[str]
    
    # Subject
    subject: Dict[str, Any]  # What's being negotiated
    description: str = ""
    
    # Status
    status: NegotiationStatus = NegotiationStatus.OPEN
    
    # Bids
    bids: List[str] = field(default_factory=list)  # bid_ids
    winning_bid_id: Optional[str] = None
    
    # Resulting contract
    contract_id: Optional[str] = None
    
    # Timing
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    expires_at: str = ""
    closed_at: Optional[str] = None
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ContractObligation:
    """An obligation in a contract."""
    id: str
    agent_id: str
    description: str
    deadline: Optional[str] = None
    is_completed: bool = False
    completed_at: Optional[str] = None
    verification_method: str = "self_report"  # self_report, automated, human_review


@dataclass
class AgentContract:
    """A binding contract between agents."""
    id: str
    negotiation_id: str
    
    # Parties
    parties: List[str]  # agent_ids
    
    # Terms
    terms: Dict[str, Any]
    description: str = ""
    
    # Obligations
    obligations: List[ContractObligation] = field(default_factory=list)
    
    # Financial terms
    total_value: float = 0.0
    currency: str = "USD"
    rewards: Dict[str, float] = field(default_factory=dict)  # agent_id -> reward
    penalties: Dict[str, float] = field(default_factory=dict)  # agent_id -> penalty
    
    # Status
    status: ContractStatus = ContractStatus.DRAFT
    
    # Approval (for GOVERNED mode)
    requires_approval: bool = False
    approval_id: Optional[str] = None
    approved_by: Optional[str] = None
    
    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    activated_at: Optional[str] = None
    completed_at: Optional[str] = None
    expires_at: str = ""
    
    # Audit
    audit_hash: Optional[str] = None
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)


class NegotiationEngine:
    """
    Engine for agent-to-agent negotiation.
    
    UNBOUNDED MODE:
    - Full negotiation autonomy
    - Binding contracts auto-execute
    - No value limits
    
    GOVERNED MODE:
    - Negotiation allowed
    - Contracts above threshold require approval
    - Value limits enforced
    """
    
    def __init__(
        self,
        mode_manager: Optional[AutonomyModeManager] = None,
        approval_callback: Optional[Callable] = None,
        wallet_system: Optional[Any] = None,
    ):
        self.mode_manager = mode_manager or get_autonomy_mode_manager()
        self.approval_callback = approval_callback
        self.wallet_system = wallet_system
        
        # Storage
        self._negotiations: Dict[str, Negotiation] = {}
        self._bids: Dict[str, Bid] = {}
        self._contracts: Dict[str, AgentContract] = {}
        
        # Agent indexes
        self._agent_negotiations: Dict[str, List[str]] = {}  # agent_id -> negotiation_ids
        self._agent_contracts: Dict[str, List[str]] = {}  # agent_id -> contract_ids
    
    async def create_task_auction(
        self,
        initiator_id: str,
        task: Dict[str, Any],
        target_agents: List[str],
        deadline_hours: float = 24.0,
        min_bid: float = 0.0,
        max_bid: Optional[float] = None,
    ) -> Negotiation:
        """
        Create an auction for a task.
        Agents can bid to complete the task.
        """
        expires_at = (datetime.utcnow() + timedelta(hours=deadline_hours)).isoformat()
        
        negotiation = Negotiation(
            id=str(uuid.uuid4()),
            type=NegotiationType.TASK_BID,
            initiator_agent_id=initiator_id,
            target_agent_ids=target_agents,
            subject=task,
            description=task.get("description", "Task auction"),
            status=NegotiationStatus.BIDDING,
            expires_at=expires_at,
            metadata={
                "min_bid": min_bid,
                "max_bid": max_bid,
            }
        )
        
        self._negotiations[negotiation.id] = negotiation
        self._index_negotiation(negotiation)
        
        logger.info(
            f"Created task auction {negotiation.id} by {initiator_id} "
            f"for {len(target_agents)} agents"
        )
        
        return negotiation
    
    async def create_resource_trade(
        self,
        initiator_id: str,
        offering: Dict[str, Any],
        requesting: Dict[str, Any],
        target_agents: List[str],
        deadline_hours: float = 24.0,
    ) -> Negotiation:
        """
        Create a resource trade negotiation.
        """
        expires_at = (datetime.utcnow() + timedelta(hours=deadline_hours)).isoformat()
        
        negotiation = Negotiation(
            id=str(uuid.uuid4()),
            type=NegotiationType.RESOURCE_TRADE,
            initiator_agent_id=initiator_id,
            target_agent_ids=target_agents,
            subject={
                "offering": offering,
                "requesting": requesting,
            },
            description=f"Trade: {offering} for {requesting}",
            status=NegotiationStatus.NEGOTIATING,
            expires_at=expires_at,
        )
        
        self._negotiations[negotiation.id] = negotiation
        self._index_negotiation(negotiation)
        
        logger.info(f"Created resource trade {negotiation.id} by {initiator_id}")
        return negotiation
    
    async def submit_bid(
        self,
        agent_id: str,
        negotiation_id: str,
        offer: Dict[str, Any],
        price: float,
        confidence: float = 0.8,
        estimated_completion: Optional[str] = None,
        estimated_duration_hours: float = 1.0,
    ) -> Bid:
        """
        Submit a bid for a negotiation.
        """
        negotiation = self._negotiations.get(negotiation_id)
        if not negotiation:
            raise ValueError(f"Negotiation {negotiation_id} not found")
        
        if agent_id not in negotiation.target_agent_ids:
            raise PermissionError("Agent not invited to this negotiation")
        
        if negotiation.status not in [NegotiationStatus.OPEN, NegotiationStatus.BIDDING]:
            raise ValueError(f"Negotiation is {negotiation.status.value}, cannot bid")
        
        # Check bid limits
        min_bid = negotiation.metadata.get("min_bid", 0)
        max_bid = negotiation.metadata.get("max_bid")
        
        if price < min_bid:
            raise ValueError(f"Bid ${price} below minimum ${min_bid}")
        if max_bid and price > max_bid:
            raise ValueError(f"Bid ${price} above maximum ${max_bid}")
        
        # Create bid
        if not estimated_completion:
            estimated_completion = (
                datetime.utcnow() + timedelta(hours=estimated_duration_hours)
            ).isoformat()
        
        bid = Bid(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            negotiation_id=negotiation_id,
            offer=offer,
            price=price,
            confidence=confidence,
            estimated_completion=estimated_completion,
            estimated_duration_hours=estimated_duration_hours,
        )
        
        self._bids[bid.id] = bid
        negotiation.bids.append(bid.id)
        
        logger.info(
            f"Bid {bid.id} submitted by {agent_id} for negotiation {negotiation_id}: ${price}"
        )
        
        return bid
    
    async def accept_bid(
        self,
        negotiation_id: str,
        bid_id: str,
        acceptor_id: str,
    ) -> AgentContract:
        """
        Accept a bid and create a binding contract.
        
        UNBOUNDED: Auto-execute
        GOVERNED: May require approval for high-value contracts
        """
        negotiation = self._negotiations.get(negotiation_id)
        if not negotiation:
            raise ValueError(f"Negotiation {negotiation_id} not found")
        
        if acceptor_id != negotiation.initiator_agent_id:
            raise PermissionError("Only initiator can accept bids")
        
        bid = self._bids.get(bid_id)
        if not bid:
            raise ValueError(f"Bid {bid_id} not found")
        
        if bid.negotiation_id != negotiation_id:
            raise ValueError("Bid does not belong to this negotiation")
        
        # Get mode and config
        mode = self.mode_manager.get_mode(acceptor_id)
        config = self.mode_manager.get_config(acceptor_id)
        
        # Check if approval needed (GOVERNED mode)
        requires_approval = False
        if mode == AutonomyMode.GOVERNED:
            if bid.price > config.contract_value_limit:
                requires_approval = True
        
        # Create contract
        contract = AgentContract(
            id=str(uuid.uuid4()),
            negotiation_id=negotiation_id,
            parties=[negotiation.initiator_agent_id, bid.agent_id],
            terms=bid.offer,
            description=f"Contract for: {negotiation.description}",
            total_value=bid.price,
            rewards={bid.agent_id: bid.price},
            penalties={bid.agent_id: bid.price * 0.1},  # 10% penalty
            requires_approval=requires_approval,
            expires_at=bid.estimated_completion,
            obligations=[
                ContractObligation(
                    id=str(uuid.uuid4()),
                    agent_id=bid.agent_id,
                    description="Complete task as specified",
                    deadline=bid.estimated_completion,
                ),
                ContractObligation(
                    id=str(uuid.uuid4()),
                    agent_id=negotiation.initiator_agent_id,
                    description="Pay upon completion",
                    deadline=bid.estimated_completion,
                ),
            ],
        )
        
        # Handle approval
        if requires_approval:
            contract.status = ContractStatus.PENDING_APPROVAL
            
            if self.approval_callback:
                approval_id = await self.approval_callback(
                    agent_id=acceptor_id,
                    action="accept_contract",
                    amount=bid.price,
                    description=f"Contract with {bid.agent_id}",
                )
                contract.approval_id = approval_id
            
            logger.info(f"Contract {contract.id} pending approval: ${bid.price}")
        else:
            contract.status = ContractStatus.ACTIVE
            contract.activated_at = datetime.utcnow().isoformat()
            logger.info(f"Contract {contract.id} activated: ${bid.price}")
        
        # Update negotiation
        negotiation.status = NegotiationStatus.AGREED
        negotiation.winning_bid_id = bid_id
        negotiation.contract_id = contract.id
        negotiation.closed_at = datetime.utcnow().isoformat()
        
        # Update bid
        bid.is_winning = True
        
        # Reject other bids
        for other_bid_id in negotiation.bids:
            if other_bid_id != bid_id:
                other_bid = self._bids.get(other_bid_id)
                if other_bid:
                    other_bid.is_rejected = True
                    other_bid.rejection_reason = "Another bid was accepted"
        
        # Store contract
        self._contracts[contract.id] = contract
        self._index_contract(contract)
        
        return contract
    
    async def complete_contract(
        self,
        contract_id: str,
        completing_agent_id: str,
        deliverables: Dict[str, Any],
    ) -> bool:
        """
        Mark a contract as completed and trigger payment.
        """
        contract = self._contracts.get(contract_id)
        if not contract:
            raise ValueError(f"Contract {contract_id} not found")
        
        if completing_agent_id not in contract.parties:
            raise PermissionError("Agent not party to this contract")
        
        if contract.status != ContractStatus.ACTIVE:
            raise ValueError(f"Contract is {contract.status.value}, cannot complete")
        
        # Mark obligations as completed
        for obligation in contract.obligations:
            if obligation.agent_id == completing_agent_id:
                obligation.is_completed = True
                obligation.completed_at = datetime.utcnow().isoformat()
        
        # Check if all obligations completed
        all_completed = all(o.is_completed for o in contract.obligations)
        
        if all_completed:
            contract.status = ContractStatus.COMPLETED
            contract.completed_at = datetime.utcnow().isoformat()
            
            # Trigger payments
            if self.wallet_system:
                for agent_id, reward in contract.rewards.items():
                    await self._pay_agent(agent_id, reward, contract_id)
            
            logger.info(f"Contract {contract_id} completed")
            return True
        
        logger.info(
            f"Contract {contract_id} obligation completed by {completing_agent_id}"
        )
        return False
    
    async def breach_contract(
        self,
        contract_id: str,
        breaching_agent_id: str,
        reason: str,
    ) -> bool:
        """
        Mark a contract as breached and apply penalties.
        """
        contract = self._contracts.get(contract_id)
        if not contract:
            raise ValueError(f"Contract {contract_id} not found")
        
        contract.status = ContractStatus.BREACHED
        contract.metadata["breach_reason"] = reason
        contract.metadata["breaching_agent"] = breaching_agent_id
        contract.metadata["breached_at"] = datetime.utcnow().isoformat()
        
        # Apply penalty
        penalty = contract.penalties.get(breaching_agent_id, 0)
        if penalty > 0 and self.wallet_system:
            await self._apply_penalty(breaching_agent_id, penalty, contract_id)
        
        logger.warning(
            f"Contract {contract_id} breached by {breaching_agent_id}: {reason}"
        )
        return True
    
    def approve_contract(
        self,
        contract_id: str,
        approver_id: str,
    ) -> AgentContract:
        """Approve a pending contract."""
        contract = self._contracts.get(contract_id)
        if not contract:
            raise ValueError(f"Contract {contract_id} not found")
        
        if contract.status != ContractStatus.PENDING_APPROVAL:
            raise ValueError("Contract not pending approval")
        
        contract.status = ContractStatus.ACTIVE
        contract.approved_by = approver_id
        contract.activated_at = datetime.utcnow().isoformat()
        
        logger.info(f"Contract {contract_id} approved by {approver_id}")
        return contract
    
    def reject_contract(
        self,
        contract_id: str,
        rejector_id: str,
        reason: str,
    ) -> AgentContract:
        """Reject a pending contract."""
        contract = self._contracts.get(contract_id)
        if not contract:
            raise ValueError(f"Contract {contract_id} not found")
        
        if contract.status != ContractStatus.PENDING_APPROVAL:
            raise ValueError("Contract not pending approval")
        
        contract.status = ContractStatus.CANCELLED
        contract.metadata["rejection_reason"] = reason
        contract.metadata["rejected_by"] = rejector_id
        
        # Reopen negotiation
        negotiation = self._negotiations.get(contract.negotiation_id)
        if negotiation:
            negotiation.status = NegotiationStatus.BIDDING
            negotiation.contract_id = None
            negotiation.winning_bid_id = None
        
        logger.info(f"Contract {contract_id} rejected by {rejector_id}: {reason}")
        return contract
    
    def get_negotiation(self, negotiation_id: str) -> Optional[Negotiation]:
        """Get a negotiation by ID."""
        return self._negotiations.get(negotiation_id)
    
    def get_contract(self, contract_id: str) -> Optional[AgentContract]:
        """Get a contract by ID."""
        return self._contracts.get(contract_id)
    
    def get_agent_negotiations(
        self,
        agent_id: str,
        status: Optional[NegotiationStatus] = None,
    ) -> List[Negotiation]:
        """Get all negotiations for an agent."""
        negotiation_ids = self._agent_negotiations.get(agent_id, [])
        negotiations = [
            self._negotiations[nid] 
            for nid in negotiation_ids 
            if nid in self._negotiations
        ]
        
        if status:
            negotiations = [n for n in negotiations if n.status == status]
        
        return negotiations
    
    def get_agent_contracts(
        self,
        agent_id: str,
        status: Optional[ContractStatus] = None,
    ) -> List[AgentContract]:
        """Get all contracts for an agent."""
        contract_ids = self._agent_contracts.get(agent_id, [])
        contracts = [
            self._contracts[cid] 
            for cid in contract_ids 
            if cid in self._contracts
        ]
        
        if status:
            contracts = [c for c in contracts if c.status == status]
        
        return contracts
    
    def get_bids(self, negotiation_id: str) -> List[Bid]:
        """Get all bids for a negotiation."""
        negotiation = self._negotiations.get(negotiation_id)
        if not negotiation:
            return []
        
        return [
            self._bids[bid_id] 
            for bid_id in negotiation.bids 
            if bid_id in self._bids
        ]
    
    def _index_negotiation(self, negotiation: Negotiation):
        """Index a negotiation for agent lookup."""
        # Index for initiator
        if negotiation.initiator_agent_id not in self._agent_negotiations:
            self._agent_negotiations[negotiation.initiator_agent_id] = []
        self._agent_negotiations[negotiation.initiator_agent_id].append(negotiation.id)
        
        # Index for targets
        for agent_id in negotiation.target_agent_ids:
            if agent_id not in self._agent_negotiations:
                self._agent_negotiations[agent_id] = []
            self._agent_negotiations[agent_id].append(negotiation.id)
    
    def _index_contract(self, contract: AgentContract):
        """Index a contract for agent lookup."""
        for agent_id in contract.parties:
            if agent_id not in self._agent_contracts:
                self._agent_contracts[agent_id] = []
            self._agent_contracts[agent_id].append(contract.id)
    
    async def _pay_agent(self, agent_id: str, amount: float, contract_id: str):
        """Pay an agent for contract completion."""
        if self.wallet_system:
            wallet = self.wallet_system.get_wallet_by_agent(agent_id)
            if wallet:
                self.wallet_system.credit(
                    wallet.id,
                    amount,
                    f"Contract completion: {contract_id}",
                )
    
    async def _apply_penalty(self, agent_id: str, amount: float, contract_id: str):
        """Apply a penalty to an agent for contract breach."""
        if self.wallet_system:
            wallet = self.wallet_system.get_wallet_by_agent(agent_id)
            if wallet:
                await self.wallet_system.spend(
                    wallet.id,
                    amount,
                    f"Contract breach penalty: {contract_id}",
                )


# Global instance
negotiation_engine = NegotiationEngine()


def get_negotiation_engine() -> NegotiationEngine:
    """Get the global negotiation engine."""
    return negotiation_engine
