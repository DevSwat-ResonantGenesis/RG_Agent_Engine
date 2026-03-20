"""
IDE-only tool definitions (git, terminal, web, deploy, notebooks, etc.)
Split from builtin_tools.py to avoid file size limits.
"""
from .registry import ToolDef, ToolParam, ToolCategory, ToolAccess, ParamType

_I = ToolAccess.IDE

IDE_GIT_TOOLS = [
    ToolDef(name="git_status", description="Working tree status.", category=ToolCategory.GIT, access={_I},
            params=[ToolParam("path", ParamType.STRING, "repo path", required=True)], priority=5),
    ToolDef(name="git_diff", description="Show diff.", category=ToolCategory.GIT, access={_I},
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("staged", ParamType.BOOLEAN)], priority=5),
    ToolDef(name="git_log", description="Commit log.", category=ToolCategory.GIT, access={_I},
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("count", ParamType.NUMBER)], priority=5),
    ToolDef(name="git_commit", description="Stage and commit.", category=ToolCategory.GIT, access={_I},
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("message", ParamType.STRING, required=True)], priority=5),
    ToolDef(name="git_push", description="Push to remote.", category=ToolCategory.GIT, access={_I},
            params=[ToolParam("path", ParamType.STRING, required=True)], priority=5),
    ToolDef(name="git_pull", description="Pull from remote.", category=ToolCategory.GIT, access={_I},
            params=[ToolParam("path", ParamType.STRING, required=True)], priority=5),
    ToolDef(name="git_branch", description="List/create/switch branches.", category=ToolCategory.GIT, access={_I},
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("action", ParamType.STRING), ToolParam("name", ParamType.STRING)], priority=5),
]

IDE_WEB_TOOLS = [
    ToolDef(name="search_web", description="Web search via DuckDuckGo.", category=ToolCategory.SEARCH, access={_I},
            params=[ToolParam("query", ParamType.STRING, required=True), ToolParam("domain", ParamType.STRING)], priority=10),
    ToolDef(name="read_url_content", description="Fetch URL text content.", category=ToolCategory.SEARCH, access={_I},
            params=[ToolParam("url", ParamType.STRING, required=True)], priority=10),
    ToolDef(name="view_content_chunk", description="Read chunk of fetched URL.", category=ToolCategory.SEARCH, access={_I},
            params=[ToolParam("document_id", ParamType.STRING, required=True), ToolParam("position", ParamType.NUMBER, required=True)], priority=15),
    ToolDef(name="browser_preview", description="Open URL in VS Code webview with console log capture.", category=ToolCategory.SEARCH, access={_I},
            params=[ToolParam("url", ParamType.STRING, required=True), ToolParam("name", ParamType.STRING)], priority=15),
]

IDE_TERMINAL_TOOLS = [
    ToolDef(name="terminal_create", description="Create persistent interactive terminal session.", category=ToolCategory.TERMINAL, access={_I},
            params=[ToolParam("name", ParamType.STRING), ToolParam("cwd", ParamType.STRING)], priority=10),
    ToolDef(name="terminal_send", description="Send text/keys to terminal. Auto-appends Enter.", category=ToolCategory.TERMINAL, access={_I},
            params=[ToolParam("session_id", ParamType.STRING, required=True), ToolParam("input", ParamType.STRING, required=True)], priority=10),
    ToolDef(name="terminal_read", description="Read recent output from terminal.", category=ToolCategory.TERMINAL, access={_I},
            params=[ToolParam("session_id", ParamType.STRING, required=True), ToolParam("last_n_chars", ParamType.NUMBER, default=5000)], priority=10),
    ToolDef(name="terminal_wait", description="Wait for new output from terminal.", category=ToolCategory.TERMINAL, access={_I},
            params=[ToolParam("session_id", ParamType.STRING, required=True), ToolParam("timeout_ms", ParamType.NUMBER, default=5000)], priority=10),
    ToolDef(name="terminal_list", description="List all active terminal sessions.", category=ToolCategory.TERMINAL, access={_I}, params=[], priority=15),
    ToolDef(name="terminal_close", description="Close terminal session.", category=ToolCategory.TERMINAL, access={_I},
            params=[ToolParam("session_id", ParamType.STRING, required=True)], priority=15),
]

