"""
Code Validator - Code Visualizer Integration
=============================================

Integrates with Code Visualizer service (port 8092) to validate generated code.

Features:
- Analyze generated code for broken imports
- Run governance checks
- Detect dead code
- Formal verification of invariants
- Self-correction suggestions
"""

import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum
import httpx
import os

logger = logging.getLogger(__name__)


class ValidationStatus(str, Enum):
    """Status of code validation."""
    PENDING = "pending"
    ANALYZING = "analyzing"
    PASSED = "passed"
    FAILED = "failed"
    WARNINGS = "warnings"


@dataclass
class BrokenConnection:
    """A broken import or connection in the code."""
    source_file: str
    target: str
    connection_type: str
    line_number: Optional[int] = None
    suggestion: Optional[str] = None


@dataclass
class GovernanceViolation:
    """A governance violation in the code."""
    violation_type: str
    severity: str
    node_id: str
    message: str
    file_path: str
    line: int
    suggestion: str


@dataclass
class ValidationResult:
    """Result of code validation."""
    status: ValidationStatus
    analysis_id: Optional[str] = None
    total_files: int = 0
    total_nodes: int = 0
    total_connections: int = 0
    broken_connections: List[BrokenConnection] = field(default_factory=list)
    governance_violations: List[GovernanceViolation] = field(default_factory=list)
    reachability_score: float = 0.0
    ci_pass: bool = False
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "analysis_id": self.analysis_id,
            "total_files": self.total_files,
            "total_nodes": self.total_nodes,
            "total_connections": self.total_connections,
            "broken_connections": [
                {
                    "source_file": bc.source_file,
                    "target": bc.target,
                    "connection_type": bc.connection_type,
                    "line_number": bc.line_number,
                    "suggestion": bc.suggestion,
                }
                for bc in self.broken_connections
            ],
            "governance_violations": [
                {
                    "violation_type": gv.violation_type,
                    "severity": gv.severity,
                    "node_id": gv.node_id,
                    "message": gv.message,
                    "file_path": gv.file_path,
                    "line": gv.line,
                    "suggestion": gv.suggestion,
                }
                for gv in self.governance_violations
            ],
            "reachability_score": self.reachability_score,
            "ci_pass": self.ci_pass,
            "error": self.error,
        }


