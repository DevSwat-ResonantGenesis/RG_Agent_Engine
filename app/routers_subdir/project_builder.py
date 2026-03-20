"""Project Builder API Routes
===========================

API endpoints for the Project Builder Agent.

Endpoints:
- POST /project-builder/build - Start a new project build
- GET /project-builder/progress/{project_id} - Get build progress
- POST /project-builder/cancel/{project_id} - Cancel a build
- GET /project-builder/templates - List available templates
- GET /project-builder/projects - List user projects
- GET /project-builder/projects/{project_id} - Get project details
- DELETE /project-builder/projects/{project_id} - Delete project
- POST /project-builder/projects/{project_id}/archive - Archive project
- POST /project-builder/projects/{project_id}/deliver - Create delivery package

Authentication:
- Uses X-Org-ID header for organization identification
- Uses X-User-ID header for user identification
- Integrates with user memory for personalization
"""

import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..project_builder import (
    get_builder_agent,
    get_workspace_manager,
    get_delivery_manager,
    ProjectType,
    BuildResult,
    BuildProgress,
    DeliveryPackage,
    ProjectState,
)
from ..db import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/project-builder", tags=["project-builder"])


# === AUTHENTICATION DEPENDENCIES ===

async def get_current_user(request: Request) -> dict:
    """
    Get current user from headers (set by gateway).
    
    Uses the same pattern as chat_service:
    - x-user-id (lowercase) - Required (defaults to 'anonymous' for dev)
    - x-org-id (lowercase) - Optional, defaults to user_id
    """
    user_id = request.headers.get("x-user-id") or "anonymous"
    org_id = request.headers.get("x-org-id") or user_id
    
    return {
        "user_id": user_id,
        "org_id": org_id,
    }


# === REQUEST/RESPONSE MODELS ===

class BuildRequest(BaseModel):
    """Request to build a new project."""
    project_name: str = Field(..., description="Project name")
    description: str = Field(..., description="Project description")
    project_type: str = Field(
        default="fullstack_react_fastapi",
        description="Project type: fullstack_react_fastapi, frontend_react, backend_fastapi"
    )
    custom_requirements: Optional[List[str]] = Field(
        default=None,
        description="Additional custom requirements"
    )
    initial_budget: float = Field(
        default=10000.0,
        description="Initial budget for the build (credits)"
    )


class BuildResponse(BaseModel):
    """Response from build request."""
    success: bool
    project_id: str
    project_path: Optional[str] = None
    files_created: int = 0
    total_cost: float = 0.0
    build_time_seconds: float = 0.0
    errors: List[str] = []


class ProjectResponse(BaseModel):
    """Response with project details."""
    project_id: str
    name: str
    description: str
    tech_stack: List[str]
    status: str
    files_count: int
    build_cost: float
    created_at: str
    updated_at: str


class ProgressResponse(BaseModel):
    """Response with build progress."""
    phase: str
    files_generated: int
    files_total: int
    progress_percent: float
    current_file: Optional[str] = None
    errors: List[str] = []


