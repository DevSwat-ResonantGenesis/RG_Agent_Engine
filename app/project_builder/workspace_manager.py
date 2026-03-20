"""
Workspace Manager - User Workspace Isolation
=============================================

Manages isolated workspaces for each user's projects.
Ensures user projects are sandboxed and don't interfere with each other.

Features:
- Create/delete user workspaces
- Project directory management
- Metadata tracking
- Cleanup and archival
"""

import os
import shutil
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from enum import Enum
import hashlib

logger = logging.getLogger(__name__)


class WorkspaceStatus(str, Enum):
    """Status of a workspace."""
    ACTIVE = "active"
    BUILDING = "building"
    COMPLETED = "completed"
    FAILED = "failed"
    ARCHIVED = "archived"


class ProjectState(str, Enum):
    """
    Formal project state for governance.
    
    State transitions:
    - GENERATED: Read-only artifact, just created by builder
    - RUNTIME: Promoted project, governed mutations allowed
    
    Only RUNTIME projects can be modified.
    Promotion creates a snapshot and binds RARA runtime roots.
    """
    GENERATED = "generated"  # Read-only, immutable artifact
    RUNTIME = "runtime"      # Promoted, governed mutations allowed


@dataclass
class StateTransition:
    """Record of a project state transition."""
    transition_id: str
    project_id: str
    from_state: str
    to_state: str
    timestamp: str
    actor: str  # user_id or agent_id
    reason: str
    snapshot_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectMetadata:
    """Metadata for a project within a workspace."""
    project_id: str
    name: str
    description: str
    tech_stack: List[str]
    created_at: str
    updated_at: str
    status: WorkspaceStatus
    files_count: int = 0
    total_size_bytes: int = 0
    build_cost: float = 0.0
    agent_id: Optional[str] = None
    # Formal governance state
    project_state: ProjectState = ProjectState.GENERATED
    promoted_at: Optional[str] = None
    runtime_snapshot_id: Optional[str] = None
    transitions: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # Ensure enums are serialized as strings
        data['status'] = self.status.value if isinstance(self.status, WorkspaceStatus) else self.status
        data['project_state'] = self.project_state.value if isinstance(self.project_state, ProjectState) else self.project_state
        return data
    
    def is_mutable(self) -> bool:
        """Check if project can be modified (must be in RUNTIME state)."""
        return self.project_state == ProjectState.RUNTIME


@dataclass
class UserWorkspace:
    """A user's workspace containing projects."""
    user_id: str
    workspace_path: str
    created_at: str
    projects: Dict[str, ProjectMetadata] = field(default_factory=dict)
    total_projects: int = 0
    total_size_bytes: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "workspace_path": self.workspace_path,
            "created_at": self.created_at,
            "projects": {k: v.to_dict() for k, v in self.projects.items()},
            "total_projects": self.total_projects,
            "total_size_bytes": self.total_size_bytes,
        }


