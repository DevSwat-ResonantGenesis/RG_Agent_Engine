"""
Agent Wallet System
===================

Manages agent wallets for autonomous financial operations.

UNBOUNDED MODE: No limits, full financial autonomy
GOVERNED MODE: Hard limits, approval for large transactions

Features:
- Credit/debit operations
- Agent-to-agent transfers
- Spending limits enforcement
- Transaction history
- Budget tracking
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime, date
from enum import Enum
import uuid
import logging

from .autonomy_mode import (
    AutonomyMode,
    AutonomyModeManager,
    get_autonomy_mode_manager,
)

logger = logging.getLogger(__name__)


class TransactionType(str, Enum):
    """Types of wallet transactions."""
    CREDIT = "credit"          # Add funds
    DEBIT = "debit"            # Spend funds
    TRANSFER_OUT = "transfer_out"  # Send to another wallet
    TRANSFER_IN = "transfer_in"    # Receive from another wallet
    REFUND = "refund"          # Reversal
    REWARD = "reward"          # Earned from contract completion
    PENALTY = "penalty"        # Deducted for contract breach


class TransactionStatus(str, Enum):
    """Status of a transaction."""
    PENDING = "pending"
    PENDING_APPROVAL = "pending_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERSED = "reversed"
    REJECTED = "rejected"


@dataclass
class WalletTransaction:
    """A single wallet transaction."""
    id: str
    wallet_id: str
    type: TransactionType
    amount: float
    currency: str = "USD"
    description: str = ""
    
    # For transfers
    counterparty_wallet_id: Optional[str] = None
    
    # Status
    status: TransactionStatus = TransactionStatus.PENDING
    
    # Approval (for GOVERNED mode)
    requires_approval: bool = False
    approval_id: Optional[str] = None
    approved_by: Optional[str] = None
    
    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Audit
    audit_hash: Optional[str] = None


@dataclass
class AgentWallet:
    """An agent's wallet for financial operations."""
    id: str
    agent_id: str
    
    # Balance
    balance: float = 0.0
    currency: str = "USD"
    
    # Limits (enforced in GOVERNED mode)
    daily_limit: float = 100.0
    transaction_limit: float = 50.0
    monthly_limit: float = 1000.0
    
    # Tracking
    daily_spent: float = 0.0
    monthly_spent: float = 0.0
    total_spent: float = 0.0
    total_earned: float = 0.0
    
    # Reset tracking
    last_daily_reset: str = field(default_factory=lambda: date.today().isoformat())
    last_monthly_reset: str = field(default_factory=lambda: date.today().replace(day=1).isoformat())
    
    # Access control
    approved_recipients: List[str] = field(default_factory=list)  # wallet_ids
    blocked_recipients: List[str] = field(default_factory=list)   # wallet_ids
    
    # Status
    is_active: bool = True
    is_frozen: bool = False
    frozen_reason: Optional[str] = None
    
    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class SpendRequest:
    """Request to spend from a wallet."""
    wallet_id: str
    amount: float
    description: str
    recipient_wallet_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SpendResult:
    """Result of a spend operation."""
    success: bool
    transaction: Optional[WalletTransaction] = None
    error: Optional[str] = None
    requires_approval: bool = False
    approval_id: Optional[str] = None


