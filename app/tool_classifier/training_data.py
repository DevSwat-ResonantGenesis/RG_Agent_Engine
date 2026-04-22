"""
Tool Router Training Data — Agent Engine Edition
===================================================

Copied from RG_Chat/app/services/tool_training_data.py and kept in sync.

High-quality labeled examples for training the neural tool classifier.
Each example is a (message, context, correct_tool) triple.

Context format: list of recent messages as dicts with "role" and "content".
Tool None means no tool needed (general reasoning / no tool).

This is the SEED dataset. Real agent interactions are collected via active
learning and merged during periodic retraining.
"""
from typing import List, Dict, Any, Optional, Tuple

# Type alias for training samples
# (user_message, recent_context, correct_skill_id_or_none)
TrainingSample = Tuple[str, List[Dict[str, str]], Optional[str]]


def get_training_data() -> List[TrainingSample]:
    """Return the full seed training dataset."""
    samples: List[TrainingSample] = []

    # ------------------------------------------------------------------
    # GOOGLE_DRIVE — file access, search, create
    # ------------------------------------------------------------------
    _gdrive = "google_drive"

    samples += [
        ("search my Google Drive for the quarterly report", [], _gdrive),
        ("find documents about the project in my Drive", [], _gdrive),
        ("upload this summary to Google Drive", [], _gdrive),
        ("create a spreadsheet in my Drive with these results", [], _gdrive),
        ("list my recent Drive files", [], _gdrive),
        ("open the marketing plan from my Drive", [], _gdrive),
        ("save this to my Google Drive as an Excel file", [], _gdrive),
        ("what files do I have in Drive", [], _gdrive),
        ("check my Drive for the meeting notes", [], _gdrive),
        ("download the budget spreadsheet from Drive", [], _gdrive),
    ]

    # Drive follow-ups
    samples += [
        ("yes save it as Excel", [
            {"role": "assistant", "content": "I can save the summary to your Google Drive. What format would you prefer — CSV, Excel, or Google Sheets?"},
        ], _gdrive),
        ("the marketing folder", [
            {"role": "assistant", "content": "Where in your Drive should I save the file?"},
        ], _gdrive),
    ]

    # ------------------------------------------------------------------
    # GOOGLE_CALENDAR — events, schedule, meetings
    # ------------------------------------------------------------------
    _gcal = "google_calendar"

    samples += [
        ("what's on my calendar today", [], _gcal),
        ("schedule a meeting for tomorrow at 3pm", [], _gcal),
        ("create a team standup event for Monday", [], _gcal),
        ("show my upcoming events this week", [], _gcal),
        ("when is my next meeting", [], _gcal),
        ("add a reminder to my calendar for Friday", [], _gcal),
        ("block 2pm to 4pm on my calendar", [], _gcal),
        ("do I have anything scheduled for Wednesday", [], _gcal),
        ("cancel my 10am meeting tomorrow", [], _gcal),
        ("move the design review to Thursday", [], _gcal),
    ]

    # ------------------------------------------------------------------
    # WEB_SEARCH — real-time info, news, prices
    # ------------------------------------------------------------------
    _web = "web_search"

    samples += [
        ("what's the weather in San Francisco right now", [], _web),
        ("latest news about artificial intelligence", [], _web),
        ("current Bitcoin price", [], _web),
        ("who won the NBA game last night", [], _web),
        ("what's trending on Twitter today", [], _web),
        ("search for recent papers on transformer models", [], _web),
        ("what happened in the stock market today", [], _web),
        ("find the latest SpaceX launch news", [], _web),
        ("current price of Tesla stock", [], _web),
        ("search for new AI startups in 2026", [], _web),
        ("what's the latest on the Ukraine situation", [], _web),
        ("look up recent developments in quantum computing", [], _web),
        ("find reviews for the new iPhone", [], _web),
        ("what are today's top headlines", [], _web),
        ("search for best restaurants in downtown LA", [], _web),
        # Weather follow-ups and location-based queries
        ("yes San Francisco", [
            {"role": "assistant", "content": "Would you like me to search for the weather in a specific location?"},
        ], _web),
        ("check the weather in New York", [], _web),
        ("will it rain tomorrow in London", [], _web),
        ("weather forecast for Tokyo this weekend", [], _web),
        ("is it going to snow in Denver", [], _web),
        ("current temperature in Berlin", [], _web),
        ("what's the forecast for Miami", [], _web),
        ("how's the weather today", [], _web),
        # Events and real-time queries
        ("what are the events today in San Francisco in tech industry", [], _web),
        ("tech events happening in New York this week", [], _web),
        ("upcoming AI conferences in 2026", [], _web),
        ("what meetups are happening tonight", [], _web),
        ("find tech events near me", [], _web),
        ("what concerts are happening this weekend", [], _web),
        ("search for hackathons in San Francisco", [], _web),
        # General real-time info
        ("what time is it in Tokyo", [], _web),
        ("what is the current stock price of Apple", [], _web),
        ("search for flight prices to London", [], _web),
        ("what are the latest sports scores", [], _web),
        ("find nearby pharmacies open now", [], _web),
        ("search for the best hotels in Barcelona", [], _web),
        ("what's on TV tonight", [], _web),
        ("find the closest gas station", [], _web),
        ("what are the movie showtimes near me", [], _web),
    ]

    # ------------------------------------------------------------------
    # IMAGE_GENERATION — create/draw/generate images
    # ------------------------------------------------------------------
    _img = "image_generation"

    samples += [
        ("generate an image of a sunset over mountains", [], _img),
        ("create a logo for my startup called NeuralFlow", [], _img),
        ("draw a cute robot holding a flower", [], _img),
        ("make me a picture of a futuristic city at night", [], _img),
        ("illustrate a dragon flying over a medieval castle", [], _img),
        ("generate a portrait in watercolor style", [], _img),
        ("create an abstract art piece with blue and gold", [], _img),
        ("draw a cyberpunk street scene", [], _img),
        ("make an image of a cozy coffee shop interior", [], _img),
        ("generate a product mockup for a mobile app", [], _img),
    ]

    # ------------------------------------------------------------------
    # CODE_VISUALIZER — GitHub repo scanning
    # ------------------------------------------------------------------
    _cv = "code_visualizer"

    samples += [
        ("scan this repo https://github.com/facebook/react", [], _cv),
        ("analyze my GitHub repository https://github.com/user/project", [], _cv),
        ("visualize the codebase structure of https://github.com/vercel/next.js", [], _cv),
        ("scan https://github.com/tensorflow/tensorflow", [], _cv),
        ("analyze the architecture of this GitHub repo https://github.com/openai/whisper", [], _cv),
        ("show me the code structure of https://github.com/langchain-ai/langchain", [], _cv),
        ("scan my project on GitHub and show the dependencies", [], _cv),
        ("visualize https://github.com/microsoft/vscode", [], _cv),
    ]

    # ------------------------------------------------------------------
    # MEMORY_SEARCH — recall past conversations
    # ------------------------------------------------------------------
    _mem = "memory_search"

    samples += [
        ("what did I say about the database last week", [], _mem),
        ("do you remember our conversation about React", [], _mem),
        ("recall what we discussed about the API design", [], _mem),
        ("search my memory for deployment instructions", [], _mem),
        ("what did we talk about yesterday", [], _mem),
        ("find our previous discussion about pricing", [], _mem),
        ("what was that thing I told you about the project structure", [], _mem),
        ("do you remember the password I mentioned", [], _mem),
        ("recall the architecture decisions we made", [], _mem),
    ]

    # ------------------------------------------------------------------
    # MEMORY_LIBRARY — open panel
    # ------------------------------------------------------------------
    _memlib = "memory_library"

    samples += [
        ("open memory library", [], _memlib),
        ("show my memories", [], _memlib),
        ("browse memories", [], _memlib),
        ("open the memory panel", [], _memlib),
    ]

    # ------------------------------------------------------------------
    # STATE_PHYSICS — panel
    # ------------------------------------------------------------------
    _sp = "state_physics"

    samples += [
        ("open state physics", [], _sp),
        ("show state physics visualization", [], _sp),
        ("state-space visualization", [], _sp),
    ]

    # ------------------------------------------------------------------
    # IDE_WORKSPACE — panel
    # ------------------------------------------------------------------
    _ide = "ide_workspace"

    samples += [
        ("open IDE", [], _ide),
        ("open the editor", [], _ide),
        ("open terminal", [], _ide),
        ("open workspace", [], _ide),
        ("launch the code editor", [], _ide),
    ]

    # ------------------------------------------------------------------
    # RABBIT_POST — community
    # ------------------------------------------------------------------
    _rabbit = "rabbit_post"

    samples += [
        ("post this to Rabbit community", [], _rabbit),
        ("share this on the forum", [], _rabbit),
        ("create a Rabbit post about my project", [], _rabbit),
        ("publish to Rabbit", [], _rabbit),
    ]

    # ------------------------------------------------------------------
    # FIGMA — designs
    # ------------------------------------------------------------------
    _figma = "figma"

    samples += [
        ("show my Figma projects", [], _figma),
        ("open my Figma designs", [], _figma),
        ("list Figma components in the dashboard project", [], _figma),
        ("search my Figma files for the login screen", [], _figma),
    ]

    # ------------------------------------------------------------------
    # SIGMA — analytics
    # ------------------------------------------------------------------
    _sigma = "sigma"

    samples += [
        ("show my Sigma dashboards", [], _sigma),
        ("open the Sigma analytics report", [], _sigma),
        ("get Sigma data for Q1", [], _sigma),
    ]

    # ------------------------------------------------------------------
    # NONE — general chat, no tool needed (CRITICAL: must be well-represented)
    # ------------------------------------------------------------------
    _none = None

    samples += [
        # General conversation
        ("hello", [], _none),
        ("hi there", [], _none),
        ("how are you", [], _none),
        ("thanks", [], _none),
        ("thank you so much", [], _none),
        ("goodbye", [], _none),
        ("what can you do", [], _none),
        ("who are you", [], _none),
        ("tell me about yourself", [], _none),
        ("that's cool", [], _none),
        ("nice", [], _none),
        ("ok", [], _none),
        ("got it", [], _none),
        ("sure", [], _none),
        ("interesting", [], _none),

        # Knowledge questions (AI can answer from training)
        ("what is machine learning", [], _none),
        ("explain how neural networks work", [], _none),
        ("what is the difference between Python and JavaScript", [], _none),
        ("how does a database work", [], _none),
        ("explain quantum computing in simple terms", [], _none),
        ("what is the theory of relativity", [], _none),
        ("how do transformers work in NLP", [], _none),
        ("what is a REST API", [], _none),
        ("explain Docker containers", [], _none),
        ("what is Kubernetes", [], _none),
        ("how does blockchain work", [], _none),
        ("what is the capital of France", [], _none),
        ("who invented the internet", [], _none),
        ("explain the water cycle", [], _none),
        ("what are design patterns in software", [], _none),

        # Coding help (no tool needed)
        ("help me write a Python function to sort a list", [], _none),
        ("how do I fix this TypeError in JavaScript", [], _none),
        ("write a React component for a login form", [], _none),
        ("explain this SQL query", [], _none),
        ("debug this code for me", [], _none),
        ("convert this Python code to JavaScript", [], _none),
        ("optimize this algorithm", [], _none),
        ("what's wrong with this function", [], _none),
        ("help me with CSS flexbox", [], _none),
        ("write a bash script to backup files", [], _none),
        ("refactor this class to be more efficient", [], _none),
        ("explain the difference between async and sync", [], _none),
        ("how do I handle errors in Python", [], _none),
        ("write unit tests for this function", [], _none),

        # Math
        ("what is 234 times 567", [], _none),
        ("solve this equation: 2x + 5 = 15", [], _none),
        ("calculate the compound interest on $10,000", [], _none),

        # Opinions/advice
        ("what do you think about React vs Vue", [], _none),
        ("should I use PostgreSQL or MongoDB", [], _none),
        ("is it better to learn Python or JavaScript first", [], _none),
        ("what's the best way to learn machine learning", [], _none),

        # Meta questions about the platform (not needing tools)
        ("how does this platform work", [], _none),
        ("what features do you have", [], _none),
        ("what's your pricing", [], _none),
        ("how do credits work", [], _none),

        # Ambiguous but NOT tool-related
        ("I'm working on a project", [], _none),
        ("I need some help", [], _none),
        ("can you assist me", [], _none),
        ("I have a question", [], _none),
        ("let me think about that", [], _none),

        # Tricky: mentions "agent" but NOT wanting to build one
        ("what is an AI agent", [], _none),
        ("explain how agents work in reinforcement learning", [], _none),
        ("what's the difference between an agent and a bot", [], _none),
        ("how do autonomous agents make decisions", [], _none),

        # Tricky: mentions "search" but NOT web search
        ("search through this code for bugs", [], _none),
        ("search the documentation for authentication", [], _none),

        # Tricky: mentions "image" but NOT generation
        ("what does this image show", [], _none),
        ("explain this screenshot", [], _none),
        ("analyze the image I uploaded", [], _none),

        # Tricky: mentions "drive" but NOT Google Drive
        ("what drives customer behavior", [], _none),
        ("the main drive behind this project is innovation", [], _none),

        # Tricky: mentions "calendar" but NOT Google Calendar
        ("what calendar year are we in", [], _none),
        ("the fiscal calendar starts in April", [], _none),

        # Tricky: mentions "code" but NOT code_visualizer
        ("write me some code", [], _none),
        ("help me understand this code", [], _none),
        ("review my code", [], _none),
        ("what's the best code editor", [], _none),
    ]

    # ------------------------------------------------------------------
    # CROSS-SKILL CONTINUITY — follow-ups that should stick with active skill
    # ------------------------------------------------------------------

    # User answering Google Drive's questions (should stay with Drive)
    samples += [
        ("the reports folder", [
            {"role": "assistant", "content": "Where in your Google Drive should I save this file?"},
        ], _gdrive),
        ("csv format please", [
            {"role": "assistant", "content": "I can save this data to your Drive. What format — CSV, Excel, or Sheets?"},
        ], _gdrive),
    ]

    # General follow-ups in general chat context (should stay None)
    samples += [
        ("yes", [
            {"role": "assistant", "content": "Machine learning is a subset of AI that learns from data. Want me to explain specific algorithms?"},
        ], _none),
        ("tell me more", [
            {"role": "assistant", "content": "Transformers use self-attention mechanisms to process sequences in parallel."},
        ], _none),
        ("can you elaborate", [
            {"role": "assistant", "content": "Docker containers package applications with their dependencies for consistent deployment."},
        ], _none),
        ("thanks that helps", [
            {"role": "assistant", "content": "Here's the refactored version of your function with better error handling."},
        ], _none),
    ]

    # ==================================================================
    # EXPANDED TOOLS — 5-8 diverse synthetic samples per tool
    # ==================================================================

    # ── fetch_url ──
    samples += [
        ("fetch the content from https://example.com", [], "fetch_url"),
        ("get the page at this URL: https://api.github.com", [], "fetch_url"),
        ("download the raw content of https://httpbin.org/json", [], "fetch_url"),
        ("can you grab what's at this link", [], "fetch_url"),
        ("pull the data from that endpoint URL", [], "fetch_url"),
    ]

    # ── read_webpage ──
    samples += [
        ("read the webpage at https://docs.python.org/3/library/asyncio.html", [], "read_webpage"),
        ("read this article for me: https://medium.com/some-article", [], "read_webpage"),
        ("I want to read the content of this blog post", [], "read_webpage"),
        ("extract the clean text from this website page", [], "read_webpage"),
        ("parse this webpage and show me the main content", [], "read_webpage"),
        ("summarize the content at this URL for me", [], "read_webpage"),
    ]

    # ── read_many_pages ──
    samples += [
        ("read these 3 pages for me", [], "read_many_pages"),
        ("fetch content from all these URLs at once", [], "read_many_pages"),
        ("read multiple webpages in parallel", [], "read_many_pages"),
        ("I have 5 links, can you read all of them", [], "read_many_pages"),
        ("batch read these documentation pages", [], "read_many_pages"),
    ]

    # ── reddit_search ──
    samples += [
        ("search reddit for discussions about rust vs go", [], "reddit_search"),
        ("what does reddit say about the new MacBook", [], "reddit_search"),
        ("find reddit threads about the housing market", [], "reddit_search"),
        ("check r/programming for posts about Python 4", [], "reddit_search"),
        ("look for reddit recommendations on laptops under $1000", [], "reddit_search"),
        ("what are redditors saying about the latest iPhone", [], "reddit_search"),
    ]

    # ── image_search ──
    samples += [
        ("find images of golden retrievers", [], "image_search"),
        ("search for photos of the Eiffel Tower", [], "image_search"),
        ("look for pictures of modern kitchen designs", [], "image_search"),
        ("find reference images of mountain landscapes", [], "image_search"),
        ("search for stock photos of business meetings", [], "image_search"),
    ]

    # ── news_search ──
    samples += [
        ("what's the latest news about AI regulation", [], "news_search"),
        ("show me today's tech news", [], "news_search"),
        ("any breaking news about the stock market", [], "news_search"),
        ("find recent news articles about climate change policy", [], "news_search"),
        ("what happened in tech news this week", [], "news_search"),
        ("latest headlines about the US economy", [], "news_search"),
    ]

    # ── places_search ──
    samples += [
        ("find coffee shops near Times Square", [], "places_search"),
        ("search for Italian restaurants in San Francisco", [], "places_search"),
        ("where's the nearest gym in downtown LA", [], "places_search"),
        ("find coworking spaces near me in Austin", [], "places_search"),
        ("best sushi restaurants in Chicago", [], "places_search"),
    ]

    # ── youtube_search ──
    samples += [
        ("find YouTube videos about machine learning", [], "youtube_search"),
        ("search YouTube for cooking tutorials", [], "youtube_search"),
        ("look for React tutorial videos on YouTube", [], "youtube_search"),
        ("find YouTube reviews of the Tesla Model 3", [], "youtube_search"),
        ("search for yoga workout videos", [], "youtube_search"),
    ]

    # ── deep_research ──
    samples += [
        ("do deep research on quantum computing applications", [], "deep_research"),
        ("I need a thorough analysis of the EV market", [], "deep_research"),
        ("research everything about mRNA vaccine technology", [], "deep_research"),
        ("do an in-depth analysis of the SaaS business model", [], "deep_research"),
        ("comprehensive research on renewable energy trends 2026", [], "deep_research"),
        ("deep dive into the competitive landscape of AI startups", [], "deep_research"),
    ]

    # ── wikipedia ──
    samples += [
        ("look up the Wikipedia article on blockchain", [], "wikipedia"),
        ("what does Wikipedia say about the Roman Empire", [], "wikipedia"),
        ("check Wikipedia for info about Ada Lovelace", [], "wikipedia"),
        ("get the Wikipedia summary of quantum entanglement", [], "wikipedia"),
        ("read the Wikipedia page about the French Revolution", [], "wikipedia"),
    ]

    # ── memory_read ──
    samples += [
        ("read my memories about the project deadline", [], "memory_read"),
        ("what did I save about the API keys", [], "memory_read"),
        ("retrieve my stored notes about the architecture", [], "memory_read"),
        ("look up what I saved about database passwords", [], "memory_read"),
        ("find my memory about the deployment process", [], "memory_read"),
    ]

    # ── memory_write ──
    samples += [
        ("save this to my memory: the server IP is 10.0.0.1", [], "memory_write"),
        ("remember that my preferred language is Python", [], "memory_write"),
        ("store this info: API rate limit is 100 req/min", [], "memory_write"),
        ("save to memory that the deploy key is abc123", [], "memory_write"),
        ("write to my memory: next meeting is Friday 3pm", [], "memory_write"),
    ]

    # ── memory_stats ──
    samples += [
        ("how much memory am I using", [], "memory_stats"),
        ("show my memory usage statistics", [], "memory_stats"),
        ("how many memories do I have stored", [], "memory_stats"),
        ("what's my memory storage utilization", [], "memory_stats"),
        ("memory capacity and usage report", [], "memory_stats"),
    ]

    # ── hash_sphere_search ──
    samples += [
        ("search my hash sphere anchors for project notes", [], "hash_sphere_search"),
        ("find anchors related to architecture decisions", [], "hash_sphere_search"),
        ("search hash sphere for blockchain-verified records", [], "hash_sphere_search"),
        ("look through my anchored memories about auth", [], "hash_sphere_search"),
        ("query the hash sphere for deployment anchors", [], "hash_sphere_search"),
    ]

    # ── hash_sphere_anchor ──
    samples += [
        ("anchor this content to the hash sphere", [], "hash_sphere_anchor"),
        ("create a blockchain-verified memory anchor", [], "hash_sphere_anchor"),
        ("save this decision as a hash sphere anchor", [], "hash_sphere_anchor"),
        ("permanently anchor this important document", [], "hash_sphere_anchor"),
        ("create an immutable anchor for this contract", [], "hash_sphere_anchor"),
    ]

    # ── hash_sphere_list_anchors ──
    samples += [
        ("list all my hash sphere anchors", [], "hash_sphere_list_anchors"),
        ("show my anchored memories", [], "hash_sphere_list_anchors"),
        ("display all blockchain-verified anchors", [], "hash_sphere_list_anchors"),
        ("what anchors do I have in the hash sphere", [], "hash_sphere_list_anchors"),
        ("get my complete anchor history", [], "hash_sphere_list_anchors"),
    ]

    # ── hash_sphere_hash ──
    samples += [
        ("generate a hash sphere hash for this document", [], "hash_sphere_hash"),
        ("compute the hash for this content", [], "hash_sphere_hash"),
        ("create a cryptographic hash of this text", [], "hash_sphere_hash"),
        ("get the hash sphere signature for this data", [], "hash_sphere_hash"),
        ("hash this content for verification", [], "hash_sphere_hash"),
    ]

    # ── hash_sphere_resonance ──
    samples += [
        ("check resonance between these two concepts", [], "hash_sphere_resonance"),
        ("how related are these two memory anchors", [], "hash_sphere_resonance"),
        ("measure the resonance between these documents", [], "hash_sphere_resonance"),
        ("compare similarity of these two hash sphere entries", [], "hash_sphere_resonance"),
        ("what's the resonance score between A and B", [], "hash_sphere_resonance"),
    ]

    # ── weather ──
    samples += [
        ("what's the weather in New York", [], "weather"),
        ("will it rain tomorrow in London", [], "weather"),
        ("weather forecast for Tokyo this weekend", [], "weather"),
        ("is it going to snow in Denver today", [], "weather"),
        ("give me the 3-day forecast for Miami", [], "weather"),
        ("current temperature in Berlin", [], "weather"),
    ]

    # ── stock_crypto ──
    samples += [
        ("what's the current price of AAPL stock", [], "stock_crypto"),
        ("how much is Bitcoin worth right now", [], "stock_crypto"),
        ("check the Ethereum price", [], "stock_crypto"),
        ("what's Tesla trading at", [], "stock_crypto"),
        ("get the latest price of NVDA", [], "stock_crypto"),
        ("how's Solana doing today", [], "stock_crypto"),
    ]

    # ── generate_chart ──
    samples += [
        ("create a bar chart with this data", [], "generate_chart"),
        ("make a pie chart showing the distribution", [], "generate_chart"),
        ("generate a line chart of monthly revenue", [], "generate_chart"),
        ("plot a scatter chart of these data points", [], "generate_chart"),
        ("create a radar chart comparing these metrics", [], "generate_chart"),
        ("make a doughnut chart of market share", [], "generate_chart"),
    ]

    # ── visualize ──
    samples += [
        ("visualize this workflow as a diagram", [], "visualize"),
        ("draw a flowchart of the authentication process", [], "visualize"),
        ("create an SVG diagram of the system architecture", [], "visualize"),
        ("make a visual representation of this data flow", [], "visualize"),
        ("diagram the microservices communication pattern", [], "visualize"),
        ("render a network topology diagram", [], "visualize"),
    ]

    # ── get_current_time ──
    samples += [
        ("what time is it in Tokyo", [], "get_current_time"),
        ("what's the current date and time", [], "get_current_time"),
        ("current time in UTC please", [], "get_current_time"),
        ("what time zone am I in", [], "get_current_time"),
        ("what day is it today", [], "get_current_time"),
    ]

    # ── get_system_info ──
    samples += [
        ("show platform system information", [], "get_system_info"),
        ("what version is the platform running", [], "get_system_info"),
        ("get system diagnostics", [], "get_system_info"),
        ("show me the platform specs", [], "get_system_info"),
        ("system health check", [], "get_system_info"),
    ]

    # ── code_visualizer_scan ──
    samples += [
        ("scan this GitHub repo: https://github.com/org/repo", [], "code_visualizer_scan"),
        ("analyze the codebase at this URL", [], "code_visualizer_scan"),
        ("run an AST scan on this repository", [], "code_visualizer_scan"),
        ("scan https://github.com/facebook/react for me", [], "code_visualizer_scan"),
        ("do a full codebase analysis of this project", [], "code_visualizer_scan"),
        ("analyze the code structure of this GitHub repo", [], "code_visualizer_scan"),
    ]

    # ── code_visualizer_functions ──
    _cv_ctx = [{"role": "assistant", "content": "Scan complete. Found 245 functions."}]
    samples += [
        ("list all functions in the project", _cv_ctx, "code_visualizer_functions"),
        ("show me the API endpoints", _cv_ctx, "code_visualizer_functions"),
        ("what functions are defined in this codebase", _cv_ctx, "code_visualizer_functions"),
        ("show all endpoint definitions", _cv_ctx, "code_visualizer_functions"),
        ("list the function signatures", _cv_ctx, "code_visualizer_functions"),
    ]

    # ── code_visualizer_trace ──
    samples += [
        ("trace the dependency flow from the auth module", [], "code_visualizer_trace"),
        ("show me what depends on the database module", [], "code_visualizer_trace"),
        ("trace imports starting from main.py", [], "code_visualizer_trace"),
        ("follow the call chain from the login function", [], "code_visualizer_trace"),
        ("what modules depend on the config module", [], "code_visualizer_trace"),
    ]

    # ── code_visualizer_governance ──
    samples += [
        ("run governance check on the codebase", [], "code_visualizer_governance"),
        ("check architecture health score", [], "code_visualizer_governance"),
        ("assess code quality and architectural drift", [], "code_visualizer_governance"),
        ("run reachability analysis on the project", [], "code_visualizer_governance"),
        ("how healthy is the codebase architecture", [], "code_visualizer_governance"),
    ]

    # ── code_visualizer_graph ──
    samples += [
        ("show the full dependency graph", [], "code_visualizer_graph"),
        ("get the complete import dependency tree", [], "code_visualizer_graph"),
        ("display the full module graph", [], "code_visualizer_graph"),
        ("show all connections between modules", [], "code_visualizer_graph"),
        ("render the dependency visualization", [], "code_visualizer_graph"),
    ]

    # ── code_visualizer_pipeline ──
    samples += [
        ("get the pipeline flow for the data ingestion", [], "code_visualizer_pipeline"),
        ("show the auto-detected pipeline", [], "code_visualizer_pipeline"),
        ("what's the data processing pipeline look like", [], "code_visualizer_pipeline"),
        ("display the ETL pipeline flow", [], "code_visualizer_pipeline"),
        ("show the request handling pipeline", [], "code_visualizer_pipeline"),
    ]

    # ── code_visualizer_filter ──
    samples += [
        ("filter the graph by authentication", [], "code_visualizer_filter"),
        ("show only nodes related to database", [], "code_visualizer_filter"),
        ("filter the dependency graph for test files", [], "code_visualizer_filter"),
        ("show graph nodes matching 'router'", [], "code_visualizer_filter"),
        ("narrow the graph to just the API layer", [], "code_visualizer_filter"),
    ]

    # ── code_visualizer_by_type ──
    samples += [
        ("show all class nodes in the project", [], "code_visualizer_by_type"),
        ("list all API endpoints by type", [], "code_visualizer_by_type"),
        ("get all function nodes", [], "code_visualizer_by_type"),
        ("show all service nodes", [], "code_visualizer_by_type"),
        ("list all external service dependencies", [], "code_visualizer_by_type"),
    ]

    # ── agents_list ──
    samples += [
        ("list my agents", [], "agents_list"),
        ("show all my AI agents", [], "agents_list"),
        ("what agents do I have", [], "agents_list"),
        ("display my agent inventory", [], "agents_list"),
        ("give me a list of all my agents", [], "agents_list"),
    ]

    # ── agents_create ──
    samples += [
        ("create a new agent called DataCollector", [], "agents_create"),
        ("make a new agent for email monitoring", [], "agents_create"),
        ("set up a fresh agent named PriceTracker", [], "agents_create"),
        ("I want to create an AI agent", [], "agents_create"),
        ("spin up a new agent for web scraping", [], "agents_create"),
    ]

    # ── agents_start ──
    samples += [
        ("start the research agent", [], "agents_start"),
        ("run my scraper agent", [], "agents_start"),
        ("kick off the monitoring agent", [], "agents_start"),
        ("launch the data collection agent now", [], "agents_start"),
        ("activate agent DataCollector", [], "agents_start"),
    ]

    # ── agents_stop ──
    samples += [
        ("stop the running agent", [], "agents_stop"),
        ("halt agent execution", [], "agents_stop"),
        ("pause the data collector agent", [], "agents_stop"),
        ("kill the running agent process", [], "agents_stop"),
        ("stop agent DataCollector", [], "agents_stop"),
    ]

    # ── agents_status ──
    samples += [
        ("what's the status of my agent", [], "agents_status"),
        ("is the scraper agent still running", [], "agents_status"),
        ("check agent health status", [], "agents_status"),
        ("get the current state of my monitoring agent", [], "agents_status"),
        ("is agent DataCollector active right now", [], "agents_status"),
    ]

    # ── agents_delete ──
    samples += [
        ("delete the test agent", [], "agents_delete"),
        ("remove agent OldScraper from my account", [], "agents_delete"),
        ("permanently delete this agent", [], "agents_delete"),
        ("I want to get rid of the unused agent", [], "agents_delete"),
        ("destroy the broken agent", [], "agents_delete"),
    ]

    # ── agents_sessions ──
    samples += [
        ("show the sessions for my agent", [], "agents_sessions"),
        ("list all runs of agent DataCollector", [], "agents_sessions"),
        ("what sessions has this agent had", [], "agents_sessions"),
        ("show execution history for my agent", [], "agents_sessions"),
        ("display the agent's run history", [], "agents_sessions"),
    ]

    # ── agents_session_steps ──
    samples += [
        ("what steps did the agent take in that run", [], "agents_session_steps"),
        ("show the execution steps for session abc123", [], "agents_session_steps"),
        ("detail the actions from the last agent run", [], "agents_session_steps"),
        ("what did the agent do step by step", [], "agents_session_steps"),
        ("break down the agent's execution steps", [], "agents_session_steps"),
    ]

    # ── agents_session_trace ──
    samples += [
        ("show the full execution trace", [], "agents_session_trace"),
        ("get the complete waterfall trace for that run", [], "agents_session_trace"),
        ("show trace with costs and safety flags", [], "agents_session_trace"),
        ("detailed execution trace with timing", [], "agents_session_trace"),
        ("give me the full session trace including token costs", [], "agents_session_trace"),
    ]

    # ── agents_session_detail ──
    samples += [
        ("show detailed info for this session", [], "agents_session_detail"),
        ("get full details about agent run abc123", [], "agents_session_detail"),
        ("what happened in this specific session", [], "agents_session_detail"),
        ("complete session information please", [], "agents_session_detail"),
        ("show everything about this agent execution", [], "agents_session_detail"),
    ]

    # ── agents_metrics ──
    samples += [
        ("get metrics for my agent", [], "agents_metrics"),
        ("show agent run statistics", [], "agents_metrics"),
        ("what's the success rate of my agent", [], "agents_metrics"),
        ("how many tokens has the agent used", [], "agents_metrics"),
        ("agent performance metrics and cost breakdown", [], "agents_metrics"),
    ]

    # ── agents_session_cancel ──
    samples += [
        ("cancel the running session", [], "agents_session_cancel"),
        ("abort the current agent execution", [], "agents_session_cancel"),
        ("terminate session abc123", [], "agents_session_cancel"),
        ("stop this agent session immediately", [], "agents_session_cancel"),
        ("cancel that agent run", [], "agents_session_cancel"),
    ]

    # ── agents_update ──
    samples += [
        ("update the agent's goal to monitor prices", [], "agents_update"),
        ("change the agent's model to GPT-4", [], "agents_update"),
        ("rename my agent to SmartScraper", [], "agents_update"),
        ("update the agent's system prompt", [], "agents_update"),
        ("change the temperature setting for my agent", [], "agents_update"),
        ("assign new tools to my agent", [], "agents_update"),
    ]

    # ── agents_available_tools ──
    samples += [
        ("what tools can agents use", [], "agents_available_tools"),
        ("list all tools available to agents", [], "agents_available_tools"),
        ("which tools can I give to my agent", [], "agents_available_tools"),
        ("show the agent tool catalog", [], "agents_available_tools"),
        ("what capabilities can agents have", [], "agents_available_tools"),
    ]

    # ── agents_templates ──
    samples += [
        ("list available agent templates", [], "agents_templates"),
        ("show me agent template options", [], "agents_templates"),
        ("what pre-built agent templates exist", [], "agents_templates"),
        ("are there any agent starter templates", [], "agents_templates"),
        ("show blueprint templates for agents", [], "agents_templates"),
    ]

    # ── agents_versions ──
    samples += [
        ("show version history for this agent", [], "agents_versions"),
        ("what versions has my agent gone through", [], "agents_versions"),
        ("display the agent change log", [], "agents_versions"),
        ("agent version history and diffs", [], "agents_versions"),
        ("list previous configurations of this agent", [], "agents_versions"),
    ]

    # ── schedule_agent ──
    samples += [
        ("schedule the agent to run every hour", [], "schedule_agent"),
        ("set up a daily schedule for my agent", [], "schedule_agent"),
        ("make the agent run on a cron schedule", [], "schedule_agent"),
        ("automate agent runs every 6 hours", [], "schedule_agent"),
        ("set recurring execution for the agent weekly", [], "schedule_agent"),
    ]

    # ── run_snapshot ──
    samples += [
        ("show a snapshot of the last run", [], "run_snapshot"),
        ("get detailed snapshot of session abc123", [], "run_snapshot"),
        ("capture the results of the most recent agent run", [], "run_snapshot"),
        ("snapshot of that agent execution", [], "run_snapshot"),
        ("show the run summary and results", [], "run_snapshot"),
    ]

    # ── list_workspace_tools ──
    samples += [
        ("what tools are available in my workspace", [], "list_workspace_tools"),
        ("show all workspace tools grouped by category", [], "list_workspace_tools"),
        ("list every tool I can use", [], "list_workspace_tools"),
        ("display the full tool catalog", [], "list_workspace_tools"),
        ("what capabilities does my workspace have", [], "list_workspace_tools"),
    ]

    # ── agent_snapshot ──
    samples += [
        ("show the full config of my agent", [], "agent_snapshot"),
        ("get the complete agent configuration", [], "agent_snapshot"),
        ("what are the current settings for agent DataCollector", [], "agent_snapshot"),
        ("dump the agent's full setup", [], "agent_snapshot"),
        ("detailed agent configuration snapshot", [], "agent_snapshot"),
    ]

    # ── session_log ──
    samples += [
        ("show the session log", [], "session_log"),
        ("get the current chat session's tool usage log", [], "session_log"),
        ("how many tools were called in this session", [], "session_log"),
        ("display session metrics and token counts", [], "session_log"),
        ("what happened in this chat session so far", [], "session_log"),
    ]

    # ── workspace_snapshot ──
    samples += [
        ("give me a workspace overview", [], "workspace_snapshot"),
        ("full workspace status report", [], "workspace_snapshot"),
        ("what's in my workspace right now", [], "workspace_snapshot"),
        ("show everything in my workspace", [], "workspace_snapshot"),
        ("workspace summary with all agents and tools", [], "workspace_snapshot"),
    ]

    # ── run_agent ──
    samples += [
        ("run the agent with this goal", [], "run_agent"),
        ("execute agent DataCollector now", [], "run_agent"),
        ("directly run this agent", [], "run_agent"),
        ("kick off an agent run with a specific goal", [], "run_agent"),
        ("trigger the agent to execute", [], "run_agent"),
    ]

    # ── present_options ──
    samples += [
        ("show me options to choose from", [], "present_options"),
        ("present interactive choices for the user", [], "present_options"),
        ("give me a multiple choice selection", [], "present_options"),
        ("display action options", [], "present_options"),
        ("offer me a list of choices", [], "present_options"),
    ]

    # ── generate_image ──
    samples += [
        ("generate an image of a sunset over the ocean", [], "generate_image"),
        ("create a picture of a futuristic city", [], "generate_image"),
        ("make me an image of a cute robot", [], "generate_image"),
        ("draw an illustration of a forest cabin", [], "generate_image"),
        ("generate artwork of a neon cyberpunk street", [], "generate_image"),
    ]

    # ── generate_audio ──
    samples += [
        ("convert this text to speech", [], "generate_audio"),
        ("read this aloud for me", [], "generate_audio"),
        ("generate speech from this paragraph", [], "generate_audio"),
        ("turn this text into an audio file", [], "generate_audio"),
        ("create a voiceover for this script", [], "generate_audio"),
    ]

    # ── generate_music ──
    samples += [
        ("generate some background music", [], "generate_music"),
        ("create a lofi beats track", [], "generate_music"),
        ("compose ambient music for studying", [], "generate_music"),
        ("make an upbeat electronic track", [], "generate_music"),
        ("generate a chill instrumental melody", [], "generate_music"),
    ]

    # ── generate_video ──
    samples += [
        ("generate a video of a product demo", [], "generate_video"),
        ("make a video from this description", [], "generate_video"),
        ("create a short video clip of a beach", [], "generate_video"),
        ("generate an animated explainer video", [], "generate_video"),
        ("produce a video of a drone flying over mountains", [], "generate_video"),
    ]

    # ── gmail_send ──
    samples += [
        ("send an email to john@example.com", [], "gmail_send"),
        ("email this report to the team", [], "gmail_send"),
        ("compose and send a Gmail to my manager", [], "gmail_send"),
        ("send this summary via email", [], "gmail_send"),
        ("mail the meeting notes to everyone", [], "gmail_send"),
    ]

    # ── gmail_read ──
    samples += [
        ("check my Gmail inbox", [], "gmail_read"),
        ("show my recent emails", [], "gmail_read"),
        ("any new emails in my inbox", [], "gmail_read"),
        ("read my unread Gmail messages", [], "gmail_read"),
        ("what emails did I get today", [], "gmail_read"),
    ]

    # ── slack_send ──
    samples += [
        ("send a message to #general on Slack", [], "slack_send"),
        ("post this update to the engineering Slack channel", [], "slack_send"),
        ("notify the team on Slack about the deployment", [], "slack_send"),
        ("send a Slack message to #alerts", [], "slack_send"),
        ("post to our Slack workspace", [], "slack_send"),
    ]

    # ── slack_read ──
    samples += [
        ("read the latest messages in #announcements", [], "slack_read"),
        ("what's new in the Slack channel", [], "slack_read"),
        ("show recent Slack messages from #engineering", [], "slack_read"),
        ("check what was posted in #random today", [], "slack_read"),
        ("read the last 10 Slack messages", [], "slack_read"),
    ]

    # ── send_email ──
    samples += [
        ("send an email via SendGrid", [], "send_email"),
        ("email this through the platform email service", [], "send_email"),
        ("dispatch an HTML email to this address", [], "send_email"),
        ("send a transactional email", [], "send_email"),
        ("deliver this message via email", [], "send_email"),
    ]

    # ── configure_smtp / delete_smtp ──
    samples += [
        ("configure my SMTP server for sending emails", [], "configure_smtp"),
        ("set up custom SMTP credentials", [], "configure_smtp"),
        ("connect my own email server", [], "configure_smtp"),
        ("configure SMTP with TLS on port 587", [], "configure_smtp"),
        ("remove the custom SMTP configuration", [], "delete_smtp"),
        ("delete my SMTP settings and use default", [], "delete_smtp"),
        ("revert to default email sending", [], "delete_smtp"),
    ]

    # ── State Physics (granular) ──
    samples += [
        ("show the full state physics universe", [], "sp_state"),
        ("get all state physics data", [], "sp_state"),
        ("display the complete universe state", [], "sp_state"),
        ("what does the state physics universe look like", [], "sp_state"),
        ("get the full universe snapshot", [], "sp_state"),
        ("reset the state physics universe", [], "sp_reset"),
        ("clear the universe and start fresh", [], "sp_reset"),
        ("reinitialize the state physics simulation", [], "sp_reset"),
        ("list all nodes in the hash sphere universe", [], "sp_nodes"),
        ("show every node in the simulation", [], "sp_nodes"),
        ("what nodes exist in the universe", [], "sp_nodes"),
        ("get universe metrics like entropy", [], "sp_metrics"),
        ("show node count and edge count in the universe", [], "sp_metrics"),
        ("what's the current entropy level", [], "sp_metrics"),
        ("create an identity node in the universe", [], "sp_identity"),
        ("add a user node to the hash sphere", [], "sp_identity"),
        ("register a new identity in state physics", [], "sp_identity"),
        ("run physics simulation for 10 steps", [], "sp_simulate"),
        ("simulate 50 physics steps", [], "sp_simulate"),
        ("advance the universe simulation", [], "sp_simulate"),
        ("create a galaxy-scale simulation", [], "sp_galaxy"),
        ("build a massive simulation with 500 users", [], "sp_galaxy"),
        ("launch a galaxy experiment", [], "sp_galaxy"),
        ("seed universe with demo data", [], "sp_demo"),
        ("populate the simulation with sample data", [], "sp_demo"),
        ("add demo users and transactions", [], "sp_demo"),
        ("get the asymmetry score", [], "sp_asymmetry"),
        ("what's the trust variance and Gini coefficient", [], "sp_asymmetry"),
        ("measure inequality in the universe", [], "sp_asymmetry"),
        ("update physics engine parameters", [], "sp_physics_config"),
        ("change the gravity constant", [], "sp_physics_config"),
        ("adjust spring constant and damping", [], "sp_physics_config"),
        ("configure entropy settings", [], "sp_entropy_config"),
        ("set position noise and trust decay", [], "sp_entropy_config"),
        ("adjust entropy engine parameters", [], "sp_entropy_config"),
        ("toggle entropy injection on", [], "sp_entropy_toggle"),
        ("disable entropy in the simulation", [], "sp_entropy_toggle"),
        ("turn entropy injection off", [], "sp_entropy_toggle"),
        ("inject a perturbation event", [], "sp_entropy_perturbation"),
        ("trigger a disruption in the universe", [], "sp_entropy_perturbation"),
        ("create a shock event with high magnitude", [], "sp_entropy_perturbation"),
        ("spawn an autonomous agent in the universe", [], "sp_agent_spawn"),
        ("add a bot to the state physics simulation", [], "sp_agent_spawn"),
        ("create an agent with 5000 budget", [], "sp_agent_spawn"),
        ("step the universe agent once", [], "sp_agent_step"),
        ("advance the autonomous agent by one step", [], "sp_agent_step"),
        ("kill the active universe agent", [], "sp_agent_kill"),
        ("terminate the simulation agent", [], "sp_agent_kill"),
        ("spawn multiple agents in the universe", [], "sp_agents_spawn"),
        ("add 5 agents to the simulation", [], "sp_agents_spawn"),
        ("kill all autonomous agents", [], "sp_agents_kill_all"),
        ("terminate every agent in the universe", [], "sp_agents_kill_all"),
        ("set up a stress test experiment", [], "sp_experiment"),
        ("run the zero_agent experiment", [], "sp_experiment"),
        ("start a long_run experiment", [], "sp_experiment"),
        ("set the memory cost multiplier", [], "sp_memory_cost"),
        ("adjust memory cost to 2x", [], "sp_memory_cost"),
        ("record a metrics snapshot", [], "sp_metrics_record"),
        ("save the current universe metrics to history", [], "sp_metrics_record"),
    ]

    # ── Community / Rabbit ──
    samples += [
        ("post this to the Rabbit community", [], "create_rabbit_post"),
        ("publish a new post on Rabbit", [], "create_rabbit_post"),
        ("write a post in the AI community", [], "create_rabbit_post"),
        ("create a discussion thread on Rabbit", [], "create_rabbit_post"),
        ("share this article on the Rabbit forum", [], "create_rabbit_post"),
        ("list all Rabbit communities", [], "list_rabbit_communities"),
        ("show available Rabbit forums", [], "list_rabbit_communities"),
        ("what communities exist on Rabbit", [], "list_rabbit_communities"),
        ("browse Rabbit community list", [], "list_rabbit_communities"),
        ("show recent posts in the community", [], "list_rabbit_posts"),
        ("get the latest Rabbit posts", [], "list_rabbit_posts"),
        ("what's trending on Rabbit", [], "list_rabbit_posts"),
        ("show top posts this week", [], "list_rabbit_posts"),
        ("upvote that Rabbit post", [], "rabbit_vote"),
        ("downvote this comment", [], "rabbit_vote"),
        ("vote on the community post", [], "rabbit_vote"),
        ("like that Rabbit post", [], "rabbit_vote"),
        ("create a new Rabbit community for developers", [], "create_rabbit_community"),
        ("start a new community called 'AI Research'", [], "create_rabbit_community"),
        ("make a Rabbit community for our team", [], "create_rabbit_community"),
        ("show me the AI community on Rabbit", [], "get_rabbit_community"),
        ("get details about the DevOps community", [], "get_rabbit_community"),
        ("info about the Python Rabbit community", [], "get_rabbit_community"),
        ("get that specific Rabbit post", [], "get_rabbit_post"),
        ("show me post #42 on Rabbit", [], "get_rabbit_post"),
        ("open this Rabbit post by ID", [], "get_rabbit_post"),
        ("search for posts about machine learning on Rabbit", [], "search_rabbit_posts"),
        ("find Rabbit discussions about Kubernetes", [], "search_rabbit_posts"),
        ("search community posts for 'deployment pipeline'", [], "search_rabbit_posts"),
        ("delete my Rabbit post", [], "delete_rabbit_post"),
        ("remove that post I made", [], "delete_rabbit_post"),
        ("delete post #15 from the community", [], "delete_rabbit_post"),
        ("comment on that Rabbit post", [], "create_rabbit_comment"),
        ("reply to the discussion thread", [], "create_rabbit_comment"),
        ("add a comment to post #42", [], "create_rabbit_comment"),
        ("show comments on that post", [], "list_rabbit_comments"),
        ("read the replies on this thread", [], "list_rabbit_comments"),
        ("what did people comment on that post", [], "list_rabbit_comments"),
        ("delete my comment on that thread", [], "delete_rabbit_comment"),
        ("remove my reply from the post", [], "delete_rabbit_comment"),
    ]

    # ── Developer ──
    samples += [
        ("run this Python code: print('hello')", [], "execute_code"),
        ("execute this JavaScript snippet", [], "execute_code"),
        ("run a bash script in the sandbox", [], "execute_code"),
        ("execute this code in a Docker container", [], "execute_code"),
        ("run this algorithm and show the output", [], "execute_code"),
        ("test this function by running it", [], "execute_code"),
        ("make an HTTP GET request to the user API", [], "http_request"),
        ("call this internal platform endpoint", [], "http_request"),
        ("POST data to /api/v1/agents", [], "http_request"),
        ("make an internal API request", [], "http_request"),
        ("hit the health check endpoint", [], "http_request"),
        ("fetch data from this external API at https://api.example.com", [], "external_http_request"),
        ("make a request to the third-party weather API", [], "external_http_request"),
        ("call the Stripe API to check payment status", [], "external_http_request"),
        ("use the dev tool to run Docker compose", [], "dev_tool"),
        ("dev tool for git operations", [], "dev_tool"),
        ("use the ED service dev tool for testing", [], "dev_tool"),
    ]

    # ── GitHub ──
    samples += [
        ("create a new GitHub repository called my-project", [], "github_create_repo"),
        ("initialize a private GitHub repo", [], "github_create_repo"),
        ("set up a new repo on GitHub", [], "github_create_repo"),
        ("list my GitHub repos", [], "github_list_repos"),
        ("show all repositories in my account", [], "github_list_repos"),
        ("what repos do I have on GitHub", [], "github_list_repos"),
        ("show files in the main branch of the repo", [], "github_list_files"),
        ("list the directory contents on GitHub", [], "github_list_files"),
        ("browse the repo file structure", [], "github_list_files"),
        ("download README.md from the repo", [], "github_download_file"),
        ("get the config file from GitHub", [], "github_download_file"),
        ("fetch the source code from GitHub", [], "github_download_file"),
        ("upload index.html to the repo", [], "github_upload_file"),
        ("push this file to GitHub", [], "github_upload_file"),
        ("commit and upload this code to the repository", [], "github_upload_file"),
        ("create a pull request from feature to main", [], "github_pull_request"),
        ("list open pull requests on the repo", [], "github_pull_request"),
        ("open a new PR for the bug fix", [], "github_pull_request"),
        ("show pending PRs", [], "github_pull_request"),
        ("create a GitHub issue for the bug", [], "github_issue"),
        ("list open issues in the repo", [], "github_issue"),
        ("file a bug report on GitHub", [], "github_issue"),
        ("show all unresolved issues", [], "github_issue"),
        ("show recent commits on main branch", [], "github_commit"),
        ("get the commit history", [], "github_commit"),
        ("what was committed last week", [], "github_commit"),
        ("comment on GitHub issue #42", [], "github_comment"),
        ("leave a review comment on the PR", [], "github_comment"),
        ("reply to the GitHub discussion", [], "github_comment"),
    ]

    # ── Git ──
    samples += [
        ("clone the repository from this URL", [], "git_clone"),
        ("git clone https://github.com/org/repo", [], "git_clone"),
        ("download the repo locally", [], "git_clone"),
        ("create a new git branch called feature-auth", [], "git_branch"),
        ("list all branches in the repo", [], "git_branch"),
        ("switch to the develop branch", [], "git_branch"),
        ("show all git branches", [], "git_branch"),
        ("merge feature-auth into main", [], "git_merge"),
        ("combine the branches together", [], "git_merge"),
        ("merge the hotfix branch", [], "git_merge"),
        ("push the changes to remote origin", [], "git_push"),
        ("git push to the upstream repo", [], "git_push"),
        ("push my commits to GitHub", [], "git_push"),
        ("pull the latest changes from remote", [], "git_pull"),
        ("git pull from origin main", [], "git_pull"),
        ("sync my local repo with remote", [], "git_pull"),
    ]

    # ── Tool Management ──
    samples += [
        ("create a custom tool for my API", [], "create_tool"),
        ("register a new HTTP tool", [], "create_tool"),
        ("define a custom endpoint tool", [], "create_tool"),
        ("add a new tool that calls my webhook", [], "create_tool"),
        ("list my custom tools", [], "list_tools"),
        ("show all user-created tools", [], "list_tools"),
        ("what custom tools do I have", [], "list_tools"),
        ("delete the old webhook tool", [], "delete_tool"),
        ("remove my custom scraper tool", [], "delete_tool"),
        ("update my custom tool's endpoint URL", [], "update_tool"),
        ("modify the tool description", [], "update_tool"),
        ("change the HTTP method for my tool", [], "update_tool"),
    ]

    # ── Platform API ──
    samples += [
        ("search the platform API for user endpoints", [], "platform_api_search"),
        ("find API endpoints for billing operations", [], "platform_api_search"),
        ("what platform APIs are available for agents", [], "platform_api_search"),
        ("search for authentication-related API routes", [], "platform_api_search"),
        ("look up platform endpoints matching 'webhook'", [], "platform_api_search"),
        ("call the platform API to get user info", [], "platform_api_call"),
        ("make an authenticated API call to /api/v1/users", [], "platform_api_call"),
        ("invoke the billing API endpoint", [], "platform_api_call"),
        ("call GET /api/v1/agents/list", [], "platform_api_call"),
        ("execute a platform API request", [], "platform_api_call"),
    ]

    # ── Filesystem / IDE ──
    samples += [
        ("read the file at /src/main.py", [], "file_read"),
        ("show me the contents of config.json", [], "file_read"),
        ("open and read this file", [], "file_read"),
        ("cat the Dockerfile", [], "file_read"),
        ("display the file content", [], "file_read"),
        ("write this content to a new file called output.txt", [], "file_write"),
        ("create a new file with this code", [], "file_write"),
        ("save this to /tmp/results.json", [], "file_write"),
        ("edit the import statement in this file", [], "file_edit"),
        ("replace 'old_var' with 'new_var' in main.py", [], "file_edit"),
        ("fix the typo in this file", [], "file_edit"),
        ("make multiple edits to the config file", [], "multi_edit"),
        ("batch edit: rename the class and update imports", [], "multi_edit"),
        ("apply several changes to this source file", [], "multi_edit"),
        ("list files in the project directory", [], "file_list"),
        ("show what's in the /src folder", [], "file_list"),
        ("directory listing of the project root", [], "file_list"),
        ("delete the temp file", [], "file_delete"),
        ("remove the old backup file", [], "file_delete"),
        ("delete the test output directory", [], "file_delete"),
        ("search for 'TODO' in all Python files", [], "grep_search"),
        ("find all occurrences of 'deprecated' in the codebase", [], "grep_search"),
        ("grep for error handling patterns", [], "grep_search"),
        ("find files named config.yaml", [], "find_by_name"),
        ("locate all Dockerfile in the project", [], "find_by_name"),
        ("find Python files matching '*_test.py'", [], "find_by_name"),
        ("run npm install", [], "run_command"),
        ("execute pip install -r requirements.txt", [], "run_command"),
        ("run the test suite", [], "run_command"),
        ("execute docker-compose up", [], "run_command"),
        ("run make build", [], "run_command"),
        ("check the status of that background command", [], "command_status"),
        ("is the build still running", [], "command_status"),
        ("check if the npm install finished", [], "command_status"),
    ]

    # ── Scraping ──
    samples += [
        ("scrape this webpage with browser automation", [], "scrape_page"),
        ("extract content from this dynamically loaded page", [], "scrape_page"),
        ("use Firecrawl to scrape this JavaScript-heavy site", [], "scrape_page"),
        ("scrape the product page with CSS selectors", [], "scrape_page"),
        ("extract data from this SPA website", [], "scrape_page"),
        ("scrape LinkedIn job listings", [], "scrape_platforms"),
        ("get Instagram profiles for these accounts", [], "scrape_platforms"),
        ("scrape TikTok videos about cooking", [], "scrape_platforms"),
        ("get Amazon product listings for laptops", [], "scrape_platforms"),
        ("scrape Zillow listings in my zip code", [], "scrape_platforms"),
        ("get Airbnb listings in Barcelona", [], "scrape_platforms"),
        ("scrape Google Maps for dental clinics", [], "scrape_platforms"),
        ("collect YouTube video data", [], "scrape_platforms"),
    ]

    # ── Documents ──
    samples += [
        ("create a Google Sheet with this data", [], "google_sheets"),
        ("read data from my spreadsheet", [], "google_sheets"),
        ("append rows to the Google Sheet", [], "google_sheets"),
        ("update cells in the spreadsheet", [], "google_sheets"),
        ("export the sheet as CSV", [], "google_sheets"),
        ("list my Google Sheets", [], "google_sheets"),
        ("create a Google Doc with this content", [], "google_docs"),
        ("read my Google document", [], "google_docs"),
        ("append to my Google Doc", [], "google_docs"),
        ("write a formatted document in Google Docs", [], "google_docs"),
        ("list my Google Docs files", [], "google_docs"),
        ("create a presentation about AI trends", [], "create_presentation"),
        ("make a slide deck for my quarterly review", [], "create_presentation"),
        ("generate a 10-slide presentation on cloud computing", [], "create_presentation"),
        ("build a pitch deck with AI-generated images", [], "create_presentation"),
        ("create slides for the team meeting", [], "create_presentation"),
    ]

    # ── Orchestrator ──
    samples += [
        ("build a new agent for monitoring prices", [], "build_agent"),
        ("create and configure a complete agent from scratch", [], "build_agent"),
        ("I need an agent built for customer support", [], "build_agent"),
        ("build me an AI assistant agent", [], "build_agent"),
        ("modify the existing agent to add more tools", [], "continue_build"),
        ("extend my agent's capabilities with new instructions", [], "continue_build"),
        ("update the agent build with better prompts", [], "continue_build"),
        ("send guidance to the builder while it's working", [], "message_build"),
        ("tell the builder to focus on error handling", [], "message_build"),
        ("guide the build process with a correction", [], "message_build"),
        ("cancel the current agent run", [], "stop_run"),
        ("abort the active run immediately", [], "stop_run"),
        ("stop the running execution", [], "stop_run"),
        ("set up a trigger for every 6 hours", [], "set_trigger"),
        ("create a daily automated trigger", [], "set_trigger"),
        ("schedule a minutely trigger for monitoring", [], "set_trigger"),
        ("name my workspace 'Marketing Hub'", [], "set_workspace_name"),
        ("rename the workspace to 'Dev Team'", [], "set_workspace_name"),
        ("brand this workspace as 'Sales Ops'", [], "set_workspace_name"),
        ("open the interface editor for this agent", [], "open_interface_editor"),
        ("launch the React app builder for my agent", [], "open_interface_editor"),
        ("create a UI for the agent's database", [], "open_interface_editor"),
        ("what do you remember about me", [], "get_user_memory"),
        ("recall my preferences and role", [], "get_user_memory"),
        ("what facts do you know about me", [], "get_user_memory"),
        ("remember that I'm a backend developer at Acme", [], "update_user_memory"),
        ("save that my timezone is Pacific", [], "update_user_memory"),
        ("store this fact: I prefer dark mode", [], "update_user_memory"),
        ("list all agent databases in the workspace", [], "list_workspace_databases"),
        ("what databases are available for cross-agent queries", [], "list_workspace_databases"),
        ("show all data sources in my workspace", [], "list_workspace_databases"),
        ("query the scraper agent's database with SQL", [], "query_cross_agent_database"),
        ("run a SELECT query against the monitor agent's data", [], "query_cross_agent_database"),
        ("read data from another agent's database", [], "query_cross_agent_database"),
        ("how many credits do I have left", [], "get_credits_info"),
        ("check my billing balance and usage", [], "get_credits_info"),
        ("what's my current credit status", [], "get_credits_info"),
        ("how much have I spent this month", [], "get_credits_info"),
        ("show me upgrade options", [], "present_billing_offer"),
        ("what plans are available for upgrade", [], "present_billing_offer"),
        ("I want to upgrade my subscription", [], "present_billing_offer"),
    ]

    # ── Stock Market ──
    samples += [
        ("get detailed stock data for Tesla", [], "stock_market_data"),
        ("show historical prices for AAPL over the last year", [], "stock_market_data"),
        ("get stock news with sentiment for NVDA", [], "stock_market_data"),
        ("5-year price history for Microsoft stock", [], "stock_market_data"),
        ("stock market news and sentiment for AMZN", [], "stock_market_data"),
        ("compare GOOG vs MSFT historical performance", [], "stock_market_data"),
    ]

    # ── OAuth Integrations ──
    samples += [
        ("create a page in Notion", [], "notion"),
        ("search my Notion databases", [], "notion"),
        ("update a Notion page", [], "notion"),
        ("query my Notion project tracker", [], "notion"),
        ("list all Notion databases", [], "notion"),
        ("send a message on Discord", [], "discord"),
        ("read Discord channel messages", [], "discord"),
        ("list my Discord servers", [], "discord"),
        ("post an announcement on Discord", [], "discord"),
        ("list my Asana tasks", [], "asana"),
        ("create a task in Asana", [], "asana"),
        ("update an Asana task status", [], "asana"),
        ("show my Asana projects", [], "asana"),
        ("show my ClickUp tasks", [], "clickup"),
        ("create a ClickUp task", [], "clickup"),
        ("list ClickUp spaces", [], "clickup"),
        ("update a ClickUp task", [], "clickup"),
        ("create an issue in Linear", [], "linear"),
        ("list my Linear projects", [], "linear"),
        ("update a Linear issue", [], "linear"),
        ("show my Linear team's issues", [], "linear"),
        ("show my Monday.com boards", [], "monday"),
        ("create an item on Monday", [], "monday"),
        ("list Monday.com board items", [], "monday"),
        ("list my Miro boards", [], "miro"),
        ("create a sticky note on Miro", [], "miro"),
        ("get my Miro board content", [], "miro"),
        ("create a Jira issue", [], "atlassian"),
        ("search Confluence pages", [], "atlassian"),
        ("list Jira projects", [], "atlassian"),
        ("find Confluence documentation", [], "atlassian"),
        ("create a Zoom meeting for tomorrow", [], "zoom"),
        ("list my Zoom recordings", [], "zoom"),
        ("get Zoom meeting participants", [], "zoom"),
        ("schedule a Zoom call", [], "zoom"),
        ("show my Calendly events", [], "calendly"),
        ("list Calendly scheduling links", [], "calendly"),
        ("check Calendly invitees", [], "calendly"),
        ("list my Dropbox files", [], "dropbox"),
        ("upload a file to Dropbox", [], "dropbox"),
        ("share a Dropbox folder", [], "dropbox"),
        ("download from Dropbox", [], "dropbox"),
        ("show my Dribbble shots", [], "dribbble"),
        ("list Dribbble design projects", [], "dribbble"),
        ("get Dribbble shot details", [], "dribbble"),
        ("get Typeform responses", [], "typeform"),
        ("list my Typeform forms", [], "typeform"),
        ("export Typeform survey results", [], "typeform"),
        ("list HubSpot contacts", [], "hubspot"),
        ("create a deal in HubSpot", [], "hubspot"),
        ("show HubSpot pipeline stages", [], "hubspot"),
        ("add a contact to HubSpot", [], "hubspot"),
        ("query Salesforce leads", [], "salesforce"),
        ("create a Salesforce record", [], "salesforce"),
        ("update a Salesforce opportunity", [], "salesforce"),
        ("search Salesforce accounts", [], "salesforce"),
        ("list PipeDrive deals", [], "pipedrive"),
        ("create a PipeDrive contact", [], "pipedrive"),
        ("check PipeDrive pipeline", [], "pipedrive"),
        ("search Attio contacts", [], "attio"),
        ("create an Attio record", [], "attio"),
        ("list Attio companies", [], "attio"),
        ("list Zoho CRM records", [], "zoho_crm"),
        ("create a Zoho lead", [], "zoho_crm"),
        ("search Zoho CRM contacts", [], "zoho_crm"),
        ("show my Mailchimp campaigns", [], "mailchimp"),
        ("create a Mailchimp campaign", [], "mailchimp"),
        ("list Mailchimp audiences", [], "mailchimp"),
        ("get Mailchimp analytics", [], "mailchimp"),
        ("list Airtable records", [], "airtable"),
        ("create an Airtable record", [], "airtable"),
        ("update an Airtable entry", [], "airtable"),
        ("query my Airtable base", [], "airtable"),
        ("list my GitLab projects", [], "gitlab"),
        ("create a GitLab issue", [], "gitlab"),
        ("show GitLab merge requests", [], "gitlab"),
        ("list GitLab pipelines", [], "gitlab"),
        ("post on LinkedIn", [], "linkedin"),
        ("share this on LinkedIn", [], "linkedin"),
        ("get my LinkedIn profile info", [], "linkedin"),
        ("post a tweet", [], "twitter_x"),
        ("search Twitter for mentions of our brand", [], "twitter_x"),
        ("read my Twitter timeline", [], "twitter_x"),
        ("post on X about our product launch", [], "twitter_x"),
        ("list my Xero invoices", [], "xero"),
        ("create an invoice in Xero", [], "xero"),
        ("show Xero bank transactions", [], "xero"),
        ("list Xero contacts", [], "xero"),
        ("check my Outlook emails", [], "microsoft"),
        ("send a Teams message to the channel", [], "microsoft"),
        ("list OneDrive files", [], "microsoft"),
        ("create a SharePoint document", [], "microsoft"),
        ("send an Outlook email", [], "microsoft"),
        ("search YouTube for tutorial videos", [], "youtube"),
        ("download this YouTube video", [], "youtube"),
        ("get YouTube channel info", [], "youtube"),
        ("find YouTube videos about Python", [], "youtube"),
    ]

    # ── Autonomous Builder ──
    samples += [
        ("I need a tool that doesn't exist yet", [], "auto_build_tool"),
        ("build a custom tool for currency conversion", [], "auto_build_tool"),
        ("can the platform create a new capability for this", [], "auto_build_tool"),
        ("automatically build a tool for PDF parsing", [], "auto_build_tool"),
        ("the platform should extend itself to handle this", [], "auto_build_tool"),
        ("list the tools that were built this session", [], "list_built_tools"),
        ("show dynamically created tools", [], "list_built_tools"),
        ("what tools did the builder create", [], "list_built_tools"),
        ("run the custom tool I just created", [], "execute_built_tool"),
        ("execute the dynamically built tool", [], "execute_built_tool"),
        ("use the auto-built currency converter", [], "execute_built_tool"),
        ("does a tool for this capability already exist", [], "check_tool_exists"),
        ("is there already a tool for PDF parsing", [], "check_tool_exists"),
        ("check if we have a translation tool", [], "check_tool_exists"),
    ]

    # ── File Operations (extended) ──
    samples += [
        ("download this file from the URL with curl", [], "file_download_curl"),
        ("fetch the binary file with authentication headers", [], "file_download_curl"),
        ("download with custom headers and follow redirects", [], "file_download_curl"),
        ("curl download from this authenticated endpoint", [], "file_download_curl"),
        ("upload this file to the API endpoint", [], "file_upload_curl"),
        ("multipart upload to the storage service", [], "file_upload_curl"),
        ("upload with custom headers to this URL", [], "file_upload_curl"),
        ("extract the ZIP file", [], "file_extract_zip"),
        ("unzip this archive to the project folder", [], "file_extract_zip"),
        ("decompress the downloaded archive", [], "file_extract_zip"),
        ("extract the tar.gz file", [], "file_extract_zip"),
    ]

    # ── Additional general chat (anchor the None class) ──
    samples += [
        ("thanks for the help", [], _none),
        ("that's really useful information", [], _none),
        ("interesting, tell me more", [], _none),
        ("okay sounds good", [], _none),
        ("I appreciate that", [], _none),
        ("makes sense to me", [], _none),
        ("got it, thanks", [], _none),
        ("perfect, that answers my question", [], _none),
        ("cool, what else can you do", [], _none),
        ("hmm let me think about that", [], _none),
        ("yeah that's what I was looking for", [], _none),
        ("no that's not what I meant", [], _none),
        ("can you explain that differently", [], _none),
        ("give me a summary of what we discussed", [], _none),
        ("what were we talking about", [], _none),
        ("never mind, forget about it", [], _none),
        ("how's your day going", [], _none),
        ("you're pretty smart", [], _none),
        ("lol that's funny", [], _none),
        ("I'm just testing you", [], _none),
    ]

    # ------------------------------------------------------------------
    # AGENT_ARCHITECT — create, manage, run, diagnose agents
    # ------------------------------------------------------------------
    _architect = "agent_architect"

    samples += [
        ("create a new agent for monitoring my website", [], _architect),
        ("build me an agent that scrapes news every morning", [], _architect),
        ("I want to create an AI agent for email automation", [], _architect),
        ("make an agent that watches my Google Drive for changes", [], _architect),
        ("set up an agent to post to Slack when PRs are merged", [], _architect),
        ("create an agent called DataCollector for web scraping", [], _architect),
        ("build a Discord bot agent", [], _architect),
        ("can you create an agent for me", [], _architect),
        ("I need an agent that monitors stock prices", [], _architect),
        ("design an agent that summarizes my emails daily", [], _architect),
        ("list my agents", [], _architect),
        ("show all my AI agents", [], _architect),
        ("what agents do I have", [], _architect),
        ("start the research agent", [], _architect),
        ("stop the running agent", [], _architect),
        ("delete my old scraper agent", [], _architect),
        ("diagnose why my agent is failing", [], _architect),
        ("run the data collection agent", [], _architect),
        ("configure my agent to use GPT-4", [], _architect),
        ("modify the monitoring agent to check every 5 minutes", [], _architect),
        ("show me agent sessions and logs", [], _architect),
        ("what tools does my agent have access to", [], _architect),
        ("set a trigger for my agent to run at 9am", [], _architect),
        ("how many agents do I have running", [], _architect),
        ("review the last run of my research agent", [], _architect),
    ]

    # Short-form / casual phrasing (production misclassifications)
    samples += [
        ("how many agents I have", [], _architect),
        ("how many agents do I have", [], _architect),
        ("create agent for me", [], _architect),
        ("create agent", [], _architect),
        ("make me an agent", [], _architect),
        ("build an agent", [], _architect),
        ("yes please create it", [
            {"role": "assistant", "content": "Would you like me to create an agent for you?"},
        ], _architect),
        ("yes please", [
            {"role": "assistant", "content": "I can create that agent using the Agent Architect. Shall I proceed?"},
        ], _architect),
        ("check my agents", [], _architect),
        ("show my agents", [], _architect),
        ("agent list", [], _architect),
        ("do I have any agents", [], _architect),
        ("what are my agents", [], _architect),
        ("how many agents are running", [], _architect),
        ("count my agents", [], _architect),
        ("agent status", [], _architect),
    ]

    # Architect follow-ups
    samples += [
        ("yes, build it with web search and email tools", [
            {"role": "assistant", "content": "I can create that agent. What tools should it have?"},
        ], _architect),
        ("name it PriceTracker and run it hourly", [
            {"role": "assistant", "content": "What should I name the agent and how often should it run?"},
        ], _architect),
        ("use groq llama model for it", [
            {"role": "assistant", "content": "Which LLM model should the agent use?"},
        ], _architect),
    ]

    # Anti-collision with "architecture" agent type — these contain "architect"
    # or "design" but are about AGENT MANAGEMENT, not system design.
    samples += [
        ("architect an agent for me", [], _architect),
        ("design an agent for data processing", [], _architect),
        ("help me design my agent", [], _architect),
        ("I need to architect an agent system", [], _architect),
        ("design an AI agent that automates reports", [], _architect),
        ("help me set up an agent", [], _architect),
        ("I want to design a new agent", [], _architect),
        ("architect a monitoring agent", [], _architect),
        ("can you design an agent for customer support", [], _architect),
        ("design me a scraping agent", [], _architect),
        ("agent architect", [], _architect),
        ("open agent architect", [], _architect),
        ("use the agent architect", [], _architect),
        ("go to agent architect", [], _architect),
        ("launch agent architect", [], _architect),
    ]

    return samples
