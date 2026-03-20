"""
Sandbox boundary for Agent Engine tool execution.
Prevents arbitrary calls and enforces security constraints.
"""

import asyncio
import time
import re
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Set, Callable, Awaitable
from enum import Enum
import hashlib


class PermissionLevel(Enum):
    DENY = 0
    READ = 1
    WRITE = 2
    EXECUTE = 3
    ADMIN = 4


class ResourceType(Enum):
    FILE = "file"
    NETWORK = "network"
    DATABASE = "database"
    API = "api"
    SYSTEM = "system"
    MEMORY = "memory"
    TOOL = "tool"


@dataclass
class ToolPermission:
    """Permission definition for a tool."""
    tool_name: str
    allowed: bool = True
    
    # Resource access
    allowed_resources: Set[ResourceType] = field(default_factory=set)
    denied_resources: Set[ResourceType] = field(default_factory=set)
    
    # Rate limits
    max_calls_per_minute: int = 100
    max_calls_per_hour: int = 1000
    
    # Argument constraints
    allowed_arg_patterns: Dict[str, str] = field(default_factory=dict)  # arg_name -> regex
    denied_arg_patterns: Dict[str, str] = field(default_factory=dict)
    
    # Output constraints
    max_output_size_bytes: int = 1024 * 1024  # 1MB
    
    # Timeout
    max_execution_seconds: float = 60.0
    
    def validate_args(self, args: Dict[str, Any]) -> List[str]:
        """Validate arguments against patterns. Returns violations."""
        violations = []
        
        for arg_name, pattern in self.allowed_arg_patterns.items():
            if arg_name in args:
                value = str(args[arg_name])
                if not re.match(pattern, value):
                    violations.append(f"Argument {arg_name} doesn't match allowed pattern")
        
        for arg_name, pattern in self.denied_arg_patterns.items():
            if arg_name in args:
                value = str(args[arg_name])
                if re.match(pattern, value):
                    violations.append(f"Argument {arg_name} matches denied pattern")
        
        return violations


@dataclass
class SandboxConfig:
    """Configuration for sandbox environment."""
    # Global limits
    max_concurrent_tools: int = 5
    max_total_calls_per_session: int = 1000
    max_session_duration_seconds: float = 3600.0
    
    # Resource limits
    max_memory_mb: int = 512
    max_cpu_seconds: float = 300.0
    max_network_requests: int = 100
    max_file_operations: int = 50
    
    # Network restrictions
    allowed_hosts: Set[str] = field(default_factory=lambda: {"localhost", "127.0.0.1"})
    denied_hosts: Set[str] = field(default_factory=lambda: {"169.254.169.254"})  # AWS metadata
    allowed_ports: Set[int] = field(default_factory=lambda: {80, 443, 8000, 8080})
    
    # File restrictions
    allowed_paths: List[str] = field(default_factory=lambda: ["/tmp", "/var/tmp"])
    denied_paths: List[str] = field(default_factory=lambda: ["/etc", "/root", "/home"])
    
    # Default tool permissions
    default_permission: ToolPermission = field(default_factory=lambda: ToolPermission(
        tool_name="*",
        allowed=True,
        max_calls_per_minute=60,
    ))


@dataclass
class SandboxViolation:
    """Record of a sandbox violation."""
    violation_type: str
    tool_name: str
    message: str
    timestamp: float = field(default_factory=time.time)
    args: Optional[Dict[str, Any]] = None
    severity: str = "warning"  # warning, error, critical


