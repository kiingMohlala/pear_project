# PEAR Roadmap

## v0.1
- Chat (session memory)
- Notes
- File reading (PDF / DOCX summarization)
- Desktop control (open apps, folders, search files)
- Generic Agent base + Task model
- Tool Registry, Events, Planner memory

## v0.2
- LLM abstraction (`BaseLLM`, Ollama, OpenAI, Anthropic)
- Ollama integration (default local provider)
- PersonalAgent uses LLM for chat
- KnowledgeStore retrieval → grounded answers

## v0.21
- Better document retrieval (chunking / embeddings) — pending
- Improved memory search — keyword search landed; embeddings next

## v0.22
- LLM-assisted planner (`PlannerLLM` → `ExecutionPlan`)
- Task graph DAG (`TaskNode`, `TaskGraph`)
- Sequential executor + result aggregation
- Parent/child / dependency-aware execution
- Plan events + plan statistics in PlannerMemory
- CLI: `/plan`, `/graph`, `/plan-history`

## v0.3
- Real Legal Agent
  - Document resolution from Knowledge Store
  - Clause extraction (LLM + heuristic)
  - Risk analysis with severity tags
  - Executive summary / full review
  - Offline-safe heuristics when LLM is echo/unavailable
- Evaluation framework
  - `evaluation/sample_contracts/`
  - `evaluation/expected_outputs/`
  - `evaluation/metrics.py`
  - `evaluation/regression_tests.py`

## v0.31
- Streaming responses (`on_token` / `BaseLLM.stream` / CLI live tokens)

## v0.32
- Embeddings abstraction (Ollama / SentenceTransformers / OpenAI / Null)
- VectorStore (SQLite persistence, cosine top-k)
- Document chunking + hybrid semantic/keyword retrieval
- Optional re-ranking; agents unchanged
- Retrieval evaluation (Top-1 / Top-3)

## v0.33 — Background Job Engine
- `Job` model + `JobManager` priority queue
- Background worker (CLI stays responsive)
- Persist queue/running jobs (restart recovery)
- pause / resume / cancel / retry + progress
- Events: JobCreated, JobQueued, JobStarted, JobProgress, JobCompleted, JobFailed, JobCancelled
- CLI: `/jobs` `/queue` `/cancel-job` `/resume-job` `/retry-job`
- `Orchestrator.submit_job()` — planner & agents unchanged

## v0.34 — Execution Tracing & Observability
- Trace / Span / Tracer with nested timing
- Spans: request, planner, retrieval, LLM, tool, job
- EventBus carries trace_id / span_id
- SQLite persistence + `/traces` `/trace` `/metrics`
- Aggregate latency, success rate, retries

## v0.34b — Scheduler (partial early)
- One-shot / interval / daily / weekly via `/schedule` (already wired)
- Cron expressions, calendar, time zones, reminders — pending

## v0.40 — Finance Agent
- CSV / debit-credit statement import & normalization
- Rule-based categorization + ledger in KnowledgeStore
- Monthly cash flow, category spend, recurring, anomalies, recommendations
- Semantic Q&A over transactions
- Large imports → background jobs
- Tracing spans for parse / categorize / analyze

## v0.50 — Production Legal Agent
- PDF/DOCX/TXT import with type detection (NDA, employment, lease, …)
- Structured clause extraction (number, title, concepts, obligations, dates)
- Risk analysis, executive summary, missing-clause checklist
- Semantic Q&A over contracts
- Contract version comparison (added/removed/modified)
- Large docs → background jobs; tracing spans for parse/analyze/compare

## v0.60 — Workflow Engine
- Workflow / WorkflowStep / WorkflowRunner
- Sequential, conditional, parallel steps; variables `{{ctx}}`
- Approvals, checkpoints, resume after restart
- Built-ins: finance_monthly_review, contract_review_summary, import_analyze_report
- CLI: /workflows /run-workflow /workflow-status /cancel-workflow /approve-workflow

