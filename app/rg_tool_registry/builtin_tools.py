"""
Built-in Tool Definitions — ALL platform tools in unified ToolDef format.
Server-side tools (search, memory, agents, media, integrations, etc.)
IDE-only tools are in builtin_tools_ide.py
"""
from typing import Dict
from .registry import ToolDef, ToolParam, ToolCategory, ToolAccess, ParamType

_R = ToolAccess.REGISTERED
_G = ToolAccess.GUEST
_A = ToolAccess.AGENT
_I = ToolAccess.IDE

# ── SEARCH & WEB ──
SEARCH_TOOLS = [
    ToolDef(name="web_search", description="Search the web for current information, news, articles, documentation.", category=ToolCategory.SEARCH,
            params=[ToolParam("query", ParamType.STRING, "search query", required=True), ToolParam("max_results", ParamType.INTEGER, "max results", default=5)],
            handler="_custom_web_search", access={_R, _G, _A}, priority=5, max_result_chars=4000),
    ToolDef(name="fetch_url", description="Fetch and read content from any URL.", category=ToolCategory.SEARCH,
            params=[ToolParam("url", ParamType.STRING, "URL to fetch", required=True)],
            handler="fetch_url", access={_R, _G, _A}, priority=10, max_result_chars=4000),
    ToolDef(name="read_webpage", description="Read a webpage and extract clean structured content.", category=ToolCategory.SEARCH,
            params=[ToolParam("url", ParamType.STRING, "URL to read", required=True), ToolParam("max_length", ParamType.INTEGER, "max chars", default=15000)],
            handler="_custom_read_webpage", access={_R, _G}, priority=10, max_result_chars=8000),
    ToolDef(name="read_many_pages", description="Read multiple web pages in parallel (max 5).", category=ToolCategory.SEARCH,
            params=[ToolParam("urls", ParamType.ARRAY, "list of URLs", required=True, items_type="string")],
            handler="_custom_read_many_pages", access={_R, _G}, priority=15, max_result_chars=8000),
    ToolDef(name="reddit_search", description="Search Reddit for discussions and recommendations.", category=ToolCategory.SEARCH,
            params=[ToolParam("query", ParamType.STRING, "search query", required=True), ToolParam("subreddit", ParamType.STRING, "limit to subreddit"), ToolParam("limit", ParamType.INTEGER, "max results", default=10)],
            handler="_custom_reddit_search", access={_R, _G}, priority=20, max_result_chars=4000),
    ToolDef(name="image_search", description="Search for images on the web.", category=ToolCategory.SEARCH,
            params=[ToolParam("query", ParamType.STRING, "search query", required=True), ToolParam("limit", ParamType.INTEGER, "number of results", default=8)],
            handler="_custom_image_search", access={_R, _G, _I}, priority=25, max_result_chars=3000),
    ToolDef(name="news_search", description="Search latest news articles.", category=ToolCategory.SEARCH,
            params=[ToolParam("query", ParamType.STRING, "news topic", required=True), ToolParam("max_results", ParamType.INTEGER, "number of results", default=5)],
            handler="_custom_news_search", access={_R, _G}, priority=25, max_result_chars=4000),
    ToolDef(name="places_search", description="Search for businesses on Google Maps.", category=ToolCategory.SEARCH,
            params=[ToolParam("query", ParamType.STRING, "what to find", required=True), ToolParam("location", ParamType.STRING, "city or area")],
            handler="_custom_places_search", access={_R, _G}, priority=30, max_result_chars=4000),
    ToolDef(name="youtube_search", description="Search YouTube for videos.", category=ToolCategory.SEARCH,
            params=[ToolParam("query", ParamType.STRING, "search query", required=True), ToolParam("limit", ParamType.INTEGER, "max results", default=5)],
            handler="_custom_youtube_search", access={_R, _G}, priority=30, max_result_chars=3000),
    ToolDef(name="deep_research", description="Deep multi-source research via Perplexity AI.", category=ToolCategory.SEARCH,
            params=[ToolParam("query", ParamType.STRING, "research question", required=True)],
            handler="_custom_deep_research", access={_R}, priority=15, max_result_chars=8000, streamable=True),
    ToolDef(name="wikipedia", description="Search and read Wikipedia articles.", category=ToolCategory.SEARCH,
            params=[ToolParam("query", ParamType.STRING, "article title or search", required=True)],
            handler="_custom_wikipedia", access={_R, _G}, priority=20, max_result_chars=4000),
]

# ── MEMORY / HASH SPHERE ──
MEMORY_TOOLS = [
    ToolDef(name="memory_read", description="Search user's long-term memory.", category=ToolCategory.MEMORY,
            params=[ToolParam("query", ParamType.STRING, "search query", required=True), ToolParam("limit", ParamType.INTEGER, "max results", default=5)],
            handler="memory.read", access={_R, _A}, priority=10, max_result_chars=4000),
    ToolDef(name="memory_write", description="Save information to long-term memory.", category=ToolCategory.MEMORY,
            params=[ToolParam("content", ParamType.STRING, "content to save", required=True), ToolParam("tags", ParamType.ARRAY, "tags", items_type="string")],
            handler="memory.write", access={_R, _A}, priority=10, max_result_chars=1000),
    ToolDef(name="memory_search", description="Deep keyword + semantic search through memories.", category=ToolCategory.MEMORY,
            params=[ToolParam("query", ParamType.STRING, "search query", required=True)],
            handler="_custom_memory_search", access={_R}, priority=15, max_result_chars=4000),
    ToolDef(name="memory_stats", description="Get memory usage stats.", category=ToolCategory.MEMORY,
            params=[], handler="_custom_memory_stats", access={_R}, priority=50, max_result_chars=1000),
]

# ── HASH SPHERE ──
HASH_SPHERE_TOOLS = [
    ToolDef(name="hash_sphere_search", description="Search Hash Sphere anchors (blockchain-verified memories).", category=ToolCategory.MEMORY,
            params=[ToolParam("query", ParamType.STRING, "search query", required=True), ToolParam("limit", ParamType.INTEGER, "max results", default=10)],
            handler="_custom_hs_search", access={_R}, priority=15, max_result_chars=4000),
    ToolDef(name="hash_sphere_anchor", description="Create a new Hash Sphere anchor (blockchain-verified memory point).", category=ToolCategory.MEMORY,
            params=[ToolParam("content", ParamType.STRING, "content to anchor", required=True), ToolParam("label", ParamType.STRING, "anchor label"), ToolParam("metadata", ParamType.OBJECT, "extra metadata")],
            handler="_custom_hs_anchor", access={_R}, priority=20, max_result_chars=2000),
    ToolDef(name="hash_sphere_list_anchors", description="List all user's Hash Sphere anchors.", category=ToolCategory.MEMORY,
            params=[ToolParam("limit", ParamType.INTEGER, "max results", default=20)],
            handler="_custom_hs_list_anchors", access={_R}, priority=25, max_result_chars=4000),
    ToolDef(name="hash_sphere_hash", description="Generate a Hash Sphere hash for content.", category=ToolCategory.MEMORY,
            params=[ToolParam("content", ParamType.STRING, "content to hash", required=True)],
            handler="_custom_hs_hash", access={_R}, priority=30, max_result_chars=1000),
    ToolDef(name="hash_sphere_resonance", description="Check resonance between two content pieces in Hash Sphere.", category=ToolCategory.MEMORY,
            params=[ToolParam("content_a", ParamType.STRING, "first content", required=True), ToolParam("content_b", ParamType.STRING, "second content", required=True)],
            handler="_custom_hs_resonance", access={_R}, priority=30, max_result_chars=2000),
]

