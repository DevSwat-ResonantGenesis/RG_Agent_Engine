"""Repo-to-Agent: Convert GitHub repositories into AI agents.

This viral feature allows users to:
1. Paste a GitHub repo URL
2. Automatically analyze the codebase
3. Generate an AI agent that understands and can work with the code
"""

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AgentDefinition, AgentVersion
from .config import settings
from .routers import compute_manifest_hash


class RepoType(str, Enum):
    """Repository type classification."""
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    RUST = "rust"
    GO = "go"
    JAVA = "java"
    CSHARP = "csharp"
    RUBY = "ruby"
    PHP = "php"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass
class RepoAnalysis:
    """Analysis results for a repository."""
    repo_url: str
    repo_name: str
    owner: str
    description: Optional[str]
    primary_language: str
    languages: Dict[str, int]
    file_count: int
    total_size: int
    readme_content: Optional[str]
    structure: Dict[str, Any]
    key_files: List[str]
    dependencies: List[str]
    entry_points: List[str]
    api_endpoints: List[Dict[str, str]]
    functions: List[Dict[str, str]]
    classes: List[Dict[str, str]]
    suggested_tools: List[str]
    suggested_capabilities: List[str]


class GitHubAnalyzer:
    """Analyzes GitHub repositories."""

    GITHUB_API = "https://api.github.com"

    # File patterns for different purposes
    CONFIG_FILES = {
        "package.json", "requirements.txt", "Pipfile", "pyproject.toml",
        "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "Gemfile",
        "composer.json", ".env.example", "config.yaml", "config.json",
    }

    ENTRY_POINT_PATTERNS = [
        r"main\.(py|js|ts|go|rs|java|rb|php)$",
        r"app\.(py|js|ts)$",
        r"index\.(py|js|ts)$",
        r"server\.(py|js|ts)$",
        r"__main__\.py$",
    ]

    API_PATTERNS = {
        "python": [
            r"@app\.(get|post|put|delete|patch)\(['\"]([^'\"]+)",
            r"@router\.(get|post|put|delete|patch)\(['\"]([^'\"]+)",
            r"@api_view\(\[([^\]]+)\]\)",
        ],
        "javascript": [
            r"app\.(get|post|put|delete|patch)\(['\"]([^'\"]+)",
            r"router\.(get|post|put|delete|patch)\(['\"]([^'\"]+)",
        ],
    }

    def __init__(self, github_token: Optional[str] = None):
        self.github_token = github_token or settings.GITHUB_TOKEN if hasattr(settings, 'GITHUB_TOKEN') else None
        self.headers = {"Accept": "application/vnd.github.v3+json"}
        if self.github_token:
            self.headers["Authorization"] = f"token {self.github_token}"

    def parse_repo_url(self, url: str) -> Tuple[str, str]:
        """Parse GitHub URL to extract owner and repo name."""
        patterns = [
            r"github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/.*)?$",
            r"^([^/]+)/([^/]+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1), match.group(2).rstrip(".git")
        raise ValueError(f"Invalid GitHub URL: {url}")

    async def analyze_repo(self, repo_url: str) -> RepoAnalysis:
        """Analyze a GitHub repository."""
        owner, repo = self.parse_repo_url(repo_url)

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get repo info
            repo_info = await self._get_repo_info(client, owner, repo)

            # Get languages
            languages = await self._get_languages(client, owner, repo)

            # Get file tree
            tree = await self._get_tree(client, owner, repo)

            # Get README
            readme = await self._get_readme(client, owner, repo)

            # Analyze structure
            structure = self._analyze_structure(tree)

            # Find key files
            key_files = self._find_key_files(tree)

            # Find entry points
            entry_points = self._find_entry_points(tree)

            # Get dependencies
            dependencies = await self._get_dependencies(client, owner, repo, tree)

            # Analyze code for APIs and functions
            api_endpoints, functions, classes = await self._analyze_code(
                client, owner, repo, tree, languages
            )

            # Suggest tools and capabilities
            suggested_tools = self._suggest_tools(languages, structure, api_endpoints)
            suggested_capabilities = self._suggest_capabilities(
                repo_info, readme, structure, api_endpoints
            )

            return RepoAnalysis(
                repo_url=repo_url,
                repo_name=repo,
                owner=owner,
                description=repo_info.get("description"),
                primary_language=repo_info.get("language", "unknown"),
                languages=languages,
                file_count=len(tree),
                total_size=repo_info.get("size", 0),
                readme_content=readme,
                structure=structure,
                key_files=key_files,
                dependencies=dependencies,
                entry_points=entry_points,
                api_endpoints=api_endpoints,
                functions=functions,
                classes=classes,
                suggested_tools=suggested_tools,
                suggested_capabilities=suggested_capabilities,
            )

    async def _get_repo_info(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, Any]:
        """Get repository information."""
        resp = await client.get(
            f"{self.GITHUB_API}/repos/{owner}/{repo}",
            headers=self.headers,
        )
        if resp.status_code == 200:
            return resp.json()
        return {}

    async def _get_languages(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Dict[str, int]:
        """Get repository languages."""
        resp = await client.get(
            f"{self.GITHUB_API}/repos/{owner}/{repo}/languages",
            headers=self.headers,
        )
        if resp.status_code == 200:
            return resp.json()
        return {}

    async def _get_tree(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> List[Dict[str, Any]]:
        """Get repository file tree."""
        resp = await client.get(
            f"{self.GITHUB_API}/repos/{owner}/{repo}/git/trees/HEAD?recursive=1",
            headers=self.headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("tree", [])
        return []

    async def _get_readme(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> Optional[str]:
        """Get README content."""
        resp = await client.get(
            f"{self.GITHUB_API}/repos/{owner}/{repo}/readme",
            headers=self.headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("encoding") == "base64":
                import base64
                return base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
        return None

    async def _get_file_content(
        self, client: httpx.AsyncClient, owner: str, repo: str, path: str
    ) -> Optional[str]:
        """Get file content."""
        resp = await client.get(
            f"{self.GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
            headers=self.headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("encoding") == "base64":
                import base64
                return base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
        return None

    def _analyze_structure(self, tree: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze repository structure."""
        structure = {
            "directories": set(),
            "file_types": {},
            "has_tests": False,
            "has_docs": False,
            "has_ci": False,
            "has_docker": False,
            "has_api": False,
        }

        for item in tree:
            path = item.get("path", "")

            if item.get("type") == "tree":
                structure["directories"].add(path)
            else:
                # Count file types
                ext = path.rsplit(".", 1)[-1] if "." in path else "none"
                structure["file_types"][ext] = structure["file_types"].get(ext, 0) + 1

            # Check for specific patterns
            path_lower = path.lower()
            if "test" in path_lower or "spec" in path_lower:
                structure["has_tests"] = True
            if "doc" in path_lower or "readme" in path_lower:
                structure["has_docs"] = True
            if ".github/workflows" in path or ".gitlab-ci" in path or "jenkinsfile" in path_lower:
                structure["has_ci"] = True
            if "dockerfile" in path_lower or "docker-compose" in path_lower:
                structure["has_docker"] = True
            if "api" in path_lower or "routes" in path_lower or "endpoints" in path_lower:
                structure["has_api"] = True

        structure["directories"] = list(structure["directories"])
        return structure

    def _find_key_files(self, tree: List[Dict[str, Any]]) -> List[str]:
        """Find key configuration and entry files."""
        key_files = []
        for item in tree:
            if item.get("type") == "blob":
                filename = item.get("path", "").split("/")[-1]
                if filename in self.CONFIG_FILES:
                    key_files.append(item["path"])
        return key_files

    def _find_entry_points(self, tree: List[Dict[str, Any]]) -> List[str]:
        """Find potential entry points."""
        entry_points = []
        for item in tree:
            if item.get("type") == "blob":
                path = item.get("path", "")
                for pattern in self.ENTRY_POINT_PATTERNS:
                    if re.search(pattern, path):
                        entry_points.append(path)
                        break
        return entry_points

    async def _get_dependencies(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
        tree: List[Dict[str, Any]],
    ) -> List[str]:
        """Extract dependencies from config files."""
        dependencies = []

        for item in tree:
            path = item.get("path", "")
            filename = path.split("/")[-1]

            if filename == "requirements.txt":
                content = await self._get_file_content(client, owner, repo, path)
                if content:
                    for line in content.split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#"):
                            dep = re.split(r"[=<>!~]", line)[0].strip()
                            if dep:
                                dependencies.append(f"python:{dep}")

            elif filename == "package.json":
                content = await self._get_file_content(client, owner, repo, path)
                if content:
                    try:
                        pkg = json.loads(content)
                        for dep in pkg.get("dependencies", {}):
                            dependencies.append(f"npm:{dep}")
                    except json.JSONDecodeError:
                        pass

        return dependencies[:50]  # Limit to 50 dependencies

    async def _analyze_code(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
        tree: List[Dict[str, Any]],
        languages: Dict[str, int],
    ) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """Analyze code for APIs, functions, and classes."""
        api_endpoints = []
        functions = []
        classes = []

        # Determine primary language
        primary_lang = "python" if "Python" in languages else "javascript"

        # Find relevant source files
        source_files = []
        for item in tree:
            if item.get("type") == "blob":
                path = item.get("path", "")
                if path.endswith((".py", ".js", ".ts")) and "test" not in path.lower():
                    source_files.append(path)

        # Analyze up to 10 key files
        for path in source_files[:10]:
            content = await self._get_file_content(client, owner, repo, path)
            if not content:
                continue

            # Find API endpoints
            for pattern in self.API_PATTERNS.get(primary_lang, []):
                for match in re.finditer(pattern, content):
                    api_endpoints.append({
                        "method": match.group(1).upper() if len(match.groups()) > 1 else "GET",
                        "path": match.group(2) if len(match.groups()) > 1 else match.group(1),
                        "file": path,
                    })

            # Find functions (Python)
            if path.endswith(".py"):
                for match in re.finditer(r"def\s+(\w+)\s*\(([^)]*)\)", content):
                    functions.append({
                        "name": match.group(1),
                        "params": match.group(2),
                        "file": path,
                    })
                for match in re.finditer(r"class\s+(\w+)\s*(?:\([^)]*\))?:", content):
                    classes.append({
                        "name": match.group(1),
                        "file": path,
                    })

            # Find functions (JavaScript/TypeScript)
            elif path.endswith((".js", ".ts")):
                for match in re.finditer(r"(?:function|const|let|var)\s+(\w+)\s*=?\s*(?:async\s*)?\(([^)]*)\)", content):
                    functions.append({
                        "name": match.group(1),
                        "params": match.group(2),
                        "file": path,
                    })
                for match in re.finditer(r"class\s+(\w+)", content):
                    classes.append({
                        "name": match.group(1),
                        "file": path,
                    })

        return api_endpoints[:20], functions[:50], classes[:30]

    def _suggest_tools(
        self,
        languages: Dict[str, int],
        structure: Dict[str, Any],
        api_endpoints: List[Dict],
    ) -> List[str]:
        """Suggest tools based on repo analysis."""
        tools = ["code_search", "file_read", "file_write"]

        if "Python" in languages:
            tools.extend(["python_execute", "pip_install"])
        if "JavaScript" in languages or "TypeScript" in languages:
            tools.extend(["node_execute", "npm_install"])

        if structure.get("has_tests"):
            tools.append("run_tests")
        if structure.get("has_docker"):
            tools.extend(["docker_build", "docker_run"])
        if api_endpoints:
            tools.append("http_request")
        if structure.get("has_ci"):
            tools.append("ci_trigger")

        return list(set(tools))

    def _suggest_capabilities(
        self,
        repo_info: Dict[str, Any],
        readme: Optional[str],
        structure: Dict[str, Any],
        api_endpoints: List[Dict],
    ) -> List[str]:
        """Suggest agent capabilities based on analysis."""
        capabilities = []

        description = repo_info.get("description", "") or ""
        readme_text = readme or ""
        combined = f"{description} {readme_text}".lower()

        # Detect capabilities from content
        capability_keywords = {
            "web scraping": ["scrape", "crawl", "spider", "beautifulsoup", "selenium"],
            "data analysis": ["pandas", "numpy", "analysis", "analytics", "data processing"],
            "machine learning": ["ml", "machine learning", "tensorflow", "pytorch", "sklearn"],
            "api integration": ["api", "rest", "graphql", "webhook"],
            "database operations": ["database", "sql", "mongodb", "postgres", "mysql"],
            "file processing": ["file", "csv", "json", "xml", "parse"],
            "automation": ["automate", "automation", "bot", "scheduler"],
            "testing": ["test", "pytest", "jest", "unittest"],
            "deployment": ["deploy", "ci/cd", "docker", "kubernetes"],
        }

        for capability, keywords in capability_keywords.items():
            if any(kw in combined for kw in keywords):
                capabilities.append(capability)

        if api_endpoints:
            capabilities.append("api server")
        if structure.get("has_docker"):
            capabilities.append("containerized deployment")

        return capabilities


class RepoToAgentConverter:
    """Converts repository analysis into an AI agent."""

    SYSTEM_PROMPT_TEMPLATE = """You are an AI agent specialized in working with the {repo_name} codebase.

## Repository Overview
- **Name**: {repo_name}
- **Owner**: {owner}
- **Description**: {description}
- **Primary Language**: {primary_language}

## Capabilities
{capabilities}

## Key Files
{key_files}

## API Endpoints
{api_endpoints}

## Available Tools
{tools}

## Instructions
1. When asked about the codebase, reference specific files and functions
2. Use the available tools to read, search, and modify code
3. Follow the coding patterns and conventions used in this repository
4. When making changes, ensure they are consistent with the existing codebase
5. Always explain your reasoning and the changes you're making

## Context from README
{readme_excerpt}
"""

    async def convert(
        self,
        analysis: RepoAnalysis,
        user_id: str,
        custom_name: Optional[str] = None,
        custom_description: Optional[str] = None,
        db_session: AsyncSession = None,
    ) -> AgentDefinition:
        """Convert repo analysis into an agent definition."""
        # Generate system prompt
        system_prompt = self._generate_system_prompt(analysis)

        # Create agent definition
        from uuid import UUID as PyUUID

        user_uuid = PyUUID(user_id)

        agent_id = uuid4()
        agent_public_hash = f"0x{hashlib.sha256(f'agent_public:{agent_id}:{user_uuid}'.encode('utf-8')).hexdigest()}"

        agent = AgentDefinition(
            id=agent_id,
            user_id=user_uuid,
            name=custom_name or f"{analysis.repo_name} Agent",
            description=custom_description or f"AI agent for {analysis.owner}/{analysis.repo_name}: {analysis.description or 'No description'}",
            system_prompt=system_prompt,
            model="gpt-4-turbo-preview",
            temperature=0.7,
            max_tokens=4096,
            tools=analysis.suggested_tools,
            safety_config={
                "max_file_size": 1000000,
                "allowed_extensions": self._get_allowed_extensions(analysis),
                "blocked_paths": [".git", "node_modules", "__pycache__", ".env"],
                "source": "repo_to_agent",
                "repo_url": analysis.repo_url,
                "repo_owner": analysis.owner,
                "repo_name": analysis.repo_name,
                "primary_language": analysis.primary_language,
                "capabilities": analysis.suggested_capabilities,
                "manifest_hash": None,
                "agent_hash": agent_public_hash,
            },
            agent_public_hash=agent_public_hash,
            is_active=True,
        )

        manifest_hash = compute_manifest_hash(
            name=agent.name,
            description=agent.description,
            system_prompt=agent.system_prompt,
            model=agent.model,
            temperature=agent.temperature,
            max_tokens=agent.max_tokens,
            tools=agent.tools,
            allowed_actions=agent.allowed_actions,
            blocked_actions=agent.blocked_actions,
        )
        agent.agent_version_hash = manifest_hash
        agent.safety_config = {**(agent.safety_config or {}), "manifest_hash": manifest_hash, "agent_hash": agent_public_hash}

        db_session.add(agent)

        db_session.add(
            AgentVersion(
                agent_id=agent.id,
                agent_public_hash=agent_public_hash,
                version_number=int(agent.version or 1),
                agent_version_hash=manifest_hash,
                changelog=None,
                config_snapshot={
                    "name": agent.name,
                    "description": agent.description,
                    "system_prompt": agent.system_prompt,
                    "model": agent.model,
                    "temperature": agent.temperature,
                    "max_tokens": agent.max_tokens,
                    "tools": agent.tools or [],
                    "safety_config": agent.safety_config or {},
                    "agent_public_hash": agent.agent_public_hash,
                    "agent_version_hash": agent.agent_version_hash,
                    "version": agent.version,
                },
            )
        )
        await db_session.commit()
        await db_session.refresh(agent)

        return agent

    def _generate_system_prompt(self, analysis: RepoAnalysis) -> str:
        """Generate system prompt from analysis."""
        # Format capabilities
        capabilities = "\n".join(f"- {cap}" for cap in analysis.suggested_capabilities) or "- General code assistance"

        # Format key files
        key_files = "\n".join(f"- `{f}`" for f in analysis.key_files[:10]) or "- No key files identified"

        # Format API endpoints
        if analysis.api_endpoints:
            api_endpoints = "\n".join(
                f"- {ep['method']} {ep['path']} ({ep['file']})"
                for ep in analysis.api_endpoints[:10]
            )
        else:
            api_endpoints = "- No API endpoints detected"

        # Format tools
        tools = "\n".join(f"- {tool}" for tool in analysis.suggested_tools)

        # Excerpt from README
        readme_excerpt = ""
        if analysis.readme_content:
            readme_excerpt = analysis.readme_content[:2000]
            if len(analysis.readme_content) > 2000:
                readme_excerpt += "\n... (truncated)"

        return self.SYSTEM_PROMPT_TEMPLATE.format(
            repo_name=analysis.repo_name,
            owner=analysis.owner,
            description=analysis.description or "No description provided",
            primary_language=analysis.primary_language,
            capabilities=capabilities,
            key_files=key_files,
            api_endpoints=api_endpoints,
            tools=tools,
            readme_excerpt=readme_excerpt or "No README available",
        )

    def _get_allowed_extensions(self, analysis: RepoAnalysis) -> List[str]:
        """Get allowed file extensions based on repo languages."""
        extensions = [".txt", ".md", ".json", ".yaml", ".yml", ".toml"]

        lang_extensions = {
            "Python": [".py", ".pyi", ".pyx"],
            "JavaScript": [".js", ".jsx", ".mjs"],
            "TypeScript": [".ts", ".tsx"],
            "Rust": [".rs"],
            "Go": [".go"],
            "Java": [".java"],
            "C#": [".cs"],
            "Ruby": [".rb"],
            "PHP": [".php"],
            "HTML": [".html", ".htm"],
            "CSS": [".css", ".scss", ".sass", ".less"],
        }

        for lang in analysis.languages:
            if lang in lang_extensions:
                extensions.extend(lang_extensions[lang])

        return list(set(extensions))


# Singleton instances
github_analyzer = GitHubAnalyzer()
repo_to_agent_converter = RepoToAgentConverter()


async def create_agent_from_repo(
    repo_url: str,
    user_id: str,
    custom_name: Optional[str] = None,
    custom_description: Optional[str] = None,
    db_session: AsyncSession = None,
) -> Dict[str, Any]:
    """Main function to create an agent from a GitHub repo."""
    # Analyze repository
    analysis = await github_analyzer.analyze_repo(repo_url)

    # Convert to agent
    agent = await repo_to_agent_converter.convert(
        analysis=analysis,
        user_id=user_id,
        custom_name=custom_name,
        custom_description=custom_description,
        db_session=db_session,
    )

    return {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "repo_url": analysis.repo_url,
        "repo_name": analysis.repo_name,
        "owner": analysis.owner,
        "primary_language": analysis.primary_language,
        "capabilities": analysis.suggested_capabilities,
        "tools": analysis.suggested_tools,
        "file_count": analysis.file_count,
        "api_endpoints_found": len(analysis.api_endpoints),
        "functions_found": len(analysis.functions),
        "classes_found": len(analysis.classes),
    }
