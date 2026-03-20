"""Agent Teams Router - NFT, Marketplace, Rental endpoints."""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import uuid
import asyncio
from sqlalchemy.orm import selectinload

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import AgentTeam, AgentTeamMember, AgentTeamWorkflow, AgentTeamRental, AgentDefinition
from .workflow_executor import execute_workflow_background


router = APIRouter(prefix="/agent-teams", tags=["agent-teams"])


# ============================================
# Request/Response Models
# ============================================

class TeamCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    agent_ids: List[str]
    workflow_config: Optional[Dict[str, Any]] = None


class TeamResponse(BaseModel):
    id: str
    org_id: Optional[str]
    name: str
    description: Optional[str]
    workflow_config: Optional[Dict[str, Any]]
    created_by: str
    status: str
    created_at: str
    updated_at: Optional[str]
    member_count: int
    metadata: Dict[str, Any] = {}


class TeamOwnershipResponse(BaseModel):
    team_id: str
    owner_id: str
    owner_type: str  # user, organization
    is_nft: bool
    nft_token_id: Optional[str]
    nft_contract_address: Optional[str]
    owner_wallet_address: Optional[str]
    is_rentable: bool
    rental_price_per_day: Optional[float]
    current_renter_id: Optional[str]
    rental_expires_at: Optional[str]


class TransferOwnershipRequest(BaseModel):
    new_owner_id: Optional[str] = None
    new_owner_wallet: Optional[str] = None
    transfer_type: str = "full"  # full, license
    price: Optional[float] = None
    license_duration_days: Optional[int] = None


class MintNFTRequest(BaseModel):
    chain: str = "ethereum"  # ethereum, polygon, solana, base
    listing_price: Optional[float] = None
    rent_price_per_day: Optional[float] = None
    allow_rentals: bool = False


class MintNFTResponse(BaseModel):
    success: bool
    token_id: str
    contract_address: str
    tx_hash: str
    message: str


class RentTeamRequest(BaseModel):
    rental_days: int
    max_usage: Optional[int] = None


class RentalResponse(BaseModel):
    rental_id: str
    team_id: str
    renter_id: str
    renter_name: Optional[str] = None
    start_date: str
    end_date: str
    daily_rate: float
    total_cost: float
    status: str
    usage_count: int = 0
    max_usage: Optional[int] = None


class MarketplaceListingResponse(BaseModel):
    team_id: str
    name: str
    description: Optional[str]
    owner_id: str
    is_nft: bool
    listing_price: Optional[float]
    rent_price_per_day: Optional[float]
    rating: Optional[float]
    total_rentals: int
    member_count: int


def _get_user_id(request: Request) -> Optional[str]:
    """Extract user_id from request headers."""
    return request.headers.get("x-user-id")


def _get_org_id(request: Request) -> Optional[str]:
    """Extract org_id from request headers."""
    return request.headers.get("x-org-id")


# ============================================
# Basic Team CRUD
# ============================================

