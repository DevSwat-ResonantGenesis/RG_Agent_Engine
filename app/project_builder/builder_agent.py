"""
Project Builder Agent - Main Agent Implementation
==================================================

The main Project Builder Agent that orchestrates project generation.

Features:
- Goal-driven project generation
- Self-trigger for autonomous execution
- Integration with Code Visualizer, State Physics, RARA
- Self-correction loop for broken imports
- Learning from build outcomes
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import json
import httpx

from .workspace_manager import (
    WorkspaceManager, 
    get_workspace_manager, 
    WorkspaceStatus,
    ProjectMetadata,
    ProjectState,
)
from .code_validator import (
    CodeValidator, 
    get_code_validator, 
    ValidationResult,
    ValidationStatus,
)
from .state_tracker import (
    StateTracker, 
    get_state_tracker, 
    AgentState,
    AgentStatus,
)
from .rara_governance import (
    RARAGovernance, 
    get_rara_governance, 
    MutationType,
    MutationResult,
)
from .template_engine import (
    TemplateEngine, 
    get_template_engine, 
    ProjectType,
    FileTemplate,
)

logger = logging.getLogger(__name__)


class BuildPhase(str, Enum):
    """Phases of project building."""
    INITIALIZING = "initializing"
    PLANNING = "planning"
    GENERATING = "generating"
    VALIDATING = "validating"
    CORRECTING = "correcting"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BuildGoal:
    """A goal for the build process."""
    goal_id: str
    description: str
    priority: float
    dependencies: List[str] = field(default_factory=list)
    status: str = "pending"
    result: Optional[str] = None


@dataclass
class BuildProgress:
    """Progress of a build."""
    phase: BuildPhase
    files_generated: int
    files_total: int
    validations_passed: int
    corrections_made: int
    current_file: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    
    @property
    def progress_percent(self) -> float:
        if self.files_total == 0:
            return 0.0
        return (self.files_generated / self.files_total) * 100


@dataclass
class BuildResult:
    """Result of a project build."""
    success: bool
    project_id: str
    project_path: Optional[str] = None
    files_created: int = 0
    total_cost: float = 0.0
    build_time_seconds: float = 0.0
    validation_result: Optional[ValidationResult] = None
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "project_id": self.project_id,
            "project_path": self.project_path,
            "files_created": self.files_created,
            "total_cost": self.total_cost,
            "build_time_seconds": self.build_time_seconds,
            "validation_result": self.validation_result.to_dict() if self.validation_result else None,
            "errors": self.errors,
        }


class ProjectBuilderAgent:
    """
    Autonomous Project Builder Agent.
    
    Builds complete fullstack projects using:
    - Template Engine for scaffolding
    - LLM for custom code generation
    - Code Visualizer for validation
    - State Physics for economic tracking
    - RARA for safety governance
    """
    
    LLM_SERVICE_URL = "http://llm_service:8000"
    MAX_CORRECTION_ATTEMPTS = 5
    MAX_FILES_PER_BATCH = 10
    
    def __init__(
        self,
        workspace_manager: WorkspaceManager = None,
        code_validator: CodeValidator = None,
        state_tracker: StateTracker = None,
        rara_governance: RARAGovernance = None,
        template_engine: TemplateEngine = None,
    ):
        self.workspace_manager = workspace_manager or get_workspace_manager()
        self.code_validator = code_validator or get_code_validator()
        self.state_tracker = state_tracker or get_state_tracker()
        self.rara_governance = rara_governance or get_rara_governance()
        self.template_engine = template_engine or get_template_engine()
        
        self._client: Optional[httpx.AsyncClient] = None
        self._active_builds: Dict[str, BuildProgress] = {}
        
        logger.info("ProjectBuilderAgent initialized")
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client
    
    async def close(self):
        """Close resources."""
        if self._client:
            await self._client.aclose()
        await self.code_validator.close()
        await self.state_tracker.close()
        await self.rara_governance.close()
    
    async def build_project(
        self,
        user_id: str,
        project_name: str,
        description: str,
        project_type: ProjectType = ProjectType.FULLSTACK_REACT_FASTAPI,
        custom_requirements: List[str] = None,
        initial_budget: float = 10000.0,
    ) -> BuildResult:
        """
        Build a complete project.
        
        Args:
            user_id: User ID
            project_name: Name of the project
            description: Project description
            project_type: Type of project to build
            custom_requirements: Additional requirements
            initial_budget: Budget for the build
            
        Returns:
            BuildResult with status and details
        """
        start_time = datetime.now(timezone.utc)
        errors = []
        
        logger.info(f"Starting build for {project_name} (user: {user_id})")
        
        project_metadata = await self.workspace_manager.create_project(
            user_id=user_id,
            project_name=project_name,
            description=description,
            tech_stack=self._get_tech_stack(project_type),
        )
        project_id = project_metadata.project_id
        
        project_path = await self.workspace_manager.get_project_path(user_id, project_id)
        if not project_path:
            return BuildResult(
                success=False,
                project_id=project_id,
                errors=["Failed to create project workspace"],
            )
        
        progress = BuildProgress(
            phase=BuildPhase.INITIALIZING,
            files_generated=0,
            files_total=0,
            validations_passed=0,
            corrections_made=0,
        )
        self._active_builds[project_id] = progress
        
        try:
            progress.phase = BuildPhase.PLANNING
            agent_state = await self.state_tracker.register_agent(
                user_id=user_id,
                project_id=project_id,
                project_name=project_name,
                initial_budget=initial_budget,
            )
            
            capabilities = await self.rara_governance.register_agent(
                agent_id=agent_state.agent_id,
                workspace_path=project_path,
                trust_score=agent_state.trust_score,
            )
            
            if await self.rara_governance.check_kill_switch():
                await self.workspace_manager.update_project_status(
                    user_id, project_id, WorkspaceStatus.FAILED
                )
                return BuildResult(
                    success=False,
                    project_id=project_id,
                    errors=["System frozen by kill switch"],
                )
            
            progress.phase = BuildPhase.GENERATING
            template_files = self.template_engine.generate_project_files(
                project_type=project_type,
                project_name=project_name,
            )
            progress.files_total = len(template_files)
            
            await self.workspace_manager.update_project_status(
                user_id, project_id, WorkspaceStatus.BUILDING
            )
            
            for i, file_template in enumerate(template_files):
                if not await self.state_tracker.check_budget(agent_state.agent_id):
                    errors.append("Budget exhausted")
                    break
                
                if await self.rara_governance.check_kill_switch():
                    errors.append("Build stopped by kill switch")
                    break
                
                file_path = Path(project_path) / file_template.path
                progress.current_file = file_template.path
                
                mutation_result = await self.rara_governance.execute_mutation(
                    agent_id=agent_state.agent_id,
                    mutation_type=MutationType.CREATE_FILE,
                    target_path=str(file_path),
                    content=file_template.content,
                    justification=f"Creating {file_template.path}",
                )
                
                if mutation_result.success:
                    await self.state_tracker.record_file_creation(
                        agent_id=agent_state.agent_id,
                        file_path=str(file_path),
                        file_size=len(file_template.content),
                    )
                    progress.files_generated += 1
                else:
                    errors.append(f"Failed to create {file_template.path}: {mutation_result.error}")
                
                if (i + 1) % self.MAX_FILES_PER_BATCH == 0:
                    await asyncio.sleep(0.1)
            
            progress.phase = BuildPhase.VALIDATING
            await self.state_tracker.record_validation(agent_state.agent_id)
            
            validation_result = await self.code_validator.analyze_project(project_path)
            
            if validation_result.status == ValidationStatus.FAILED:
                progress.phase = BuildPhase.CORRECTING
                
                for attempt in range(self.MAX_CORRECTION_ATTEMPTS):
                    if not validation_result.broken_connections:
                        break
                    
                    for broken in validation_result.broken_connections[:5]:
                        fix_content = await self._generate_fix(broken, project_path)
                        if fix_content:
                            fix_path = Path(project_path) / broken.target
                            await self.rara_governance.execute_mutation(
                                agent_id=agent_state.agent_id,
                                mutation_type=MutationType.CREATE_FILE,
                                target_path=str(fix_path),
                                content=fix_content,
                                justification=f"Fixing broken import: {broken.target}",
                            )
                            await self.state_tracker.record_fix(agent_state.agent_id)
                            progress.corrections_made += 1
                    
                    validation_result = await self.code_validator.analyze_project(project_path)
                    
                    if validation_result.status != ValidationStatus.FAILED:
                        break
            
            if validation_result.status in [ValidationStatus.PASSED, ValidationStatus.WARNINGS]:
                progress.validations_passed += 1
            
            progress.phase = BuildPhase.FINALIZING
            
            await self.state_tracker.record_success(agent_state.agent_id)
            
            final_state = await self.state_tracker.get_agent_state(agent_state.agent_id)
            total_cost = final_state.total_spent if final_state else 0.0
            
            await self._save_build_log(
                project_path=project_path,
                files_created=progress.files_generated,
                total_cost=total_cost,
                validation_result=validation_result,
                errors=errors,
            )
            
            await self.workspace_manager.update_project_status(
                user_id=user_id,
                project_id=project_id,
                status=WorkspaceStatus.COMPLETED,
                files_count=progress.files_generated,
                build_cost=total_cost,
            )
            
            progress.phase = BuildPhase.COMPLETED
            
            build_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            
            logger.info(
                f"Build completed for {project_name}: "
                f"{progress.files_generated} files, "
                f"cost: {total_cost}, "
                f"time: {build_time:.1f}s"
            )
            
            return BuildResult(
                success=len(errors) == 0,
                project_id=project_id,
                project_path=project_path,
                files_created=progress.files_generated,
                total_cost=total_cost,
                build_time_seconds=build_time,
                validation_result=validation_result,
                errors=errors,
            )
            
        except Exception as e:
            logger.error(f"Build failed: {e}", exc_info=True)
            progress.phase = BuildPhase.FAILED
            
            await self.workspace_manager.update_project_status(
                user_id, project_id, WorkspaceStatus.FAILED
            )
            
            return BuildResult(
                success=False,
                project_id=project_id,
                project_path=project_path,
                errors=[str(e)],
            )
        
        finally:
            if project_id in self._active_builds:
                del self._active_builds[project_id]
    
    def _get_tech_stack(self, project_type: ProjectType) -> List[str]:
        """Get tech stack for project type."""
        stacks = {
            ProjectType.FULLSTACK_REACT_FASTAPI: ["react", "typescript", "fastapi", "python", "postgresql"],
            ProjectType.FRONTEND_REACT: ["react", "typescript", "vite", "tailwindcss"],
            ProjectType.BACKEND_FASTAPI: ["fastapi", "python", "sqlalchemy", "postgresql"],
        }
        return stacks.get(project_type, [])
    
    async def _generate_fix(
        self,
        broken_connection,
        project_path: str,
    ) -> Optional[str]:
        """Generate fix for broken connection using LLM."""
        client = await self._get_client()
        
        prompt = f"""Generate the missing file content for:
File: {broken_connection.target}
Required by: {broken_connection.source_file}
Connection type: {broken_connection.connection_type}

Generate minimal, working code that exports what is needed.
Output ONLY the code, no explanations."""
        
        try:
            response = await client.post(
                f"{self.LLM_SERVICE_URL}/llm/chat/completions",
                json={
                    "messages": [
                        {"role": "system", "content": "You are a code generator. Output only code, no explanations."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                },
            )
            
            if response.status_code == 200:
                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content.startswith("```"):
                    lines = content.split("\n")
                    content = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
                return content
                
        except Exception as e:
            logger.warning(f"Failed to generate fix: {e}")
        
        return None
    
    async def _save_build_log(
        self,
        project_path: str,
        files_created: int,
        total_cost: float,
        validation_result: ValidationResult,
        errors: List[str],
    ):
        """Save build log to project."""
        log_path = Path(project_path) / ".resonant" / "build_log.json"
        
        try:
            if log_path.exists():
                with open(log_path, "r") as f:
                    log_data = json.load(f)
            else:
                log_data = {"builds": []}
            
            log_data["builds"].append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "files_created": files_created,
                "total_cost": total_cost,
                "validation_status": validation_result.status.value if validation_result else None,
                "broken_connections": len(validation_result.broken_connections) if validation_result else 0,
                "errors": errors,
            })
            
            with open(log_path, "w") as f:
                json.dump(log_data, f, indent=2)
                
        except Exception as e:
            logger.warning(f"Failed to save build log: {e}")
    
    async def get_build_progress(self, project_id: str) -> Optional[BuildProgress]:
        """Get progress of an active build."""
        return self._active_builds.get(project_id)
    
    async def cancel_build(self, project_id: str) -> bool:
        """Cancel an active build."""
        if project_id in self._active_builds:
            self._active_builds[project_id].phase = BuildPhase.FAILED
            self._active_builds[project_id].errors.append("Build cancelled by user")
            return True
        return False
    
    async def list_templates(self) -> List[Dict[str, str]]:
        """List available project templates."""
        return self.template_engine.list_templates()
    
    async def modify_project(
        self,
        user_id: str,
        project_id: str,
        modification_request: str,
        target_files: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Modify an existing project based on user request.
        
        GOVERNANCE: Only RUNTIME projects can be modified.
        Projects must be promoted from GENERATED → RUNTIME first.
        
        Args:
            user_id: User ID
            project_id: Project ID to modify
            modification_request: Description of changes to make
            target_files: Optional list of specific files to modify
            
        Returns:
            Dict with modified files and status
        """
        logger.info(f"Modifying project {project_id} for user {user_id}")
        
        # GOVERNANCE CHECK: Verify project is in RUNTIME state
        can_modify = await self.workspace_manager.can_modify_project(user_id, project_id)
        if not can_modify:
            project_state = await self.workspace_manager.get_project_state(user_id, project_id)
            if project_state == ProjectState.GENERATED:
                return {
                    "success": False,
                    "error": "Project is in GENERATED state. Must promote to RUNTIME before modification.",
                    "error_code": "PROJECT_NOT_PROMOTED",
                    "project_state": project_state.value if project_state else None,
                    "action_required": "Call /projects/{project_id}/promote first",
                    "modified_files": [],
                }
            return {
                "success": False,
                "error": "Project cannot be modified",
                "project_state": project_state.value if project_state else None,
                "modified_files": [],
            }
        
        # Get project path
        project_path = await self.workspace_manager.get_project_path(user_id, project_id)
        if not project_path:
            return {
                "success": False,
                "error": "Project not found",
                "modified_files": [],
            }
        
        # Load existing project files
        project_files = await self._load_project_files(project_path, target_files)
        if not project_files:
            return {
                "success": False,
                "error": "No files found in project",
                "modified_files": [],
            }
        
        # Build context for LLM
        file_context = self._build_file_context(project_files)
        
        # Generate modifications using LLM
        modified_files = await self._generate_modifications(
            modification_request,
            file_context,
            project_files,
        )
        
        if not modified_files:
            return {
                "success": False,
                "error": "Failed to generate modifications",
                "modified_files": [],
            }
        
        # Save modified files through RARA
        saved_files = []
        for file_info in modified_files:
            try:
                file_path = Path(project_path) / file_info["path"]
                file_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Try RARA first, fallback to local
                result = await self.rara_governance.execute_mutation(
                    agent_id=f"modifier_{user_id}",
                    mutation_type=MutationType.WRITE_FILE,
                    target_path=str(file_path),
                    content=file_info["content"],
                )
                
                if not result.success:
                    # Local fallback
                    with open(file_path, "w") as f:
                        f.write(file_info["content"])
                
                saved_files.append({
                    "path": file_info["path"],
                    "action": file_info.get("action", "modified"),
                })
            except Exception as e:
                logger.warning(f"Failed to save {file_info['path']}: {e}")
        
        # Update project metadata
        await self.workspace_manager.update_project_status(
            user_id,
            project_id,
            WorkspaceStatus.ACTIVE,
            files_count=len(project_files) + len([f for f in modified_files if f.get("action") == "created"]),
        )
        
        return {
            "success": True,
            "modified_files": saved_files,
            "total_modified": len(saved_files),
        }
    
    async def _load_project_files(
        self,
        project_path: str,
        target_files: List[str] = None,
    ) -> List[Dict[str, str]]:
        """Load project files from disk."""
        files = []
        project_dir = Path(project_path)
        
        # File extensions to include
        code_extensions = {
            ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".scss",
            ".json", ".yaml", ".yml", ".md", ".txt", ".sql", ".sh",
        }
        
        # Directories to skip
        skip_dirs = {".git", "node_modules", "__pycache__", ".resonant", "venv", ".venv", "dist", "build"}
        
        for file_path in project_dir.rglob("*"):
            if file_path.is_file():
                # Skip if in excluded directory
                if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
                    continue
                
                # Check extension
                if file_path.suffix.lower() not in code_extensions:
                    continue
                
                # If target_files specified, only include those
                rel_path = str(file_path.relative_to(project_dir))
                if target_files and rel_path not in target_files:
                    continue
                
                try:
                    content = file_path.read_text(encoding="utf-8")
                    files.append({
                        "path": rel_path,
                        "content": content,
                        "language": self._get_language(file_path.suffix),
                    })
                except Exception as e:
                    logger.warning(f"Failed to read {file_path}: {e}")
        
        return files
    
    def _build_file_context(self, files: List[Dict[str, str]]) -> str:
        """Build context string from project files."""
        context_parts = []
        
        # Limit context size
        max_chars = 50000
        current_chars = 0
        
        for file_info in files:
            file_header = f"\n=== {file_info['path']} ({file_info['language']}) ===\n"
            file_content = file_info["content"]
            
            # Truncate large files
            if len(file_content) > 5000:
                file_content = file_content[:5000] + "\n... (truncated)"
            
            entry = file_header + file_content
            if current_chars + len(entry) > max_chars:
                break
            
            context_parts.append(entry)
            current_chars += len(entry)
        
        return "\n".join(context_parts)
    
    async def _generate_modifications(
        self,
        request: str,
        file_context: str,
        existing_files: List[Dict[str, str]],
    ) -> List[Dict[str, Any]]:
        """Generate file modifications using LLM."""
        client = await self._get_client()
        
        system_prompt = """You are a code modification assistant. Given existing project files and a modification request, generate the updated file contents.

Output format: JSON array of objects with:
- "path": file path (relative to project root)
- "content": full file content (not diff)
- "action": "modified" or "created"
- "explanation": brief explanation of changes

IMPORTANT:
- Output ONLY valid JSON, no markdown code blocks
- Include the COMPLETE file content, not just changes
- Maintain existing code style and conventions
- Only modify files that need changes"""

        user_prompt = f"""## Existing Project Files:
{file_context}

## Modification Request:
{request}

Generate the modified files as a JSON array."""

        try:
            response = await client.post(
                f"{self.LLM_SERVICE_URL}/llm/chat/completions",
                json={
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 8000,
                },
                timeout=120.0,
            )
            
            if response.status_code == 200:
                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                # Parse JSON response
                content = content.strip()
                if content.startswith("```"):
                    lines = content.split("\n")
                    content = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
                
                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse LLM response as JSON: {e}")
                    return []
            else:
                logger.warning(f"LLM request failed: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"Failed to generate modifications: {e}")
            return []
    
    def _get_language(self, suffix: str) -> str:
        """Get language from file suffix."""
        lang_map = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".html": "html",
            ".css": "css",
            ".scss": "scss",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".md": "markdown",
            ".sql": "sql",
            ".sh": "bash",
        }
        return lang_map.get(suffix.lower(), "text")
    
    async def get_project_files(
        self,
        user_id: str,
        project_id: str,
    ) -> List[Dict[str, str]]:
        """Get all files from a project."""
        project_path = await self.workspace_manager.get_project_path(user_id, project_id)
        if not project_path:
            return []
        return await self._load_project_files(project_path)


_builder_agent: Optional[ProjectBuilderAgent] = None


async def get_builder_agent() -> ProjectBuilderAgent:
    """Get singleton builder agent instance."""
    global _builder_agent
    if _builder_agent is None:
        _builder_agent = ProjectBuilderAgent()
    return _builder_agent