# ── UTILITIES ──
UTILITY_TOOLS = [
    ToolDef(name="weather", description="Get current weather and 3-day forecast.", category=ToolCategory.UTILITIES,
            params=[ToolParam("location", ParamType.STRING, "city name", required=True)],
            handler="_custom_weather", access={_R, _G}, priority=20, max_result_chars=2000),
    ToolDef(name="stock_crypto", description="Get real-time stock or crypto prices.", category=ToolCategory.UTILITIES,
            params=[ToolParam("symbol", ParamType.STRING, "ticker e.g. AAPL, BTC-USD", required=True)],
            handler="_custom_stock_crypto", access={_R, _G}, priority=25, max_result_chars=2000),
    ToolDef(name="generate_chart", description="Generate chart image from data.", category=ToolCategory.UTILITIES,
            params=[ToolParam("type", ParamType.STRING, "chart type", default="bar", enum=["bar","line","pie","doughnut","radar","scatter"]),
                    ToolParam("labels", ParamType.ARRAY, "x-axis labels", required=True, items_type="string"),
                    ToolParam("datasets", ParamType.ARRAY, "data sets", required=True)],
            handler="_custom_generate_chart", access={_R, _G}, priority=30, max_result_chars=2000),
    ToolDef(name="visualize", description="Generate SVG diagram inline in chat.", category=ToolCategory.VISUAL,
            params=[ToolParam("description", ParamType.STRING, "what to visualize", required=True)],
            handler="_custom_visualize", access={_R, _G}, priority=30, max_result_chars=8000),
    ToolDef(name="get_current_time", description="Get current date, time, timezone.", category=ToolCategory.SYSTEM,
            params=[ToolParam("timezone", ParamType.STRING, "timezone", default="UTC")],
            handler="_custom_get_current_time", access={_R, _A}, priority=5, max_result_chars=500),
    ToolDef(name="get_system_info", description="Get platform system info.", category=ToolCategory.SYSTEM,
            params=[], handler="_custom_get_system_info", access={_R}, priority=50, max_result_chars=1000),
]

# ── CODE VISUALIZER ──
CODE_VISUALIZER_TOOLS = [
    ToolDef(name="code_visualizer_scan", description="AST-scan project: functions, classes, endpoints, imports, pipelines, dead code.", category=ToolCategory.CODE_ANALYSIS,
            params=[ToolParam("path", ParamType.STRING, "path or GitHub URL", required=True)],
            handler="_custom_cv_scan", access={_R, _I}, priority=10, max_result_chars=8000, streamable=True),
    ToolDef(name="code_visualizer_functions", description="List all functions and API endpoints.", category=ToolCategory.CODE_ANALYSIS,
            params=[ToolParam("path", ParamType.STRING, required=True)], handler="_custom_cv_functions", access={_R, _I}, priority=15, max_result_chars=8000),
    ToolDef(name="code_visualizer_trace", description="Trace dependency flow from any node.", category=ToolCategory.CODE_ANALYSIS,
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("query", ParamType.STRING, "node name", required=True)],
            handler="_custom_cv_trace", access={_R, _I}, priority=15, max_result_chars=8000),
    ToolDef(name="code_visualizer_governance", description="Architecture governance: reachability, drift, health score.", category=ToolCategory.CODE_ANALYSIS,
            params=[ToolParam("path", ParamType.STRING, required=True)], handler="_custom_cv_governance", access={_R, _I}, priority=20, max_result_chars=8000),
    ToolDef(name="code_visualizer_graph", description="Get full dependency graph.", category=ToolCategory.CODE_ANALYSIS,
            params=[ToolParam("path", ParamType.STRING, required=True)], handler="_custom_cv_graph", access={_R, _I}, priority=20, max_result_chars=8000),
    ToolDef(name="code_visualizer_pipeline", description="Get auto-detected pipeline flow.", category=ToolCategory.CODE_ANALYSIS,
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("pipeline_name", ParamType.STRING, required=True)],
            handler="_custom_cv_pipeline", access={_R, _I}, priority=20, max_result_chars=8000),
    ToolDef(name="code_visualizer_filter", description="Filter graph by file path, node type, or keyword.", category=ToolCategory.CODE_ANALYSIS,
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("keyword", ParamType.STRING)],
            handler="_custom_cv_filter", access={_R, _I}, priority=25, max_result_chars=8000),
    ToolDef(name="code_visualizer_by_type", description="Get all nodes of a type.", category=ToolCategory.CODE_ANALYSIS,
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("node_type", ParamType.STRING, required=True,
                    enum=["function","class","api_endpoint","service","file","import","external_service","database"])],
            handler="_custom_cv_by_type", access={_R, _I}, priority=25, max_result_chars=8000),
]

# ── AGENTS OS ──
AGENT_TOOLS = [
    ToolDef(name="agents_list", description="List user's AI agents.", category=ToolCategory.AGENTS,
            params=[], handler="_custom_agents_list", access={_R}, priority=10),
    ToolDef(name="agents_create", description="Create a new AI agent.", category=ToolCategory.AGENTS,
            params=[ToolParam("name", ParamType.STRING, "agent name", required=True), ToolParam("goal", ParamType.STRING, "agent goal", required=True)],
            handler="_custom_agents_create", access={_R}, priority=10),
    ToolDef(name="agents_start", description="Start/run an agent.", category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING, "agent UUID", required=True)],
            handler="_custom_agents_start", access={_R}, priority=10),
    ToolDef(name="agents_stop", description="Stop a running agent.", category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING, required=True)], handler="_custom_agents_stop", access={_R}, priority=10),
    ToolDef(name="agents_status", description="Get agent config and status.", category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING, required=True)], handler="_custom_agents_status", access={_R}, priority=10),
    ToolDef(name="workspace_snapshot", description="Full overview of workspace.", category=ToolCategory.AGENTS,
            params=[], handler="_custom_workspace_snapshot", access={_R}, priority=15),
    ToolDef(name="run_agent", description="Directly run an agent.", category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING), ToolParam("goal", ParamType.STRING)],
            handler="_custom_run_agent", access={_R}, priority=10),
    ToolDef(name="present_options", description="Present interactive options to the user.", category=ToolCategory.AGENTS,
            params=[ToolParam("title", ParamType.STRING, required=True), ToolParam("options", ParamType.ARRAY, required=True)],
            handler="_custom_present_options", access={_R}, priority=20),
]