## v0.70 — Desktop Agent & Secure Automation
- Workspace sandbox (`~/PEAR_Workspace`)
- File tools: list/copy/move/rename/delete(trash)/mkdir
- Permission groups: read, write, delete, launch, capture
- Approval flow for destructive ops
- Tracing spans for every desktop action
- CLI: /desktop /files /workspace /permissions

## v0.80 — Browser Agent & Secure Web Automation
- BrowserAgent: navigate, search, extract, download, screenshot, save page
- Playwright when installed; simulated navigation fallback
- Permission groups: browser_read/write/download/upload/login
- Downloads under workspace; approval for write/download
- CLI: /browser /open /downloads /history /browser-permissions

## v0.90 — Connector Framework
- Connector lifecycle: connect / authenticate / execute / disconnect
- ConnectorRegistry + encrypted CredentialStore
- Reference: local_files, email, calendar, github
- Workflow step type `connector`
- CLI: /connectors /connect /disconnect /auth-status /credentials

## v1.00 — Multimodal Foundation
- BaseSpeech / BaseVision / MediaManager
- Whisper / cloud / offline speech; Tesseract / multimodal LLM / offline vision
- Media pipeline → KnowledgeStore semantic index
- Permissions: microphone, camera, screen_capture, image_processing
- Workflow step type `media`
- CLI: /listen /transcribe /ocr /describe-image /vision /media

## v1.10 — Plugin SDK
- Plugin manifest + lifecycle (install/load/enable/disable/uninstall)
- PluginManager discovery, deps, checksum verification
- Scoped PluginAPI for tools/agents/connectors/workflows/commands
- Example plugins: weather, notion, slack
- CLI: /plugins /plugin-info /plugin-enable /plugin-disable /plugin-remove

## v1.20 — Evaluation & Continuous Improvement
- EvaluationEngine with suite registry
- Benchmarks: planner, retrieval, legal, finance, desktop, workflow, media, plugins
- Metrics: success rate, score, latency; baseline regression detection
- History JSON/JSONL + CSV export for CI
- CLI: /evaluate /benchmarks /benchmark-history /compare-builds /quality-report
- `evaluation/run_nightly.py` for scheduled jobs

## v1.30 — Research Agent
- ResearchAgent: search → rank → synthesize → cite
- Source credibility scoring + dedupe
- Executive/detailed cited reports; KnowledgeStore caching
- Background jobs for deep research
- Tracing: research.search/rank/summarize/report
- CLI: /research /sources /research-report
- Evaluation suite: ranking, dedupe, citation quality

## v1.40 — Computer Use Agent
- ComputerUseAgent + ComputerController (pyautogui or sim)
- observe → locate (OCR) → act → verify
- Mouse, keyboard, scroll, drag, window focus
- Approval for destructive GUI actions
- CLI: /computer /capture-ui /click /type
- Evaluation suite: click, locate, observe, recovery

## v1.50 — Email Agent
- EmailAgent on Email connector + KnowledgeStore
- Sync, prioritize, semantic search, thread summary, drafts, follow-ups
- Attachment notes + background sync jobs
- Tracing: email.sync/index/search/summarize/compose
- CLI: /inbox /email-search /summarize-thread /draft-email

## v1.60 — Calendar Agent
- CalendarAgent on Calendar connector + KnowledgeStore
- NL scheduling, conflicts, free/busy, recurring, reminders
- Agenda summaries + semantic search
- Tracing: calendar.sync/search/schedule/reminder/summary
- CLI: /calendar /agenda /schedule /free-time /reminders

## v1.70 — Voice Assistant
- Wake-word detection (configurable phrase)
- STT via MediaManager / BaseSpeech; TTS via BaseTTS (system/offline)
- Conversation turns routed through planner
- Barge-in interrupt + mute
- Tracing: voice.listen/transcribe/plan/speak
- CLI: /voice /listen /mute /voice-settings