@router.post("", response_model=TeamResponse, status_code=201)
async def create_team(
    payload: TeamCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create a new agent team."""
    user_id = _get_user_id(request)
    org_id = _get_org_id(request)
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    team = AgentTeam(
        user_id=user_id,
        org_id=org_id,
        name=payload.name,
        description=payload.description,
        config=payload.workflow_config,
        member_agent_ids=[uuid.UUID(aid) for aid in payload.agent_ids] if payload.agent_ids else [],
    )
    session.add(team)
    await session.commit()
    await session.refresh(team)
    
    return TeamResponse(
        id=str(team.id),
        org_id=str(team.org_id) if team.org_id else None,
        name=team.name,
        description=team.description,
        workflow_config=team.config,
        created_by=str(team.user_id),
        status=team.status,
        created_at=team.created_at.isoformat(),
        updated_at=team.updated_at.isoformat() if team.updated_at else None,
        member_count=len(team.member_agent_ids) if team.member_agent_ids else 0,
    )


@router.get("", response_model=List[TeamResponse])
async def list_teams(
    request: Request,
    status_filter: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """List teams for the organization."""
    user_id = _get_user_id(request)
    org_id = _get_org_id(request)
    
    stmt = select(AgentTeam)
    if org_id:
        stmt = stmt.where(AgentTeam.org_id == org_id)
    elif user_id:
        stmt = stmt.where(AgentTeam.user_id == user_id)
    
    if status_filter:
        stmt = stmt.where(AgentTeam.status == status_filter)
    
    result = await session.execute(stmt)
    teams = result.scalars().all()
    
    return [
        TeamResponse(
            id=str(t.id),
            org_id=str(t.org_id) if t.org_id else None,
            name=t.name,
            description=t.description,
            workflow_config=t.config,
            created_by=str(t.user_id),
            status=t.status,
            created_at=t.created_at.isoformat(),
            updated_at=t.updated_at.isoformat() if t.updated_at else None,
            member_count=len(t.member_agent_ids) if t.member_agent_ids else 0,
        )
        for t in teams
    ]


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get team by ID."""
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == team_id)
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    return TeamResponse(
        id=str(team.id),
        org_id=str(team.org_id) if team.org_id else None,
        name=team.name,
        description=team.description,
        workflow_config=team.config,
        created_by=str(team.user_id),
        status=team.status,
        created_at=team.created_at.isoformat(),
        updated_at=team.updated_at.isoformat() if team.updated_at else None,
        member_count=len(team.member_agent_ids) if team.member_agent_ids else 0,
    )


