"""
Agent Wallet Integration
========================

Real blockchain wallet management for autonomous agents.
Enables agents to:
- Hold and manage cryptocurrency
- Make payments for services
- Receive payments for work
- Stake tokens for governance
- Interact with smart contracts
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum
from decimal import Decimal
import hashlib
import secrets

import httpx

logger = logging.getLogger(__name__)


class WalletChain(str, Enum):
    """Supported blockchain networks."""
    ETHEREUM = "ethereum"
    POLYGON = "polygon"
    BASE = "base"
    SOLANA = "solana"
    RESONANT = "resonant"  # Native chain


class TransactionType(str, Enum):
    """Types of wallet transactions."""
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    PAYMENT = "payment"
    RECEIPT = "receipt"
    STAKE = "stake"
    UNSTAKE = "unstake"
    REWARD = "reward"
    FEE = "fee"


@dataclass
class WalletTransaction:
    """A wallet transaction record."""
    tx_id: str
    tx_type: TransactionType
    chain: WalletChain
    amount: Decimal
    currency: str
    from_address: Optional[str]
    to_address: Optional[str]
    status: str  # pending, confirmed, failed
    tx_hash: Optional[str] = None
    block_number: Optional[int] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "tx_id": self.tx_id,
            "type": self.tx_type.value,
            "chain": self.chain.value,
            "amount": str(self.amount),
            "currency": self.currency,
            "from": self.from_address,
            "to": self.to_address,
            "status": self.status,
            "tx_hash": self.tx_hash,
            "block_number": self.block_number,
            "timestamp": self.timestamp,
        }


@dataclass
class AgentWallet:
    """An agent's cryptocurrency wallet."""
    agent_id: str
    wallet_id: str
    addresses: Dict[str, str]  # chain -> address
    balances: Dict[str, Decimal]  # currency -> balance
    staked: Dict[str, Decimal]  # currency -> staked amount
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def get_balance(self, currency: str = "RGT") -> Decimal:
        return self.balances.get(currency, Decimal(0))
    
    def get_address(self, chain: WalletChain) -> Optional[str]:
        return self.addresses.get(chain.value)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "wallet_id": self.wallet_id,
            "addresses": self.addresses,
            "balances": {k: str(v) for k, v in self.balances.items()},
            "staked": {k: str(v) for k, v in self.staked.items()},
            "created_at": self.created_at,
        }