class SandboxBoundary:
    """
    Enforces security boundaries for agent tool execution.
    
    Features:
    - Tool permission management
    - Rate limiting per tool
    - Argument validation
    - Resource access control
    - Audit logging
    """
    
    def __init__(self, config: Optional[SandboxConfig] = None):
        self.config = config or SandboxConfig()
        
        self._permissions: Dict[str, ToolPermission] = {}
        self._call_counts: Dict[str, List[float]] = {}  # tool -> timestamps
        self._session_calls: int = 0
        self._session_start: float = time.time()
        
        self._violations: List[SandboxViolation] = []
        self._blocked_calls: int = 0
        
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_tools)
    
    def register_tool(self, permission: ToolPermission) -> None:
        """Register a tool with its permissions."""
        self._permissions[permission.tool_name] = permission
    
    def get_permission(self, tool_name: str) -> ToolPermission:
        """Get permission for a tool."""
        return self._permissions.get(tool_name, self.config.default_permission)
    
    def _check_rate_limit(self, tool_name: str, permission: ToolPermission) -> Optional[str]:
        """Check if tool is within rate limits."""
        now = time.time()
        
        if tool_name not in self._call_counts:
            self._call_counts[tool_name] = []
        
        # Clean old entries
        minute_ago = now - 60
        hour_ago = now - 3600
        self._call_counts[tool_name] = [
            t for t in self._call_counts[tool_name] if t > hour_ago
        ]
        
        # Check minute limit
        minute_calls = sum(1 for t in self._call_counts[tool_name] if t > minute_ago)
        if minute_calls >= permission.max_calls_per_minute:
            return f"Rate limit exceeded: {minute_calls}/{permission.max_calls_per_minute} calls per minute"
        
        # Check hour limit
        hour_calls = len(self._call_counts[tool_name])
        if hour_calls >= permission.max_calls_per_hour:
            return f"Rate limit exceeded: {hour_calls}/{permission.max_calls_per_hour} calls per hour"
        
        return None
    
    def _check_session_limits(self) -> Optional[str]:
        """Check session-level limits."""
        if self._session_calls >= self.config.max_total_calls_per_session:
            return f"Session call limit exceeded: {self._session_calls}"
        
        elapsed = time.time() - self._session_start
        if elapsed > self.config.max_session_duration_seconds:
            return f"Session duration exceeded: {elapsed:.0f}s"
        
        return None
    
    def _check_network_access(self, host: str, port: int) -> Optional[str]:
        """Check if network access is allowed."""
        if host in self.config.denied_hosts:
            return f"Access to host {host} is denied"
        
        if self.config.allowed_hosts and host not in self.config.allowed_hosts:
            return f"Host {host} not in allowed list"
        
        if self.config.allowed_ports and port not in self.config.allowed_ports:
            return f"Port {port} not in allowed list"
        
        return None
    
    def _check_file_access(self, path: str) -> Optional[str]:
        """Check if file access is allowed."""
        # Check denied paths first
        for denied in self.config.denied_paths:
            if path.startswith(denied):
                return f"Access to path {path} is denied"
        
        # Check allowed paths
        if self.config.allowed_paths:
            allowed = any(path.startswith(p) for p in self.config.allowed_paths)
            if not allowed:
                return f"Path {path} not in allowed list"
        
        return None
    
    async def validate_call(
        self,
        tool_name: str,
        args: Dict[str, Any],
    ) -> tuple:
        """
        Validate a tool call before execution.
        
        Returns:
            (allowed: bool, violations: List[str])
        """
        violations = []
        
        # Get permission
        permission = self.get_permission(tool_name)
        
        # Check if tool is allowed
        if not permission.allowed:
            violations.append(f"Tool {tool_name} is not allowed")
            self._record_violation("tool_denied", tool_name, violations[0], args)
            return False, violations
        
        # Check session limits
        session_error = self._check_session_limits()
        if session_error:
            violations.append(session_error)
            self._record_violation("session_limit", tool_name, session_error, args)
            return False, violations
        
        # Check rate limits
        rate_error = self._check_rate_limit(tool_name, permission)
        if rate_error:
            violations.append(rate_error)
            self._record_violation("rate_limit", tool_name, rate_error, args)
            return False, violations
        
        # Validate arguments
        arg_violations = permission.validate_args(args)
        if arg_violations:
            violations.extend(arg_violations)
            for v in arg_violations:
                self._record_violation("invalid_args", tool_name, v, args)
            return False, violations
        
        # Check for network access in args
        if "url" in args or "host" in args:
            host = args.get("host", "")
            if "url" in args:
                # Extract host from URL
                import urllib.parse
                parsed = urllib.parse.urlparse(args["url"])
                host = parsed.hostname or ""
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
            else:
                port = args.get("port", 80)
            
            network_error = self._check_network_access(host, port)
            if network_error:
                violations.append(network_error)
                self._record_violation("network_denied", tool_name, network_error, args)
                return False, violations
        
        # Check for file access in args
        if "path" in args or "file" in args or "filename" in args:
            path = args.get("path") or args.get("file") or args.get("filename", "")
            file_error = self._check_file_access(path)
            if file_error:
                violations.append(file_error)
                self._record_violation("file_denied", tool_name, file_error, args)
                return False, violations
        
        return True, []
    
    async def execute_tool(
        self,
        tool_name: str,
        handler: Callable[..., Awaitable[Any]],
        args: Dict[str, Any],
    ) -> Any:
        """
        Execute a tool within the sandbox boundary.
        
        Raises:
            PermissionError: If call is not allowed
            TimeoutError: If execution exceeds timeout
        """
        # Validate call
        allowed, violations = await self.validate_call(tool_name, args)
        if not allowed:
            self._blocked_calls += 1
            raise PermissionError(f"Tool call blocked: {violations}")
        
        permission = self.get_permission(tool_name)
        
        async with self._semaphore:
            # Record call
            now = time.time()
            if tool_name not in self._call_counts:
                self._call_counts[tool_name] = []
            self._call_counts[tool_name].append(now)
            self._session_calls += 1
            
            try:
                # Execute with timeout
                result = await asyncio.wait_for(
                    handler(**args),
                    timeout=permission.max_execution_seconds,
                )
                
                # Check output size
                result_size = len(str(result).encode())
                if result_size > permission.max_output_size_bytes:
                    self._record_violation(
                        "output_size",
                        tool_name,
                        f"Output size {result_size} exceeds limit",
                        args,
                    )
                    # Truncate result
                    result = str(result)[:permission.max_output_size_bytes]
                
                return result
                
            except asyncio.TimeoutError:
                self._record_violation(
                    "timeout",
                    tool_name,
                    f"Execution exceeded {permission.max_execution_seconds}s",
                    args,
                    severity="error",
                )
                raise
    
    def _record_violation(
        self,
        violation_type: str,
        tool_name: str,
        message: str,
        args: Optional[Dict[str, Any]] = None,
        severity: str = "warning",
    ) -> None:
        """Record a sandbox violation."""
        # Sanitize args for logging
        safe_args = None
        if args:
            safe_args = {k: "***" if "password" in k.lower() or "secret" in k.lower() or "key" in k.lower() else v for k, v in args.items()}
        
        violation = SandboxViolation(
            violation_type=violation_type,
            tool_name=tool_name,
            message=message,
            args=safe_args,
            severity=severity,
        )
        self._violations.append(violation)
        
        # Keep only last 1000 violations
        if len(self._violations) > 1000:
            self._violations = self._violations[-1000:]
    
    def reset_session(self) -> None:
        """Reset session counters."""
        self._session_calls = 0
        self._session_start = time.time()
        self._call_counts.clear()
    
    def get_violations(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent violations."""
        return [
            {
                "type": v.violation_type,
                "tool": v.tool_name,
                "message": v.message,
                "timestamp": v.timestamp,
                "severity": v.severity,
            }
            for v in self._violations[-limit:]
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get sandbox statistics."""
        return {
            "session_calls": self._session_calls,
            "session_duration_seconds": time.time() - self._session_start,
            "blocked_calls": self._blocked_calls,
            "total_violations": len(self._violations),
            "registered_tools": len(self._permissions),
            "call_counts": {k: len(v) for k, v in self._call_counts.items()},
        }


# Default tool permissions for ResonantGenesis
DEFAULT_TOOL_PERMISSIONS = [
    ToolPermission(
        tool_name="web_search",
        allowed=True,
        allowed_resources={ResourceType.NETWORK},
        max_calls_per_minute=30,
    ),
    ToolPermission(
        tool_name="read_file",
        allowed=True,
        allowed_resources={ResourceType.FILE},
        max_calls_per_minute=60,
        denied_arg_patterns={"path": r"^/(etc|root|home).*"},
    ),
    ToolPermission(
        tool_name="write_file",
        allowed=True,
        allowed_resources={ResourceType.FILE},
        max_calls_per_minute=30,
        allowed_arg_patterns={"path": r"^/tmp/.*"},
    ),
    ToolPermission(
        tool_name="execute_code",
        allowed=True,
        allowed_resources={ResourceType.SYSTEM},
        max_calls_per_minute=10,
        max_execution_seconds=30.0,
    ),
    ToolPermission(
        tool_name="database_query",
        allowed=True,
        allowed_resources={ResourceType.DATABASE},
        max_calls_per_minute=100,
        denied_arg_patterns={"query": r".*(DROP|DELETE|TRUNCATE|ALTER).*"},
    ),
]


def create_default_sandbox() -> SandboxBoundary:
    """Create sandbox with default permissions."""
    sandbox = SandboxBoundary()
    for perm in DEFAULT_TOOL_PERMISSIONS:
        sandbox.register_tool(perm)
    return sandbox
