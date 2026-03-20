# RG Agent Engine

> **Part of the [ResonantGenesis](https://dev-swat.com) platform** — Agent framework with autonomous execution, goal pursuit, and multi-agent orchestration.

[![Status: Production](https://img.shields.io/badge/Status-Production-brightgreen.svg)]()
[![License: RG Source Available](https://img.shields.io/badge/License-RG%20Source%20Available-blue.svg)](LICENSE.txt)

## Features
- Agent creation and execution framework
- Autonomous daemon with goal pursuit
- World model and agent reasoning
- Multi-agent orchestration and swarm controller
- Project builder agent
- Tool execution with sandbox boundaries
- Delegation and cross-service escalation

## Volume Mounts
- `rg_llm` — Shared LLM client library (read-only)
- `platform_tools` — Shared agent tools (read-only)
- `build_projects` — User workspace storage

## Includes
- `shared/agent/` — Agent shared libraries (sandbox, delegation, concurrency, wallet)
- `agents/` — Agent configuration files

## Quick Start
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Deployment
- **Container**: `agent_engine_service` | **Port**: 8000
- **Server path**: `/home/deploy/RG_Agent_Engine`

---
**Organization**: [DevSwat-ResonantGenesis](https://github.com/DevSwat-ResonantGenesis) | **Platform**: [dev-swat.com](https://dev-swat.com)