@router.post("/build", response_model=BuildResponse)
async def build_project(
    request: BuildRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """
    Start building a new project.
    
    This endpoint initiates an autonomous project build using the Project Builder Agent.
    The build runs in the background and can be monitored via the progress endpoint.
    
    **Authentication Required**: X-User-ID or X-Org-ID header
    
    **Credit Cost**: ~50 credits per file created
    """
    try:
        project_type_map = {
            "fullstack_react_fastapi": ProjectType.FULLSTACK_REACT_FASTAPI,
            "frontend_react": ProjectType.FRONTEND_REACT,
            "backend_fastapi": ProjectType.BACKEND_FASTAPI,
        }
        
        project_type = project_type_map.get(request.project_type)
        if not project_type:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid project type: {request.project_type}. Valid types: fullstack_react_fastapi, frontend_react, backend_fastapi"
            )
        
        builder = await get_builder_agent()
        user_id = current_user["user_id"]
        
        result = await builder.build_project(
            user_id=user_id,
            project_name=request.project_name,
            description=request.description,
            project_type=project_type,
            custom_requirements=request.custom_requirements,
            initial_budget=request.initial_budget,
        )
        
        logger.info(f"Build completed for user {user_id}: {result.project_id}")
        
        return BuildResponse(
            success=result.success,
            project_id=result.project_id,
            project_path=result.project_path,
            files_created=result.files_created,
            total_cost=result.total_cost,
            build_time_seconds=result.build_time_seconds,
            errors=result.errors,
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Build failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/progress/{project_id}", response_model=Optional[ProgressResponse])
async def get_build_progress(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get progress of an active build.
    
    Returns None if no active build is found for the project.
    
    **Authentication Required**: X-User-ID or X-Org-ID header
    """
    builder = await get_builder_agent()
    progress = await builder.get_build_progress(project_id)
    
    if not progress:
        return None
    
    return ProgressResponse(
        phase=progress.phase.value,
        files_generated=progress.files_generated,
        files_total=progress.files_total,
        progress_percent=progress.progress_percent,
        current_file=progress.current_file,
        errors=progress.errors,
    )


@router.post("/cancel/{project_id}")
async def cancel_build(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Cancel an active build.
    
    **Authentication Required**: X-User-ID or X-Org-ID header
    """
    builder = await get_builder_agent()
    cancelled = await builder.cancel_build(project_id)
    
    if not cancelled:
        raise HTTPException(
            status_code=404,
            detail=f"No active build found for project {project_id}"
        )
    
    logger.info(f"Build cancelled by user {current_user['user_id']}: {project_id}")
    return {"message": f"Build cancelled for project {project_id}"}


@router.get("/templates")
async def list_templates(
    current_user: dict = Depends(get_current_user),
):
    """
    List available project templates.
    
    **Authentication Required**: X-User-ID or X-Org-ID header
    
    Returns available templates with their descriptions and tech stacks.
    """
    builder = await get_builder_agent()
    templates = await builder.list_templates()
    return {"templates": templates}


@router.get("/projects")
async def list_projects(
    current_user: dict = Depends(get_current_user),
):
    """
    List all projects for the authenticated user.
    
    **Authentication Required**: X-User-ID or X-Org-ID header
    """
    workspace_manager = get_workspace_manager()
    user_id = current_user["user_id"]
    projects = await workspace_manager.list_projects(user_id)
    
    return {
        "user_id": user_id,
        "total_projects": len(projects),
        "projects": [
            {
                "project_id": p.project_id,
                "name": p.name,
                "description": p.description,
                "tech_stack": p.tech_stack,
                "status": p.status.value if hasattr(p.status, 'value') else p.status,
                "files_count": p.files_count,
                "build_cost": p.build_cost,
                "created_at": p.created_at,
                "updated_at": p.updated_at,
                "project_state": p.project_state.value if hasattr(p.project_state, 'value') else (p.project_state if p.project_state else "generated"),
            }
            for p in projects
        ]
    }


@router.get("/projects/{project_id}", response_model=Optional[ProjectResponse])
async def get_project(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get project details.
    
    **Authentication Required**: X-User-ID or X-Org-ID header
    """
    workspace_manager = get_workspace_manager()
    user_id = current_user["user_id"]
    project = await workspace_manager.get_project(user_id, project_id)
    
    if not project:
        raise HTTPException(
            status_code=404,
            detail=f"Project {project_id} not found"
        )
    
    return ProjectResponse(
        project_id=project.project_id,
        name=project.name,
        description=project.description,
        tech_stack=project.tech_stack,
        status=project.status.value,
        files_count=project.files_count,
        build_cost=project.build_cost,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Delete a project.
    
    **Authentication Required**: X-User-ID or X-Org-ID header
    
    **Warning**: This action is irreversible.
    """
    workspace_manager = get_workspace_manager()
    user_id = current_user["user_id"]
    
    try:
        await workspace_manager.delete_project(user_id, project_id)
        logger.info(f"Project deleted by user {user_id}: {project_id}")
        return {"message": f"Project {project_id} deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/projects/{project_id}/archive")
async def archive_project(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Archive a project to zip file.
    
    **Authentication Required**: X-User-ID or X-Org-ID header
    """
    workspace_manager = get_workspace_manager()
    user_id = current_user["user_id"]
    
    try:
        archive_path = await workspace_manager.archive_project(user_id, project_id)
        logger.info(f"Project archived by user {user_id}: {project_id}")
        return {
            "message": f"Project {project_id} archived",
            "archive_path": archive_path,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/projects/{project_id}/deliver")
async def create_delivery(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Create a delivery package for a project.
    
    **Authentication Required**: X-User-ID or X-Org-ID header
    
    Creates a zip file with:
    - All project files
    - Documentation (SETUP.md, API.md)
    - Analysis report
    - Build statistics
    """
    workspace_manager = get_workspace_manager()
    delivery_manager = get_delivery_manager()
    user_id = current_user["user_id"]
    
    project = await workspace_manager.get_project(user_id, project_id)
    if not project:
        raise HTTPException(
            status_code=404,
            detail=f"Project {project_id} not found"
        )
    
    project_path = await workspace_manager.get_project_path(user_id, project_id)
    if not project_path:
        raise HTTPException(
            status_code=404,
            detail=f"Project path not found for {project_id}"
        )
    
    try:
        package = await delivery_manager.create_delivery_package(
            project_path=project_path,
            project_id=project_id,
            project_name=project.name,
        )
        
        logger.info(f"Delivery created by user {user_id}: {project_id}")
        return package.to_dict()
        
    except Exception as e:
        logger.error(f"Failed to create delivery: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workspace/stats")
async def get_workspace_stats(
    current_user: dict = Depends(get_current_user),
):
    """
    Get workspace statistics for the authenticated user.
    
    **Authentication Required**: X-User-ID or X-Org-ID header
    """
    workspace_manager = get_workspace_manager()
    user_id = current_user["user_id"]
    stats = await workspace_manager.get_workspace_stats(user_id)
    return stats


@router.get("/deliveries")
async def list_deliveries(
    project_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """
    List all delivery packages.
    
    **Authentication Required**: X-User-ID or X-Org-ID header
    """
    delivery_manager = get_delivery_manager()
    deliveries = await delivery_manager.list_deliveries(project_id)
    return {"deliveries": deliveries}


# === FRONTEND COMPATIBILITY ENDPOINT ===

class FrontendProjectRequest(BaseModel):
    """Frontend-compatible project generation request."""
    description: str
    project_type: Optional[str] = "react"
    files: Optional[List[dict]] = None
    context: Optional[dict] = None


class FrontendProjectFile(BaseModel):
    """Frontend-compatible file response."""
    path: str
    content: str
    language: str
    explanation: str


class FrontendProjectResponse(BaseModel):
    """Frontend-compatible project generation response."""
    files: List[FrontendProjectFile]
    project_structure: dict
    setup_instructions: str
    anchors: List[str]


@router.post("/generate", response_model=FrontendProjectResponse)
async def generate_project_frontend_compatible(
    request: FrontendProjectRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """
    Frontend-compatible project generation endpoint.
    
    This endpoint matches the frontend's expected API format at /code/project/generate.
    It wraps the Project Builder agent for compatibility.
    
    **Authentication Required**: x-user-id header
    """
    builder = await get_builder_agent()
    user_id = current_user["user_id"]
    
    # Map frontend project_type to our ProjectType
    type_mapping = {
        "react": ProjectType.FRONTEND_REACT,
        "vue": ProjectType.FRONTEND_VUE,
        "python": ProjectType.BACKEND_FASTAPI,
        "fastapi": ProjectType.BACKEND_FASTAPI,
        "node": ProjectType.BACKEND_EXPRESS,
        "express": ProjectType.BACKEND_EXPRESS,
        "nextjs": ProjectType.FULLSTACK_REACT_FASTAPI,
        "fullstack": ProjectType.FULLSTACK_REACT_FASTAPI,
    }
    
    project_type = type_mapping.get(
        request.project_type or "react",
        ProjectType.FULLSTACK_REACT_FASTAPI
    )
    
    # Generate project name from description with unique suffix
    import uuid
    base_name = request.description[:30].lower().replace(" ", "-").replace("_", "-")
    base_name = "".join(c for c in base_name if c.isalnum() or c == "-")
    # Add unique suffix to avoid conflicts
    project_name = f"{base_name}-{uuid.uuid4().hex[:6]}"
    
    try:
        result = await builder.build_project(
            user_id=user_id,
            project_name=project_name,
            description=request.description,
            project_type=project_type,
            initial_budget=10000.0,
        )
        
        if not result.success:
            raise HTTPException(
                status_code=500,
                detail=f"Project generation failed: {', '.join(result.errors)}"
            )
        
        # Get generated files
        workspace_manager = get_workspace_manager()
        project_path = result.project_path
        
        files = []
        if project_path:
            from pathlib import Path
            project_dir = Path(project_path)
            
            # Language detection by extension
            lang_map = {
                ".py": "python",
                ".js": "javascript",
                ".jsx": "javascript",
                ".ts": "typescript",
                ".tsx": "typescript",
                ".json": "json",
                ".md": "markdown",
                ".html": "html",
                ".css": "css",
                ".yaml": "yaml",
                ".yml": "yaml",
                ".txt": "text",
            }
            
            for file_path in project_dir.rglob("*"):
                if file_path.is_file():
                    try:
                        content = file_path.read_text()
                        rel_path = str(file_path.relative_to(project_dir))
                        ext = file_path.suffix.lower()
                        language = lang_map.get(ext, "text")
                        
                        files.append(FrontendProjectFile(
                            path=rel_path,
                            content=content,
                            language=language,
                            explanation=f"Generated {language} file: {rel_path}",
                        ))
                    except Exception:
                        pass
        
        # Build project structure
        project_structure = {}
        for f in files:
            parts = f.path.split("/")
            current = project_structure
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = f.language
        
        # Generate setup instructions
        setup_instructions = f"""# {project_name}

## Setup Instructions

1. Navigate to the project directory
2. Install dependencies:
   - For frontend: `npm install` or `yarn`
   - For backend: `pip install -r requirements.txt`
3. Start the development server:
   - Frontend: `npm run dev` or `yarn dev`
   - Backend: `uvicorn main:app --reload`

## Project Structure

This project was generated by ResonantGenesis Project Builder.
Total files: {len(files)}
"""
        
        return FrontendProjectResponse(
            files=files,
            project_structure=project_structure,
            setup_instructions=setup_instructions,
            anchors=[result.project_id],
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Frontend project generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# === PROJECT STATE & MODIFICATION ENDPOINTS ===

class PromoteProjectRequest(BaseModel):
    """Request to promote a project to RUNTIME state."""
    reason: Optional[str] = Field(
        default="User requested promotion for modification",
        description="Reason for promotion"
    )


@router.post("/projects/{project_id}/promote")
async def promote_project(
    project_id: str,
    request: PromoteProjectRequest = PromoteProjectRequest(),
    current_user: dict = Depends(get_current_user),
):
    """
    Promote a project from GENERATED to RUNTIME state.
    
    **Authentication Required**: X-User-ID header
    
    GOVERNANCE:
    - Only RUNTIME projects can be modified
    - Promotion creates a snapshot of the current state
    - Promotion binds RARA runtime roots for governed mutations
    - All transitions are logged for audit
    
    State transitions:
    - GENERATED (read-only) → RUNTIME (governed mutations allowed)
    """
    workspace_manager = get_workspace_manager()
    user_id = current_user["user_id"]
    
    try:
        result = await workspace_manager.promote_project(
            user_id=user_id,
            project_id=project_id,
            reason=request.reason,
        )
        
        logger.info(f"Project {project_id} promoted by user {user_id}: {result}")
        return result
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to promote project: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/state")
async def get_project_state(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get the current governance state of a project.
    
    **Authentication Required**: X-User-ID header
    
    Returns:
    - project_state: GENERATED or RUNTIME
    - can_modify: Whether the project can be modified
    - promoted_at: When the project was promoted (if RUNTIME)
    - snapshot_id: ID of the promotion snapshot (if RUNTIME)
    """
    workspace_manager = get_workspace_manager()
    user_id = current_user["user_id"]
    
    project = await workspace_manager.get_project(user_id, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    
    return {
        "project_id": project_id,
        "project_state": project.project_state.value if hasattr(project.project_state, 'value') else project.project_state,
        "can_modify": project.is_mutable(),
        "promoted_at": project.promoted_at,
        "snapshot_id": project.runtime_snapshot_id,
        "transitions_count": len(project.transitions),
    }


class ModifyProjectRequest(BaseModel):
    """Request to modify an existing project."""
    modification_request: str = Field(..., description="Description of changes to make")
    target_files: Optional[List[str]] = Field(None, description="Specific files to modify")


@router.post("/projects/{project_id}/modify")
async def modify_project(
    project_id: str,
    request: ModifyProjectRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Modify an existing project using LLM.
    
    **Authentication Required**: X-User-ID header
    
    Loads existing project files, sends to LLM with modification request,
    and saves the updated files back to the project.
    """
    builder = await get_builder_agent()
    user_id = current_user["user_id"]
    
    try:
        result = await builder.modify_project(
            user_id=user_id,
            project_id=project_id,
            modification_request=request.modification_request,
            target_files=request.target_files,
        )
        
        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Modification failed")
            )
        
        logger.info(f"Project {project_id} modified by user {user_id}: {result.get('total_modified')} files")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Project modification failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/files")
async def get_project_files(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get all files from a project.
    
    **Authentication Required**: X-User-ID header
    
    Returns list of files with path, content, and language.
    """
    builder = await get_builder_agent()
    user_id = current_user["user_id"]
    
    try:
        files = await builder.get_project_files(user_id, project_id)
        
        if not files:
            raise HTTPException(
                status_code=404,
                detail=f"Project {project_id} not found or has no files"
            )
        
        return {
            "project_id": project_id,
            "files": files,
            "total_files": len(files),
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get project files: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# === HEALTH CHECK ===

@router.get("/health")
async def health_check():
    """Health check endpoint (no auth required)."""
    return {
        "status": "healthy",
        "service": "project-builder",
        "version": "1.0.0",
    }