# ── MEDIA GENERATION ──
MEDIA_TOOLS = [
    ToolDef(name="generate_image", description="Generate an AI image from text.", category=ToolCategory.MEDIA,
            params=[ToolParam("prompt", ParamType.STRING, "image description", required=True)],
            handler="generate_image", access={_R, _A}, requires_api_key="openai", priority=30, max_result_chars=2000),
    ToolDef(name="generate_audio", description="Generate speech from text (TTS).", category=ToolCategory.MEDIA,
            params=[ToolParam("text", ParamType.STRING, "text to speak", required=True)],
            handler="generate_audio", access={_R, _A}, requires_api_key="openai", priority=35, max_result_chars=2000),
    ToolDef(name="generate_music", description="Generate music from text.", category=ToolCategory.MEDIA,
            params=[ToolParam("prompt", ParamType.STRING, "music description", required=True)],
            handler="generate_music", access={_R, _A}, requires_api_key="suno", priority=40, max_result_chars=2000),
]

# ── INTEGRATIONS ──
INTEGRATION_TOOLS = [
    ToolDef(name="gmail_send", description="Send email via Gmail.", category=ToolCategory.INTEGRATIONS,
            params=[ToolParam("to", ParamType.STRING, required=True), ToolParam("subject", ParamType.STRING, required=True), ToolParam("body", ParamType.STRING, required=True)],
            handler="gmail_send", access={_R, _A}, requires_api_key="gmail", priority=30),
    ToolDef(name="gmail_read", description="Read recent Gmail inbox.", category=ToolCategory.INTEGRATIONS,
            params=[ToolParam("query", ParamType.STRING), ToolParam("max_results", ParamType.INTEGER, default=5)],
            handler="gmail_read", access={_R, _A}, requires_api_key="gmail", priority=30),
    ToolDef(name="slack_send", description="Send Slack message.", category=ToolCategory.INTEGRATIONS,
            params=[ToolParam("channel", ParamType.STRING, required=True), ToolParam("message", ParamType.STRING, required=True)],
            handler="slack_send_message", access={_R, _A}, requires_api_key="slack", priority=30),
    ToolDef(name="slack_read", description="Read Slack channel messages.", category=ToolCategory.INTEGRATIONS,
            params=[ToolParam("channel", ParamType.STRING, required=True), ToolParam("limit", ParamType.INTEGER, default=10)],
            handler="slack_read_messages", access={_R, _A}, requires_api_key="slack", priority=30),
    ToolDef(name="google_calendar", description="Google Calendar: list/create events.", category=ToolCategory.INTEGRATIONS,
            params=[ToolParam("action", ParamType.STRING, required=True, enum=["list_events","create_event","check_availability"])],
            handler="google_calendar", access={_R, _A}, requires_api_key="google-calendar", priority=35),
    ToolDef(name="google_drive", description="Google Drive: list/search/read files.", category=ToolCategory.INTEGRATIONS,
            params=[ToolParam("action", ParamType.STRING, required=True, enum=["list_files","search","read_file","create_file"])],
            handler="google_drive", access={_R, _A}, requires_api_key="google-drive", priority=35),
    ToolDef(name="figma", description="Figma: list projects, get file, inspect components.", category=ToolCategory.INTEGRATIONS,
            params=[ToolParam("action", ParamType.STRING, required=True, enum=["list_projects","get_file","list_components"])],
            handler="figma", access={_R, _A}, requires_api_key="figma", priority=35),
    ToolDef(name="sigma", description="Sigma Computing dashboards and analytics.", category=ToolCategory.INTEGRATIONS,
            params=[ToolParam("action", ParamType.STRING, required=True, enum=["list_workbooks","get_workbook"])],
            handler="sigma", access={_R, _A}, requires_api_key="sigma", priority=35),
]

# ── STATE PHYSICS ──
STATE_PHYSICS_TOOLS = [
    ToolDef(name="sp_state", description="Get full State Physics universe — nodes, edges, metrics, invariants.", category=ToolCategory.STATE_PHYSICS,
            params=[], handler="_custom_sp_state", access={_R}, priority=20),
    ToolDef(name="sp_reset", description="Reset State Physics universe to initial state.", category=ToolCategory.STATE_PHYSICS,
            params=[], handler="_custom_sp_reset", access={_R}, priority=25),
    ToolDef(name="sp_nodes", description="List all nodes in Hash Sphere universe.", category=ToolCategory.STATE_PHYSICS,
            params=[], handler="_custom_sp_nodes", access={_R}, priority=20),
    ToolDef(name="sp_metrics", description="Get universe metrics — node count, edge count, entropy.", category=ToolCategory.STATE_PHYSICS,
            params=[], handler="_custom_sp_metrics", access={_R}, priority=20),
    ToolDef(name="sp_identity", description="Create identity node in Hash Sphere universe.", category=ToolCategory.STATE_PHYSICS,
            params=[ToolParam("dsid", ParamType.STRING, "unique ID", required=True), ToolParam("node_type", ParamType.STRING, "user|service|agent|data", default="user"), ToolParam("trust", ParamType.NUMBER, "trust 0-1", default=0.5), ToolParam("value", ParamType.NUMBER, "value", default=0)],
            handler="_custom_sp_identity", access={_R}, priority=25),
    ToolDef(name="sp_simulate", description="Run N physics simulation steps.", category=ToolCategory.STATE_PHYSICS,
            params=[ToolParam("steps", ParamType.INTEGER, "simulation steps", default=1)],
            handler="_custom_sp_simulate", access={_R}, priority=25),
    ToolDef(name="sp_galaxy", description="Create galaxy-scale simulation.", category=ToolCategory.STATE_PHYSICS,
            params=[ToolParam("num_users", ParamType.INTEGER, default=500), ToolParam("num_transactions", ParamType.INTEGER, default=1500), ToolParam("num_services", ParamType.INTEGER, default=10), ToolParam("enable_agent", ParamType.BOOLEAN, default=True), ToolParam("enable_entropy", ParamType.BOOLEAN, default=True)],
            handler="_custom_sp_galaxy", access={_R}, priority=30),
    ToolDef(name="sp_demo", description="Seed universe with demo data.", category=ToolCategory.STATE_PHYSICS,
            params=[ToolParam("num_users", ParamType.INTEGER, default=30), ToolParam("num_transactions", ParamType.INTEGER, default=80)],
            handler="_custom_sp_demo", access={_R}, priority=30),
    ToolDef(name="sp_asymmetry", description="Get asymmetry score — trust variance and Gini.", category=ToolCategory.STATE_PHYSICS,
            params=[], handler="_custom_sp_asymmetry", access={_R}, priority=25),
    ToolDef(name="sp_physics_config", description="Update physics engine parameters.", category=ToolCategory.STATE_PHYSICS,
            params=[ToolParam("gravity_constant", ParamType.NUMBER), ToolParam("repulsion_constant", ParamType.NUMBER), ToolParam("spring_constant", ParamType.NUMBER), ToolParam("damping", ParamType.NUMBER)],
            handler="_custom_sp_physics_config", access={_R}, priority=30),
    ToolDef(name="sp_entropy_config", description="Update entropy engine parameters.", category=ToolCategory.STATE_PHYSICS,
            params=[ToolParam("position_noise", ParamType.NUMBER), ToolParam("velocity_noise", ParamType.NUMBER), ToolParam("trust_decay", ParamType.NUMBER), ToolParam("value_decay", ParamType.NUMBER), ToolParam("activity_probability", ParamType.NUMBER)],
            handler="_custom_sp_entropy_config", access={_R}, priority=30),
    ToolDef(name="sp_entropy_toggle", description="Enable or disable entropy injection.", category=ToolCategory.STATE_PHYSICS,
            params=[ToolParam("enabled", ParamType.BOOLEAN, default=True)],
            handler="_custom_sp_entropy_toggle", access={_R}, priority=30),
    ToolDef(name="sp_entropy_perturbation", description="Inject perturbation event.", category=ToolCategory.STATE_PHYSICS,
            params=[ToolParam("magnitude", ParamType.NUMBER, "perturbation strength", default=1.0)],
            handler="_custom_sp_entropy_perturbation", access={_R}, priority=30),
    ToolDef(name="sp_agent_spawn", description="Spawn autonomous agent in universe.", category=ToolCategory.STATE_PHYSICS,
            params=[ToolParam("budget", ParamType.NUMBER, default=5000), ToolParam("action_probability", ParamType.NUMBER, default=0.3)],
            handler="_custom_sp_agent_spawn", access={_R}, priority=25),
    ToolDef(name="sp_agent_step", description="Step the active agent once.", category=ToolCategory.STATE_PHYSICS,
            params=[], handler="_custom_sp_agent_step", access={_R}, priority=25),
    ToolDef(name="sp_agent_kill", description="Kill the active agent.", category=ToolCategory.STATE_PHYSICS,
            params=[], handler="_custom_sp_agent_kill", access={_R}, priority=25),
    ToolDef(name="sp_agents_spawn", description="Spawn multiple agents.", category=ToolCategory.STATE_PHYSICS,
            params=[ToolParam("count", ParamType.INTEGER, default=3), ToolParam("budget", ParamType.NUMBER, default=1000), ToolParam("action_probability", ParamType.NUMBER, default=0.3)],
            handler="_custom_sp_agents_spawn", access={_R}, priority=25),
    ToolDef(name="sp_agents_kill_all", description="Kill all autonomous agents.", category=ToolCategory.STATE_PHYSICS,
            params=[], handler="_custom_sp_agents_kill_all", access={_R}, priority=25),
    ToolDef(name="sp_experiment", description="Setup named experiment — zero_agent, stress_test, long_run.", category=ToolCategory.STATE_PHYSICS,
            params=[ToolParam("experiment", ParamType.STRING, "experiment name", required=True, enum=["zero_agent","stress_test","long_run"])],
            handler="_custom_sp_experiment", access={_R}, priority=30),
    ToolDef(name="sp_memory_cost", description="Set memory cost multiplier.", category=ToolCategory.STATE_PHYSICS,
            params=[ToolParam("cost_multiplier", ParamType.NUMBER, default=1.0)],
            handler="_custom_sp_memory_cost", access={_R}, priority=30),
    ToolDef(name="sp_metrics_record", description="Record metrics snapshot to history.", category=ToolCategory.STATE_PHYSICS,
            params=[], handler="_custom_sp_metrics_record", access={_R}, priority=30),
]