class WorkspaceManager:
    """
    Manages user workspaces for the Project Builder.
    
    Directory Structure:
    /opt/resonant/user_workspaces/
    └── {user_id}/
        ├── .workspace.json          # Workspace metadata
        └── {project_name}/
            ├── .resonant/
            │   ├── project.json     # Project metadata
            │   ├── agent_state.json # Agent state
            │   └── build_log.json   # Build history
            ├── frontend/            # Frontend code
            └── backend/             # Backend code
    """
    
    BASE_PATH = os.getenv("USER_WORKSPACES_PATH", "/opt/resonant/user_workspaces")
    MAX_PROJECTS_PER_USER = 50
    MAX_PROJECT_SIZE_MB = 500
    
    def __init__(self, base_path: str = None):
        self.base_path = Path(base_path or self.BASE_PATH)
        self._ensure_base_path()
        self._workspaces: Dict[str, UserWorkspace] = {}
        logger.info(f"WorkspaceManager initialized with base path: {self.base_path}")
    
    def _ensure_base_path(self):
        """Ensure base workspace directory exists."""
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def _get_workspace_path(self, user_id: str) -> Path:
        """Get path to user's workspace."""
        return self.base_path / user_id
    
    def _get_project_path(self, user_id: str, project_name: str) -> Path:
        """Get path to a specific project."""
        safe_name = self._sanitize_name(project_name)
        return self._get_workspace_path(user_id) / safe_name
    
    def _sanitize_name(self, name: str) -> str:
        """Sanitize project name for filesystem."""
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.lower())
        return safe[:50]
    
    def _generate_project_id(self, user_id: str, project_name: str) -> str:
        """Generate unique project ID."""
        data = f"{user_id}:{project_name}:{datetime.now(timezone.utc).isoformat()}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]
    
    async def get_or_create_workspace(self, user_id: str) -> UserWorkspace:
        """Get existing workspace or create new one."""
        if user_id in self._workspaces:
            return self._workspaces[user_id]
        
        workspace_path = self._get_workspace_path(user_id)
        metadata_file = workspace_path / ".workspace.json"
        
        if metadata_file.exists():
            with open(metadata_file, "r") as f:
                data = json.load(f)
                workspace = UserWorkspace(
                    user_id=data["user_id"],
                    workspace_path=str(workspace_path),
                    created_at=data["created_at"],
                    total_projects=data.get("total_projects", 0),
                    total_size_bytes=data.get("total_size_bytes", 0),
                )
                for pid, pdata in data.get("projects", {}).items():
                    workspace.projects[pid] = ProjectMetadata(**pdata)
        else:
            workspace_path.mkdir(parents=True, exist_ok=True)
            workspace = UserWorkspace(
                user_id=user_id,
                workspace_path=str(workspace_path),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            await self._save_workspace_metadata(workspace)
        
        # Scan filesystem for projects not in metadata (recovery)
        await self._scan_and_load_projects(workspace)
        
        self._workspaces[user_id] = workspace
        logger.info(f"Workspace loaded for user {user_id} with {len(workspace.projects)} projects")
        return workspace
    
    async def _scan_and_load_projects(self, workspace: UserWorkspace):
        """Scan workspace directory for projects and load them into memory."""
        workspace_path = Path(workspace.workspace_path)
        
        # NOTE: Old format (.project.json directly in workspace) is DEPRECATED
        # Those were created by autonomous daemon without proper user isolation
        # We only load projects from subdirectories with .resonant/project.json (new format)
        # This ensures proper user isolation - each user only sees their own projects
        
        # Check for new format: subdirectories with .resonant/project.json
        for item in workspace_path.iterdir():
            if not item.is_dir() or item.name.startswith("."):
                continue
            
            # Check for .resonant/project.json (new format)
            resonant_project = item / ".resonant" / "project.json"
            
            if resonant_project.exists():
                try:
                    with open(resonant_project, "r") as f:
                        data = json.load(f)
                        project_id = data.get("project_id", item.name)
                        
                        if project_id not in workspace.projects:
                            # Handle project_state - default to GENERATED for backward compatibility
                            project_state_str = data.get("project_state", "generated")
                            try:
                                project_state = ProjectState(project_state_str) if isinstance(project_state_str, str) else project_state_str
                            except ValueError:
                                project_state = ProjectState.GENERATED
                            
                            # Handle status - could be string or enum
                            status_str = data.get("status", "active")
                            try:
                                status = WorkspaceStatus(status_str) if isinstance(status_str, str) else status_str
                            except ValueError:
                                status = WorkspaceStatus.ACTIVE
                            
                            metadata = ProjectMetadata(
                                project_id=project_id,
                                name=data.get("project_name", data.get("name", item.name)),
                                description=data.get("description", ""),
                                tech_stack=data.get("tech_stack", []),
                                created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
                                updated_at=data.get("updated_at", data.get("created_at", datetime.now(timezone.utc).isoformat())),
                                status=status,
                                files_count=len(data.get("files", [])),
                                project_state=project_state,
                                promoted_at=data.get("promoted_at"),
                                runtime_snapshot_id=data.get("runtime_snapshot_id"),
                                transitions=data.get("transitions", []),
                            )
                            workspace.projects[project_id] = metadata
                            logger.info(f"Loaded project {project_id} from filesystem (state: {project_state.value})")
                except Exception as e:
                    logger.warning(f"Failed to load project from {resonant_project}: {e}")
        
        # Update total count and save
        workspace.total_projects = len(workspace.projects)
        await self._save_workspace_metadata(workspace)
    
    async def _save_workspace_metadata(self, workspace: UserWorkspace):
        """Save workspace metadata to disk."""
        metadata_file = Path(workspace.workspace_path) / ".workspace.json"
        with open(metadata_file, "w") as f:
            json.dump(workspace.to_dict(), f, indent=2)
    
    async def create_project(
        self,
        user_id: str,
        project_name: str,
        description: str,
        tech_stack: List[str],
    ) -> ProjectMetadata:
        """Create a new project in user's workspace."""
        workspace = await self.get_or_create_workspace(user_id)
        
        if workspace.total_projects >= self.MAX_PROJECTS_PER_USER:
            raise ValueError(f"Maximum projects ({self.MAX_PROJECTS_PER_USER}) reached")
        
        project_id = self._generate_project_id(user_id, project_name)
        project_path = self._get_project_path(user_id, project_name)
        
        if project_path.exists():
            raise ValueError(f"Project '{project_name}' already exists")
        
        project_path.mkdir(parents=True)
        resonant_dir = project_path / ".resonant"
        resonant_dir.mkdir()
        
        now = datetime.now(timezone.utc).isoformat()
        metadata = ProjectMetadata(
            project_id=project_id,
            name=project_name,
            description=description,
            tech_stack=tech_stack,
            created_at=now,
            updated_at=now,
            status=WorkspaceStatus.ACTIVE,
        )
        
        with open(resonant_dir / "project.json", "w") as f:
            json.dump(metadata.to_dict(), f, indent=2)
        
        with open(resonant_dir / "build_log.json", "w") as f:
            json.dump({"builds": [], "created_at": now}, f, indent=2)
        
        workspace.projects[project_id] = metadata
        workspace.total_projects += 1
        await self._save_workspace_metadata(workspace)
        
        logger.info(f"Created project {project_name} ({project_id}) for user {user_id}")
        return metadata
    
    async def get_project(self, user_id: str, project_id: str) -> Optional[ProjectMetadata]:
        """Get project metadata."""
        workspace = await self.get_or_create_workspace(user_id)
        return workspace.projects.get(project_id)
    
    async def update_project_status(
        self,
        user_id: str,
        project_id: str,
        status: WorkspaceStatus,
        files_count: int = None,
        total_size_bytes: int = None,
        build_cost: float = None,
    ):
        """Update project status and metrics."""
        workspace = await self.get_or_create_workspace(user_id)
        project = workspace.projects.get(project_id)
        
        if not project:
            raise ValueError(f"Project {project_id} not found")
        
        project.status = status
        project.updated_at = datetime.now(timezone.utc).isoformat()
        
        if files_count is not None:
            project.files_count = files_count
        if total_size_bytes is not None:
            project.total_size_bytes = total_size_bytes
        if build_cost is not None:
            project.build_cost = build_cost
        
        project_path = self._find_project_path(user_id, project_id)
        if project_path:
            resonant_dir = project_path / ".resonant"
            with open(resonant_dir / "project.json", "w") as f:
                json.dump(project.to_dict(), f, indent=2)
        
        await self._save_workspace_metadata(workspace)
    
    def _find_project_path(self, user_id: str, project_id: str) -> Optional[Path]:
        """Find project path by ID."""
        workspace_path = self._get_workspace_path(user_id)
        for item in workspace_path.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                project_file = item / ".resonant" / "project.json"
                if project_file.exists():
                    with open(project_file, "r") as f:
                        data = json.load(f)
                        if data.get("project_id") == project_id:
                            return item
        return None
    
    async def get_project_path(self, user_id: str, project_id: str) -> Optional[str]:
        """Get absolute path to project directory."""
        path = self._find_project_path(user_id, project_id)
        return str(path) if path else None
    
    async def list_projects(self, user_id: str) -> List[ProjectMetadata]:
        """List all projects for a user."""
        workspace = await self.get_or_create_workspace(user_id)
        return list(workspace.projects.values())
    
    async def delete_project(self, user_id: str, project_id: str):
        """Delete a project."""
        workspace = await self.get_or_create_workspace(user_id)
        
        if project_id not in workspace.projects:
            raise ValueError(f"Project {project_id} not found")
        
        project_path = self._find_project_path(user_id, project_id)
        if project_path and project_path.exists():
            shutil.rmtree(project_path)
        
        del workspace.projects[project_id]
        workspace.total_projects -= 1
        await self._save_workspace_metadata(workspace)
        
        logger.info(f"Deleted project {project_id} for user {user_id}")
    
    async def promote_project(
        self,
        user_id: str,
        project_id: str,
        reason: str = "User requested promotion to runtime",
    ) -> Dict[str, Any]:
        """
        Promote a project from GENERATED to RUNTIME state.
        
        This is a formal state transition that:
        1. Creates a snapshot of the current state
        2. Binds RARA runtime roots for governed mutations
        3. Logs the transition for audit
        
        Only RUNTIME projects can be modified.
        
        Args:
            user_id: User ID
            project_id: Project ID to promote
            reason: Reason for promotion
            
        Returns:
            Dict with promotion result and snapshot_id
        """
        workspace = await self.get_or_create_workspace(user_id)
        project = workspace.projects.get(project_id)
        
        if not project:
            raise ValueError(f"Project {project_id} not found")
        
        if project.project_state == ProjectState.RUNTIME:
            return {
                "success": True,
                "already_runtime": True,
                "project_id": project_id,
                "snapshot_id": project.runtime_snapshot_id,
            }
        
        project_path = self._find_project_path(user_id, project_id)
        if not project_path:
            raise ValueError(f"Project path not found for {project_id}")
        
        # Create snapshot of current state
        snapshot_id = self._create_snapshot(project_path, project_id)
        
        # Record state transition
        transition = StateTransition(
            transition_id=hashlib.sha256(f"{project_id}:{datetime.now().isoformat()}".encode()).hexdigest()[:16],
            project_id=project_id,
            from_state=ProjectState.GENERATED.value,
            to_state=ProjectState.RUNTIME.value,
            timestamp=datetime.now(timezone.utc).isoformat(),
            actor=user_id,
            reason=reason,
            snapshot_id=snapshot_id,
        )
        
        # Update project metadata
        project.project_state = ProjectState.RUNTIME
        project.promoted_at = datetime.now(timezone.utc).isoformat()
        project.runtime_snapshot_id = snapshot_id
        project.transitions.append(transition.to_dict())
        project.updated_at = datetime.now(timezone.utc).isoformat()
        
        # Save to disk
        resonant_dir = project_path / ".resonant"
        with open(resonant_dir / "project.json", "w") as f:
            json.dump(project.to_dict(), f, indent=2)
        
        # Save transition log
        transitions_file = resonant_dir / "transitions.json"
        transitions_data = []
        if transitions_file.exists():
            with open(transitions_file, "r") as f:
                transitions_data = json.load(f)
        transitions_data.append(transition.to_dict())
        with open(transitions_file, "w") as f:
            json.dump(transitions_data, f, indent=2)
        
        await self._save_workspace_metadata(workspace)
        
        logger.info(f"Promoted project {project_id} to RUNTIME state (snapshot: {snapshot_id})")
        
        return {
            "success": True,
            "project_id": project_id,
            "from_state": ProjectState.GENERATED.value,
            "to_state": ProjectState.RUNTIME.value,
            "snapshot_id": snapshot_id,
            "transition_id": transition.transition_id,
        }
    
    def _create_snapshot(self, project_path: Path, project_id: str) -> str:
        """Create a snapshot of project state for rollback capability."""
        snapshot_dir = project_path / ".resonant" / "snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        snapshot_id = f"snap_{project_id[:8]}_{timestamp}"
        
        # Create snapshot manifest (file hashes)
        manifest = {
            "snapshot_id": snapshot_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": {},
        }
        
        for file_path in project_path.rglob("*"):
            if file_path.is_file() and ".resonant" not in str(file_path):
                rel_path = str(file_path.relative_to(project_path))
                try:
                    content = file_path.read_bytes()
                    file_hash = hashlib.sha256(content).hexdigest()
                    manifest["files"][rel_path] = {
                        "hash": file_hash,
                        "size": len(content),
                    }
                except Exception as e:
                    logger.warning(f"Failed to hash {rel_path}: {e}")
        
        # Save manifest
        manifest_path = snapshot_dir / f"{snapshot_id}.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        
        logger.info(f"Created snapshot {snapshot_id} with {len(manifest['files'])} files")
        return snapshot_id
    
    async def get_project_state(self, user_id: str, project_id: str) -> Optional[ProjectState]:
        """Get the current state of a project."""
        workspace = await self.get_or_create_workspace(user_id)
        project = workspace.projects.get(project_id)
        return project.project_state if project else None
    
    async def can_modify_project(self, user_id: str, project_id: str) -> bool:
        """Check if a project can be modified (must be in RUNTIME state)."""
        workspace = await self.get_or_create_workspace(user_id)
        project = workspace.projects.get(project_id)
        return project.is_mutable() if project else False

    async def archive_project(self, user_id: str, project_id: str) -> str:
        """Archive a project to zip file."""
        project_path = self._find_project_path(user_id, project_id)
        if not project_path:
            raise ValueError(f"Project {project_id} not found")
        
        archive_dir = self._get_workspace_path(user_id) / ".archives"
        archive_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_name = f"{project_path.name}_{timestamp}"
        archive_path = shutil.make_archive(
            str(archive_dir / archive_name),
            "zip",
            project_path,
        )
        
        await self.update_project_status(user_id, project_id, WorkspaceStatus.ARCHIVED)
        logger.info(f"Archived project {project_id} to {archive_path}")
        
        return archive_path
    
    async def get_workspace_stats(self, user_id: str) -> Dict[str, Any]:
        """Get workspace statistics."""
        workspace = await self.get_or_create_workspace(user_id)
        workspace_path = Path(workspace.workspace_path)
        
        total_size = 0
        for item in workspace_path.rglob("*"):
            if item.is_file():
                total_size += item.stat().st_size
        
        workspace.total_size_bytes = total_size
        
        return {
            "user_id": user_id,
            "workspace_path": workspace.workspace_path,
            "total_projects": workspace.total_projects,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "projects_by_status": {
                status.value: len([p for p in workspace.projects.values() if p.status == status])
                for status in WorkspaceStatus
            },
        }
    
    async def cleanup_old_projects(self, user_id: str, days_old: int = 30):
        """Clean up projects older than specified days."""
        workspace = await self.get_or_create_workspace(user_id)
        cutoff = datetime.now(timezone.utc).timestamp() - (days_old * 24 * 60 * 60)
        
        to_delete = []
        for project_id, project in workspace.projects.items():
            if project.status == WorkspaceStatus.ARCHIVED:
                updated = datetime.fromisoformat(project.updated_at.replace("Z", "+00:00"))
                if updated.timestamp() < cutoff:
                    to_delete.append(project_id)
        
        for project_id in to_delete:
            await self.delete_project(user_id, project_id)
        
        logger.info(f"Cleaned up {len(to_delete)} old projects for user {user_id}")
        return len(to_delete)


_workspace_manager: Optional[WorkspaceManager] = None


def get_workspace_manager() -> WorkspaceManager:
    """Get singleton workspace manager instance."""
    global _workspace_manager
    if _workspace_manager is None:
        _workspace_manager = WorkspaceManager()
    return _workspace_manager
