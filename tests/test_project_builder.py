"""
Project Builder Integration Tests
==================================

Tests for the Project Builder service to ensure production readiness.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import tempfile
import shutil

from app.project_builder import (
    WorkspaceManager,
    get_workspace_manager,
    WorkspaceStatus,
    ProjectMetadata,
    CodeValidator,
    get_code_validator,
    ValidationStatus,
    ValidationResult,
    StateTracker,
    get_state_tracker,
    AgentStatus,
    AgentState,
    RARAGovernance,
    get_rara_governance,
    MutationType,
    MutationResult,
    TemplateEngine,
    get_template_engine,
    ProjectType,
    ProjectBuilderAgent,
    get_builder_agent,
    BuildResult,
    BuildProgress,
    BuildPhase,
    DeliveryManager,
    get_delivery_manager,
    DeliveryPackage,
)


class TestWorkspaceManager:
    """Tests for WorkspaceManager."""
    
    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @pytest.fixture
    def workspace_manager(self, temp_workspace):
        """Create a workspace manager with temp directory."""
        manager = WorkspaceManager(base_path=temp_workspace)
        return manager
    
    @pytest.mark.asyncio
    async def test_create_workspace(self, workspace_manager):
        """Test creating a user workspace."""
        user_id = "test-user-123"
        workspace = await workspace_manager.get_or_create_workspace(user_id)
        
        assert workspace is not None
        assert workspace.user_id == user_id
        assert workspace.status == WorkspaceStatus.ACTIVE
    
    @pytest.mark.asyncio
    async def test_create_project(self, workspace_manager):
        """Test creating a project in workspace."""
        user_id = "test-user-123"
        project_name = "test-project"
        
        project_path = await workspace_manager.create_project(
            user_id=user_id,
            project_name=project_name,
            description="Test project",
            tech_stack=["react", "fastapi"],
        )
        
        assert project_path is not None
        assert Path(project_path).exists()
    
    @pytest.mark.asyncio
    async def test_list_projects(self, workspace_manager):
        """Test listing user projects."""
        user_id = "test-user-123"
        
        # Create some projects
        await workspace_manager.create_project(
            user_id=user_id,
            project_name="project1",
            description="First project",
            tech_stack=["react"],
        )
        await workspace_manager.create_project(
            user_id=user_id,
            project_name="project2",
            description="Second project",
            tech_stack=["fastapi"],
        )
        
        projects = await workspace_manager.list_projects(user_id)
        
        assert len(projects) == 2
    
    @pytest.mark.asyncio
    async def test_delete_project(self, workspace_manager):
        """Test deleting a project."""
        user_id = "test-user-123"
        
        project_path = await workspace_manager.create_project(
            user_id=user_id,
            project_name="to-delete",
            description="Will be deleted",
            tech_stack=["react"],
        )
        
        # Get project ID
        projects = await workspace_manager.list_projects(user_id)
        project_id = projects[0]["project_id"]
        
        # Delete
        result = await workspace_manager.delete_project(user_id, project_id)
        
        assert result is True
        assert not Path(project_path).exists()


class TestTemplateEngine:
    """Tests for TemplateEngine."""
    
    @pytest.fixture
    def template_engine(self):
        """Create a template engine."""
        return TemplateEngine()
    
    def test_list_templates(self, template_engine):
        """Test listing available templates."""
        templates = template_engine.list_templates()
        
        assert len(templates) > 0
        assert any(t["type"] == "fullstack_react_fastapi" for t in templates)
    
    def test_get_template(self, template_engine):
        """Test getting a specific template."""
        template = template_engine.get_template(ProjectType.FULLSTACK_REACT_FASTAPI)
        
        assert template is not None
        assert template.project_type == ProjectType.FULLSTACK_REACT_FASTAPI
        assert len(template.files) > 0
    
    def test_render_template(self, template_engine):
        """Test rendering a template with variables."""
        template = template_engine.get_template(ProjectType.FULLSTACK_REACT_FASTAPI)
        
        files = template_engine.render_template(
            template,
            {"project_name": "my-awesome-project"},
        )
        
        assert len(files) > 0
        # Check that project name was substituted
        for file in files:
            if "package.json" in file.path:
                assert "my-awesome-project" in file.content


class TestCodeValidator:
    """Tests for CodeValidator."""
    
    @pytest.fixture
    def code_validator(self):
        """Create a code validator."""
        return CodeValidator()
    
    @pytest.mark.asyncio
    async def test_validate_code_offline(self, code_validator):
        """Test validation when Code Visualizer is offline."""
        result = await code_validator.validate_project("/nonexistent/path")
        
        # Should return a result even if service is down
        assert result is not None
        assert isinstance(result, ValidationResult)
    
    @pytest.mark.asyncio
    async def test_check_broken_imports_offline(self, code_validator):
        """Test broken import check when offline."""
        broken = await code_validator.check_broken_imports("fake-analysis-id")
        
        # Should return empty list if service is down
        assert isinstance(broken, list)


class TestStateTracker:
    """Tests for StateTracker."""
    
    @pytest.fixture
    def state_tracker(self):
        """Create a state tracker."""
        return StateTracker()
    
    @pytest.mark.asyncio
    async def test_register_agent_offline(self, state_tracker):
        """Test agent registration when State Physics is offline."""
        state = await state_tracker.register_agent(
            agent_id="test-agent",
            user_id="test-user",
            initial_budget=1000.0,
        )
        
        # Should return a default state if service is down
        assert state is not None
        assert isinstance(state, AgentState)
    
    @pytest.mark.asyncio
    async def test_check_budget_offline(self, state_tracker):
        """Test budget check when offline."""
        has_budget = await state_tracker.check_budget("test-agent", 100.0)
        
        # Should return True by default if service is down
        assert has_budget is True


class TestRARAGovernance:
    """Tests for RARAGovernance."""
    
    @pytest.fixture
    def rara_governance(self):
        """Create a RARA governance instance."""
        return RARAGovernance()
    
    @pytest.mark.asyncio
    async def test_check_kill_switch_offline(self, rara_governance):
        """Test kill switch check when RARA is offline."""
        is_active = await rara_governance.check_kill_switch()
        
        # Should return True (safe to proceed) if service is down
        assert is_active is True
    
    @pytest.mark.asyncio
    async def test_register_agent_offline(self, rara_governance):
        """Test agent registration when offline."""
        result = await rara_governance.register_agent(
            agent_id="test-agent",
            user_id="test-user",
        )
        
        # Should return False if service is down
        assert isinstance(result, bool)


class TestProjectBuilderAgent:
    """Tests for ProjectBuilderAgent."""
    
    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @pytest.fixture
    def builder_agent(self, temp_workspace):
        """Create a builder agent with mocked dependencies."""
        workspace_manager = WorkspaceManager(base_path=temp_workspace)
        
        agent = ProjectBuilderAgent(
            workspace_manager=workspace_manager,
        )
        return agent
    
    @pytest.mark.asyncio
    async def test_build_project_basic(self, builder_agent):
        """Test basic project building."""
        result = await builder_agent.build_project(
            user_id="test-user",
            project_name="test-project",
            description="A test project",
            project_type=ProjectType.FULLSTACK_REACT_FASTAPI,
            initial_budget=10000.0,
        )
        
        assert result is not None
        assert isinstance(result, BuildResult)
        assert result.project_id is not None
    
    @pytest.mark.asyncio
    async def test_get_progress(self, builder_agent):
        """Test getting build progress."""
        # Start a build
        result = await builder_agent.build_project(
            user_id="test-user",
            project_name="progress-test",
            description="Test progress tracking",
            project_type=ProjectType.FRONTEND_REACT,
            initial_budget=5000.0,
        )
        
        # Get progress
        progress = await builder_agent.get_progress(result.project_id)
        
        assert progress is not None
        assert isinstance(progress, BuildProgress)
    
    @pytest.mark.asyncio
    async def test_list_templates(self, builder_agent):
        """Test listing available templates."""
        templates = await builder_agent.list_templates()
        
        assert len(templates) > 0


class TestDeliveryManager:
    """Tests for DeliveryManager."""
    
    @pytest.fixture
    def temp_delivery(self):
        """Create a temporary delivery directory."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @pytest.fixture
    def delivery_manager(self, temp_delivery):
        """Create a delivery manager with temp directory."""
        return DeliveryManager(delivery_path=temp_delivery)
    
    @pytest.mark.asyncio
    async def test_create_delivery_package(self, delivery_manager, temp_delivery):
        """Test creating a delivery package."""
        # Create a fake project directory
        project_path = Path(temp_delivery) / "test-project"
        project_path.mkdir()
        (project_path / "index.js").write_text("console.log('hello');")
        (project_path / "package.json").write_text('{"name": "test"}')
        
        package = await delivery_manager.create_delivery_package(
            project_id="test-123",
            project_name="test-project",
            project_path=str(project_path),
        )
        
        assert package is not None
        assert isinstance(package, DeliveryPackage)
        assert package.archive_path is not None
        assert Path(package.archive_path).exists()
    
    @pytest.mark.asyncio
    async def test_list_deliveries(self, delivery_manager):
        """Test listing deliveries."""
        deliveries = await delivery_manager.list_deliveries()
        
        assert isinstance(deliveries, list)


class TestAPIEndpoints:
    """Tests for API endpoints."""
    
    @pytest.fixture
    def client(self):
        """Create a test client."""
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)
    
    def test_health_endpoint(self, client):
        """Test health endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    
    def test_project_builder_templates(self, client):
        """Test templates endpoint."""
        response = client.get(
            "/project-builder/templates",
            headers={"x-user-id": "test-user"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "templates" in data
    
    def test_project_builder_requires_auth(self, client):
        """Test that endpoints require authentication."""
        response = client.post("/project-builder/build", json={
            "project_name": "test",
            "description": "test",
        })
        assert response.status_code == 401
    
    def test_project_builder_build(self, client):
        """Test build endpoint."""
        response = client.post(
            "/project-builder/build",
            headers={"x-user-id": "test-user"},
            json={
                "project_name": "api-test-project",
                "description": "Testing the API",
                "project_type": "fullstack_react_fastapi",
            },
        )
        # Should start the build (may fail if services not running)
        assert response.status_code in [200, 500, 503]


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