# ── COMMUNITY / RABBIT ──
COMMUNITY_TOOLS = [
    ToolDef(name="create_rabbit_post", description="Create post in Rabbit community.", category=ToolCategory.COMMUNITY,
            params=[ToolParam("title", ParamType.STRING, required=True), ToolParam("body", ParamType.STRING), ToolParam("community_slug", ParamType.STRING, required=True)],
            handler="_custom_rabbit_create_post", access={_R, _A}, priority=30),
    ToolDef(name="list_rabbit_communities", description="List all Rabbit communities.", category=ToolCategory.COMMUNITY,
            params=[], handler="_custom_rabbit_list_communities", access={_R, _A}, priority=30),
    ToolDef(name="list_rabbit_posts", description="List Rabbit posts.", category=ToolCategory.COMMUNITY,
            params=[ToolParam("community_slug", ParamType.STRING), ToolParam("limit", ParamType.INTEGER, default=20)],
            handler="_custom_rabbit_list_posts", access={_R}, priority=30),
    ToolDef(name="rabbit_vote", description="Vote on Rabbit post/comment.", category=ToolCategory.COMMUNITY,
            params=[ToolParam("target_id", ParamType.INTEGER, required=True), ToolParam("value", ParamType.INTEGER, required=True)],
            handler="_custom_rabbit_vote", access={_R}, priority=35),
    ToolDef(name="create_rabbit_community", description="Create a new Rabbit community.", category=ToolCategory.COMMUNITY,
            params=[ToolParam("slug", ParamType.STRING, "url-friendly id", required=True), ToolParam("name", ParamType.STRING, "display name", required=True), ToolParam("description", ParamType.STRING)],
            handler="_custom_rabbit_create_community", access={_R}, priority=30),
    ToolDef(name="get_rabbit_community", description="Get a Rabbit community by slug.", category=ToolCategory.COMMUNITY,
            params=[ToolParam("slug", ParamType.STRING, required=True)],
            handler="_custom_rabbit_get_community", access={_R}, priority=30),
    ToolDef(name="search_rabbit_posts", description="Search Rabbit posts by keyword.", category=ToolCategory.COMMUNITY,
            params=[ToolParam("query", ParamType.STRING, required=True), ToolParam("limit", ParamType.INTEGER, default=20)],
            handler="_custom_rabbit_search_posts", access={_R}, priority=30),
    ToolDef(name="get_rabbit_post", description="Get a specific Rabbit post by ID.", category=ToolCategory.COMMUNITY,
            params=[ToolParam("post_id", ParamType.INTEGER, required=True)],
            handler="_custom_rabbit_get_post", access={_R}, priority=30),
    ToolDef(name="delete_rabbit_post", description="Delete a Rabbit post (owner only).", category=ToolCategory.COMMUNITY,
            params=[ToolParam("post_id", ParamType.INTEGER, required=True)],
            handler="_custom_rabbit_delete_post", access={_R}, priority=35),
    ToolDef(name="create_rabbit_comment", description="Comment on a Rabbit post.", category=ToolCategory.COMMUNITY,
            params=[ToolParam("post_id", ParamType.INTEGER, required=True), ToolParam("body", ParamType.STRING, required=True), ToolParam("parent_comment_id", ParamType.INTEGER)],
            handler="_custom_rabbit_create_comment", access={_R}, priority=30),
    ToolDef(name="list_rabbit_comments", description="List comments on a Rabbit post.", category=ToolCategory.COMMUNITY,
            params=[ToolParam("post_id", ParamType.INTEGER, required=True)],
            handler="_custom_rabbit_list_comments", access={_R}, priority=30),
    ToolDef(name="delete_rabbit_comment", description="Delete a Rabbit comment (owner only).", category=ToolCategory.COMMUNITY,
            params=[ToolParam("comment_id", ParamType.INTEGER, required=True)],
            handler="_custom_rabbit_delete_comment", access={_R}, priority=35),
]

