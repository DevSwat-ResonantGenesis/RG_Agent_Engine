"""
Agent Publishing to External Blockchain

Handles publishing agents to Base network for public verification.
"""

import os
import logging
import hashlib
from typing import Optional

logger = logging.getLogger(__name__)


def _normalize_0x_hash(value: str) -> str:
    if not value:
        return value
    return value if value.startswith("0x") else f"0x{value}"


async def publish_agent_to_blockchain(
    agent_id: str,
    agent_name: str,
    agent_description: str,
    manifest_hash: str,
    metadata_uri: Optional[str] = None,
) -> dict:
    """
    Publish agent to external Base network blockchain.
    
    Args:
        agent_id: Agent UUID
        agent_name: Agent name
        agent_description: Agent description
        manifest_hash: Hash of agent manifest
        metadata_uri: IPFS URI for metadata (optional)
    
    Returns:
        {
            "success": bool,
            "tx_hash": str,
            "network": str,
            "error": str (if failed)
        }
    """
    try:
        # Check if Base network is enabled
        if not os.getenv("ENABLE_BASE_NETWORK", "false").lower() == "true":
            logger.info("Base network disabled, skipping blockchain registration")
            return {
                "success": False,
                "error": "Base network not enabled"
            }
        
        # Import chain client
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent.parent.parent))
            from node.src.resonant_node.chain.client import ChainClient
        except ImportError as e:
            logger.error(f"Failed to import ChainClient: {e}")
            return {
                "success": False,
                "error": "Chain client not available"
            }
        
        # Get Base network configuration
        rpc_url = os.getenv("BASE_RPC_URL")
        agent_contract = os.getenv("BASE_AGENT_CONTRACT")
        
        if not rpc_url or not agent_contract:
            logger.error("Base network configuration missing")
            return {
                "success": False,
                "error": "Base network not configured"
            }
        
        # Initialize chain client
        chain_client = ChainClient(
            rpc_url=rpc_url,
            agent_contract=agent_contract
        )

        await chain_client.connect()
        if not chain_client.connected:
            return {
                "success": False,
                "error": "Failed to connect to Base RPC"
            }

        private_key = (
            os.getenv("BASE_PRIVATE_KEY")
            or os.getenv("BASE_AGENT_PRIVATE_KEY")
            or os.getenv("BASE_DEPLOYER_PRIVATE_KEY")
        )

        if not private_key:
            return {
                "success": False,
                "error": "Missing Base deployer private key (set BASE_PRIVATE_KEY / BASE_AGENT_PRIVATE_KEY / BASE_DEPLOYER_PRIVATE_KEY)"
            }
        
        # Generate manifest hash if not provided
        if not manifest_hash:
            manifest_data = f"{agent_id}:{agent_name}:{agent_description}"
            manifest_hash = f"0x{hashlib.sha256(manifest_data.encode()).hexdigest()}"
        else:
            manifest_hash = _normalize_0x_hash(manifest_hash)
        
        # Register agent on Base network
        logger.info(f"Registering agent {agent_id} on Base network...")
        
        tx_hash = await chain_client.register_agent(
            manifest_hash=manifest_hash,
            metadata_uri=metadata_uri or f"ipfs://genesis-agent-{agent_id}",
            private_key=private_key,
        )

        if not tx_hash:
            return {
                "success": False,
                "error": "Chain client returned no tx hash"
            }
        
        logger.info(f"Agent {agent_id} registered on Base network: {tx_hash}")
        
        return {
            "success": True,
            "tx_hash": tx_hash,
            "network": "base-sepolia",
            "contract": agent_contract
        }
        
    except Exception as e:
        logger.error(f"Failed to publish agent to blockchain: {e}")
        return {
            "success": False,
            "error": str(e)
        }


async def publish_agent_team_to_blockchain(
    team_id: str,
    team_name: str,
    agent_hashes: list[str],
    metadata_uri: Optional[str] = None,
) -> dict:
    """
    Publish agent team to external Base network blockchain.
    
    Args:
        team_id: Team UUID
        team_name: Team name
        agent_hashes: List of agent hashes in team
        metadata_uri: IPFS URI for metadata (optional)
    
    Returns:
        {
            "success": bool,
            "tx_hash": str,
            "network": str,
            "error": str (if failed)
        }
    """
    try:
        # Check if Base network is enabled
        if not os.getenv("ENABLE_BASE_NETWORK", "false").lower() == "true":
            logger.info("Base network disabled, skipping team blockchain registration")
            return {
                "success": False,
                "error": "Base network not enabled"
            }
        
        # Import chain client
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent.parent.parent))
            from node.src.resonant_node.chain.client import ChainClient
        except ImportError as e:
            logger.error(f"Failed to import ChainClient: {e}")
            return {
                "success": False,
                "error": "Chain client not available"
            }
        
        # Get Base network configuration
        rpc_url = os.getenv("BASE_RPC_URL")
        agent_contract = os.getenv("BASE_AGENT_CONTRACT")
        
        if not rpc_url or not agent_contract:
            logger.error("Base network configuration missing")
            return {
                "success": False,
                "error": "Base network not configured"
            }
        
        # Initialize chain client
        chain_client = ChainClient(
            rpc_url=rpc_url,
            agent_contract=agent_contract
        )

        await chain_client.connect()
        if not chain_client.connected:
            return {
                "success": False,
                "error": "Failed to connect to Base RPC"
            }

        if not hasattr(chain_client, "register_agent_team"):
            return {
                "success": False,
                "error": "Team publish not supported: chain client has no register_agent_team"
            }
        
        # Generate team manifest hash
        team_data = f"{team_id}:{team_name}:{','.join(agent_hashes)}"
        team_hash = hashlib.sha256(team_data.encode()).hexdigest()
        
        # Register team on Base network
        logger.info(f"Registering agent team {team_id} on Base network...")
        
        tx_hash = await chain_client.register_agent_team(
            team_id=team_id,
            agent_hashes=agent_hashes,
            metadata_uri=metadata_uri or f"ipfs://genesis-team-{team_id}"
        )

        if not tx_hash:
            return {
                "success": False,
                "error": "Chain client returned no tx hash"
            }

        logger.info(f"Agent team {team_id} registered on Base network: {tx_hash}")
        
        return {
            "success": True,
            "tx_hash": tx_hash,
            "network": "base-sepolia",
            "contract": agent_contract
        }
        
    except Exception as e:
        logger.error(f"Failed to publish team to blockchain: {e}")
        return {
            "success": False,
            "error": str(e)
        }
