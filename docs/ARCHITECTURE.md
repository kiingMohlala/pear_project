# PEAR Architecture (v3.00)

## Overview

PEAR is a modular personal AI platform. Capabilities are layered so agents never call each other directly; the planner, task graph, jobs, goals, and collaboration manager coordinate work.

```
Clients (CLI / REST / WebSocket / Dashboard)
                │
         Service Layer (auth, sessions, rate limits)
                │
            Orchestrator
       ┌────────┼────────┐
   Planner   Memory    Workers
       │         │         │
  TaskGraph  Knowledge  Local/Remote
       │     Embeddings
   Executor
       │
   Agents ← Tool Registry ← Connectors ← Plugins
```

## Core principles

1. **Agent isolation** — collaboration only via planner/collaboration manager.
2. **Registries** — tools, connectors, plugins discovered dynamically.
3. **Persistence** — per-user session dirs; schema version in `.pear_schema.json`.
4. **Observability** — traces, events, audit log, metrics.
5. **Optional externals** — Ollama, Playwright, n8n, speech/vision degrade gracefully.

## Subsystems

| Subsystem | Role |
|-----------|------|
| Planner + TaskGraph | Decompose objectives; DAG execution |
| Memory + Intelligence | Working / long-term / knowledge + scoring |
| Jobs + Goals | Background and multi-day autonomy |
| Workers | Distributed capability routing |
| Collaboration | Sequential / parallel / reviewer / consensus |
| Learning | Advisory optimization recommendations |
| Service | Multi-user API + dashboard |
| Ops | Config profiles, backup, rate limits, audit |

## Security boundaries

- Token auth + roles (`admin`, `user`, `api_client`)
- Permission gates on tools/connectors
- Approval hooks for sensitive actions
- Plugin permission-scoped API
- Audit JSONL for auth and admin actions