# ── AGENTS OS (extended) ──
AGENT_TOOLS_EXTENDED = [
    ToolDef(name="agents_delete", description="Delete an agent.", category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING, required=True)],
            handler="_custom_agents_delete", access={_R}, priority=15),
    ToolDef(name="agents_sessions", description="List sessions/runs for an agent.", category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING, required=True), ToolParam("status", ParamType.STRING, enum=["running","completed","failed","cancelled"]), ToolParam("limit", ParamType.INTEGER, default=20)],
            handler="_custom_agents_sessions", access={_R}, priority=15),
    ToolDef(name="agents_session_steps", description="Get execution steps for a session.", category=ToolCategory.AGENTS,
            params=[ToolParam("session_id", ParamType.STRING, required=True)],
            handler="_custom_agents_session_steps", access={_R}, priority=15),
    ToolDef(name="agents_session_trace", description="Full execution trace — steps, waterfall, cost, safety flags.", category=ToolCategory.AGENTS,
            params=[ToolParam("session_id", ParamType.STRING, required=True)],
            handler="_custom_agents_session_trace", access={_R}, priority=15),
    ToolDef(name="agents_metrics", description="Get agent run metrics (sessions, tokens, success rate).", category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING)],
            handler="_custom_agents_metrics", access={_R}, priority=20),
    ToolDef(name="agents_session_detail", description="Detailed info for a single session.", category=ToolCategory.AGENTS,
            params=[ToolParam("session_id", ParamType.STRING, required=True)],
            handler="_custom_agents_session_detail", access={_R}, priority=15),
    ToolDef(name="agents_session_cancel", description="Cancel a running session.", category=ToolCategory.AGENTS,
            params=[ToolParam("session_id", ParamType.STRING, required=True)],
            handler="_custom_agents_session_cancel", access={_R}, priority=10),
    ToolDef(name="agents_update", description="Update agent config — name, goal, model, tools, etc.", category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING, required=True), ToolParam("name", ParamType.STRING), ToolParam("goal", ParamType.STRING), ToolParam("model", ParamType.STRING), ToolParam("tools", ParamType.ARRAY, items_type="string"), ToolParam("system_prompt", ParamType.STRING), ToolParam("temperature", ParamType.NUMBER), ToolParam("is_active", ParamType.BOOLEAN)],
            handler="_custom_agents_update", access={_R}, priority=10),
    ToolDef(name="agents_available_tools", description="List all tools agents can use.", category=ToolCategory.AGENTS,
            params=[], handler="_custom_agents_available_tools", access={_R}, priority=20),
    ToolDef(name="agents_templates", description="List available agent templates.", category=ToolCategory.AGENTS,
            params=[], handler="_custom_agents_templates", access={_R}, priority=25),
    ToolDef(name="agents_versions", description="Get version history for an agent.", category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING, required=True)],
            handler="_custom_agents_versions", access={_R}, priority=25),
    ToolDef(name="schedule_agent", description="Set recurring schedule for an agent.", category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING), ToolParam("agent_name", ParamType.STRING), ToolParam("schedule", ParamType.STRING, "e.g. hourly, daily, cron expression", required=True), ToolParam("goal", ParamType.STRING)],
            handler="_custom_schedule_agent", access={_R}, priority=15),
    ToolDef(name="run_snapshot", description="Get detailed snapshot of a specific agent run.", category=ToolCategory.AGENTS,
            params=[ToolParam("session_id", ParamType.STRING, required=True)],
            handler="_custom_run_snapshot", access={_R}, priority=15),
    ToolDef(name="list_workspace_tools", description="List ALL available tools grouped by category.", category=ToolCategory.AGENTS,
            params=[], handler="_custom_list_workspace_tools", access={_R}, priority=20),
    ToolDef(name="agent_snapshot", description="Full config of a specific agent.", category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING), ToolParam("agent_name", ParamType.STRING)],
            handler="_custom_agent_snapshot", access={_R}, priority=15),
    ToolDef(name="session_log", description="Get current chat session's log — tools, loops, tokens, elapsed.", category=ToolCategory.AGENTS,
            params=[], handler="_custom_session_log", access={_R}, priority=20),
]

# ── AGENT ARCHITECT (meta-agent that creates & configures other agents) ──
AGENT_ARCHITECT_TOOLS = [
    ToolDef(name="architect_plan", description="Analyze a user request and produce a JSON blueprint for one or more production-ready agents with optimal tools, models, schedules, goals, and webhooks.",
            category=ToolCategory.AGENTS,
            params=[ToolParam("request", ParamType.STRING, "natural language description of what agents to build", required=True)],
            handler="_custom_architect_plan", access={_R, _A}, priority=5),
    ToolDef(name="architect_create_agent", description="Create a fully-configured agent from a blueprint — sets tools, model, provider, system prompt, safety config.",
            category=ToolCategory.AGENTS,
            params=[ToolParam("name", ParamType.STRING, "agent name", required=True),
                    ToolParam("description", ParamType.STRING, "agent purpose", required=True),
                    ToolParam("provider", ParamType.STRING, "LLM provider", default="groq"),
                    ToolParam("model", ParamType.STRING, "model name", default="llama-3.3-70b-versatile"),
                    ToolParam("tools", ParamType.ARRAY, "tool names to assign", items_type="string"),
                    ToolParam("mode", ParamType.STRING, "governed or unbounded", default="governed"),
                    ToolParam("system_prompt", ParamType.STRING, "custom system prompt"),
                    ToolParam("temperature", ParamType.NUMBER, default=0.6)],
            handler="_custom_architect_create_agent", access={_R, _A}, priority=5),
    ToolDef(name="architect_assign_goal", description="Assign a goal to an agent created by Agent Architect.",
            category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING, required=True),
                    ToolParam("goal", ParamType.STRING, "goal description", required=True),
                    ToolParam("priority", ParamType.INTEGER, "1-10", default=5)],
            handler="_custom_architect_assign_goal", access={_R, _A}, priority=5),
    ToolDef(name="architect_create_schedule", description="Create a recurring schedule for an agent — cron or interval.",
            category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING, required=True),
                    ToolParam("name", ParamType.STRING, "schedule name", required=True),
                    ToolParam("goal", ParamType.STRING, "what to do each run", required=True),
                    ToolParam("cron_expression", ParamType.STRING, "cron e.g. '0 */6 * * *'"),
                    ToolParam("interval_seconds", ParamType.INTEGER, "seconds between runs")],
            handler="_custom_architect_create_schedule", access={_R, _A}, priority=10),
    ToolDef(name="architect_create_webhook", description="Create a webhook trigger for an agent so it can be invoked externally.",
            category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING, required=True),
                    ToolParam("name", ParamType.STRING, "webhook name", required=True)],
            handler="_custom_architect_create_webhook", access={_R, _A}, priority=10),
    ToolDef(name="architect_set_autonomy", description="Set an agent's autonomy mode (governed, supervised, unbounded).",
            category=ToolCategory.AGENTS,
            params=[ToolParam("agent_id", ParamType.STRING, required=True),
                    ToolParam("mode", ParamType.STRING, "autonomy mode", required=True, enum=["governed","supervised","unbounded"]),
                    ToolParam("reason", ParamType.STRING, "why changing mode")],
            handler="_custom_architect_set_autonomy", access={_R, _A}, priority=10),
    ToolDef(name="architect_list_available_tools", description="List all tools available to assign to agents.",
            category=ToolCategory.AGENTS,
            params=[], handler="_custom_architect_list_tools", access={_R, _A}, priority=15),
    ToolDef(name="architect_list_providers", description="List available LLM providers and models for agent creation.",
            category=ToolCategory.AGENTS,
            params=[], handler="_custom_architect_list_providers", access={_R, _A}, priority=15),
]