## v1.80 — Memory Intelligence
- Importance scoring (recency, frequency, feedback, outcomes)
- Semantic clustering + consolidation into summaries
- Decay/archival policies; stable preferences/facts protected
- Preference/fact extraction from user utterances
- CLI: /memories /memory-stats /memory-cleanup /memory-search
- Tracing: memory.score/consolidate/cluster/archive

## v1.90 — Multi-Agent Collaboration
- CollaborationManager: sequential, parallel, reviewer, consensus
- ReviewerAgent + CriticAgent
- Confidence scoring, disagreement detection, refine loops
- CLI: /collaborate /review /consensus
- Tracing: collab.run/agent/review/consensus

## v2.00 — Autonomous Goal Execution
- GoalManager with persistent lifecycle (pending→…→completed/failed)
- Dynamic step graphs, milestones, progress tracking
- Waiting states + pause/resume; adaptive replan on failure
- Integrates jobs, collaboration, tracing, EventBus
- CLI: /goals /goal /goal-status /pause-goal /resume-goal /cancel-goal /goal-create

## v2.10 — Learning & Self-Optimization
- LearningEngine consumes routes, collab, workflows, evaluations, goals
- Recommendations with confidence (no silent behavior changes)
- Planner bias / retrieval boost / collab mode suggestions (advisory)
- Apply + rollback tracking
- CLI: /learning /learning-report /recommendations /optimization-history

## v2.20 — Service Layer & Multi-User Platform
- REST + WebSocket API (`service/app.py`)
- Auth (admin/user/api_client) + per-user session isolation
- Streaming chat, goals/jobs/workflows/traces/memories/plugins/connectors
- Web dashboard at `/`
- Docker + Compose; `/health` `/ready` `/metrics`
- CLI remains process-local; same Orchestrator core

## v2.30 — Distributed Worker Runtime
- WorkerManager: register, heartbeat, drain/enable/disable
- Capability routing (gpu, browser, desktop, research, finance, legal, …)
- Dispatch protocol: ack, retry, timeout recovery
- Local thread pool + remote HTTP workers
- Metrics: dispatch latency, queue time, utilization
- CLI: /workers /worker-status /drain-worker /enable-worker /disable-worker

## v2.35 — Optional n8n Connector
- N8NConnector (webhook + REST), disabled by default
- Actions: list/execute workflows, execution status, cancel, callbacks
- CredentialStore API key / bearer auth
- CLI: /n8n /n8n-workflows /run-n8n /n8n-status
- Offline mocked tests

## v2.40 — Operations & Production Hardening
- Config profiles: development / testing / production
- Structured logging + correlation IDs
- Backup/restore + checksum verification
- Rate limiting, audit log, resource metrics
- Worker quarantine after repeated failures
- Admin CLI: scripts/admin_cli.py
- CLI: /backup /diagnostics /config

## v3.00 — Release Candidate
- Public API freeze + schema migration
- Architecture / developer / deployment / user docs
- E2E + performance suites, security review
- CI workflow + release checklist
- Config templates & packaging

## v3.0.0 (stable)
- Production validation complete (docs/VALIDATION_REPORT_v3.md)
- Public APIs frozen; schema version 3
- Published docs, CI, Docker packaging

## v3.1 (current) — Ecosystem & Maturity
- Roadmap: docs/ROADMAP_v3.1.md
- Optional connectors: Slack, Notion, Google Drive, Jira (disabled by default)
- Tutorials + sample workflows
- Opt-in planner learned bias (`planner_use_learned_bias`)
- Strict v3.0 API compatibility
- Thin client stub: clients/python/pear_client
- Cookbook draft: docs/COOKBOOK.md
- Updated progress in docs/ROADMAP_v3.1.md

## v3.10 — Controlled Recursive Self-Improvement
- SelfImprovementEngine: analyze → propose → validate → scorecard
- Sandbox parameter tweaks only (no code mutation)
- Full suite gate + auto-reject on regression thresholds
- Human approval required before deploy (default)
- Persistent history + rollback
- CLI: /self-improve /improvement-report /improvement-history /rollback-improvement
- Spans: self_improve.analyze/propose/validate/compare/rollback

