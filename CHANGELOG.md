# Changelog

## 3.10.0

- Controlled RSI: SelfImprovementEngine, proposals, scorecards, sandbox validation
- Auto-reject on regression; human approval before deploy
- CLI self-improve commands; reversible config-parameter changes only

## 3.1.0 (planning / scaffolding)

- v3.1 roadmap (ecosystem, optimization, clients)
- Optional connector scaffolds: Slack, Notion, GDrive, Jira
- Tutorials and sample workflow JSON
- Config: `planner_use_learned_bias` (default false)

## 3.0.0

Stable production release after validation:
- Full regression + E2E + perf suites green
- Multi-user stress: 30 concurrent chats, 0 errors, p95 ~56ms (EchoLLM offline)
- Major agent workflows exercised (legal, finance, research, desktop, browser, email, calendar, computer, collab, goals, workers)
- Schema migration + API freeze documented
- See docs/VALIDATION_REPORT_v3.md

## 3.0.0-rc1

- API freeze markers and schema migration (`core/version.py`)
- Architecture, developer, deployment, user, security docs
- E2E + performance test suites
- CI regression runner
- Release packaging templates

## 2.x

- Service layer, workers, ops hardening, n8n connector
- Goals, learning, collaboration, multimodal, plugins
- Specialist agents: legal, finance, desktop, browser, research, computer, email, calendar