# ── DEVELOPER ──
DEVELOPER_TOOLS = [
    ToolDef(name="execute_code", description="Run code in Docker sandbox (python/js/bash).", category=ToolCategory.DEVELOPER,
            params=[ToolParam("code", ParamType.STRING, required=True), ToolParam("language", ParamType.STRING, required=True, enum=["python","javascript","bash"])],
            handler="execute_code", access={_R, _A}, priority=15, max_result_chars=8000),
    ToolDef(name="http_request", description="HTTP request to internal platform APIs.", category=ToolCategory.DEVELOPER,
            params=[ToolParam("url", ParamType.STRING, required=True), ToolParam("method", ParamType.STRING, default="GET")],
            handler="http_request", access={_R, _A}, priority=20),
    ToolDef(name="external_http_request", description="HTTP request to any external URL.", category=ToolCategory.DEVELOPER,
            params=[ToolParam("url", ParamType.STRING, required=True), ToolParam("method", ParamType.STRING, default="GET")],
            handler="external_http_request", access={_A}, priority=20),
    ToolDef(name="dev_tool", description="Bridge to ED service for file ops, git, docker, testing.", category=ToolCategory.DEVELOPER,
            params=[ToolParam("tool_name", ParamType.STRING, required=True), ToolParam("parameters", ParamType.OBJECT)],
            handler="dev_tool", access={_A}, priority=20),
]

# ── GITHUB ──
GITHUB_TOOLS = [
    ToolDef(name="github_create_repo", description="Create GitHub repository.", category=ToolCategory.GITHUB,
            params=[ToolParam("name", ParamType.STRING, required=True), ToolParam("private", ParamType.BOOLEAN, default=False)],
            handler="_custom_github_create_repo", access={_R}, priority=25),
    ToolDef(name="github_list_repos", description="List GitHub repositories.", category=ToolCategory.GITHUB,
            params=[ToolParam("owner", ParamType.STRING)], handler="_custom_github_list_repos", access={_R}, priority=25),
    ToolDef(name="github_list_files", description="List files in a GitHub repo.", category=ToolCategory.GITHUB,
            params=[ToolParam("owner", ParamType.STRING, required=True), ToolParam("repo", ParamType.STRING, required=True), ToolParam("path", ParamType.STRING, default="")],
            handler="_custom_github_list_files", access={_R}, priority=25),
    ToolDef(name="github_download_file", description="Download file from GitHub repo.", category=ToolCategory.GITHUB,
            params=[ToolParam("owner", ParamType.STRING, required=True), ToolParam("repo", ParamType.STRING, required=True), ToolParam("path", ParamType.STRING, required=True)],
            handler="_custom_github_download_file", access={_R}, priority=25),
    ToolDef(name="github_upload_file", description="Upload file to GitHub repo.", category=ToolCategory.GITHUB,
            params=[ToolParam("owner", ParamType.STRING, required=True), ToolParam("repo", ParamType.STRING, required=True), ToolParam("path", ParamType.STRING, required=True), ToolParam("content", ParamType.STRING, required=True), ToolParam("message", ParamType.STRING, required=True)],
            handler="_custom_github_upload_file", access={_R}, priority=25),
    ToolDef(name="github_pull_request", description="Create or list pull requests.", category=ToolCategory.GITHUB,
            params=[ToolParam("owner", ParamType.STRING, required=True), ToolParam("repo", ParamType.STRING, required=True), ToolParam("action", ParamType.STRING, default="list", enum=["create","list"])],
            handler="_custom_github_pull_request", access={_R}, priority=25),
    ToolDef(name="github_issue", description="Create or list issues.", category=ToolCategory.GITHUB,
            params=[ToolParam("owner", ParamType.STRING, required=True), ToolParam("repo", ParamType.STRING, required=True), ToolParam("action", ParamType.STRING, default="list", enum=["create","list"])],
            handler="_custom_github_issue", access={_R}, priority=25),
    ToolDef(name="github_commit", description="Get commits in a repository.", category=ToolCategory.GITHUB,
            params=[ToolParam("owner", ParamType.STRING, required=True), ToolParam("repo", ParamType.STRING, required=True), ToolParam("sha", ParamType.STRING), ToolParam("limit", ParamType.INTEGER, default=10)],
            handler="_custom_github_commits", access={_R}, priority=25),
    ToolDef(name="github_comment", description="Comment on a GitHub issue or PR.", category=ToolCategory.GITHUB,
            params=[ToolParam("owner", ParamType.STRING, required=True), ToolParam("repo", ParamType.STRING, required=True), ToolParam("issue_number", ParamType.INTEGER, required=True), ToolParam("body", ParamType.STRING, required=True)],
            handler="_custom_github_comment", access={_R}, priority=25),
]

# ── TOOL MANAGEMENT ──
TOOL_MANAGEMENT_TOOLS = [
    ToolDef(name="create_tool", description="Create custom HTTP tool stored in DB. Tool becomes available platform-wide when is_shared=true.", category=ToolCategory.SYSTEM,
            params=[ToolParam("tool_name", ParamType.STRING, required=True), ToolParam("description", ParamType.STRING, required=True), ToolParam("endpoint_url", ParamType.STRING, required=True),
                    ToolParam("category", ParamType.STRING, "tool category (auto-created if new)", default="custom"),
                    ToolParam("http_method", ParamType.STRING, default="GET"), ToolParam("parameters", ParamType.OBJECT, "JSON param schema"),
                    ToolParam("request_body", ParamType.OBJECT, "JSON body template"), ToolParam("is_shared", ParamType.BOOLEAN, "make available platform-wide", default=False)],
            handler="_custom_create_tool", access={_R, _G, _A}, priority=40),
    ToolDef(name="list_tools", description="List user's custom tools and all shared platform tools.", category=ToolCategory.SYSTEM,
            params=[], handler="_custom_list_tools", access={_R, _G, _A}, priority=40),
    ToolDef(name="delete_tool", description="Delete a custom tool.", category=ToolCategory.SYSTEM,
            params=[ToolParam("tool_name", ParamType.STRING, required=True)], handler="_custom_delete_tool", access={_R, _G, _A}, priority=40),
    ToolDef(name="update_tool", description="Update an existing custom tool.", category=ToolCategory.SYSTEM,
            params=[ToolParam("tool_name", ParamType.STRING, required=True), ToolParam("description", ParamType.STRING), ToolParam("endpoint_url", ParamType.STRING), ToolParam("http_method", ParamType.STRING)],
            handler="_custom_update_tool", access={_R, _G, _A}, priority=40),
    ToolDef(name="auto_build_tool", description="LLM designs, validates (AST safety scan), and registers a new tool at runtime. Describe what the tool should do and it will be created automatically.", category=ToolCategory.SYSTEM,
            params=[ToolParam("capability", ParamType.STRING, "what the tool should do", required=True),
                    ToolParam("category", ParamType.STRING, "tool category", default="custom"),
                    ToolParam("is_shared", ParamType.BOOLEAN, "make available platform-wide", default=True)],
            handler="_custom_auto_build_tool", access={_R, _G, _A}, priority=35),
    ToolDef(name="check_tool_exists", description="Check if a capability exists as a tool. Returns the tool if found, or suggests building one if not.", category=ToolCategory.SYSTEM,
            params=[ToolParam("capability", ParamType.STRING, "what you need the tool to do", required=True)],
            handler="_custom_check_tool_exists", access={_R, _G, _A}, priority=35),
]

