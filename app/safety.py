"""Safety envelope for agent execution - Hardened security rules."""

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID as PyUUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SafetyRule, AgentSession, AgentStep
from .config import settings

logger = logging.getLogger(__name__)


class ThreatLevel(str, Enum):
    """Threat level classification."""
    CRITICAL = "critical"  # Immediate block, alert admin
    HIGH = "high"  # Block, log incident
    MEDIUM = "medium"  # Require approval
    LOW = "low"  # Warn and log
    INFO = "info"  # Log only


class SafetyEnvelope:
    """Enforces safety constraints on agent actions - Hardened version."""

    # Critical blocked patterns - immediate block
    CRITICAL_PATTERNS = [
        (r"rm\s+-rf\s+/", "Recursive root deletion attempt"),
        (r"rm\s+-rf\s+~", "Home directory deletion attempt"),
        (r"mkfs\.", "Filesystem format attempt"),
        (r"dd\s+if=.*of=/dev/", "Direct disk write attempt"),
        (r":(){ :|:& };:", "Fork bomb detected"),
        (r">\s*/dev/sd[a-z]", "Direct device write"),
        (r"chmod\s+-R\s+777\s+/", "Dangerous permission change"),
        (r"curl.*\|\s*(ba)?sh", "Remote code execution via curl"),
        (r"wget.*\|\s*(ba)?sh", "Remote code execution via wget"),
    ]

    # High-risk patterns - block
    HIGH_RISK_PATTERNS = [
        (r"sudo\s+", "Privilege escalation attempt"),
        (r"su\s+-", "User switch attempt"),
        (r"passwd\s+", "Password change attempt"),
        (r"useradd\s+", "User creation attempt"),
        (r"userdel\s+", "User deletion attempt"),
        (r"chown\s+-R\s+root", "Ownership change to root"),
        (r"/etc/passwd", "Password file access"),
        (r"/etc/shadow", "Shadow file access"),
        (r"\.ssh/", "SSH directory access"),
        (r"id_rsa", "Private key access"),
        (r"authorized_keys", "SSH keys modification"),
    ]

    # SQL injection patterns
    SQL_INJECTION_PATTERNS = [
        (r"DROP\s+(TABLE|DATABASE|INDEX)", "SQL DROP statement"),
        (r"DELETE\s+FROM.*WHERE\s+1\s*=\s*1", "Mass DELETE statement"),
        (r"TRUNCATE\s+TABLE", "SQL TRUNCATE statement"),
        (r";\s*--", "SQL comment injection"),
        (r"UNION\s+SELECT", "SQL UNION injection"),
        (r"OR\s+1\s*=\s*1", "SQL OR injection"),
        (r"'\s*OR\s+'", "SQL string injection"),
        (r"EXEC\s+xp_", "SQL Server xp_ execution"),
        (r"INTO\s+OUTFILE", "SQL file write"),
        (r"LOAD_FILE\s*\(", "SQL file read"),
    ]

    # Code injection patterns
    CODE_INJECTION_PATTERNS = [
        (r"eval\s*\(", "Eval injection"),
        (r"exec\s*\(", "Exec injection"),
        (r"__import__\s*\(", "Python import injection"),
        (r"subprocess\.(call|run|Popen)", "Subprocess execution"),
        (r"os\.(system|popen|exec)", "OS command execution"),
        (r"child_process", "Node.js child process"),
        (r"Runtime\.getRuntime\(\)\.exec", "Java runtime exec"),
        (r"ProcessBuilder", "Java process builder"),
        (r"<script>", "XSS script injection"),
        (r"javascript:", "JavaScript protocol"),
        (r"on(error|load|click)=", "Event handler injection"),
    ]

    # Network/exfiltration patterns
    EXFILTRATION_PATTERNS = [
        (r"nc\s+-[el]", "Netcat listener"),
        (r"ncat\s+", "Ncat usage"),
        (r"socat\s+", "Socat usage"),
        (r"curl\s+.*-d\s+.*@", "Curl data exfiltration"),
        (r"scp\s+.*:", "SCP file transfer"),
        (r"rsync\s+.*:", "Rsync transfer"),
        (r"ftp\s+", "FTP connection"),
        (r"tftp\s+", "TFTP connection"),
    ]

    # Sensitive data patterns - require approval
    SENSITIVE_PATTERNS = [
        (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "Email address"),
        (r"\b\d{3}-\d{2}-\d{4}\b", "SSN format"),
        (r"\b\d{9}\b", "9-digit number (potential SSN)"),
        (r"\b4[0-9]{12}(?:[0-9]{3})?\b", "Visa card number"),
        (r"\b5[1-5][0-9]{14}\b", "Mastercard number"),
        (r"\b3[47][0-9]{13}\b", "Amex card number"),
        (r"\b6(?:011|5[0-9]{2})[0-9]{12}\b", "Discover card number"),
        (r"(password|passwd|pwd)\s*[=:]\s*['\"]?[^\s'\"]+", "Password in text"),
        (r"(api[_-]?key|apikey)\s*[=:]\s*['\"]?[^\s'\"]+", "API key in text"),
        (r"(secret|token|bearer)\s*[=:]\s*['\"]?[^\s'\"]+", "Secret/token in text"),
        (r"(aws_access_key|aws_secret)\s*[=:]\s*['\"]?[^\s'\"]+", "AWS credentials"),
        (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
        (r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----", "Private key"),
        (r"-----BEGIN\s+CERTIFICATE-----", "Certificate"),
    ]

    # Blocked domains for network requests
    BLOCKED_DOMAINS = {
        "localhost", "127.0.0.1", "0.0.0.0",
        "169.254.169.254",  # AWS metadata
        "metadata.google.internal",  # GCP metadata
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",  # Private ranges
    }

    # Blocked file extensions
    BLOCKED_EXTENSIONS = {
        ".exe", ".dll", ".so", ".dylib",  # Executables
        ".sh", ".bash", ".zsh", ".ps1",  # Scripts
        ".bat", ".cmd", ".vbs", ".js",  # Windows scripts
        ".pem", ".key", ".p12", ".pfx",  # Keys/certs
    }

    # Maximum sizes
    MAX_OUTPUT_SIZE = 1_000_000  # 1MB
    MAX_INPUT_SIZE = 100_000  # 100KB
    MAX_URL_LENGTH = 2048

    def __init__(self):
        self.custom_rules: List[SafetyRule] = []
        self.incident_log: List[Dict[str, Any]] = []
        self.blocked_ips: Set[str] = set()
        self.rate_limit_cache: Dict[str, List[float]] = {}

    async def load_rules(self, session: AsyncSession, agent_id: Optional[str] = None):
        """Load safety rules from database."""
        stmt = select(SafetyRule).where(SafetyRule.is_active == True)
        if agent_id:
            try:
                agent_uuid = PyUUID(agent_id)
            except ValueError:
                agent_uuid = None

            if agent_uuid:
                stmt = stmt.where(
                    (SafetyRule.applies_to_agents == None) |
                    (SafetyRule.applies_to_agents.contains([agent_uuid]))
                )
        stmt = stmt.order_by(SafetyRule.priority.desc())

        try:
            result = await session.execute(stmt)
            self.custom_rules = result.scalars().all()
        except SQLAlchemyError as e:
            logger.warning(f"Safety rules load failed: {e}")
            try:
                await session.rollback()
            except Exception as rollback_error:
                logger.warning(f"Safety rules rollback failed: {rollback_error}")
            self.custom_rules = []

    def _log_incident(
        self,
        threat_level: ThreatLevel,
        category: str,
        description: str,
        action_data: Dict[str, Any],
        session_id: Optional[str] = None,
    ):
        """Log a security incident."""
        incident = {
            "timestamp": datetime.utcnow().isoformat(),
            "threat_level": threat_level.value,
            "category": category,
            "description": description,
            "session_id": session_id,
            "action_hash": hashlib.sha256(str(action_data).encode()).hexdigest()[:16],
        }
        self.incident_log.append(incident)
        # Keep only last 1000 incidents in memory
        if len(self.incident_log) > 1000:
            self.incident_log = self.incident_log[-1000:]

    def _check_patterns(
        self,
        content: str,
        patterns: List[Tuple[str, str]],
    ) -> List[Tuple[str, str]]:
        """Check content against pattern list. Returns list of (pattern, description) matches."""
        matches = []
        for pattern, description in patterns:
            if re.search(pattern, content, re.IGNORECASE):
                matches.append((pattern, description))
        return matches

    def _check_url_safety(self, url: str) -> Tuple[bool, str]:
        """Check if a URL is safe to access."""
        if len(url) > self.MAX_URL_LENGTH:
            return False, "URL exceeds maximum length"
        
        # Check for blocked domains
        for domain in self.BLOCKED_DOMAINS:
            if domain in url.lower():
                return False, f"Access to {domain} is blocked"
        
        # Check for suspicious protocols
        if url.startswith(("file://", "ftp://", "gopher://", "data:")):
            return False, "Blocked protocol in URL"
        
        return True, ""

    def _check_file_safety(self, filepath: str) -> Tuple[bool, str]:
        """Check if a file path is safe."""
        # Check extension
        for ext in self.BLOCKED_EXTENSIONS:
            if filepath.lower().endswith(ext):
                return False, f"Blocked file extension: {ext}"
        
        # Check for path traversal
        if ".." in filepath or filepath.startswith("/"):
            return False, "Path traversal or absolute path detected"
        
        return True, ""

    async def check_action(
        self,
        action_type: str,
        action_data: Dict[str, Any],
        agent_session: AgentSession,
        session: AsyncSession,
    ) -> Tuple[bool, List[str], bool]:
        """
        Check if an action is allowed with hardened security.
        
        Returns:
            Tuple of (is_allowed, violations, requires_approval)
        """
        violations = []
        requires_approval = False
        session_id = str(agent_session.id) if agent_session else None

        # Per-agent limits from safety_config, fallback to global
        _sc = {}
        if hasattr(agent_session, 'agent') and agent_session.agent:
            _sc = agent_session.agent.safety_config if isinstance(getattr(agent_session.agent, 'safety_config', None), dict) else {}
        _max_loops = settings.MAX_LOOP_ITERATIONS
        _max_tokens = settings.MAX_TOKENS_PER_RUN
        _sc_loops = _sc.get("max_loops")
        if _sc_loops and isinstance(_sc_loops, int) and 1 <= _sc_loops <= 100:
            _max_loops = _sc_loops
        _sc_tokens = _sc.get("max_tokens_per_run")
        if _sc_tokens and isinstance(_sc_tokens, int) and _sc_tokens > 0:
            _max_tokens = _sc_tokens

        # Check loop limits
        if agent_session.loop_count >= _max_loops:
            violations.append(f"Max loop iterations ({_max_loops}) exceeded")

        # Check token limits
        if agent_session.total_tokens_used >= _max_tokens:
            violations.append(f"Max tokens ({_max_tokens}) exceeded")

        # Check timeout
        if agent_session.started_at:
            elapsed = (datetime.now(timezone.utc) - agent_session.started_at).total_seconds()
            if elapsed >= settings.SAFETY_TIMEOUT_SECONDS:
                violations.append(f"Session timeout ({settings.SAFETY_TIMEOUT_SECONDS}s) exceeded")

        # Check input size
        content_to_check = str(action_data)
        if len(content_to_check) > self.MAX_INPUT_SIZE:
            violations.append(f"Input size exceeds maximum ({self.MAX_INPUT_SIZE} bytes)")
            self._log_incident(ThreatLevel.MEDIUM, "size_limit", "Input size exceeded", action_data, session_id)

        # CRITICAL patterns - immediate block and alert
        critical_matches = self._check_patterns(content_to_check, self.CRITICAL_PATTERNS)
        for pattern, desc in critical_matches:
            violations.append(f"CRITICAL: {desc}")
            self._log_incident(ThreatLevel.CRITICAL, "critical_pattern", desc, action_data, session_id)

        # HIGH-RISK patterns - block
        high_matches = self._check_patterns(content_to_check, self.HIGH_RISK_PATTERNS)
        for pattern, desc in high_matches:
            violations.append(f"HIGH RISK: {desc}")
            self._log_incident(ThreatLevel.HIGH, "high_risk_pattern", desc, action_data, session_id)

        # SQL injection patterns - block
        sql_matches = self._check_patterns(content_to_check, self.SQL_INJECTION_PATTERNS)
        for pattern, desc in sql_matches:
            violations.append(f"SQL INJECTION: {desc}")
            self._log_incident(ThreatLevel.HIGH, "sql_injection", desc, action_data, session_id)

        # Code injection patterns - block
        code_matches = self._check_patterns(content_to_check, self.CODE_INJECTION_PATTERNS)
        for pattern, desc in code_matches:
            violations.append(f"CODE INJECTION: {desc}")
            self._log_incident(ThreatLevel.HIGH, "code_injection", desc, action_data, session_id)

        # Exfiltration patterns - block
        exfil_matches = self._check_patterns(content_to_check, self.EXFILTRATION_PATTERNS)
        for pattern, desc in exfil_matches:
            violations.append(f"EXFILTRATION: {desc}")
            self._log_incident(ThreatLevel.HIGH, "exfiltration", desc, action_data, session_id)

        # Sensitive data patterns - require approval ONLY for respond/think actions.
        # Tool calls (web_search, fetch_url, etc.) routinely contain emails, tokens,
        # API-key-like strings in their results — these are false positives that cause
        # sessions to get stuck in WAITING_APPROVAL forever.
        if action_type not in ("tool_call",):
            sensitive_matches = self._check_patterns(content_to_check, self.SENSITIVE_PATTERNS)
            if sensitive_matches:
                for pattern, desc in sensitive_matches:
                    violations.append(f"SENSITIVE DATA: {desc}")
                    self._log_incident(ThreatLevel.MEDIUM, "sensitive_data", desc, action_data, session_id)
                requires_approval = True

        # Check URLs in action data
        if "url" in action_data:
            url_safe, url_reason = self._check_url_safety(str(action_data["url"]))
            if not url_safe:
                violations.append(f"URL BLOCKED: {url_reason}")
                self._log_incident(ThreatLevel.HIGH, "blocked_url", url_reason, action_data, session_id)

        tool_input = action_data.get("tool_input") if isinstance(action_data, dict) else None
        if isinstance(tool_input, dict) and "url" in tool_input:
            url_safe, url_reason = self._check_url_safety(str(tool_input["url"]))
            if not url_safe:
                violations.append(f"URL BLOCKED: {url_reason}")
                self._log_incident(ThreatLevel.HIGH, "blocked_url", url_reason, action_data, session_id)

        # Check file paths in action data
        for key in ["file", "path", "filepath", "filename"]:
            if key in action_data:
                file_safe, file_reason = self._check_file_safety(str(action_data[key]))
                if not file_safe:
                    violations.append(f"FILE BLOCKED: {file_reason}")
                    self._log_incident(ThreatLevel.HIGH, "blocked_file", file_reason, action_data, session_id)

        # Apply custom rules
        for rule in self.custom_rules:
            rule_result = await self._apply_rule(rule, action_type, action_data, agent_session)
            if rule_result:
                if rule.action == "block":
                    violations.append(f"Rule '{rule.name}' blocked action")
                    self._log_incident(ThreatLevel.MEDIUM, "custom_rule", f"Rule {rule.name} triggered", action_data, session_id)
                elif rule.action == "require_approval":
                    requires_approval = True
                elif rule.action == "warn":
                    violations.append(f"Warning from rule '{rule.name}'")

        # === DSID-P SRR (Semantic Risk Rating) Enforcement ===
        # Get agent's SRR from safety_config
        agent_srr = self._get_agent_srr(agent_session)
        action_srr = self._get_action_srr(action_type, action_data)
        
        # SRR-5 agents require approval for most actions
        if agent_srr >= 5:
            if action_type in ["tool_call", "api_call"]:
                requires_approval = True
                violations.append(f"SRR-5 agent requires approval for {action_type}")
        
        # SRR-4 agents require approval for high-risk actions
        if agent_srr >= 4:
            if action_srr >= 4:
                requires_approval = True
                violations.append(f"SRR-4+ action requires approval")
        
        # Block cross-domain escalation (low SRR agent trying high-risk action)
        if agent_srr <= 2 and action_srr >= 4:
            violations.append(f"SRR VIOLATION: Low-risk agent (SRR-{agent_srr}) cannot perform high-risk action (SRR-{action_srr})")
            self._log_incident(ThreatLevel.HIGH, "srr_violation", f"Cross-domain escalation blocked", action_data, session_id)

        # Determine if action is allowed (any CRITICAL, HIGH, SQL, CODE, EXFIL, or exceeded blocks it)
        blocking_keywords = ["critical", "high risk", "sql injection", "code injection", "exfiltration", "exceeded", "blocked"]
        is_allowed = not any(
            any(kw in v.lower() for kw in blocking_keywords)
            for v in violations
        )
        
        return is_allowed, violations, requires_approval

    async def _apply_rule(
        self,
        rule: SafetyRule,
        action_type: str,
        action_data: Dict[str, Any],
        agent_session: AgentSession,
    ) -> bool:
        """Apply a single safety rule. Returns True if rule triggers."""
        if rule.rule_type == "rate_limit":
            return await self._check_rate_limit(rule, agent_session)
        elif rule.rule_type == "content_filter":
            return self._check_content_filter(rule, action_data)
        elif rule.rule_type == "action_block":
            return self._check_action_block(rule, action_type, action_data)
        elif rule.rule_type == "resource_limit":
            return self._check_resource_limit(rule, agent_session)
        return False

    async def _check_rate_limit(self, rule: SafetyRule, session: AgentSession) -> bool:
        """Check rate limiting rules."""
        params = rule.parameters or {}
        max_calls = params.get("max_calls", 100)
        window_seconds = params.get("window_seconds", 60)
        
        # Simple check based on session metrics
        if session.total_tool_calls >= max_calls:
            return True
        return False

    def _check_content_filter(self, rule: SafetyRule, action_data: Dict[str, Any]) -> bool:
        """Check content filtering rules."""
        params = rule.parameters or {}
        patterns = params.get("patterns", [])
        content = str(action_data)
        
        for pattern in patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return True
        return False

    def _check_action_block(
        self, rule: SafetyRule, action_type: str, action_data: Dict[str, Any]
    ) -> bool:
        """Check action blocking rules."""
        params = rule.parameters or {}
        blocked_actions = params.get("blocked_actions", [])
        blocked_tools = params.get("blocked_tools", [])
        
        if action_type in blocked_actions:
            return True
        
        tool_name = action_data.get("tool_name", "")
        if tool_name in blocked_tools:
            return True
        
        return False

    def _check_resource_limit(self, rule: SafetyRule, session: AgentSession) -> bool:
        """Check resource limit rules."""
        params = rule.parameters or {}
        
        max_tokens = params.get("max_tokens")
        if max_tokens and session.total_tokens_used >= max_tokens:
            return True
        
        max_loops = params.get("max_loops")
        if max_loops and session.loop_count >= max_loops:
            return True
        
        return False

    def sanitize_output(self, output: str) -> str:
        """Sanitize output to remove sensitive information."""
        sanitized = output
        
        for pattern in self.SENSITIVE_PATTERNS:
            sanitized = re.sub(pattern, "[REDACTED]", sanitized, flags=re.IGNORECASE)
        
        return sanitized

    def _get_agent_srr(self, agent_session: AgentSession) -> int:
        """
        Get DSID-P Semantic Risk Rating for agent from safety_config.
        
        SRR Levels:
        - SRR-1: Minimal risk (summarization, search)
        - SRR-2: Low risk (creative, communication)
        - SRR-3: Medium risk (workflow, planning)
        - SRR-4: High risk (finance, system control)
        - SRR-5: Critical risk (legal, medical, governance)
        """
        if not agent_session:
            return 2  # Default to low risk
        
        # Try to get from session context
        context = getattr(agent_session, 'context', {}) or {}
        if isinstance(context, dict):
            srr = context.get('semantic_risk_rating')
            if srr:
                return int(srr)
        
        return 2  # Default to low risk

    def _get_action_srr(self, action_type: str, action_data: Dict[str, Any]) -> int:
        """
        Get DSID-P Semantic Risk Rating for an action.
        
        Maps action types and tool names to risk levels.
        """
        tool_name = (action_data.get("tool_name") or "").lower()
        
        # SRR-5: Critical risk
        critical_keywords = ["medical", "legal", "compliance", "governance", "admin", "sudo"]
        if any(k in tool_name for k in critical_keywords):
            return 5
        
        # SRR-4: High risk
        high_keywords = ["finance", "payment", "transfer", "execute", "deploy", "delete"]
        if any(k in tool_name for k in high_keywords):
            return 4
        
        # SRR-3: Medium risk
        if action_type == "tool_call":
            return 3
        
        # SRR-2: Low risk
        if action_type == "respond":
            return 2
        
        # SRR-1: Minimal risk
        return 1


class ApprovalManager:
    """Manages human-in-the-loop approvals."""

    async def request_approval(
        self,
        session_id: str,
        step_id: str,
        action_type: str,
        action_data: Dict[str, Any],
        reason: str,
        db_session: AsyncSession,
    ) -> str:
        """Request human approval for an action."""
        # Update step to require approval
        result = await db_session.execute(
            select(AgentStep).where(AgentStep.id == step_id)
        )
        step = result.scalar_one_or_none()
        if step:
            step.required_approval = True
            step.approval_status = "pending"
            await db_session.commit()
        
        # In production, this would:
        # 1. Send notification to user
        # 2. Create approval request in queue
        # 3. Wait for response or timeout
        
        return "pending"

    async def check_approval(
        self,
        step_id: str,
        db_session: AsyncSession,
    ) -> Optional[str]:
        """Check if an approval has been granted."""
        result = await db_session.execute(
            select(AgentStep).where(AgentStep.id == step_id)
        )
        step = result.scalar_one_or_none()
        if step:
            return step.approval_status
        return None

    async def grant_approval(
        self,
        step_id: str,
        approved: bool,
        db_session: AsyncSession,
    ):
        """Grant or deny approval for a step."""
        result = await db_session.execute(
            select(AgentStep).where(AgentStep.id == step_id)
        )
        step = result.scalar_one_or_none()
        if step:
            step.approval_status = "approved" if approved else "rejected"
            await db_session.commit()


safety_envelope = SafetyEnvelope()
approval_manager = ApprovalManager()


# DSID-P Section 40: Security Threat Monitor
class SecurityThreatMonitor:
    """
    DSID-P Protocol Security Architecture (Section 40).
    
    Monitors 7 security layers for threats:
    L1 - Cryptographic Identity Security
    L2 - Data & Memory Security
    L3 - Semantic Engine Security
    L4 - Governance Contract Security
    L5 - Coordination DAG Integrity
    L6 - Registry/Ledger Security
    L7 - Federation & Sovereign Boundary Security
    """
    
    def __init__(self):
        self.threat_counts = {
            "l1_identity": 0,
            "l2_data_memory": 0,
            "l3_semantic": 0,
            "l4_governance": 0,
            "l5_coordination": 0,
            "l6_registry": 0,
            "l7_federation": 0,
        }
        self.threat_log: List[Dict[str, Any]] = []
    
    def record_threat(self, layer: str, threat_type: str, details: Dict[str, Any]):
        """Record a detected security threat."""
        self.threat_counts[layer] = self.threat_counts.get(layer, 0) + 1
        self.threat_log.append({
            "layer": layer,
            "threat_type": threat_type,
            "details": details,
            "timestamp": datetime.utcnow().isoformat(),
        })
        # Keep bounded
        if len(self.threat_log) > 1000:
            self.threat_log = self.threat_log[-500:]
    
    def get_status(self) -> Dict[str, Any]:
        """Get security status across all layers."""
        layers = {}
        for layer, count in self.threat_counts.items():
            status = "secure" if count == 0 else "alert" if count < 5 else "critical"
            layers[layer] = {"status": status, "threats_detected": count}
        
        overall = "secure"
        if any(c > 0 for c in self.threat_counts.values()):
            overall = "alert"
        if any(c >= 5 for c in self.threat_counts.values()):
            overall = "critical"
        
        return {"security_layers": layers, "overall": overall}
    
    def check_identity_threat(self, action_data: Dict[str, Any]) -> bool:
        """Check for identity-related threats."""
        if "impersonate" in str(action_data).lower():
            self.record_threat("l1_identity", "impersonation_attempt", action_data)
            return True
        return False
    
    def check_semantic_threat(self, action_data: Dict[str, Any]) -> bool:
        """Check for semantic-related threats."""
        if "poison" in str(action_data).lower() or "adversarial" in str(action_data).lower():
            self.record_threat("l3_semantic", "semantic_attack", action_data)
            return True
        return False


security_monitor = SecurityThreatMonitor()