class AgentWalletSystem:
    """
    Manages agent wallets for autonomous financial operations.
    
    UNBOUNDED MODE:
    - No spending limits
    - No approval required
    - Unlimited transfers
    
    GOVERNED MODE:
    - Daily/transaction limits enforced
    - Large transactions require approval
    - Recipient restrictions
    """
    
    def __init__(
        self,
        mode_manager: Optional[AutonomyModeManager] = None,
        approval_callback: Optional[callable] = None,
    ):
        self.mode_manager = mode_manager or get_autonomy_mode_manager()
        self.approval_callback = approval_callback
        
        # Storage
        self._wallets: Dict[str, AgentWallet] = {}
        self._agent_wallets: Dict[str, str] = {}  # agent_id -> wallet_id
        self._transactions: Dict[str, WalletTransaction] = {}
        self._wallet_transactions: Dict[str, List[str]] = {}  # wallet_id -> transaction_ids
    
    def create_wallet(
        self,
        agent_id: str,
        initial_balance: float = 0.0,
        daily_limit: float = 100.0,
        transaction_limit: float = 50.0,
        monthly_limit: float = 1000.0,
    ) -> AgentWallet:
        """Create a wallet for an agent."""
        if agent_id in self._agent_wallets:
            raise ValueError(f"Agent {agent_id} already has a wallet")
        
        wallet = AgentWallet(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            balance=initial_balance,
            daily_limit=daily_limit,
            transaction_limit=transaction_limit,
            monthly_limit=monthly_limit,
        )
        
        self._wallets[wallet.id] = wallet
        self._agent_wallets[agent_id] = wallet.id
        self._wallet_transactions[wallet.id] = []
        
        logger.info(f"Created wallet {wallet.id} for agent {agent_id}")
        return wallet
    
    def get_wallet(self, wallet_id: str) -> Optional[AgentWallet]:
        """Get a wallet by ID."""
        return self._wallets.get(wallet_id)
    
    def get_wallet_by_agent(self, agent_id: str) -> Optional[AgentWallet]:
        """Get a wallet by agent ID."""
        wallet_id = self._agent_wallets.get(agent_id)
        if wallet_id:
            return self._wallets.get(wallet_id)
        return None
    
    def get_or_create_wallet(self, agent_id: str) -> AgentWallet:
        """Get existing wallet or create new one."""
        wallet = self.get_wallet_by_agent(agent_id)
        if not wallet:
            wallet = self.create_wallet(agent_id)
        return wallet
    
    async def spend(self, request: SpendRequest) -> SpendResult:
        """
        Spend from a wallet.
        
        UNBOUNDED: Always allowed
        GOVERNED: Check limits, may require approval
        """
        wallet = self._wallets.get(request.wallet_id)
        if not wallet:
            return SpendResult(success=False, error="Wallet not found")
        
        if not wallet.is_active or wallet.is_frozen:
            return SpendResult(
                success=False, 
                error=f"Wallet is {'frozen' if wallet.is_frozen else 'inactive'}: {wallet.frozen_reason or ''}"
            )
        
        # Check balance
        if wallet.balance < request.amount:
            return SpendResult(success=False, error="Insufficient balance")
        
        # Reset limits if needed
        self._maybe_reset_limits(wallet)
        
        # Get mode and config
        mode = self.mode_manager.get_mode(wallet.agent_id)
        config = self.mode_manager.get_config(wallet.agent_id)
        
        # GOVERNED mode: Check limits
        if mode == AutonomyMode.GOVERNED:
            # Check transaction limit (use minimum of wallet limit and mode config limit)
            effective_transaction_limit = min(wallet.transaction_limit, config.transaction_limit)
            if request.amount > effective_transaction_limit:
                # Requires approval
                transaction = self._create_transaction(
                    wallet, request, TransactionStatus.PENDING_APPROVAL
                )
                transaction.requires_approval = True
                
                # Request approval
                if self.approval_callback:
                    approval_id = await self.approval_callback(
                        agent_id=wallet.agent_id,
                        action="wallet_spend",
                        amount=request.amount,
                        description=request.description,
                    )
                    transaction.approval_id = approval_id
                
                return SpendResult(
                    success=False,
                    transaction=transaction,
                    requires_approval=True,
                    approval_id=transaction.approval_id,
                    error=f"Transaction ${request.amount} exceeds limit ${effective_transaction_limit}"
                )
            
            # Check daily limit
            if wallet.daily_spent + request.amount > config.max_budget_per_day:
                return SpendResult(
                    success=False,
                    error=f"Daily limit exceeded: ${config.max_budget_per_day}"
                )
            
            # Check monthly limit
            if wallet.monthly_spent + request.amount > config.max_budget_per_month:
                return SpendResult(
                    success=False,
                    error=f"Monthly limit exceeded: ${config.max_budget_per_month}"
                )
        
        # Execute transaction
        transaction = self._create_transaction(wallet, request, TransactionStatus.COMPLETED)
        
        # Update wallet
        wallet.balance -= request.amount
        wallet.daily_spent += request.amount
        wallet.monthly_spent += request.amount
        wallet.total_spent += request.amount
        wallet.updated_at = datetime.utcnow().isoformat()
        
        transaction.completed_at = datetime.utcnow().isoformat()
        
        logger.info(
            f"Wallet {wallet.id} spent ${request.amount}: {request.description}"
        )
        
        return SpendResult(success=True, transaction=transaction)
    
    async def transfer(
        self,
        from_wallet_id: str,
        to_wallet_id: str,
        amount: float,
        description: str,
    ) -> SpendResult:
        """
        Transfer between agent wallets.
        
        UNBOUNDED: Always allowed
        GOVERNED: Check approved recipients, limits
        """
        from_wallet = self._wallets.get(from_wallet_id)
        to_wallet = self._wallets.get(to_wallet_id)
        
        if not from_wallet:
            return SpendResult(success=False, error="Source wallet not found")
        if not to_wallet:
            return SpendResult(success=False, error="Destination wallet not found")
        
        # Get mode
        mode = self.mode_manager.get_mode(from_wallet.agent_id)
        
        # GOVERNED: Check recipient restrictions
        if mode == AutonomyMode.GOVERNED:
            if to_wallet_id in from_wallet.blocked_recipients:
                return SpendResult(success=False, error="Recipient is blocked")
            
            if from_wallet.approved_recipients and to_wallet_id not in from_wallet.approved_recipients:
                return SpendResult(success=False, error="Recipient not in approved list")
        
        # Spend from source wallet
        spend_result = await self.spend(SpendRequest(
            wallet_id=from_wallet_id,
            amount=amount,
            description=f"Transfer to {to_wallet.agent_id}: {description}",
            recipient_wallet_id=to_wallet_id,
        ))
        
        if not spend_result.success:
            return spend_result
        
        # Credit destination wallet
        to_wallet.balance += amount
        to_wallet.total_earned += amount
        to_wallet.updated_at = datetime.utcnow().isoformat()
        
        # Create credit transaction for destination
        credit_transaction = WalletTransaction(
            id=str(uuid.uuid4()),
            wallet_id=to_wallet_id,
            type=TransactionType.TRANSFER_IN,
            amount=amount,
            description=f"Transfer from {from_wallet.agent_id}: {description}",
            counterparty_wallet_id=from_wallet_id,
            status=TransactionStatus.COMPLETED,
            completed_at=datetime.utcnow().isoformat(),
        )
        
        self._transactions[credit_transaction.id] = credit_transaction
        self._wallet_transactions[to_wallet_id].append(credit_transaction.id)
        
        logger.info(
            f"Transfer ${amount} from {from_wallet.agent_id} to {to_wallet.agent_id}"
        )
        
        return spend_result
    
    def credit(
        self,
        wallet_id: str,
        amount: float,
        description: str,
        transaction_type: TransactionType = TransactionType.CREDIT,
    ) -> WalletTransaction:
        """Add funds to a wallet."""
        wallet = self._wallets.get(wallet_id)
        if not wallet:
            raise ValueError("Wallet not found")
        
        transaction = WalletTransaction(
            id=str(uuid.uuid4()),
            wallet_id=wallet_id,
            type=transaction_type,
            amount=amount,
            description=description,
            status=TransactionStatus.COMPLETED,
            completed_at=datetime.utcnow().isoformat(),
        )
        
        wallet.balance += amount
        wallet.total_earned += amount
        wallet.updated_at = datetime.utcnow().isoformat()
        
        self._transactions[transaction.id] = transaction
        self._wallet_transactions[wallet_id].append(transaction.id)
        
        logger.info(f"Credited ${amount} to wallet {wallet_id}: {description}")
        return transaction
    
    def approve_transaction(
        self,
        transaction_id: str,
        approver_id: str,
    ) -> SpendResult:
        """Approve a pending transaction."""
        transaction = self._transactions.get(transaction_id)
        if not transaction:
            return SpendResult(success=False, error="Transaction not found")
        
        if transaction.status != TransactionStatus.PENDING_APPROVAL:
            return SpendResult(success=False, error="Transaction not pending approval")
        
        wallet = self._wallets.get(transaction.wallet_id)
        if not wallet:
            return SpendResult(success=False, error="Wallet not found")
        
        # Check balance again
        if wallet.balance < transaction.amount:
            transaction.status = TransactionStatus.FAILED
            return SpendResult(success=False, error="Insufficient balance")
        
        # Execute transaction
        wallet.balance -= transaction.amount
        wallet.daily_spent += transaction.amount
        wallet.monthly_spent += transaction.amount
        wallet.total_spent += transaction.amount
        wallet.updated_at = datetime.utcnow().isoformat()
        
        transaction.status = TransactionStatus.COMPLETED
        transaction.approved_by = approver_id
        transaction.completed_at = datetime.utcnow().isoformat()
        
        logger.info(f"Transaction {transaction_id} approved by {approver_id}")
        return SpendResult(success=True, transaction=transaction)
    
    def reject_transaction(
        self,
        transaction_id: str,
        rejector_id: str,
        reason: str,
    ) -> SpendResult:
        """Reject a pending transaction."""
        transaction = self._transactions.get(transaction_id)
        if not transaction:
            return SpendResult(success=False, error="Transaction not found")
        
        if transaction.status != TransactionStatus.PENDING_APPROVAL:
            return SpendResult(success=False, error="Transaction not pending approval")
        
        transaction.status = TransactionStatus.REJECTED
        transaction.metadata["rejection_reason"] = reason
        transaction.metadata["rejected_by"] = rejector_id
        
        logger.info(f"Transaction {transaction_id} rejected by {rejector_id}: {reason}")
        return SpendResult(success=True, transaction=transaction)
    
    def freeze_wallet(
        self,
        wallet_id: str,
        reason: str,
        frozen_by: str,
    ) -> bool:
        """Freeze a wallet (emergency)."""
        wallet = self._wallets.get(wallet_id)
        if not wallet:
            return False
        
        wallet.is_frozen = True
        wallet.frozen_reason = f"{reason} (by {frozen_by})"
        wallet.updated_at = datetime.utcnow().isoformat()
        
        logger.warning(f"Wallet {wallet_id} frozen: {reason}")
        return True
    
    def unfreeze_wallet(
        self,
        wallet_id: str,
        unfrozen_by: str,
    ) -> bool:
        """Unfreeze a wallet."""
        wallet = self._wallets.get(wallet_id)
        if not wallet:
            return False
        
        wallet.is_frozen = False
        wallet.frozen_reason = None
        wallet.updated_at = datetime.utcnow().isoformat()
        
        logger.info(f"Wallet {wallet_id} unfrozen by {unfrozen_by}")
        return True
    
    def get_transactions(
        self,
        wallet_id: str,
        limit: int = 100,
        status: Optional[TransactionStatus] = None,
    ) -> List[WalletTransaction]:
        """Get transactions for a wallet."""
        transaction_ids = self._wallet_transactions.get(wallet_id, [])
        transactions = [
            self._transactions[tid] 
            for tid in transaction_ids 
            if tid in self._transactions
        ]
        
        if status:
            transactions = [t for t in transactions if t.status == status]
        
        return transactions[-limit:]
    
    def get_balance(self, wallet_id: str) -> float:
        """Get wallet balance."""
        wallet = self._wallets.get(wallet_id)
        return wallet.balance if wallet else 0.0
    
    def get_remaining_daily_budget(self, wallet_id: str) -> float:
        """Get remaining daily budget."""
        wallet = self._wallets.get(wallet_id)
        if not wallet:
            return 0.0
        
        self._maybe_reset_limits(wallet)
        
        mode = self.mode_manager.get_mode(wallet.agent_id)
        if mode == AutonomyMode.UNBOUNDED:
            return float('inf')
        
        config = self.mode_manager.get_config(wallet.agent_id)
        return max(0, config.max_budget_per_day - wallet.daily_spent)
    
    def _create_transaction(
        self,
        wallet: AgentWallet,
        request: SpendRequest,
        status: TransactionStatus,
    ) -> WalletTransaction:
        """Create a transaction record."""
        tx_type = TransactionType.TRANSFER_OUT if request.recipient_wallet_id else TransactionType.DEBIT
        
        transaction = WalletTransaction(
            id=str(uuid.uuid4()),
            wallet_id=wallet.id,
            type=tx_type,
            amount=request.amount,
            description=request.description,
            counterparty_wallet_id=request.recipient_wallet_id,
            status=status,
            metadata=request.metadata,
        )
        
        self._transactions[transaction.id] = transaction
        self._wallet_transactions[wallet.id].append(transaction.id)
        
        return transaction
    
    def _maybe_reset_limits(self, wallet: AgentWallet):
        """Reset daily/monthly limits if needed."""
        today = date.today().isoformat()
        this_month = date.today().replace(day=1).isoformat()
        
        if wallet.last_daily_reset != today:
            wallet.daily_spent = 0.0
            wallet.last_daily_reset = today
            logger.debug(f"Reset daily limit for wallet {wallet.id}")
        
        if wallet.last_monthly_reset != this_month:
            wallet.monthly_spent = 0.0
            wallet.last_monthly_reset = this_month
            logger.debug(f"Reset monthly limit for wallet {wallet.id}")


# Global instance
agent_wallet_system = AgentWalletSystem()


def get_agent_wallet_system() -> AgentWalletSystem:
    """Get the global agent wallet system."""
    return agent_wallet_system