# ── GIT OPERATIONS ──
GIT_TOOLS = [
    ToolDef(name="git_clone", description="Clone a Git repository.", category=ToolCategory.GIT,
            params=[ToolParam("url", ParamType.STRING, "repo URL", required=True), ToolParam("path", ParamType.STRING, "destination path")],
            handler="_custom_git_proxy", access={_R}, priority=25),
    ToolDef(name="git_branch", description="Create, list, or switch Git branches.", category=ToolCategory.GIT,
            params=[ToolParam("action", ParamType.STRING, default="list", enum=["create","list","switch"]), ToolParam("branch", ParamType.STRING), ToolParam("path", ParamType.STRING)],
            handler="_custom_git_proxy", access={_R}, priority=25),
    ToolDef(name="git_merge", description="Merge a branch into current branch.", category=ToolCategory.GIT,
            params=[ToolParam("branch", ParamType.STRING, required=True), ToolParam("path", ParamType.STRING)],
            handler="_custom_git_proxy", access={_R}, priority=25),
    ToolDef(name="git_push", description="Push commits to remote.", category=ToolCategory.GIT,
            params=[ToolParam("remote", ParamType.STRING, default="origin"), ToolParam("branch", ParamType.STRING), ToolParam("path", ParamType.STRING)],
            handler="_custom_git_proxy", access={_R}, priority=25),
    ToolDef(name="git_pull", description="Pull changes from remote.", category=ToolCategory.GIT,
            params=[ToolParam("remote", ParamType.STRING, default="origin"), ToolParam("branch", ParamType.STRING), ToolParam("path", ParamType.STRING)],
            handler="_custom_git_proxy", access={_R}, priority=25),
]

# ── EMAIL ──
EMAIL_TOOLS = [
    ToolDef(name="send_email", description="Send an email via SendGrid. Supports HTML.", category=ToolCategory.INTEGRATIONS,
            params=[ToolParam("to", ParamType.STRING, required=True), ToolParam("subject", ParamType.STRING, required=True), ToolParam("body", ParamType.STRING, required=True)],
            handler="_custom_send_email", access={_R}, priority=30),
]

# ── PLATFORM API ──
PLATFORM_API_TOOLS = [
    ToolDef(name="platform_api_search", description="Search ~383 platform API endpoints.", category=ToolCategory.PLATFORM_API,
            params=[ToolParam("query", ParamType.STRING, required=True), ToolParam("category", ParamType.STRING)],
            handler="_custom_platform_api_search", access={_R, _I}, priority=15),
    ToolDef(name="platform_api_call", description="Call any authenticated platform API endpoint.", category=ToolCategory.PLATFORM_API,
            params=[ToolParam("method", ParamType.STRING, default="GET"), ToolParam("path", ParamType.STRING, required=True), ToolParam("body", ParamType.OBJECT)],
            handler="_custom_platform_api_call", access={_R, _I}, priority=15),
]

# ── FILESYSTEM (shared between IDE and agentic chat) ──
IDE_FILESYSTEM_TOOLS = [
    ToolDef(name="file_read", description="Read file with offset/limit.", category=ToolCategory.FILESYSTEM, access={_I, _R},
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("offset", ParamType.NUMBER), ToolParam("limit", ParamType.NUMBER)], priority=1),
    ToolDef(name="file_write", description="Create or overwrite file.", category=ToolCategory.FILESYSTEM, access={_I, _R},
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("content", ParamType.STRING, required=True)], priority=1),
    ToolDef(name="file_edit", description="Replace exact unique string in file.", category=ToolCategory.FILESYSTEM, access={_I, _R},
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("old_string", ParamType.STRING, required=True), ToolParam("new_string", ParamType.STRING, required=True), ToolParam("replace_all", ParamType.BOOLEAN)], priority=1),
    ToolDef(name="multi_edit", description="Atomic batch edits on one file.", category=ToolCategory.FILESYSTEM, access={_I},
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("edits", ParamType.ARRAY, required=True)], priority=1),
    ToolDef(name="file_list", description="List directory contents.", category=ToolCategory.FILESYSTEM, access={_I, _R},
            params=[ToolParam("path", ParamType.STRING, required=True)], priority=1),
    ToolDef(name="file_delete", description="Delete file or directory.", category=ToolCategory.FILESYSTEM, access={_I, _R},
            params=[ToolParam("path", ParamType.STRING, required=True)], priority=5),
    ToolDef(name="grep_search", description="Search text pattern in files via ripgrep.", category=ToolCategory.FILESYSTEM, access={_I},
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("pattern", ParamType.STRING, required=True), ToolParam("include", ParamType.STRING)], priority=1),
    ToolDef(name="find_by_name", description="Find files by name glob.", category=ToolCategory.FILESYSTEM, access={_I},
            params=[ToolParam("path", ParamType.STRING, required=True), ToolParam("pattern", ParamType.STRING, required=True)], priority=1),
    ToolDef(name="run_command", description="Run shell command.", category=ToolCategory.FILESYSTEM, access={_I},
            params=[ToolParam("command", ParamType.STRING, required=True), ToolParam("cwd", ParamType.STRING), ToolParam("blocking", ParamType.BOOLEAN, default=True)], priority=1),
    ToolDef(name="command_status", description="Check background command status.", category=ToolCategory.FILESYSTEM, access={_I},
            params=[ToolParam("command_id", ParamType.STRING, required=True)], priority=5),
]