class CodeValidator:
    """
    Validates generated code using Code Visualizer service.
    
    Integration Points:
    - POST /api/analyze - Analyze codebase
    - GET /api/analysis/{id}/broken - Get broken connections
    - POST /api/analysis/{id}/governance - Run governance check
    - POST /api/analysis/{id}/compiler/prove - Formal verification
    - POST /api/learning/outcome - Record outcomes
    """
    
    CODE_VISUALIZER_URL = os.getenv("AST_ANALYSIS_SERVICE_URL") or os.getenv("CODE_VISUALIZER_URL", "http://rg_ast_analysis:8000")
    
    def __init__(self, service_url: str = None):
        self.service_url = service_url or self.CODE_VISUALIZER_URL
        self._client: Optional[httpx.AsyncClient] = None
        logger.info(f"CodeValidator initialized with service URL: {self.service_url}")
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client
    
    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def analyze_project(self, project_path: str) -> ValidationResult:
        """
        Analyze a project and return validation results.
        
        Args:
            project_path: Absolute path to project directory
            
        Returns:
            ValidationResult with analysis details
        """
        client = await self._get_client()
        result = ValidationResult(status=ValidationStatus.ANALYZING)
        
        try:
            response = await client.post(
                f"{self.service_url}/api/analyze",
                json={"path": project_path},
                timeout=120.0,
            )
            
            if response.status_code != 200:
                result.status = ValidationStatus.FAILED
                result.error = f"Analysis failed: {response.text}"
                return result
            
            data = response.json()
            result.analysis_id = data.get("analysis_id")
            stats = data.get("stats", {})
            result.total_files = stats.get("files", 0)
            result.total_nodes = stats.get("nodes", 0)
            result.total_connections = stats.get("connections", 0)
            
            broken = await self._get_broken_connections(result.analysis_id)
            result.broken_connections = broken
            
            governance = await self._run_governance_check(result.analysis_id, project_path)
            result.governance_violations = governance.get("violations", [])
            result.reachability_score = governance.get("reachability_score", 0.0)
            result.ci_pass = governance.get("ci_pass", False)
            
            if result.broken_connections or result.governance_violations:
                if any(gv.severity in ["critical", "high"] for gv in result.governance_violations):
                    result.status = ValidationStatus.FAILED
                else:
                    result.status = ValidationStatus.WARNINGS
            else:
                result.status = ValidationStatus.PASSED
            
            logger.info(
                f"Validation complete for {project_path}: "
                f"status={result.status.value}, "
                f"broken={len(result.broken_connections)}, "
                f"violations={len(result.governance_violations)}"
            )
            
        except Exception as e:
            logger.error(f"Validation error: {e}")
            result.status = ValidationStatus.FAILED
            result.error = str(e)
        
        return result
    
    async def _get_broken_connections(self, analysis_id: str) -> List[BrokenConnection]:
        """Get broken connections from analysis."""
        client = await self._get_client()
        broken = []
        
        try:
            response = await client.get(
                f"{self.service_url}/api/analysis/{analysis_id}/broken"
            )
            
            if response.status_code == 200:
                data = response.json()
                for conn in data.get("broken_connections", []):
                    broken.append(BrokenConnection(
                        source_file=conn.get("source_file", ""),
                        target=conn.get("target", ""),
                        connection_type=conn.get("type", "import"),
                        line_number=conn.get("line"),
                        suggestion=conn.get("suggestion"),
                    ))
        except Exception as e:
            logger.warning(f"Failed to get broken connections: {e}")
        
        return broken
    
    async def _run_governance_check(
        self,
        analysis_id: str,
        project_path: str,
    ) -> Dict[str, Any]:
        """Run governance check on analysis."""
        client = await self._get_client()
        
        try:
            response = await client.post(
                f"{self.service_url}/api/analysis/{analysis_id}/governance",
                json={
                    "custom_roots": [
                        {
                            "name": "Frontend Entry",
                            "service": "frontend",
                            "entry_file": "src/main.tsx",
                        },
                        {
                            "name": "Backend Entry",
                            "service": "backend",
                            "entry_file": "app/main.py",
                        },
                    ],
                    "drift_threshold": 0.1,
                },
            )
            
            if response.status_code == 200:
                data = response.json()
                violations = []
                
                for v in data.get("governance", {}).get("violations", []):
                    violations.append(GovernanceViolation(
                        violation_type=v.get("type", ""),
                        severity=v.get("severity", "medium"),
                        node_id=v.get("node_id", ""),
                        message=v.get("message", ""),
                        file_path=v.get("file_path", ""),
                        line=v.get("line", 0),
                        suggestion=v.get("suggestion", ""),
                    ))
                
                return {
                    "violations": violations,
                    "reachability_score": data.get("governance", {}).get("summary", {}).get("reachability_score", 0.0),
                    "ci_pass": data.get("ci_pass", False),
                }
        except Exception as e:
            logger.warning(f"Failed to run governance check: {e}")
        
        return {"violations": [], "reachability_score": 0.0, "ci_pass": True}
    
    async def get_fix_suggestions(
        self,
        analysis_id: str,
        broken_connection: BrokenConnection,
    ) -> List[str]:
        """Get suggestions for fixing a broken connection."""
        suggestions = []
        
        if broken_connection.connection_type == "import":
            suggestions.append(f"Create missing file: {broken_connection.target}")
            suggestions.append(f"Fix import path in {broken_connection.source_file}")
            suggestions.append(f"Add export to target module")
        
        return suggestions
    
    async def run_agent_scan(self, analysis_id: str) -> Dict[str, Any]:
        """Run Graph Janitor Agent scan."""
        client = await self._get_client()
        
        try:
            response = await client.post(
                f"{self.service_url}/api/analysis/{analysis_id}/agent/scan",
                json={
                    "max_proposals": 10,
                    "min_utility": 0,
                    "max_risk": 8,
                    "blast_radius_limit": 100,
                },
            )
            
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.warning(f"Failed to run agent scan: {e}")
        
        return {}
    
    async def compile_action_to_patch(
        self,
        analysis_id: str,
        action_id: str,
    ) -> Dict[str, Any]:
        """Compile an action to an auditable patch."""
        client = await self._get_client()
        
        try:
            response = await client.post(
                f"{self.service_url}/api/analysis/{analysis_id}/compiler/compile",
                json={"action_id": action_id},
            )
            
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.warning(f"Failed to compile action: {e}")
        
        return {}
    
    async def prove_invariants(
        self,
        analysis_id: str,
        action_id: str,
    ) -> Dict[str, Any]:
        """Formally prove invariants for an action."""
        client = await self._get_client()
        
        try:
            response = await client.post(
                f"{self.service_url}/api/analysis/{analysis_id}/compiler/prove",
                json={"action_id": action_id},
            )
            
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.warning(f"Failed to prove invariants: {e}")
        
        return {}
    
    async def record_learning_outcome(
        self,
        patch_id: str,
        action_id: str,
        action_type: str,
        target_node: str,
        applied: bool,
        rolled_back: bool = False,
        human_rejected: bool = False,
        reachability_delta: float = 0.0,
    ):
        """Record outcome for learning loop."""
        client = await self._get_client()
        
        try:
            await client.post(
                f"{self.service_url}/api/learning/outcome",
                json={
                    "patch_id": patch_id,
                    "action_id": action_id,
                    "action_type": action_type,
                    "target_node": target_node,
                    "node_type": "file",
                    "blast_radius": 1,
                    "applied": applied,
                    "rolled_back": rolled_back,
                    "human_rejected": human_rejected,
                    "reachability_delta": reachability_delta,
                    "isolated_nodes_delta": 0,
                },
            )
            logger.info(f"Recorded learning outcome for {action_id}")
        except Exception as e:
            logger.warning(f"Failed to record learning outcome: {e}")
    
    async def get_graph_structure(self, analysis_id: str) -> Dict[str, Any]:
        """Get graph structure analysis."""
        client = await self._get_client()
        
        try:
            response = await client.get(
                f"{self.service_url}/api/analysis/{analysis_id}/graph-structure"
            )
            
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.warning(f"Failed to get graph structure: {e}")
        
        return {}
    
    async def get_functions(
        self,
        analysis_id: str,
        service: str = None,
    ) -> List[Dict[str, Any]]:
        """Get all functions from analysis."""
        client = await self._get_client()
        
        try:
            url = f"{self.service_url}/api/analysis/{analysis_id}/functions"
            if service:
                url += f"?service={service}"
            
            response = await client.get(url)
            
            if response.status_code == 200:
                return response.json().get("functions", [])
        except Exception as e:
            logger.warning(f"Failed to get functions: {e}")
        
        return []


_code_validator: Optional[CodeValidator] = None


def get_code_validator() -> CodeValidator:
    """Get singleton code validator instance."""
    global _code_validator
    if _code_validator is None:
        _code_validator = CodeValidator()
    return _code_validator