## Security Hardening (v3.1)
- Session TTL/idle/revocation, login lockout
- IDOR guards, input/upload limits, credential rotation
- Security regression tests

## Beta Testing System (current) — PEAR Mobile closed beta
- Invitation keys with 30-day TTL, revoke, extend
- Single device/account binding
- Activation + feedback UI (`/beta`, `/beta/feedback`)
- Consent-gated diagnostics/telemetry
- Admin dashboard `/admin/beta`
- Seed pack: `python scripts/seed_beta_keys.py --count 20`

## AI Quant Research Lab (concept)
- Strategy DSL, parallel backtest, evolution, walk-forward/MC robustness
- Multi-objective rank + regime KB + explainability (no price prediction)
- Package: quant/ — docs/QUANT_LAB.md

## Quant v0.10 — Operator UX
- Dashboard, candidate/hypothesis views, decision explanations, lineage
- docs/QUANT_V010_UX.md

## Quant v0.9 — PEAR Quant Connector
- Research-only connector via ConnectorRegistry
- Forbidden: real orders, capital, trading credentials
- CLI /quant* · docs/QUANT_CONNECTOR_V09.md

## Quant Lab v0.8 — Independent Review & Ranking
- Disjoint independent validation (anti-leakage)
- Multi-dimensional scorecards + sample-aware confidence
- Hypothesis comparison by robustness, not return
- Research decisions + queryable lineage
- docs/QUANT_V08_REVIEW.md

## Quant Lab v0.7 — Evidence-Driven Hypotheses
- Immutable Hypothesis + falsification criteria
- Evidence-gated generation; ungrounded ideas rejected
- Spawn candidates into full research pipeline only
- Lineage: experiment → hypothesis → candidate → experiment
- docs/QUANT_V07_HYPOTHESIS.md

## Quant Lab v0.6 — Multi-Market Shadow Matrix
- Candidate × market × timeframe shadow trials
- Regime/liquidity analysis + robustness ranking (not raw return)
- Comparative reports with sample-size gates
- docs/QUANT_V06_MATRIX.md

## Quant Lab v0.5 — Live Shadow-Market Validation
- ShadowEngine: live feed, frozen candidates, zero orders
- kind=shadow ledger, server_ts, no broker API
- docs/QUANT_V05_SHADOW.md

## Quant Lab v0.4 — Research Intelligence & Experiment Memory
- Immutable sealed experiments + content hash
- Research memory: similar experiments, failure patterns, family/market summaries
- Analysis: OOS degradation, paper divergence, parameter stability
- Human research reports (no profitability claims)
- docs/QUANT_V04_RESEARCH.md

## Quant Lab v0.3 — Long-Horizon Paper Validation
- Frozen strategy trials, 30/60/90 checkpoints
- Execution costs + delay; backtest vs paper divergence (primary gate)
- Auto-retire on DD / consecutive losses / HIGH divergence
- Market data store + gap detection; restart recovery
- docs/QUANT_V03_VALIDATION.md

## Quant Lab v0.2 — Paper Trading Validation
- PaperTradingEngine + demo broker adapters (sim/OANDA practice/IB paper/MT demo)
- SQLite trade/signal store, rolling metrics, regime PnL
- Promotion ladder with auto demote/retire
- docs/QUANT_PAPER_v02.md

## v0.5
- Desktop Vision (screen understanding)

## v0.6
- Voice (Whisper + TTS)

## v0.7
- Automation (n8n integration)

## v1.0
- Autonomous Personal AI

---

## Design Principles
- Generic `Agent` base class with description + capabilities
- Central Tool Registry (agents request tools, do not own them)
- Tasks with parent/child support
- Events for observability
- Agents never call each other – only via Planner
- LLM provider is swappable; agents depend on `BaseLLM` only
- Planner owns *what* work to do; agents own *how*
- Measure agent quality with evaluation/regression, not only unit tests