IDE_PLANNING_TOOLS = [
    ToolDef(name="todo_list", description="Create/update task list.", category=ToolCategory.PLANNING, access={_I},
            params=[ToolParam("todos", ParamType.ARRAY, "todo items", required=True)], priority=5),
    ToolDef(name="ask_user", description="Ask user a question with structured options.", category=ToolCategory.PLANNING, access={_I},
            params=[ToolParam("question", ParamType.STRING, required=True), ToolParam("options", ParamType.ARRAY)], priority=5),
    ToolDef(name="save_memory", description="Save to persistent memory (syncs to Hash Sphere).", category=ToolCategory.PLANNING, access={_I},
            params=[ToolParam("key", ParamType.STRING, required=True), ToolParam("content", ParamType.STRING, required=True), ToolParam("tags", ParamType.ARRAY, items_type="string")], priority=10),
    ToolDef(name="read_memory", description="Read memories by key, tag, or semantic query.", category=ToolCategory.PLANNING, access={_I},
            params=[ToolParam("key", ParamType.STRING), ToolParam("tag", ParamType.STRING), ToolParam("query", ParamType.STRING)], priority=10),
    ToolDef(name="code_search", description="Search codebase with multi-pass strategy.", category=ToolCategory.PLANNING, access={_I},
            params=[ToolParam("query", ParamType.STRING, required=True), ToolParam("path", ParamType.STRING)], priority=5),
]

IDE_DEPLOY_TOOLS = [
    ToolDef(name="deploy_web_app", description="Build and deploy a web app.", category=ToolCategory.DEPLOY, access={_I},
            params=[ToolParam("project_path", ParamType.STRING, required=True), ToolParam("framework", ParamType.STRING), ToolParam("subdomain", ParamType.STRING)], priority=20),
    ToolDef(name="check_deploy_status", description="Check deployment status.", category=ToolCategory.DEPLOY, access={_I},
            params=[ToolParam("deployment_id", ParamType.STRING, required=True)], priority=25),
]

IDE_NOTEBOOK_TOOLS = [
    ToolDef(name="read_notebook", description="Read Jupyter notebook.", category=ToolCategory.NOTEBOOKS, access={_I},
            params=[ToolParam("path", ParamType.STRING, required=True)], priority=15),
    ToolDef(name="edit_notebook", description="Edit notebook cell.", category=ToolCategory.NOTEBOOKS, access={_I},
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("cell_number", ParamType.NUMBER, required=True), ToolParam("new_source", ParamType.STRING, required=True)], priority=15),
]

IDE_CHECKPOINT_TOOLS = [
    ToolDef(name="save_checkpoint", description="Save conversation checkpoint for cross-session continuity.", category=ToolCategory.CHECKPOINTS, access={_I},
            params=[ToolParam("summary", ParamType.STRING, required=True), ToolParam("key_files", ParamType.ARRAY, items_type="string"), ToolParam("pending_tasks", ParamType.ARRAY, items_type="string")], priority=20),
    ToolDef(name="load_checkpoint", description="Load latest conversation checkpoint.", category=ToolCategory.CHECKPOINTS, access={_I}, params=[], priority=20),
]

ALL_IDE_TOOLS = (
    IDE_GIT_TOOLS + IDE_WEB_TOOLS + IDE_TERMINAL_TOOLS +
    IDE_PLANNING_TOOLS + IDE_DEPLOY_TOOLS + IDE_NOTEBOOK_TOOLS + IDE_CHECKPOINT_TOOLS
)