# ── CHAT SKILLS (high-level orchestrator skills for Resonant Chat & AI assistant) ──
# These are top-level skill entry points that the LLM routes user messages to.
# Each wraps multiple granular tools into a single user-facing capability.
CHAT_SKILL_TOOLS = [
    # --- Analysis ---
    ToolDef(name="skill_code_visualizer",
            description="Scan and analyze a GitHub repository or codebase. ONLY when user provides a GitHub URL or explicitly asks to scan/analyze a repo/codebase.",
            category=ToolCategory.CODE_ANALYSIS,
            params=[ToolParam("message", ParamType.STRING, "user message", required=True)],
            handler="_execute_code_visualizer", access={_R}, priority=5,
            requires_api_key=None),
    ToolDef(name="skill_state_physics",
            description="Open State Physics visualization panel. ONLY when user explicitly says 'open state physics', 'show state physics', or 'state-space visualization'.",
            category=ToolCategory.STATE_PHYSICS,
            params=[ToolParam("message", ParamType.STRING, "user message", required=True)],
            handler="_execute_state_physics", access={_R}, priority=10),
    ToolDef(name="skill_sigma",
            description="Access Sigma Computing dashboards. When user asks about their Sigma reports or analytics.",
            category=ToolCategory.INTEGRATIONS,
            params=[ToolParam("message", ParamType.STRING, "user message", required=True)],
            handler="_execute_integration", access={_R}, priority=15, requires_api_key="sigma"),

    # --- Search ---
    ToolDef(name="skill_web_search",
            description="Search the web for real-time information. ONLY for current events, live prices, weather, recent news, or facts that require up-to-date data the AI cannot know.",
            category=ToolCategory.SEARCH,
            params=[ToolParam("message", ParamType.STRING, "user message", required=True)],
            handler="_execute_web_search", access={_R, _G}, priority=5,
            requires_api_key="tavily"),

    # --- Generation ---
    ToolDef(name="skill_image_generation",
            description="Generate an image with DALL-E. ONLY when user explicitly asks to generate/create/draw/make an image, picture, or illustration.",
            category=ToolCategory.MEDIA,
            params=[ToolParam("message", ParamType.STRING, "user message", required=True)],
            handler="_execute_image_generation", access={_R}, priority=10,
            requires_api_key="openai"),

    # --- Memory ---
    ToolDef(name="skill_memory_search",
            description="Search user's long-term memory for previously stored information. When user asks 'what did I say about X' or 'do you remember X'.",
            category=ToolCategory.MEMORY,
            params=[ToolParam("message", ParamType.STRING, "user message", required=True)],
            handler="_execute_memory_search", access={_R}, priority=5),
    ToolDef(name="skill_memory_library",
            description="Open the memory library panel. ONLY when user explicitly says 'open memory library', 'show my memories', or 'browse memories'.",
            category=ToolCategory.MEMORY,
            params=[ToolParam("message", ParamType.STRING, "user message", required=True)],
            handler="_execute_memory_library", access={_R}, priority=10),

    # --- Agents ---
    ToolDef(name="skill_agents_os",
            description="Create, manage, rename, delete, or configure AI agents. ONLY when user explicitly asks to create/build/manage/rename/delete agents or open Agents OS.",
            category=ToolCategory.AGENTS,
            params=[ToolParam("message", ParamType.STRING, "user message", required=True)],
            handler="_execute_agents_os", access={_R}, priority=5),
    ToolDef(name="skill_agent_architect",
            description="Design and build advanced autonomous agents from a high-level description. When user wants a powerful/professional/advanced/autonomous agent built with optimal setup — tools, schedules, budgets, webhooks, goals, API connections. Use this instead of agents_os when user describes WHAT they need (not just 'create agent') and wants smart auto-configuration.",
            category=ToolCategory.AGENTS,
            params=[ToolParam("message", ParamType.STRING, "user message", required=True)],
            handler="_execute_agent_architect", access={_R}, priority=3),

    # --- Utility ---
    ToolDef(name="skill_ide_workspace",
            description="Open the IDE workspace split panel. ONLY when user explicitly says 'open IDE', 'open editor', 'open terminal', or 'open workspace'. Do NOT trigger for coding questions.",
            category=ToolCategory.DEVELOPER,
            params=[ToolParam("message", ParamType.STRING, "user message", required=True)],
            handler="_execute_ide_workspace", access={_R}, priority=10),
    ToolDef(name="skill_rabbit_post",
            description="Create a post on Rabbit community forum. When user wants to post something to a Rabbit community.",
            category=ToolCategory.COMMUNITY,
            params=[ToolParam("message", ParamType.STRING, "user message", required=True)],
            handler="_execute_rabbit_post", access={_R}, priority=10),

    # --- Integrations ---
    ToolDef(name="skill_google_drive",
            description="Access Google Drive files. When user asks about their Drive files, documents, or wants to search/read/create files.",
            category=ToolCategory.INTEGRATIONS,
            params=[ToolParam("message", ParamType.STRING, "user message", required=True)],
            handler="_execute_integration", access={_R}, priority=15, requires_api_key="google-drive"),
    ToolDef(name="skill_google_calendar",
            description="Access Google Calendar. When user asks about their schedule, events, meetings, or wants to create/view calendar events.",
            category=ToolCategory.INTEGRATIONS,
            params=[ToolParam("message", ParamType.STRING, "user message", required=True)],
            handler="_execute_integration", access={_R}, priority=15, requires_api_key="google-calendar"),
    ToolDef(name="skill_figma",
            description="Access Figma designs. When user asks about their Figma projects, design files, or components.",
            category=ToolCategory.INTEGRATIONS,
            params=[ToolParam("message", ParamType.STRING, "user message", required=True)],
            handler="_execute_integration", access={_R}, priority=15, requires_api_key="figma"),
]

# Mapping: skill_id (used by Resonant Chat) -> unified tool name
SKILL_ID_TO_TOOL_NAME = {
    "code_visualizer": "skill_code_visualizer",
    "web_search": "skill_web_search",
    "image_generation": "skill_image_generation",
    "memory_search": "skill_memory_search",
    "memory_library": "skill_memory_library",
    "agents_os": "skill_agents_os",
    "agent_architect": "skill_agent_architect",
    "state_physics": "skill_state_physics",
    "ide_workspace": "skill_ide_workspace",
    "rabbit_post": "skill_rabbit_post",
    "google_drive": "skill_google_drive",
    "google_calendar": "skill_google_calendar",
    "figma": "skill_figma",
    "sigma": "skill_sigma",
}
TOOL_NAME_TO_SKILL_ID = {v: k for k, v in SKILL_ID_TO_TOOL_NAME.items()}


def get_chat_skill_descriptions() -> Dict[str, str]:
    """Build _SKILL_TOOL_DESCRIPTIONS dict from unified CHAT_SKILL_TOOLS.
    Returns {skill_id: description} for LLM detection in Resonant Chat."""
    descs = {}
    for tool in CHAT_SKILL_TOOLS:
        skill_id = TOOL_NAME_TO_SKILL_ID.get(tool.name)
        if skill_id:
            descs[skill_id] = tool.description
    return descs


# ═══════════════════════════════════════════════════════════
# ALL_TOOLS — single flat list of every tool on the platform
# ═══════════════════════════════════════════════════════════
ALL_TOOLS = (
    SEARCH_TOOLS
    + MEMORY_TOOLS
    + HASH_SPHERE_TOOLS
    + UTILITY_TOOLS
    + CODE_VISUALIZER_TOOLS
    + AGENT_TOOLS
    + AGENT_TOOLS_EXTENDED
    + AGENT_ARCHITECT_TOOLS
    + MEDIA_TOOLS
    + INTEGRATION_TOOLS
    + STATE_PHYSICS_TOOLS
    + COMMUNITY_TOOLS
    + DEVELOPER_TOOLS
    + GITHUB_TOOLS
    + GIT_TOOLS
    + EMAIL_TOOLS
    + TOOL_MANAGEMENT_TOOLS
    + PLATFORM_API_TOOLS
    + IDE_FILESYSTEM_TOOLS
    + CHAT_SKILL_TOOLS
)


def build_registry() -> "ToolRegistry":
    """Build a fully-populated ToolRegistry with ALL platform tools."""
    from .registry import ToolRegistry
    registry = ToolRegistry()
    registry.register_bulk(ALL_TOOLS)
    return registry