@router.delete("/{team_id}")
async def delete_team(
    team_id: str,
    request: Request,
    cancel_workflows: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """Delete (archive) a team."""
    user_id = _get_user_id(request)
    
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == team_id)
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if str(team.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this team")
    
    # Check for active workflows
    if not cancel_workflows:
        workflows_result = await session.execute(
            select(AgentTeamWorkflow).where(
                AgentTeamWorkflow.team_id == team_id,
                AgentTeamWorkflow.status.in_(["pending", "running"])
            )
        )
        active_workflows = workflows_result.scalars().all()
        if active_workflows:
            raise HTTPException(
                status_code=400,
                detail=f"Team has {len(active_workflows)} active workflow(s). Cancel them first or use cancel_workflows=true"
            )
    else:
        # Cancel all active workflows
        workflows_result = await session.execute(
            select(AgentTeamWorkflow).where(
                AgentTeamWorkflow.team_id == team_id,
                AgentTeamWorkflow.status.in_(["pending", "running"])
            )
        )
        active_workflows = workflows_result.scalars().all()
        for wf in active_workflows:
            wf.status = "cancelled"
            wf.completed_at = datetime.utcnow()
    
    team.status = "archived"
    team.archived_at = datetime.utcnow()
    await session.commit()
    
    return {"status": "archived", "team_id": team_id}


@router.patch("/{team_id}/archive")
async def archive_team(
    team_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Archive a team."""
    user_id = _get_user_id(request)
    
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == team_id)
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if str(team.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to archive this team")
    
    team.status = "archived"
    team.archived_at = datetime.utcnow()
    await session.commit()
    await session.refresh(team)
    
    return TeamResponse(
        id=str(team.id),
        org_id=str(team.org_id) if team.org_id else None,
        name=team.name,
        description=team.description,
        workflow_config=team.config,
        created_by=str(team.user_id),
        status=team.status,
        created_at=team.created_at.isoformat(),
        updated_at=team.updated_at.isoformat() if team.updated_at else None,
        member_count=len(team.member_agent_ids) if team.member_agent_ids else 0,
    )


@router.patch("/{team_id}/unarchive")
async def unarchive_team(
    team_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Unarchive a team."""
    user_id = _get_user_id(request)
    
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == team_id)
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if str(team.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to unarchive this team")
    
    if team.status != "archived":
        raise HTTPException(status_code=400, detail="Team is not archived")
    
    team.status = "active"
    team.archived_at = None
    await session.commit()
    await session.refresh(team)
    
    return TeamResponse(
        id=str(team.id),
        org_id=str(team.org_id) if team.org_id else None,
        name=team.name,
        description=team.description,
        workflow_config=team.config,
        created_by=str(team.user_id),
        status=team.status,
        created_at=team.created_at.isoformat(),
        updated_at=team.updated_at.isoformat() if team.updated_at else None,
        member_count=len(team.member_agent_ids) if team.member_agent_ids else 0,
    )


# ============================================
# Team Members Endpoints
# ============================================

@router.get("/{team_id}/members")
async def get_team_members(
    team_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get members of a team."""
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == team_id)
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    # Return member agent IDs from the team
    members = []
    if team.member_agent_ids:
        for idx, agent_id in enumerate(team.member_agent_ids):
            members.append({
                "id": str(uuid.uuid4()),
                "team_id": team_id,
                "agent_id": str(agent_id),
                "role": "member",
                "order": idx,
                "config": {}
            })
    
    return members


# ============================================
# Workflow Execution Endpoints
# ============================================

@router.get("/{team_id}/workflows")
async def get_team_workflows(
    team_id: str,
    status_filter: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """Get workflows for a team."""
    stmt = select(AgentTeamWorkflow).where(AgentTeamWorkflow.team_id == team_id)
    
    if status_filter:
        stmt = stmt.where(AgentTeamWorkflow.status == status_filter)
    
    result = await session.execute(stmt)
    workflows = result.scalars().all()
    
    return [
        {
            "id": str(wf.id),
            "team_id": str(wf.team_id),
            "user_id": str(wf.user_id) if wf.user_id else None,
            "project_id": None,
            "input_data": wf.context or {},
            "status": wf.status,
            "current_step": 0,
            "result": wf.final_output,
            "error": wf.error_message,
            "created_at": wf.created_at.isoformat(),
            "updated_at": wf.created_at.isoformat(),
        }
        for wf in workflows
    ]


@router.post("/{team_id}/execute")
async def execute_team_workflow(
    team_id: str,
    request: Request,
    input_data: Dict[str, Any],
    project_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """Execute a workflow with this team."""
    user_id = _get_user_id(request)
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == team_id)
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    # Create workflow execution record
    workflow = AgentTeamWorkflow(
        team_id=team.id,
        user_id=user_id,
        goal=input_data.get("goal", "Execute team workflow"),
        context=input_data,
        status="pending",
        started_at=datetime.utcnow(),
    )
    session.add(workflow)
    await session.commit()
    await session.refresh(workflow)
    
    # Trigger workflow execution asynchronously
    import asyncio
    asyncio.create_task(execute_workflow_background(str(workflow.id), team, input_data))
    
    return {
        "id": str(workflow.id),
        "team_id": str(team.id),
        "user_id": user_id,
        "project_id": project_id,
        "input_data": input_data,
        "status": "pending",
        "current_step": 0,
        "result": None,
        "error": None,
        "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.created_at.isoformat(),
    }


@router.get("/workflows/{workflow_id}")
async def get_workflow_status(
    workflow_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get workflow execution status."""
    result = await session.execute(
        select(AgentTeamWorkflow).where(AgentTeamWorkflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()
    
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    return {
        "id": str(workflow.id),
        "status": workflow.status,
        "current_step": 0,
        "total_steps": workflow.total_steps,
        "steps": [],
        "result": workflow.final_output,
        "error": workflow.error_message,
    }


@router.get("/workflows/{workflow_id}/conversation")
async def get_workflow_conversation(
    workflow_id: str,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    """Get conversation history for a workflow."""
    result = await session.execute(
        select(AgentTeamWorkflow).where(AgentTeamWorkflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()
    
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Get conversation from workflow context if available
    conversation = workflow.context.get("conversation", []) if workflow.context else []
    
    return [
        {
            "id": str(uuid.uuid4()),
            "from_agent_id": msg.get("from", "system"),
            "to_agent_id": msg.get("to", "all"),
            "message": msg.get("content", ""),
            "message_type": msg.get("type", "text"),
            "metadata": msg.get("metadata", {}),
            "created_at": msg.get("timestamp", workflow.created_at.isoformat()),
        }
        for msg in conversation[:limit]
    ]


@router.post("/workflows/{workflow_id}/cancel")
async def cancel_workflow(
    workflow_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Cancel a running workflow."""
    user_id = _get_user_id(request)
    
    result = await session.execute(
        select(AgentTeamWorkflow).where(AgentTeamWorkflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()
    
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    if str(workflow.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to cancel this workflow")
    
    if workflow.status in ["completed", "failed", "cancelled"]:
        raise HTTPException(status_code=400, detail=f"Workflow already {workflow.status}")
    
    workflow.status = "cancelled"
    workflow.completed_at = datetime.utcnow()
    await session.commit()
    await session.refresh(workflow)
    
    return {
        "id": str(workflow.id),
        "team_id": str(workflow.team_id),
        "user_id": str(workflow.user_id) if workflow.user_id else None,
        "project_id": None,
        "input_data": workflow.context or {},
        "status": workflow.status,
        "current_step": 0,
        "result": workflow.final_output,
        "error": workflow.error_message,
        "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.created_at.isoformat(),
    }


# ============================================
# NFT / Ownership Endpoints
# ============================================

@router.get("/{team_id}/ownership", response_model=TeamOwnershipResponse)
async def get_team_ownership(
    team_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get ownership details for a team."""
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == team_id)
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    return TeamOwnershipResponse(
        team_id=str(team.id),
        owner_id=str(team.user_id),
        owner_type="organization" if team.org_id else "user",
        is_nft=bool(team.nft_token_id),
        nft_token_id=team.nft_token_id,
        nft_contract_address=team.nft_contract_address,
        owner_wallet_address=team.owner_address,
        is_rentable=team.is_rentable,
        rental_price_per_day=team.rental_price_per_hour * 24 if team.rental_price_per_hour else None,
        current_renter_id=str(team.current_renter_id) if team.current_renter_id else None,
        rental_expires_at=team.rental_expires_at.isoformat() if team.rental_expires_at else None,
    )


@router.post("/{team_id}/mint-nft", response_model=MintNFTResponse)
async def mint_team_as_nft(
    team_id: str,
    payload: MintNFTRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Mint team as an NFT on the specified blockchain."""
    user_id = _get_user_id(request)
    
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == team_id)
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if str(team.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to mint this team")
    
    if team.nft_token_id:
        raise HTTPException(status_code=400, detail="Team is already minted as NFT")
    
    # Generate mock NFT data (in production, this would interact with blockchain)
    token_id = f"agent-team-{team_id[:8]}-{uuid.uuid4().hex[:8]}"
    contract_address = f"0x{''.join(['ab' for _ in range(20)])}"  # Mock address
    tx_hash = f"0x{''.join(['cd' for _ in range(32)])}"  # Mock tx hash
    
    # Update team with NFT info
    team.nft_token_id = token_id
    team.nft_contract_address = contract_address
    team.is_rentable = payload.allow_rentals
    if payload.rent_price_per_day:
        team.rental_price_per_hour = payload.rent_price_per_day / 24
    
    await session.commit()
    
    return MintNFTResponse(
        success=True,
        token_id=token_id,
        contract_address=contract_address,
        tx_hash=tx_hash,
        message=f"Team successfully minted as NFT on {payload.chain}",
    )


@router.post("/{team_id}/transfer")
async def transfer_team_ownership(
    team_id: str,
    payload: TransferOwnershipRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Transfer team ownership to another user."""
    user_id = _get_user_id(request)
    
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == team_id)
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if str(team.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to transfer this team")
    
    if payload.transfer_type == "full":
        if payload.new_owner_id:
            team.user_id = payload.new_owner_id
        if payload.new_owner_wallet:
            team.owner_address = payload.new_owner_wallet
        
        await session.commit()
        
        return {
            "success": True,
            "transaction_id": str(uuid.uuid4()),
            "message": "Ownership transferred successfully",
        }
    elif payload.transfer_type == "license":
        # Create a rental/license instead of full transfer
        if not payload.license_duration_days:
            raise HTTPException(status_code=400, detail="License duration required")
        
        rental = AgentTeamRental(
            team_id=team.id,
            renter_id=payload.new_owner_id,
            owner_id=team.user_id,
            price_per_hour=payload.price / (payload.license_duration_days * 24) if payload.price else 0,
            total_hours=payload.license_duration_days * 24,
            total_price=payload.price or 0,
            expires_at=datetime.utcnow() + timedelta(days=payload.license_duration_days),
        )
        session.add(rental)
        await session.commit()
        
        return {
            "success": True,
            "transaction_id": str(rental.id),
            "message": f"License granted for {payload.license_duration_days} days",
        }
    
    raise HTTPException(status_code=400, detail="Invalid transfer type")


# ============================================
# Rental Endpoints
# ============================================

@router.post("/{team_id}/rent", response_model=RentalResponse)
async def rent_team(
    team_id: str,
    payload: RentTeamRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Rent a team for a specified duration."""
    user_id = _get_user_id(request)
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    result = await session.execute(
        select(AgentTeam).where(AgentTeam.id == team_id)
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if not team.is_rentable:
        raise HTTPException(status_code=400, detail="Team is not available for rental")
    
    if team.current_renter_id and team.rental_expires_at and team.rental_expires_at > datetime.utcnow():
        raise HTTPException(status_code=400, detail="Team is currently rented")
    
    daily_rate = (team.rental_price_per_hour or 0) * 24
    total_cost = daily_rate * payload.rental_days
    
    start_date = datetime.utcnow()
    end_date = start_date + timedelta(days=payload.rental_days)
    
    rental = AgentTeamRental(
        team_id=team.id,
        renter_id=user_id,
        owner_id=team.user_id,
        price_per_hour=team.rental_price_per_hour or 0,
        total_hours=payload.rental_days * 24,
        total_price=total_cost,
        expires_at=end_date,
    )
    session.add(rental)
    
    # Update team rental status
    team.current_renter_id = user_id
    team.rental_expires_at = end_date
    
    await session.commit()
    await session.refresh(rental)
    
    return RentalResponse(
        rental_id=str(rental.id),
        team_id=str(team.id),
        renter_id=user_id,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        daily_rate=daily_rate,
        total_cost=total_cost,
        status="active",
        max_usage=payload.max_usage,
    )


@router.get("/{team_id}/rentals", response_model=List[RentalResponse])
async def list_team_rentals(
    team_id: str,
    session: AsyncSession = Depends(get_session),
):
    """List all rentals for a team (for owners)."""
    result = await session.execute(
        select(AgentTeamRental).where(AgentTeamRental.team_id == team_id)
    )
    rentals = result.scalars().all()
    
    return [
        RentalResponse(
            rental_id=str(r.id),
            team_id=str(r.team_id),
            renter_id=str(r.renter_id),
            start_date=r.started_at.isoformat(),
            end_date=r.expires_at.isoformat(),
            daily_rate=(r.price_per_hour or 0) * 24,
            total_cost=r.total_price,
            status=r.status,
        )
        for r in rentals
    ]


@router.get("/my-rentals", response_model=List[RentalResponse])
async def list_my_rentals(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """List teams rented by current user."""
    user_id = _get_user_id(request)
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    result = await session.execute(
        select(AgentTeamRental).where(AgentTeamRental.renter_id == user_id)
    )
    rentals = result.scalars().all()
    
    return [
        RentalResponse(
            rental_id=str(r.id),
            team_id=str(r.team_id),
            renter_id=str(r.renter_id),
            start_date=r.started_at.isoformat(),
            end_date=r.expires_at.isoformat(),
            daily_rate=(r.price_per_hour or 0) * 24,
            total_cost=r.total_price,
            status=r.status,
        )
        for r in rentals
    ]


# ============================================
# Marketplace Endpoints
# ============================================

@router.get("/marketplace", response_model=List[MarketplaceListingResponse])
async def list_marketplace(
    session: AsyncSession = Depends(get_session),
):
    """List teams available in the marketplace."""
    result = await session.execute(
        select(AgentTeam).where(
            AgentTeam.is_public == True,
            AgentTeam.status == "active",
        )
    )
    teams = result.scalars().all()
    
    return [
        MarketplaceListingResponse(
            team_id=str(t.id),
            name=t.name,
            description=t.description,
            owner_id=str(t.user_id),
            is_nft=bool(t.nft_token_id),
            listing_price=None,  # Would come from marketplace listing
            rent_price_per_day=(t.rental_price_per_hour or 0) * 24 if t.is_rentable else None,
            rating=None,
            total_rentals=0,  # Would be calculated from rentals
            member_count=len(t.member_agent_ids) if t.member_agent_ids else 0,
        )
        for t in teams
    ]