class WalletManager:
    """
    Manages agent wallets and blockchain interactions.
    """
    
    def __init__(self, blockchain_service_url: str = None):
        self.blockchain_service_url = blockchain_service_url or "http://blockchain_service:8006"
        self.wallets: Dict[str, AgentWallet] = {}
        self.transactions: Dict[str, List[WalletTransaction]] = {}
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client
    
    async def close(self):
        if self._client:
            await self._client.aclose()
    
    # ============== Wallet Creation ==============
    
    async def create_wallet(self, agent_id: str, chains: List[WalletChain] = None) -> AgentWallet:
        """Create a new wallet for an agent."""
        if agent_id in self.wallets:
            return self.wallets[agent_id]
        
        chains = chains or [WalletChain.RESONANT, WalletChain.POLYGON]
        wallet_id = self._generate_wallet_id(agent_id)
        
        # Generate addresses for each chain
        addresses = {}
        for chain in chains:
            address = await self._generate_address(chain, agent_id)
            addresses[chain.value] = address
        
        wallet = AgentWallet(
            agent_id=agent_id,
            wallet_id=wallet_id,
            addresses=addresses,
            balances={"RGT": Decimal(0), "MATIC": Decimal(0)},
            staked={"RGT": Decimal(0)},
        )
        
        self.wallets[agent_id] = wallet
        self.transactions[agent_id] = []
        
        # Persist to DB
        await self._persist_wallet(wallet)
        
        logger.info(f"Created wallet for agent {agent_id}: {wallet_id}")
        return wallet
    
    async def _generate_address(self, chain: WalletChain, agent_id: str) -> str:
        """Generate a wallet address for a chain."""
        # In production, this would use proper key derivation
        # For now, generate a deterministic address
        seed = f"{agent_id}:{chain.value}:{secrets.token_hex(16)}"
        address_hash = hashlib.sha256(seed.encode()).hexdigest()
        
        if chain in [WalletChain.ETHEREUM, WalletChain.POLYGON, WalletChain.BASE]:
            return f"0x{address_hash[:40]}"
        elif chain == WalletChain.SOLANA:
            return address_hash[:44]
        else:  # Resonant native
            return f"rgt_{address_hash[:32]}"
    
    def _generate_wallet_id(self, agent_id: str) -> str:
        """Generate a unique wallet ID."""
        return f"wallet_{hashlib.md5(agent_id.encode()).hexdigest()[:16]}"
    
    # ============== Balance Operations ==============
    
    async def get_balance(self, agent_id: str, currency: str = "RGT") -> Decimal:
        """Get wallet balance."""
        wallet = self.wallets.get(agent_id)
        if not wallet:
            wallet = await self._load_wallet_from_db(agent_id)
        if not wallet:
            return Decimal(0)
        return wallet.get_balance(currency)
    
    async def get_all_balances(self, agent_id: str) -> Dict[str, str]:
        """Get all balances for an agent."""
        wallet = self.wallets.get(agent_id)
        if not wallet:
            return {}
        return {k: str(v) for k, v in wallet.balances.items()}
    
    async def deposit(
        self,
        agent_id: str,
        amount: Decimal,
        currency: str = "RGT",
        source: str = "platform",
    ) -> WalletTransaction:
        """Deposit funds into agent wallet."""
        wallet = self.wallets.get(agent_id)
        if not wallet:
            wallet = await self.create_wallet(agent_id)
        
        # Update balance
        if currency not in wallet.balances:
            wallet.balances[currency] = Decimal(0)
        wallet.balances[currency] += amount
        
        # Record transaction
        tx = WalletTransaction(
            tx_id=f"tx_{secrets.token_hex(8)}",
            tx_type=TransactionType.DEPOSIT,
            chain=WalletChain.RESONANT,
            amount=amount,
            currency=currency,
            from_address=source,
            to_address=wallet.addresses.get("resonant"),
            status="confirmed",
            metadata={"source": source},
        )
        
        self.transactions[agent_id].append(tx)
        await self._persist_wallet(wallet)
        logger.info(f"Deposited {amount} {currency} to agent {agent_id}")
        
        return tx
    
    async def withdraw(
        self,
        agent_id: str,
        amount: Decimal,
        currency: str = "RGT",
        to_address: str = None,
    ) -> Optional[WalletTransaction]:
        """Withdraw funds from agent wallet."""
        wallet = self.wallets.get(agent_id)
        if not wallet:
            return None
        
        current_balance = wallet.get_balance(currency)
        if current_balance < amount:
            logger.warning(f"Insufficient balance for agent {agent_id}")
            return None
        
        # Update balance
        wallet.balances[currency] -= amount
        
        # Record transaction
        tx = WalletTransaction(
            tx_id=f"tx_{secrets.token_hex(8)}",
            tx_type=TransactionType.WITHDRAWAL,
            chain=WalletChain.RESONANT,
            amount=amount,
            currency=currency,
            from_address=wallet.addresses.get("resonant"),
            to_address=to_address or "platform",
            status="confirmed",
        )
        
        self.transactions[agent_id].append(tx)
        await self._persist_wallet(wallet)
        logger.info(f"Withdrew {amount} {currency} from agent {agent_id}")
        
        return tx
    
    # ============== Spend Operations (BINDING ECONOMIC CONSTRAINT) ==============
    
    async def spend(
        self,
        agent_id: str,
        amount: Decimal,
        purpose: str,
        currency: str = "RGT",
    ) -> Optional[WalletTransaction]:
        """
        Spend funds for an action - THIS IS THE BINDING ECONOMIC CONSTRAINT.
        
        Unlike balance checks, this actually deducts funds.
        Actions cannot proceed without successful spend.
        """
        wallet = self.wallets.get(agent_id)
        if not wallet:
            logger.warning(f"Wallet not found for spend: {agent_id}")
            return None
        
        current_balance = wallet.get_balance(currency)
        if current_balance < amount:
            logger.warning(f"Insufficient funds for {agent_id}: need {amount}, have {current_balance}")
            return None
        
        # DEDUCT FUNDS - This is the binding constraint
        wallet.balances[currency] -= amount
        
        # Record transaction
        tx = WalletTransaction(
            tx_id=f"tx_{secrets.token_hex(8)}",
            tx_type=TransactionType.FEE,
            chain=WalletChain.RESONANT,
            amount=amount,
            currency=currency,
            from_address=wallet.addresses.get("resonant"),
            to_address="platform_treasury",
            status="confirmed",
            metadata={"purpose": purpose, "action_cost": True},
        )
        
        self.transactions[agent_id].append(tx)
        await self._persist_wallet(wallet)
        logger.info(f"Agent {agent_id} spent {amount} {currency} for: {purpose}")
        
        return tx
    
    # ============== Payment Operations ==============
    
    async def make_payment(
        self,
        from_agent_id: str,
        to_agent_id: str,
        amount: Decimal,
        currency: str = "RGT",
        reason: str = "service_payment",
    ) -> Optional[WalletTransaction]:
        """Make a payment from one agent to another."""
        from_wallet = self.wallets.get(from_agent_id)
        to_wallet = self.wallets.get(to_agent_id)
        
        if not from_wallet:
            logger.warning(f"Source wallet not found: {from_agent_id}")
            return None
        
        if not to_wallet:
            to_wallet = await self.create_wallet(to_agent_id)
        
        # Check balance
        if from_wallet.get_balance(currency) < amount:
            logger.warning(f"Insufficient balance for payment from {from_agent_id}")
            return None
        
        # Execute transfer
        from_wallet.balances[currency] -= amount
        if currency not in to_wallet.balances:
            to_wallet.balances[currency] = Decimal(0)
        to_wallet.balances[currency] += amount
        
        # Record transactions
        tx_id = f"tx_{secrets.token_hex(8)}"
        
        payment_tx = WalletTransaction(
            tx_id=tx_id,
            tx_type=TransactionType.PAYMENT,
            chain=WalletChain.RESONANT,
            amount=amount,
            currency=currency,
            from_address=from_wallet.addresses.get("resonant"),
            to_address=to_wallet.addresses.get("resonant"),
            status="confirmed",
            metadata={"reason": reason, "to_agent": to_agent_id},
        )
        
        receipt_tx = WalletTransaction(
            tx_id=f"{tx_id}_receipt",
            tx_type=TransactionType.RECEIPT,
            chain=WalletChain.RESONANT,
            amount=amount,
            currency=currency,
            from_address=from_wallet.addresses.get("resonant"),
            to_address=to_wallet.addresses.get("resonant"),
            status="confirmed",
            metadata={"reason": reason, "from_agent": from_agent_id},
        )
        
        self.transactions[from_agent_id].append(payment_tx)
        self.transactions[to_agent_id].append(receipt_tx)
        
        logger.info(f"Payment: {amount} {currency} from {from_agent_id} to {to_agent_id}")
        return payment_tx
    
    # ============== Staking Operations ==============
    
    async def stake(
        self,
        agent_id: str,
        amount: Decimal,
        currency: str = "RGT",
    ) -> Optional[WalletTransaction]:
        """Stake tokens for governance/rewards."""
        wallet = self.wallets.get(agent_id)
        if not wallet:
            return None
        
        if wallet.get_balance(currency) < amount:
            return None
        
        # Move from balance to staked
        wallet.balances[currency] -= amount
        if currency not in wallet.staked:
            wallet.staked[currency] = Decimal(0)
        wallet.staked[currency] += amount
        
        tx = WalletTransaction(
            tx_id=f"tx_{secrets.token_hex(8)}",
            tx_type=TransactionType.STAKE,
            chain=WalletChain.RESONANT,
            amount=amount,
            currency=currency,
            from_address=wallet.addresses.get("resonant"),
            to_address="staking_contract",
            status="confirmed",
        )
        
        self.transactions[agent_id].append(tx)
        logger.info(f"Staked {amount} {currency} for agent {agent_id}")
        
        return tx
    
    async def unstake(
        self,
        agent_id: str,
        amount: Decimal,
        currency: str = "RGT",
    ) -> Optional[WalletTransaction]:
        """Unstake tokens."""
        wallet = self.wallets.get(agent_id)
        if not wallet:
            return None
        
        staked_amount = wallet.staked.get(currency, Decimal(0))
        if staked_amount < amount:
            return None
        
        # Move from staked to balance
        wallet.staked[currency] -= amount
        wallet.balances[currency] += amount
        
        tx = WalletTransaction(
            tx_id=f"tx_{secrets.token_hex(8)}",
            tx_type=TransactionType.UNSTAKE,
            chain=WalletChain.RESONANT,
            amount=amount,
            currency=currency,
            from_address="staking_contract",
            to_address=wallet.addresses.get("resonant"),
            status="confirmed",
        )
        
        self.transactions[agent_id].append(tx)
        return tx
    
    # ============== Transaction History ==============
    
    def get_transactions(
        self,
        agent_id: str,
        tx_type: TransactionType = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get transaction history for an agent."""
        txs = self.transactions.get(agent_id, [])
        
        if tx_type:
            txs = [t for t in txs if t.tx_type == tx_type]
        
        return [t.to_dict() for t in txs[-limit:]]
    
    # ============== Wallet Info ==============
    
    def get_wallet(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get wallet info for an agent (sync — checks cache only)."""
        wallet = self.wallets.get(agent_id)
        if not wallet:
            return None
        return wallet.to_dict()

    async def get_wallet_async(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get wallet info, loading from DB if not cached."""
        wallet = self.wallets.get(agent_id)
        if not wallet:
            wallet = await self._load_wallet_from_db(agent_id)
        if not wallet:
            return None
        return wallet.to_dict()
    
    def get_all_wallets(self) -> List[Dict[str, Any]]:
        """Get all wallet infos."""
        return [w.to_dict() for w in self.wallets.values()]

    # ============== DB Persistence (Phase 3.3 gap fix) ==============

    async def _persist_wallet(self, wallet: AgentWallet):
        """Save wallet state to DB (agent_wallets table)."""
        try:
            from .db import async_session
            from sqlalchemy import text
            import json

            async with async_session() as db:
                balances_json = json.dumps({k: str(v) for k, v in wallet.balances.items()})
                staked_json = json.dumps({k: str(v) for k, v in wallet.staked.items()})
                addresses_json = json.dumps(wallet.addresses)

                await db.execute(text("""
                    INSERT INTO agent_wallets (agent_id, wallet_id, addresses, balances, staked)
                    VALUES (:agent_id::uuid, :wallet_id, :addresses::json, :balances::json, :staked::json)
                    ON CONFLICT (agent_id)
                    DO UPDATE SET balances = :balances::json, staked = :staked::json, updated_at = NOW()
                """), {
                    "agent_id": wallet.agent_id,
                    "wallet_id": wallet.wallet_id,
                    "addresses": addresses_json,
                    "balances": balances_json,
                    "staked": staked_json,
                })
                await db.commit()
        except Exception as e:
            if "does not exist" not in str(e).lower():
                logger.warning(f"Failed to persist wallet for {wallet.agent_id}: {e}")

    async def _load_wallet_from_db(self, agent_id: str) -> Optional[AgentWallet]:
        """Load wallet from DB if not in memory."""
        try:
            from .db import async_session
            from sqlalchemy import text
            import json

            async with async_session() as db:
                result = await db.execute(
                    text("SELECT * FROM agent_wallets WHERE agent_id = :aid"),
                    {"aid": agent_id},
                )
                row = result.mappings().first()
                if not row:
                    return None

                balances_raw = row.get("balances") or {}
                if isinstance(balances_raw, str):
                    balances_raw = json.loads(balances_raw)
                staked_raw = row.get("staked") or {}
                if isinstance(staked_raw, str):
                    staked_raw = json.loads(staked_raw)
                addresses_raw = row.get("addresses") or {}
                if isinstance(addresses_raw, str):
                    addresses_raw = json.loads(addresses_raw)

                wallet = AgentWallet(
                    agent_id=agent_id,
                    wallet_id=row["wallet_id"],
                    addresses=addresses_raw,
                    balances={k: Decimal(str(v)) for k, v in balances_raw.items()},
                    staked={k: Decimal(str(v)) for k, v in staked_raw.items()},
                    created_at=row["created_at"].isoformat() if row.get("created_at") else "",
                )
                self.wallets[agent_id] = wallet
                self.transactions.setdefault(agent_id, [])
                return wallet
        except Exception as e:
            if "does not exist" not in str(e).lower():
                logger.warning(f"Failed to load wallet from DB for {agent_id}: {e}")
            return None


# Singleton instance
_wallet_manager: Optional[WalletManager] = None


def get_wallet_manager() -> WalletManager:
    """Get the singleton wallet manager."""
    global _wallet_manager
    if _wallet_manager is None:
        _wallet_manager = WalletManager()
    return _wallet_manager


async def init_wallet_manager(blockchain_service_url: str = None) -> WalletManager:
    """Initialize the wallet manager."""
    global _wallet_manager
    _wallet_manager = WalletManager(blockchain_service_url)
    return _wallet_manager
